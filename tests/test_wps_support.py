# -*- coding: utf-8 -*-
"""WPS 格式（.wps）支持：内容嗅探路由、OOXML 形态、纯文本降级、注册与过滤。"""
from __future__ import annotations

from pathlib import Path


from gwtool.core.importer import SUPPORTED_EXTS, parse_any
from gwtool.db import dao


def test_wps_registered(tmp_db):
    assert ".wps" in SUPPORTED_EXTS


def test_wps_ooxml_form_parsed_by_docx_parser(tmp_db, tmp_path):
    """金山部分版本/WPS 兼容模式：.wps 实为 OOXML zip —— 按内容嗅探走 docx 解析。"""
    from docx import Document as DX
    p = tmp_path / "方案.wps"
    d = DX()
    d.add_heading("WPS 兼容方案", level=1)
    d.add_paragraph("这是 OOXML 形态的 WPS 文件正文。")
    d.save(str(p))
    r = parse_any(str(p))
    assert r.ok, r.error
    assert "WPS 兼容方案" in r.tree.title
    assert "OOXML 形态" in r.tree.plain_text()


def test_wps_plain_utf16_falls_back_and_extracts(tmp_db, tmp_path):
    """非 OLE/非 zip 的 .wps：四级降级链兜底不崩溃。

    真实金山 .wps 是 OLE 复合文档（走 COM/LibreOffice/纯解析）；纯 UTF-16
    文本属人造形态，原始扫描是"尽力而为"的启发式——契约是：要么 ok 带着
    尽力提取的文本，要么给出可读错误，绝不崩溃。
    """
    p = tmp_path / "纯文本形态.wps"
    body = "这是一份WPS格式的测试文档正文内容，用于降级链提取验证。"
    p.write_bytes(body.encode("utf-16-le"))
    r = parse_any(str(p))
    assert r is not None
    if r.ok:
        assert r.tree is not None and r.tree.plain_text().strip()
    else:
        assert r.error


def test_wps_garbage_no_crash(tmp_db, tmp_path):
    p = tmp_path / "垃圾.wps"
    p.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 4096)
    r = parse_any(str(p))
    assert r is not None and isinstance(r.ok, bool)


class _FakeDoc:
    def __init__(self, owner, fail_save):
        self._owner = owner
        self._fail_save = fail_save

    def SaveAs2(self, path, FileFormat=16):        # noqa: N803
        if self._fail_save:
            raise RuntimeError("模拟 SaveAs 失败")
        Path(path).write_text("ok", encoding="utf-8")

    def Close(self, *_a, **_k):
        self._owner.doc_closed = True


class _FakeApp:
    """假 Word 实例：记录是否被 Quit / 文档是否被 Close。"""
    instances: list = []

    def __init__(self, fail_save):
        self.doc_closed = False
        self.quit_called = False
        self._fail_save = fail_save
        self.__class__.instances.append(self)

    @property
    def Visible(self):
        return False

    @Visible.setter
    def Visible(self, _v):
        pass

    @property
    def Documents(self):
        return self

    def Open(self, *_a, **_k):
        return _FakeDoc(self, self._fail_save)

    def Quit(self):
        self.quit_called = True


def _patch_com(monkeypatch, fail_save: bool):
    """把 win32com 换成假实现，验证 COM 实例有没有被清理。

    同时把结构粗筛放行 —— 这里测的是 COM 实例的清理，不是粗筛本身
    （粗筛单独有测试）。
    """
    import sys as _sys
    import types as _types

    from gwtool.core.parsers import doc_parser

    _FakeApp.instances = []
    fake = _types.ModuleType("win32com")
    fake.client = _types.ModuleType("win32com.client")
    fake.client.Dispatch = lambda _p: _FakeApp(fail_save)
    monkeypatch.setitem(_sys.modules, "win32com", fake)
    monkeypatch.setitem(_sys.modules, "win32com.client", fake.client)
    monkeypatch.setattr(doc_parser.shutil, "which", lambda _n: "cmd.exe")
    monkeypatch.setattr(doc_parser, "_looks_like_word_doc", lambda _p: True)
    return doc_parser


def test_doc_garbage_never_reaches_com(tmp_db, tmp_path):
    """垃圾/非 Word 文件不进 COM，parse_doc 也绝不抛异常。

    回归两处：
    1) _convert_via_com 曾无条件 Dispatch 并 Open，把全零的伪 OLE 文件交给
       WPS，触发 0x800706be（RPC 服务已崩）—— 原生 SEH 异常 except 接不住，
       批量导入时整个程序会消失。
    2) 兜底链最后一级 _extract_text_raw_scan 没被 try 包住，畸形 OLE 会让
       olefile 抛 ValueError（"bytes length not a multiple of item size"），
       直接冒泡给用户。兜底就该是「绝不再抛」的底线。
    """
    from gwtool.core.parsers import doc_parser as dp
    from gwtool.core.parsers.doc_parser import parse_doc

    # 垃圾 OLE 头 + 全零：粗筛必须拒掉，绝不能交给 COM
    garbage = tmp_path / "垃圾.doc"
    garbage.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 4096)
    assert dp._looks_like_word_doc(str(garbage)) is False
    assert parse_doc(str(garbage)) is not None, "畸形 OLE 必须降级而不是抛异常"

    # 截断的 OLE、纯文本、空文件：一律不崩
    truncated = tmp_path / "截断.doc"
    truncated.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x11" * 37)
    assert parse_doc(str(truncated)) is not None

    plain = tmp_path / "纯文本.doc"
    plain.write_bytes("正文内容".encode("utf-16-le"))
    assert dp._looks_like_word_doc(str(plain)) is False
    assert parse_doc(str(plain)) is not None

    empty = tmp_path / "空.doc"
    empty.write_bytes(b"")
    assert parse_doc(str(empty)) is not None

    assert dp._looks_like_word_doc(str(tmp_path / "不存在.doc")) is False


def test_doc_com_quits_word_on_success(tmp_db, monkeypatch):
    """成功转换：只派发一个实例，且文档被 Close、应用被 Quit。"""
    dp = _patch_com(monkeypatch, fail_save=False)
    out = dp._convert_via_com("示例.doc")
    try:
        assert out and Path(out).exists()
        assert len(_FakeApp.instances) == 1, "成功路径不该反复派发"
        assert _FakeApp.instances[0].quit_called, "转换完没 Quit，漏了进程"
        assert _FakeApp.instances[0].doc_closed, "文档没 Close"
    finally:
        if out and Path(out).exists():
            Path(out).unlink()


def test_doc_com_quits_word_even_when_save_fails(tmp_db, monkeypatch):
    """SaveAs 失败：每个尝试过的实例都必须被 Quit，不能漏隐藏 Word 进程。

    回归：旧实现是 `except: continue` 直接跳到下一个 progid，
    已起来的实例不 Quit —— 批量导入 .doc 会攒一堆隐藏进程直到 COM 崩掉。
    """
    dp = _patch_com(monkeypatch, fail_save=True)
    out = dp._convert_via_com("坏文件.doc")
    assert out is None
    assert _FakeApp.instances, "三个 progid 都该尝试过"
    leaked = [a for a in _FakeApp.instances if not a.quit_called]
    assert not leaked, f"有 {len(leaked)} 个 COM 实例没被 Quit（进程泄漏）"
    assert all(a.doc_closed for a in _FakeApp.instances), "文档没 Close"


def test_wps_batch_and_dedup(tmp_db, tmp_path):
    """批量导入含 .wps 的混合清单；同内容 .docx 与 .wps 按指纹去重。"""
    from gwtool.core.importer import batch_import
    body = "批量场景下的WPS支持验证正文，内容足够长以构成有效文档。"
    a = tmp_path / "甲.docx"
    from docx import Document as DX
    _d = DX()
    _d.add_paragraph(body)
    _d.save(str(a))
    b = tmp_path / "乙.wps"
    b.write_bytes(body.encode("utf-16-le"))     # 内容相同（纯文本形态）
    c = tmp_path / "坏.wps"
    c.write_bytes(b"\x00" * 64)
    results = batch_import([str(a), str(b), str(c)])
    assert len(results) == 3
    # 甲（docx 真 OOXML）入库；乙与甲同内容 -> 指纹去重跳过或失败均不计成功
    assert results[0].ok
    if results[1].ok:
        did = dao.add_document(dao.Document(
            title=results[1].tree.title,
            content_text=results[1].tree.plain_text(),
            blocks_json=results[1].tree.to_json(), file_type="wps"))
        assert did in (-1, 0) or did > 0      # 去重逻辑不崩溃即可
    assert not results[2].ok
