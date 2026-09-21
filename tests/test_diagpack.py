# -*- coding: utf-8 -*-
"""一键诊断包（D2）的测试。

**重点是隐私红线**：诊断包会被用户发给技术支持（可能经微信、邮件流转），
里面绝不能出现公文正文、附件内容或词条正文。本产品的用户多为党政机关与
涉密单位，这条一旦破了，后果不是"不好用"而是"不能用"。
"""
from __future__ import annotations

import pathlib
import zipfile

from gwtool.core import diagpack
from gwtool.db import dao

ROOT = pathlib.Path(__file__).resolve().parent.parent
SECRET = "关于某某敏感事项的请示正文-9271"


class TestSections:
    def test_system_info_has_essentials(self, tmp_db):
        keys = dict(diagpack.system_info())
        for key in ("操作系统", "CPU 架构", "Python", "数据目录"):
            assert key in keys, f"缺少 {key}"

    def test_dependency_info_lists_known_packages(self, tmp_db):
        keys = dict(diagpack.dependency_info())
        for name in ("PySide6", "numpy", "python-docx"):
            assert name in keys
        # 每项都要有值（"未安装"也是值），不能是空串
        assert all(v for v in keys.values())

    def test_capability_info_covers_l4_l5_ocr(self, tmp_db):
        keys = dict(diagpack.capability_info())
        assert any("L4" in k for k in keys)
        assert any("L5" in k for k in keys)
        assert any("OCR" in k for k in keys)

    def test_capability_reports_using_bundled(self, tmp_db):
        """交付口径必须问"是不是自带引擎"，只看 available 会骗人。"""
        keys = dict(diagpack.capability_info())
        assert "OCR · 走自带引擎" in keys

    def test_database_info_counts_rows(self, tmp_db):
        dao.add_document(dao.Document(title="甲", content_text="内容"))
        keys = dict(diagpack.database_info())
        assert keys["资料库文档"] == "1 条"
        assert "完整性自检" in keys

    def test_database_info_has_no_text_columns(self, tmp_db):
        """只报行数：概览里不得出现任何正文字段名。"""
        keys = dict(diagpack.database_info())
        for bad in ("content_text", "blocks_json", "标题", "正文"):
            assert bad not in " ".join(keys), f"概览泄漏了字段 {bad}"


class TestReport:
    def test_report_has_all_sections(self, tmp_db):
        text = diagpack.build_text_report()
        for title in ("系统信息", "依赖版本", "能力探测", "数据库概览"):
            assert title in text
        assert "生成时间" in text

    def test_report_is_plain_text(self, tmp_db):
        text = diagpack.build_text_report()
        assert "**" not in text, "报告会显示在纯文本环境，不应含 Markdown 标记"


class TestLogTail:
    def test_returns_placeholder_when_no_log(self, tmp_db, monkeypatch):
        from gwtool import logs
        monkeypatch.setattr(logs, "log_path", lambda: None)
        assert "暂无日志" in diagpack.log_tail()

    def test_truncates_large_log(self, tmp_db, tmp_path, monkeypatch):
        from gwtool import logs
        big = tmp_path / "big.log"
        big.write_bytes(b"A" * 10000)
        monkeypatch.setattr(logs, "log_path", lambda: big)
        got = diagpack.log_tail(max_bytes=1000)
        assert "仅保留最后" in got
        # 超长行必须被脱敏（隐私红线）：完整的 1000 个 'A' 不得原样出现，
        # 但要保留行首与长度标记，保证排障者知道"有一行、多大"
        assert ("A" * 1000) not in got
        assert "内容已隐去" in got
        assert len(got) < 3000

    def test_survives_unreadable_log(self, tmp_db, tmp_path, monkeypatch):
        from gwtool import logs
        ghost = tmp_path / "ghost.log"      # 声明存在但读不到
        monkeypatch.setattr(logs, "log_path", lambda: ghost)
        monkeypatch.setattr(type(ghost), "exists", lambda self: True,
                            raising=False)
        # 不抛即可（内容为何不重要）
        assert isinstance(diagpack.log_tail(), str)


class TestPrivacy:
    """隐私红线：这些用例一旦失败，产品在涉密场景就不可用。"""

    def test_preview_states_what_is_excluded(self, tmp_db):
        text = "\n".join(diagpack.preview_lines())
        assert "不包含" in text
        assert "正文" in text

    def test_pack_contains_no_document_text(self, tmp_db, tmp_path):
        dao.add_document(dao.Document(title=SECRET, content_text=SECRET))
        out = tmp_path / "diag.zip"
        diagpack.build(out)
        with zipfile.ZipFile(str(out)) as zf:
            blob = b"".join(zf.read(n) for n in zf.namelist())
        assert SECRET.encode("utf-8") not in blob, "诊断包泄漏了公文正文"

    def test_pack_contains_no_attachment_bytes(self, tmp_db, tmp_path):
        dao.add_document(dao.Document(title="带附件", content_text="x"))
        out = tmp_path / "diag.zip"
        diagpack.build(out)
        with zipfile.ZipFile(str(out)) as zf:
            assert not any(n.startswith("attachments") for n in zf.namelist())

    def test_pack_contains_no_dictionary_entries(self, tmp_db, tmp_path):
        dao.add_dictionary_entry("某某专用术语", "", "", "")
        out = tmp_path / "diag.zip"
        diagpack.build(out)
        with zipfile.ZipFile(str(out)) as zf:
            blob = b"".join(zf.read(n) for n in zf.namelist()).decode(
                "utf-8", "replace")
        assert "某某专用术语" not in blob


class TestBuild:
    def test_zip_has_two_entries(self, tmp_db, tmp_path):
        out = tmp_path / "diag.zip"
        got = diagpack.build(out)
        assert got == str(out)
        with zipfile.ZipFile(str(out)) as zf:
            names = zf.namelist()
        assert "诊断报告.txt" in names
        assert "运行日志.txt" in names

    def test_zip_is_readable_utf8(self, tmp_db, tmp_path):
        out = tmp_path / "diag.zip"
        diagpack.build(out)
        with zipfile.ZipFile(str(out)) as zf:
            text = zf.read("诊断报告.txt").decode("utf-8")
        assert "公文汇编助手" in text

    def test_default_filename_is_zip(self):
        assert diagpack.default_filename().endswith(".zip")


class TestWiring:
    def test_help_menu_has_diagpack(self):
        src = (ROOT / "gwtool" / "ui" / "main_window.py").read_text(
            encoding="utf-8")
        assert "生成诊断包…" in src
        assert "diagpack.build" in src

    def test_preview_shown_before_build(self):
        """必须先展示内容清单再生成——否则隐私承诺无法被用户核查。"""
        src = (ROOT / "gwtool" / "ui" / "main_window.py").read_text(
            encoding="utf-8")
        idx = src.index("def export_diagpack")
        block = src[idx:idx + 1200]
        assert block.index("preview_lines()") < block.index("diagpack.build")
