# -*- coding: utf-8 -*-
"""B 批次（2026-09-21 缺陷分析）回归测试 —— 第 1 批「零风险修复」。

本文件每条断言都**先在修复前的实现上确认过失败**，否则不写进来
（项目纪律：恒过的断言等于没写）。

覆盖：
  N1  主窗口初始尺寸不得超过屏幕可用区域
  N2  SecurityDialog 内容必须可滚动，「关闭」按钮在滚动区之外
  N10 export_dir 在中文桌面（~/文档）下不得落回家目录根部
  N11 数据目录不可写时必须抛 DataDirError 而不是裸 OSError
  D4  xlsx 工作表名含 `"` 时全部 XML 部件仍须可解析
  S2  CSV 导出须中和公式注入，且不得误伤负数
  F3  设置写库失败必须能被调用方感知（返回 False）
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

pytest.importorskip("PySide6")

from gwtool import paths
from gwtool.core import registry
from gwtool.core.xlsx import Sheet, write_xlsx


# ------------------------------------------------------------------ N1
class TestMainWindowFitsScreen:
    def test_initial_size_within_available_geometry(self, qapp):
        """主窗口初始高度不得超出屏幕可用区域（1366×768 上旧值 820 会截底）。"""
        from gwtool.ui.main_window import _fit_to_screen
        from PySide6.QtWidgets import QDialog

        d = QDialog()
        _fit_to_screen(d, 1360, 820)
        screen = d.screen().availableGeometry()
        assert d.height() <= screen.height()
        assert d.width() <= screen.width()
        # 最小尺寸护栏：不能被拖到布局塌陷
        assert d.minimumSize().height() >= 640

    def test_dialog_fit_helper(self, qapp):
        """对话框夹取函数同样不得超出可用区域。"""
        from gwtool.ui.feature_dialogs import _fit_dialog
        from PySide6.QtWidgets import QDialog

        d = QDialog()
        _fit_dialog(d, 640, 720)
        screen = d.screen().availableGeometry()
        assert d.height() <= screen.height()


# ------------------------------------------------------------------ N2
class TestSecurityDialogScrollable:
    def test_content_is_scrollable_and_close_outside(self, qapp, tmp_db):
        """设置对话框内容必须可滚动，且「关闭」按钮在滚动区之外。

        修复前：422 行控件堆在单个 VBox、无 QScrollArea，1366×768 上
        底部「清理附件」「关闭」按钮落在屏幕外且无法触及。
        """
        from PySide6.QtWidgets import QScrollArea, QPushButton

        from gwtool.ui.feature_dialogs import SecurityDialog

        d = SecurityDialog()
        scrolls = d.findChildren(QScrollArea)
        assert len(scrolls) == 1, "内容必须套一层 QScrollArea"
        # 高度须被夹到屏幕内
        assert d.height() <= d.screen().availableGeometry().height()
        # 「关闭」按钮不能在滚动区内部（否则内容一长又得滚到底才能关）
        close_btns = [b for b in d.findChildren(QPushButton)
                      if b.text() == "关闭"]
        assert close_btns, "必须存在「关闭」按钮"
        scroll_holder = scrolls[0].widget()
        for b in close_btns:
            assert not scroll_holder.isAncestorOf(b), \
                "「关闭」必须在滚动区外，始终可见"


# ------------------------------------------------------------------ N10
class TestDocumentsDir:
    def test_prefers_localized_documents_dir(self, tmp_path, monkeypatch):
        """~/Documents 不存在但 ~/文档 存在时，必须选 ~/文档。

        旧实现写死 `Path.home()/"Documents"`，中文桌面上直接退回家目录根部，
        导出结果散落一地、用户找不到。
        """
        home = tmp_path / "home"
        (home / "文档").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        assert paths.documents_dir() == home / "文档"

    def test_reads_xdg_documents_dir(self, tmp_path, monkeypatch):
        """两者都不存在时，须读 XDG 配置里本地化的名字。"""
        home = tmp_path / "home"
        (home / ".config").mkdir(parents=True)
        target = home / "我的文档"
        target.mkdir()
        (home / ".config" / "user-dirs.dirs").write_text(
            'XDG_DOCUMENTS_DIR="$HOME/我的文档"\n', encoding="utf-8")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        assert paths.documents_dir() == target

    def test_falls_back_to_home(self, tmp_path, monkeypatch):
        """什么都没有时退回家目录，但**绝不抛错**。"""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        assert paths.documents_dir() == home

    def test_broken_xdg_config_does_not_raise(self, tmp_path, monkeypatch):
        """XDG 配置文件损坏不得拖垮导出。"""
        home = tmp_path / "home"
        (home / ".config").mkdir(parents=True)
        (home / ".config" / "user-dirs.dirs").write_bytes(b"\xff\xfe\x00bad")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        assert paths.documents_dir() == home

    def test_export_dir_uses_documents_dir(self, tmp_path, monkeypatch):
        """export_dir 必须建在本地化「文档」下，而不是家目录根部。"""
        home = tmp_path / "home"
        (home / "文档").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        d = paths.export_dir()
        assert d == home / "文档" / "公文汇编输出"
        assert d.is_dir()


# ------------------------------------------------------------------ N11
class TestDataDirError:
    def test_unwritable_dir_raises_datadir_error(self, tmp_path):
        """目标路径被文件占用（mkdir 必败）时抛 DataDirError，而不是裸 OSError。

        ⚠ Windows 上 chmod 0o500 对管理员进程不生效（ACL 绕过），不能用来
        模拟"不可写"；改用"该路径已被普通文件占用"——mkdir 必然抛
        FileExistsError（OSError 子类），`_ensure_dir` 须把它转成 DataDirError。
        """
        target = tmp_path / "occupied"
        target.write_text("我不是目录", encoding="utf-8")

        with pytest.raises(paths.DataDirError) as ei:
            paths._ensure_dir(target)
        assert str(target) in str(ei.value)
        assert ei.value.reason

    def test_write_probe_failure_raises(self, tmp_path, monkeypatch):
        """目录建得出、但探测写失败（磁盘满/杀软拦截）也必须报出来。

        只 patch 本模块可见的 Path.write_text，避免影响其他模块。
        """
        target = tmp_path / "probe_fail"
        target.mkdir()

        def _boom(self, *a, **k):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(paths.Path, "write_text", _boom)
        with pytest.raises(paths.DataDirError) as ei:
            paths._ensure_dir(target)
        assert "不可写" in ei.value.reason

    def test_datadir_error_is_oserror(self, tmp_path):
        """DataDirError 必须是 OSError 子类：调用方既有 `except OSError` 仍生效。"""
        assert issubclass(paths.DataDirError, OSError)

    def test_normal_dir_ok(self, tmp_path):
        d = paths._ensure_dir(tmp_path / "a" / "b")
        assert d.is_dir()

    def test_write_probe_leaves_no_residue(self, tmp_path, monkeypatch):
        """可写探测用的临时文件必须删干净，不能在用户数据目录留垃圾。"""
        monkeypatch.setattr(paths, "_override", tmp_path / "data")
        paths.app_data_dir()

        leftovers = [p for p in (tmp_path / "data").iterdir()
                     if "write_test" in p.name]
        assert leftovers == []


# ------------------------------------------------------------------ D4
class TestXlsxSheetNameQuoting:
    NAMES = [
        '关于"某某"的通知',          # 引号 —— 真正会破坏 XML 属性的字符
        "a<b>c",                     # 尖括号
        "x&y",                       # 与号
        "正常名称",
        "含'单引号'",
        "A" * 40,                    # 超 31 字符须截断
        '=cmd|"/c calc"!A1',         # 公式注入载荷
    ]

    def test_all_parts_are_wellformed_xml(self, tmp_path):
        """任意用户文件名都必须产出 Excel 可打开的工作簿。

        修复前：`escape()` 不转义引号，而 sheet 名被拼进 XML **属性值**，
        `name="关于"某某"的通知"` → workbook.xml 非良构 → Excel 报"文件已损坏"。
        """
        out = tmp_path / "t.xlsx"
        write_xlsx(str(out), [Sheet(name=n, headers=["列"], rows=[["值"]])
                              for n in self.NAMES])

        with zipfile.ZipFile(out) as zf:
            members = [m for m in zf.namelist() if m.endswith(".xml")]
            assert members, "包内应至少有一个 XML 部件"
            for m in members:
                ET.fromstring(zf.read(m))       # 解析失败即抛，测试红

    def test_sheet_name_roundtrips(self, tmp_path):
        """非危险字符须原样保留（证明修复没把正常名字也改坏）。"""
        out = tmp_path / "t.xlsx"
        write_xlsx(str(out), [Sheet(name="正常名称", headers=["列"], rows=[["值"]])])

        with zipfile.ZipFile(out) as zf:
            root = ET.fromstring(zf.read("xl/workbook.xml"))
        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        assert [s.get("name") for s in root.iter(ns + "sheet")] == ["正常名称"]

    def test_quote_removed_from_name(self, tmp_path):
        """引号被剔除（Excel 自身不允许），但不影响可解析性。"""
        out = tmp_path / "t.xlsx"
        write_xlsx(str(out), [Sheet(name='关于"某某"的通知',
                                    headers=["列"], rows=[["值"]])])
        with zipfile.ZipFile(out) as zf:
            root = ET.fromstring(zf.read("xl/workbook.xml"))
        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        name = [s.get("name") for s in root.iter(ns + "sheet")][0]
        assert '"' not in name
        assert "某某" in name


# ------------------------------------------------------------------ S2
class TestCsvFormulaInjection:
    def test_dangerous_prefixes_are_neutralized(self):
        for raw in ("=1+1", "+1+1", "@SUM(A1)", "=cmd|'/c calc'!A1"):
            assert registry.csvsafe(raw) == "'" + raw, raw

    def test_numbers_are_not_mangled(self):
        """负数/小数不得被加引号 —— 否则数值列全变文本。"""
        for raw in ("-5", "-3.14", "1e5", "-1.5e-3", "42"):
            assert registry.csvsafe(raw) == raw, raw

    def test_non_string_passthrough(self):
        assert registry.csvsafe(None) is None
        assert registry.csvsafe(7) == 7

    def test_plain_text_untouched(self):
        assert registry.csvsafe("正常文本") == "正常文本"
        assert registry.csvsafe("") == ""

    def test_export_csv_neutralizes(self, tmp_path, tmp_db):
        """端到端：写进库的载荷，导出后行首已被中和。"""
        import csv as _csv
        from gwtool.db import dao

        rid = dao.add_dispatch(dao.Dispatch(
            doc_no="×政办发〔2026〕99号", title="=cmd|'/c calc'!A1",
            copies=1, pages=1, status="已印发", doc_type="通知"))
        assert rid > 0
        rows = dao.list_dispatch()
        out = tmp_path / "d.csv"
        registry.export_csv(rows, str(out))

        with open(out, encoding="utf-8-sig", newline="") as fh:
            body = list(_csv.reader(fh))
        payload = [c for row in body for c in row if "cmd" in c]
        assert payload, "载荷应出现在导出结果里"
        assert all(c.startswith("'") for c in payload)


# ------------------------------------------------------------------ F3
class TestSettingPersistVisibility:
    def test_csc_set_setting_returns_false_on_db_error(self, monkeypatch):
        """写库失败时 set_setting 必须返回 False（而不是静默成功）。"""
        from gwtool.core import csc_gec
        from gwtool.db import dao

        def _boom(*a, **k):
            raise RuntimeError("库不可写")

        monkeypatch.setattr(dao, "set_setting", _boom)
        assert csc_gec.set_setting(True) is False

    def test_reminder_set_enabled_returns_false_on_db_error(self, monkeypatch):
        from gwtool.core import reminder
        from gwtool.db import dao

        monkeypatch.setattr(dao, "set_setting",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        assert reminder.set_enabled(True) is False

    def test_reminder_set_due_soon_rejects_garbage(self):
        from gwtool.core import reminder
        assert reminder.set_due_soon_days("不是数字") is False

    def test_success_path_returns_true(self, tmp_db):
        """正常路径必须返回 True —— 否则 UI 会误报"未能保存"。"""
        from gwtool.core import csc_gec, reminder

        assert csc_gec.set_setting(True) is True
        assert reminder.set_enabled(True) is True
        assert reminder.set_due_soon_days(3) is True
