# -*- coding: utf-8 -*-
"""DOC 解析模块（gwtool/core/parsers/doc_parser.py）真实链路深探。

核心 fixture 是**真实的 Word 97-2003 二进制样张**（tests/fixtures/
sample.doc，由本机 WPS COM 生成并归档），零 mock：

- 纯 Python 分片表解析（FIB/CLX/PCD）用真实 OLE 结构驱动 —— 曾在此
  发现真实缺陷：PCD 的 fc 用 "<HII"（10B）解包越过 8B 的 PCD 边界，
  最后一个分片必然 struct.error，导致整条纯 Python 路径从未工作过，
  一直靠 raw_scan 兜底伪装正常。
- 畸形文件通过**真实字节篡改**样张构造（FIB 魔数、CLX 偏移），验证
  四级降级链（COM → soffice → olefile → raw_scan）的兜底语义。
- COM 完美转换路径需要本机 Word/WPS：探测到可用时顺带验证，没有则
  由纯 Python 路径承接（parse_doc 的转换层自动降级，无需 mock）。
"""
from __future__ import annotations

import shutil
import struct
import sys
from pathlib import Path

import pytest

from gwtool.core.parsers import doc_parser
from gwtool.core.parsers.doc_parser import (
    _extract_text,
    _extract_text_olefile,
    _extract_text_raw_scan,
    _looks_like_word_doc,
    _normalize_doc_text,
    _parse_clx,
    _text_to_tree,
    parse_doc,
)

FIXTURE_DOC = Path(__file__).parent / "fixtures" / "sample.doc"

pytestmark = pytest.mark.skipif(
    not FIXTURE_DOC.exists(), reason="缺少真实 .doc 样张 fixture")


def _mutated_doc(tmp_path: Path, offset: int, patch: bytes) -> str:
    """真实字节篡改样张：读原始 bytes、定点覆写、落盘为新文件。"""
    data = bytearray(FIXTURE_DOC.read_bytes())
    data[offset:offset + len(patch)] = patch
    out = tmp_path / "mutated.doc"
    out.write_bytes(bytes(data))
    return str(out)


def _stream_data_offset() -> int:
    """WordDocument 流数据在 .doc 文件中的真实字节偏移。

    流内偏移 ≠ 文件偏移：OLE 容器头、SAT、目录项都在流数据之前。直接拿
    FIB 的流内偏移（0x000A/0x01A2 等）当文件偏移去突变，砸烂的是容器头，
    连 raw_scan 都打不开。这里用 olefile 读出流头部真实字节，再到原始
    文件字节里定位，保证突变真正命中流内 FIB。
    """
    import olefile

    ole = olefile.OleFileIO(str(FIXTURE_DOC))
    try:
        head = ole.openstream("WordDocument").read(64)
    finally:
        ole.close()
    data = FIXTURE_DOC.read_bytes()
    pos = data.find(head)
    assert pos > 0, "样张中未定位到 WordDocument 流数据"
    return pos


# ---------------------------------------------------------- 结构粗筛
def test_looks_like_word_doc_real_sample():
    assert _looks_like_word_doc(str(FIXTURE_DOC)) is True


def test_looks_like_word_doc_rejects_plain_text(tmp_path):
    f = tmp_path / "plain.doc"
    f.write_text("这不是 OLE 文档", encoding="utf-8")
    assert _looks_like_word_doc(str(f)) is False


def test_looks_like_word_doc_rejects_truncated_ole(tmp_path):
    """只有 OLE magic 的 8 字节截断文件：olefile 打不开 → False。"""
    f = tmp_path / "trunc.doc"
    f.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    assert _looks_like_word_doc(str(f)) is False


def test_looks_like_word_doc_rejects_missing_file(tmp_path):
    assert _looks_like_word_doc(str(tmp_path / "nope.doc")) is False


# ---------------------------------------------------------- 纯 Python 解析
def test_extract_text_olefile_reads_full_text():
    """真实分片表解析：完整提取标题、编号标题与两段正文。"""
    text = _extract_text_olefile(str(FIXTURE_DOC))
    assert "公文汇编助手DOC解析样张" in text
    assert "一、生成背景" in text
    assert "编号层次分明" in text


def test_parse_doc_full_tree_structure():
    tree = parse_doc(str(FIXTURE_DOC))
    assert tree.title == "公文汇编助手DOC解析样张"
    types = [(b.type, b.text) for b in tree.blocks]
    assert types[0] == ("heading", "公文汇编助手DOC解析样张")
    assert any(t == "heading" and "一、生成背景" in s for t, s in types)  # _HEADING_RE 分支
    assert any(t == "paragraph" and "编号层次分明" in s for t, s in types)


def test_raw_scan_extracts_chinese_fragments():
    """兜底扫描：从真实 WordDocument 流提取 UTF-16 中文/字母数字片段。"""
    text = _extract_text_raw_scan(str(FIXTURE_DOC))
    assert "公文汇编助手DOC解析样张" in text
    assert all(len(p) >= 8 for p in text.split("\n"))   # 短片段过滤真实生效


def test_fib_magic_corrupt_falls_to_raw_scan(tmp_path):
    """FIB 魔数 0xA5EC 被篡改（流内定位，OLE 容器完好）：olefile 路径
    抛 ValueError → raw_scan 兜底仍出文本；整链 _extract_text 绝不抛。"""
    bad = _mutated_doc(tmp_path, _stream_data_offset(), b"\x00\x00")
    with pytest.raises(Exception):
        _extract_text_olefile(bad)
    assert "公文汇编助手DOC解析样张" in _extract_text_raw_scan(bad)
    assert "公文汇编助手DOC解析样张" in _extract_text(bad)


def test_wild_clx_offset_falls_to_raw_scan(tmp_path):
    """fcClx/lcbClx 指向虚假区间（流内 0x01A2/0x01A6 定点突变）：
    _parse_clx 抛 ValueError → 兜底 raw_scan 仍出文本。"""
    base = _stream_data_offset()
    bad = _mutated_doc(tmp_path, base + 0x01A2, struct.pack("<I", 0x7FFFFFF0))
    bad2 = _mutated_doc(tmp_path, base + 0x01A6, struct.pack("<I", 0))
    for p in (bad, bad2):
        with pytest.raises(Exception):
            _extract_text_olefile(p)
        assert "公文汇编助手DOC解析样张" in _extract_text_raw_scan(p)
        assert "公文汇编助手DOC解析样张" in _extract_text(p)


def test_extract_text_garbage_ole_returns_empty(tmp_path):
    """magic 对但结构全烂（OLE 打不开 + raw_scan 崩）：绝不再抛，返回空。"""
    f = tmp_path / "garbage.doc"
    f.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600)
    assert _extract_text(str(f)) == ""


# ---------------------------------------------------------- CLX 解析器
def test_parse_clx_raises_on_garbage():
    with pytest.raises(ValueError, match="CLX 结构异常"):
        _parse_clx(b"\x03\x00\x00")            # 非法类型字节


def test_parse_clx_raises_on_empty():
    with pytest.raises(ValueError, match="未找到分片表"):
        _parse_clx(b"")


def test_parse_clx_real_sample():
    """从真实样张提取 CLX 并解析出非空分片表（fc 带压缩标志位）。"""
    import olefile

    ole = olefile.OleFileIO(str(FIXTURE_DOC))
    try:
        word = ole.openstream("WordDocument").read()
        flags = struct.unpack_from("<H", word, 0x000A)[0]
        tbl = ole.openstream("1Table" if flags & 0x0200 else "0Table").read()
        fc_clx, lcb_clx = struct.unpack_from("<II", word, 0x01A2)
        pieces = _parse_clx(tbl[fc_clx:fc_clx + lcb_clx])
    finally:
        ole.close()
    assert pieces, "真实样张的 CLX 应至少含一个分片"
    for cp_start, cp_end, fc in pieces:
        assert cp_end > cp_start
        assert fc & 0x3FFFFFFF


def test_parse_clx_skips_prc_records():
    """真实 CLX 前垫一条 Prc 记录（t=1）：解析器跳过后仍找到分片表。"""
    import olefile

    ole = olefile.OleFileIO(str(FIXTURE_DOC))
    try:
        word = ole.openstream("WordDocument").read()
        flags = struct.unpack_from("<H", word, 0x000A)[0]
        tbl = ole.openstream("1Table" if flags & 0x0200 else "0Table").read()
        fc_clx, lcb_clx = struct.unpack_from("<II", word, 0x01A2)
        clx = tbl[fc_clx:fc_clx + lcb_clx]
    finally:
        ole.close()
    prc = b"\x01\x04\x00" + b"\x00\x11\x22\x33"   # t=1 + cb=4 + 4B 载荷
    pieces = _parse_clx(prc + clx)
    assert pieces, "跳过 Prc 后应解析出与原 CLX 一致的分片表"


def test_table_stream_name_flip_falls_to_other_table(tmp_path):
    """fWhichTblStm 位翻转（流内 0x000A 定点突变）：表格流名被误判 →
    命中「打开失败回退另一张表」分支，全文照常提取。"""
    import olefile

    data = bytearray(FIXTURE_DOC.read_bytes())
    flags_off = _stream_data_offset() + 0x000A
    flags = struct.unpack_from("<H", data, flags_off)[0]
    struct.pack_into("<H", data, flags_off, flags ^ 0x0200)
    out = tmp_path / "flipped.doc"
    out.write_bytes(bytes(data))
    ole = olefile.OleFileIO(str(out))
    try:
        has1, has0 = ole.exists("1Table"), ole.exists("0Table")
    finally:
        ole.close()
    assert has1 != has0, "样张应恰好只有一张表流"
    text = _extract_text_olefile(str(out))
    assert "公文汇编助手DOC解析样张" in text


# ---------------------------------------------------------- 文本规范化
def test_normalize_doc_text_control_chars():
    raw = "段一\r\x07段二\r\x0b段三\x0c段四\x13字段\x14\x15\x7f正文"
    out = _normalize_doc_text(raw)
    # \r\x07→\n；\r/\x0b/\x0c→\n（段二后两个换行）；\x13\x14\x15 删除；
    # \x7f 被过滤 —— 中间不产生换行
    assert out == "段一\n段二\n\n段三\n段四字段正文"


# ---------------------------------------------------------- COM/soffice 降级
def test_convert_via_com_rejects_non_word(tmp_path):
    """结构粗筛拦截：非 Word 的 OLE 不进 COM（防 SEH 崩进程）。"""
    f = tmp_path / "plain.doc"
    f.write_text("文本", encoding="utf-8")
    if not shutil.which("cmd") and sys.platform.startswith("win"):
        pytest.skip("环境无 cmd（理论不会发生在 Windows）")
    assert doc_parser._convert_via_com(str(f)) is None


def test_convert_via_soffice_unavailable_returns_none(tmp_path):
    """无 LibreOffice 的机器：直接 None（CI 与本机均无 soffice）。"""
    if shutil.which("soffice") or shutil.which("libreoffice"):
        pytest.skip("本机有 LibreOffice，走真实转换分支")
    assert doc_parser._convert_via_soffice(str(FIXTURE_DOC)) is None


def test_parse_doc_fallback_chain_produces_tree(tmp_path):
    """parse_doc 全链（真实机器无 soffice，COM 可用则完美转换，否则纯 Python）。"""
    tree = parse_doc(str(FIXTURE_DOC))
    assert tree.blocks
    assert tree.title


# ---------------------------------------------------------- 树结构边界
def test_text_to_tree_long_first_para_is_not_title():
    tree = _text_to_tree("这是一段超过五十个字符的长文本，" * 5 + "结尾\n正文段落")
    assert tree.title != tree.blocks[0].text     # 长首段不提升为标题
    assert tree.blocks[0].type == "paragraph"


def test_text_to_tree_empty_text():
    tree = _text_to_tree("")
    assert tree.blocks == []
    assert tree.title == ""
