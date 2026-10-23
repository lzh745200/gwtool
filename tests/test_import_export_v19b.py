# -*- coding: utf-8 -*-
"""v1.10.0（B 批 T2）导入/导出链五项修复的回归。

审计实测（v1.9 只读审计 §导入导出）：
  · P1-05 ``.doc`` 链路 COM 优先：5 份 8.53 s vs 纯 Python 0.003 s（≈2405×），
    而 ``parse_doc`` 只取纯文本，COM 的"格式保真"收益被整体丢弃；
  · P1-06 Markdown/HTML 固定 utf-8 读取：GBK 文件整篇乱码且不报错；
  · P1-07 正文含 ``\\x00`` 时汇编导出抛英文
    ``ValueError: All strings must be XML compatible`` —— 一篇坏材料拖垮整批；
  · P1-09 一键排版加「　　」+ 模板 ``firstLineChars=200`` = 视觉 4 字缩进
    （PDF 首行 x0 143.4pt vs 111.4pt，差 32pt = 2 个三号字）；
  · P1-10 移交包里同名附件被塞进 excluded —— 交接出去的包**缺件**。

本文件按"正例 + 反例"逐项钉住修复后的契约。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from gwtool.core import exporter
from gwtool.core.formatter import (ensure_indent_placeholder, run_full_cleanup,
                                   strip_control_chars)
from gwtool.core.importer import friendly_import_error, parse_any, sanitize_tree
from gwtool.core.model import Block, DocTree, PARAGRAPH, TABLE
from gwtool.core.parsers import doc_parser
from gwtool.core.parsers.md_html_parser import parse_html, parse_md
from gwtool.db import dao

FIXTURE_DOC = Path(__file__).parent / "fixtures" / "sample.doc"
needs_doc = pytest.mark.skipif(not FIXTURE_DOC.exists(),
                               reason="缺少真实 .doc 样张 fixture")

_TMP_PARAGRAPH = "\u3000\u3000各单位要认真贯彻落实安全生产责任制。"


def _clean_tree(text: str) -> DocTree:
    return DocTree(title="测试", blocks=[Block(type=PARAGRAPH, text=text)])


# ============================================================ ① .doc 走哪条链
class TestDocComPolicy:
    """默认纯 Python、COM 仅显式开启（注入假 COM 验证"根本没被调用"）。"""

    @pytest.fixture(autouse=True)
    def _reset_policy(self, monkeypatch):
        """每个用例前后都把进程内设置与环境变量复原，避免污染其他用例。"""
        monkeypatch.setattr(doc_parser, "_prefer_com_setting", None)
        monkeypatch.delenv(doc_parser._ENV_PREFER_COM, raising=False)
        yield
        doc_parser.set_prefer_com(None)

    @needs_doc
    def test_default_never_calls_com(self, monkeypatch):
        """默认链路**一次也不调** ``_convert_via_com`` —— 这是 2405× 差距的来源。"""
        called = {"n": 0}

        def fake_com(_path):
            called["n"] += 1
            return None

        monkeypatch.setattr(doc_parser, "_convert_via_com", fake_com)
        tree = doc_parser.parse_doc(str(FIXTURE_DOC))
        assert called["n"] == 0, "默认不应尝试 COM（只取纯文本时纯属浪费）"
        assert any("样张" in (b.text or "") for b in tree.blocks)

    @needs_doc
    def test_prefer_com_opt_in_does_call_com(self, monkeypatch):
        """``prefer_com=True`` 时才走保真链；COM 失败仍回退纯 Python 不抛。"""
        called = {"n": 0}

        def fake_com(_path):
            called["n"] += 1
            return None            # 模拟"没装 Word/WPS"

        monkeypatch.setattr(doc_parser, "_convert_via_com", fake_com)
        tree = doc_parser.parse_doc(str(FIXTURE_DOC), prefer_com=True)
        assert called["n"] == 1
        assert any("样张" in (b.text or "") for b in tree.blocks), "COM 不可用须回退纯 Python"

    def test_prefer_com_enabled_env_and_override(self, monkeypatch):
        """入口优先级：显式设置 > 环境变量 > 默认关。"""
        assert doc_parser.prefer_com_enabled() is False
        monkeypatch.setenv(doc_parser._ENV_PREFER_COM, "1")
        assert doc_parser.prefer_com_enabled() is True
        doc_parser.set_prefer_com(False)
        assert doc_parser.prefer_com_enabled() is False, "显式设置应压过环境变量"
        doc_parser.set_prefer_com(None)
        assert doc_parser.prefer_com_enabled() is True, "复位后回到环境变量"

    @needs_doc
    def test_parse_any_passes_policy_through(self, monkeypatch):
        """``parse_any(..., prefer_doc_com=True)`` 应透传到 parse_doc。"""
        seen = {}

        real = doc_parser.parse_doc

        def spy(path, *, prefer_com=None):
            seen["prefer_com"] = prefer_com
            return real(path, prefer_com=False)

        monkeypatch.setattr(doc_parser, "parse_doc", spy)
        # importer 里是 `from .parsers.doc_parser import parse_doc` 的值绑定，
        # 需要同时替换 importer 命名空间里的引用才能被 spy 到。
        import gwtool.core.importer as importer_mod
        monkeypatch.setattr(importer_mod, "parse_doc", spy)
        r = parse_any(str(FIXTURE_DOC), prefer_doc_com=True)
        assert r.ok
        assert seen.get("prefer_com") is True


# ============================================================ ①b .doc 质量兜底
class TestDocQualityFallback:
    """纯 Python 结果"明显不可信"且本机有 COM 时，自动用 COM 重试一次。"""

    @pytest.fixture(autouse=True)
    def _reset_policy(self, monkeypatch):
        monkeypatch.setattr(doc_parser, "_prefer_com_setting", None)
        monkeypatch.delenv(doc_parser._ENV_PREFER_COM, raising=False)
        yield
        doc_parser.set_prefer_com(None)

    # ---------------- 判据本身
    def test_predicate_short_text_in_big_file(self, tmp_path):
        big = tmp_path / "big.doc"
        big.write_bytes(b"\x00" * (30 * 1024))
        assert doc_parser.is_suspicious_extraction(str(big), "") is True
        assert doc_parser.is_suspicious_extraction(str(big), "短") is True
        assert doc_parser.is_suspicious_extraction(str(big), "正" * 200) is False

    def test_predicate_small_file_not_suspicious(self, tmp_path):
        """便函/通知这类小文件字少是正常的，不能触发 COM（否则每份多 1.5~6s）。"""
        small = tmp_path / "small.doc"
        small.write_bytes(b"\x00" * 4096)
        assert doc_parser.is_suspicious_extraction(str(small), "关于召开会议的通知") is False

    def test_predicate_garbled_text(self, tmp_path):
        f = tmp_path / "g.doc"
        f.write_bytes(b"\x00" * 4096)
        assert doc_parser.is_suspicious_extraction(str(f), "\ufffd" * 40 + "正文") is True

    def test_predicate_normal_text(self, tmp_path):
        f = tmp_path / "n.doc"
        f.write_bytes(b"\x00" * (30 * 1024))
        text = "各单位要认真贯彻落实安全生产责任制，确保各项任务按期完成。" * 4
        assert doc_parser.is_suspicious_extraction(str(f), text) is False

    # ---------------- 重试行为（注入假 COM，CI 无 COM 也能跑）
    def _fake_com_docx(self, path: str, text: str):
        """构造一个假的"COM 转换产物目录"（内部是真 docx）。"""
        import tempfile as _tf

        from docx import Document as Docx

        outdir = Path(_tf.mkdtemp(prefix="fake_com_"))
        d = Docx()
        d.add_paragraph(text)
        d.save(str(outdir / (Path(path).stem + "_conv.docx")))
        return str(outdir)

    def test_retries_com_when_result_suspicious(self, tmp_path, monkeypatch):
        f = tmp_path / "可疑.doc"
        f.write_bytes(b"\x00" * (30 * 1024))
        good = "从 COM 保真链救回的正文：各单位要认真贯彻落实安全生产责任制。" * 2
        called = {"n": 0}

        def fake_com(path):
            called["n"] += 1
            return self._fake_com_docx(path, good)

        monkeypatch.setattr(doc_parser, "_extract_pure", lambda _p: "短")   # 不可信
        monkeypatch.setattr(doc_parser, "_convert_via_soffice", lambda _p: None)
        monkeypatch.setattr(doc_parser, "_com_available", lambda: True)
        monkeypatch.setattr(doc_parser, "_convert_via_com", fake_com)

        text = doc_parser._extract_text(str(f))
        assert called["n"] == 1, "结果可疑时应重试 COM 一次"
        assert "从 COM 保真链救回" in text

    def test_no_com_when_result_is_fine(self, tmp_path, monkeypatch):
        """反例：正常文本一次 COM 都不该碰（这是 2405× 的来源）。"""
        f = tmp_path / "正常.doc"
        f.write_bytes(b"\x00" * (30 * 1024))
        called = {"n": 0}

        def fake_com(path):
            called["n"] += 1
            return None

        monkeypatch.setattr(doc_parser, "_extract_pure",
                            lambda _p: "正常正文内容。" * 30)
        monkeypatch.setattr(doc_parser, "_com_available", lambda: True)
        monkeypatch.setattr(doc_parser, "_convert_via_com", fake_com)

        text = doc_parser._extract_text(str(f))
        assert text.startswith("正常正文内容")
        assert called["n"] == 0, "结果可信时不能付 COM 的代价"

    def test_no_retry_when_com_unavailable(self, tmp_path, monkeypatch):
        """无 COM 的机器（CI/麒麟）：可疑也不重试，返回原结果而不是空。"""
        f = tmp_path / "可疑2.doc"
        f.write_bytes(b"\x00" * (30 * 1024))
        monkeypatch.setattr(doc_parser, "_extract_pure", lambda _p: "短")
        monkeypatch.setattr(doc_parser, "_convert_via_soffice", lambda _p: None)
        monkeypatch.setattr(doc_parser, "_com_available", lambda: False)
        assert doc_parser._extract_text(str(f)) == "短"

    @needs_doc
    def test_real_fixture_never_triggers_com(self, monkeypatch):
        """真实样张（9.7KB、文本完整）默认路径不应触发任何 COM。"""
        called = {"n": 0}

        def fake_com(_path):
            called["n"] += 1
            return None

        monkeypatch.setattr(doc_parser, "_com_available", lambda: True)
        monkeypatch.setattr(doc_parser, "_convert_via_com", fake_com)
        tree = doc_parser.parse_doc(str(FIXTURE_DOC))
        assert called["n"] == 0
        assert any("样张" in (b.text or "") for b in tree.blocks)


# ============================================================ ② MD/HTML 编码探测
class TestMarkdownHtmlEncoding:
    """GBK 的 .md/.html 不再整篇乱码（与 .txt 同一套编码探测）。"""

    TEXT = "# 关于安全生产的通知\n\n各单位要认真贯彻落实安全生产责任制。\n"

    def test_gbk_markdown(self, tmp_path):
        p = tmp_path / "gbk.md"
        p.write_bytes(self.TEXT.encode("gbk"))
        tree = parse_md(str(p))
        assert tree.title == "关于安全生产的通知"
        assert any("安全生产责任制" in b.text for b in tree.blocks)

    def test_gbk_html(self, tmp_path):
        html = ("<html><head><title>关于安全生产的通知</title></head><body>"
                "<h1>关于安全生产的通知</h1>"
                "<p>各单位要认真贯彻落实安全生产责任制。</p></body></html>")
        p = tmp_path / "gbk.html"
        p.write_bytes(html.encode("gbk"))
        tree = parse_html(str(p))
        assert tree.title == "关于安全生产的通知"
        assert any("安全生产责任制" in b.text for b in tree.blocks)

    def test_utf8_bom_still_works(self, tmp_path):
        """反例守护：v1.8 就支持的 UTF-8-BOM 不能因为换读取方式而回退。"""
        p = tmp_path / "bom.md"
        p.write_bytes(self.TEXT.encode("utf-8-sig"))
        assert parse_md(str(p)).title == "关于安全生产的通知"

    def test_utf16_markdown(self, tmp_path):
        p = tmp_path / "u16.md"
        p.write_bytes(self.TEXT.encode("utf-16"))
        assert parse_md(str(p)).title == "关于安全生产的通知"

    def test_importer_end_to_end_gbk_md(self, tmp_path):
        p = tmp_path / "gbk2.md"
        p.write_bytes(self.TEXT.encode("gbk"))
        r = parse_any(str(p))
        assert r.ok, r.error
        assert "安全生产责任制" in r.tree.plain_text()


# ============================================================ ③ 控制字符清洗
class TestControlCharSanitize:
    """``\\x00`` 等控制字符在入库前清掉；\t\n\r 保留。"""

    def test_strip_keeps_tab_newline_cr(self):
        new, n = strip_control_chars("甲\t乙\n丙\r丁\x00戊\x1f己\x7f")
        assert new == "甲\t乙\n丙\r丁戊己"
        assert n == 3

    def test_soft_break_becomes_newline(self):
        """\\x0b/\\x0c 是 Word 的软换行/分页符，转成换行而不是直接删（否则两段粘一段）。"""
        new, n = strip_control_chars("上段\x0b下段\x0c新页")
        assert new == "上段\n下段\n新页"
        assert n >= 2

    def test_sanitize_tree_covers_title_blocks_and_cells(self):
        tree = DocTree(title="标题\x00", blocks=[
            Block(type=PARAGRAPH, text="正文\x00含空字节"),
            Block(type=TABLE, rows=[["单元格\x01甲", "乙\x02"]]),
        ])
        n = sanitize_tree(tree)
        assert n >= 4
        assert tree.title == "标题"
        assert tree.blocks[0].text == "正文含空字节"
        assert tree.blocks[1].rows == [["单元格甲", "乙"]]

    def test_parse_any_txt_with_nul(self, tmp_path):
        """含 \\x00 的 txt：解析成功、内容被清洗、不再往下游传坏字符。"""
        p = tmp_path / "ctrl.txt"
        p.write_text("关于材料报送的通知\n正文含空字节\x00与控制符\x1f。", encoding="utf-8")
        r = parse_any(str(p))
        assert r.ok, r.error
        plain = r.tree.plain_text()
        assert "\x00" not in plain and "\x1f" not in plain
        assert "正文含空字节与控制符" in plain

    def test_compile_like_pipeline_does_not_raise(self, tmp_path):
        """清洗后走真实的 DOCX 生成不再抛英文 ValueError（基线会抛）。

        反例：**未清洗**的同一段文本必须仍然抛 —— 证明这条闸门确实有用，
        而不是 python-docx 恰好不校验了。
        """
        from gwtool.core.docxgen import generate_docx
        from gwtool.core.template import default_template

        tpl = default_template()
        dirty = _clean_tree("正文含空字节\x00与控制符\x1f。")
        with pytest.raises(ValueError):
            generate_docx([dirty], tpl, str(tmp_path / "dirty.docx"))

        sanitize_tree(dirty)
        out = tmp_path / "clean.docx"
        generate_docx([dirty], tpl, str(out))
        assert out.is_file() and out.stat().st_size > 0

    def test_import_error_is_chinese(self, tmp_path):
        """错误提示中文化：中文在前，原始类名/信息留在括号里便于排障。"""
        r = parse_any(str(tmp_path / "不存在.docx"))
        assert not r.ok
        assert r.error.startswith("文件不存在"), r.error

        # 非"文件没了"类的异常：中文结论 + 括号内保留类名与原始信息
        msg = friendly_import_error(ValueError("bad zip"))
        assert msg.startswith("文件无法解析")
        assert "ValueError" in msg and "bad zip" in msg

        # 扩展名与实际格式不符（伪装 .docx 的文本文件）
        fake = tmp_path / "伪装.docx"
        fake.write_text("这不是一个 zip 包", encoding="utf-8")
        r2 = parse_any(str(fake))
        assert not r2.ok
        assert r2.error.startswith("文件"), r2.error


# ============================================================ ④ 缩进归一（不叠加）
class TestIndentNoDouble:
    """模板 firstLineChars 是唯一缩进来源；正文不再自带「　　」。"""

    def test_ensure_indent_strips_placeholder(self):
        new, n = ensure_indent_placeholder(_TMP_PARAGRAPH, 2)
        assert new == "各单位要认真贯彻落实安全生产责任制。"
        assert n == 1

    def test_ensure_indent_does_not_add(self):
        """反例：不再"补全"，否则与模板叠加成 4 字（审计实测 143.4pt vs 111.4pt）。"""
        new, n = ensure_indent_placeholder("正文段落", 2)
        assert new == "正文段落"
        assert n == 0

    def test_ensure_indent_keeps_headings(self):
        assert ensure_indent_placeholder("一、总体要求", 2) == ("一、总体要求", 0)

    @pytest.mark.parametrize("indent", [2, 0])
    def test_run_full_cleanup_never_leaves_placeholder(self, indent):
        text = _TMP_PARAGRAPH + "\n第二段正文。"
        new, log = run_full_cleanup(text, template_indent_chars=indent)
        assert not any(ln.startswith("\u3000") for ln in new.splitlines() if ln)
        assert isinstance(log, list)

    def test_run_full_cleanup_logs_normalization(self):
        new, log = run_full_cleanup(_TMP_PARAGRAPH, template_indent_chars=2)
        assert any("缩进" in item for item in log), log

    def test_docx_indent_equals_template_once(self, tmp_path):
        """端到端：模板 2 字缩进 + 排版后文本 → DOCX 里只有 2 字。

        断言两点：段落文本**不带**「　　」（否则渲染成 4 字）、
        ``w:firstLineChars`` 恰为 200（= 2 字），即缩进只生效一次。
        """
        from docx import Document as Docx

        from gwtool.core.docxgen import generate_docx
        from gwtool.core.template import default_template

        fixed, _log = run_full_cleanup(_TMP_PARAGRAPH + "\n第二段正文内容。")
        tree = DocTree(title="缩进测试", blocks=[
            Block(type=PARAGRAPH, text=ln) for ln in fixed.splitlines() if ln.strip()])
        tpl = default_template()
        assert tpl.first_line_indent_chars == 2
        out = tmp_path / "indent.docx"
        generate_docx([tree], tpl, str(out))

        body = [p for p in Docx(str(out)).paragraphs if "各单位" in p.text]
        assert body, "找不到正文段落"
        par = body[0]
        assert not par.text.startswith("\u3000"), "正文仍带全角空格占位 → 与模板叠加"
        pPr = par._p.pPr
        assert pPr is not None and pPr.ind is not None
        assert pPr.ind.get(
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}firstLineChars"
        ) == "200"


# ============================================================ ⑤ 同名附件都随包
class TestSameNameAttachments:
    """同名附件加序号后缀，全部随包；manifest 记录原文件名。"""

    def _doc(self, title: str, tmp_path, n: int = 1) -> int:
        return dao.add_document(dao.Document(
            title=title, content_text=(title + "的正文内容。") * 3, blocks_json="[]",
            file_path=str(tmp_path / "不存在" / f"{title}.docx")))

    def _attach(self, doc_id: int, tmp_path, name: str, blob: bytes,
                stored: str) -> None:
        from gwtool.paths import attachments_dir
        base = attachments_dir()
        base.mkdir(parents=True, exist_ok=True)
        (base / Path(stored).name).write_bytes(blob)
        dao.add_attachment(doc_id, name, stored, len(blob))

    def test_two_same_name_attachments_both_packed(self, tmp_path, tmp_db):
        d1 = self._doc("甲材料", tmp_path)
        d2 = self._doc("乙材料", tmp_path)
        self._attach(d1, tmp_path, "证明材料.pdf", b"AAA", "attachments/s1.pdf")
        self._attach(d2, tmp_path, "证明材料.pdf", b"BBB", "attachments/s2.pdf")

        out = tmp_path / "pack.zip"
        rep = exporter.build(exporter.ExportRequest(out_path=str(out)))
        assert rep["attachments"] == 2, "同名附件不能只收 1 个"
        assert rep["excluded"] == 0, "同名不是「该排除」的理由（会让包缺件）"

        with zipfile.ZipFile(str(out)) as zf:
            atts = {n: zf.read(n) for n in zf.namelist()
                    if n.startswith("attachments/")}
        assert len(atts) == 2
        assert sorted(atts.values()) == [b"AAA", b"BBB"], "两份附件内容都要在包里"

        man = exporter.read_manifest(out)
        rows = man["attachments"]["included"]
        assert len(rows) == 2
        assert {r["origin_name"] for r in rows} == {"证明材料.pdf"}, "原文件名必须留档"
        assert len({r["path"] for r in rows}) == 2, "包内路径必须唯一"
        assert any(r.get("renamed") for r in rows), "改名后应写明原因"
        assert exporter.verify_package(out)["ok"] is True

    def test_three_same_name_get_sequential_suffix(self, tmp_path, tmp_db):
        doc = self._doc("甲材料", tmp_path)
        for i in range(3):
            self._attach(doc, tmp_path, "附件.pdf", f"v{i}".encode(),
                         f"attachments/t{i}.pdf")
        out = tmp_path / "pack3.zip"
        rep = exporter.build(exporter.ExportRequest(out_path=str(out)))
        assert rep["attachments"] == 3
        man = exporter.read_manifest(out)
        paths = [r["path"] for r in man["attachments"]["included"]]
        assert len(set(paths)) == 3
        assert any(p.endswith("_2.pdf") for p in paths)
        assert any(p.endswith("_3.pdf") for p in paths)

    def test_distinct_names_not_renamed(self, tmp_path, tmp_db):
        doc = self._doc("甲材料", tmp_path)
        self._attach(doc, tmp_path, "甲.pdf", b"a", "attachments/a.pdf")
        self._attach(doc, tmp_path, "乙.pdf", b"b", "attachments/b.pdf")
        out = tmp_path / "pack2.zip"
        exporter.build(exporter.ExportRequest(out_path=str(out)))
        rows = exporter.read_manifest(out)["attachments"]["included"]
        assert not any(r.get("renamed") for r in rows)
        assert {r["path"] for r in rows} == {"attachments/甲.pdf", "attachments/乙.pdf"}

    def test_missing_file_still_excluded(self, tmp_path, tmp_db):
        """反例守护：真的读不到文件时仍要进 excluded 并注明原因（绝不静默丢）。"""
        doc = self._doc("甲材料", tmp_path)
        dao.add_attachment(doc, "已删除.pdf", "attachments/ghost.pdf", 12)
        out = tmp_path / "pack.zip"
        rep = exporter.build(exporter.ExportRequest(out_path=str(out)))
        assert rep["attachments"] == 0
        assert rep["excluded"] == 1
        seg = json.loads(json.dumps(exporter.read_manifest(out)["attachments"]))
        assert "不在数据目录" in seg["excluded"][0]["reason"]
