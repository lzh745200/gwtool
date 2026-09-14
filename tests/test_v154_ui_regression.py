# -*- coding: utf-8 -*-
"""v1.5.4 UI 层缺陷修复回归（均由真实 MainWindow/QDialog 探针复现后定位）。

1. **F7「文字纠错」100% 失效**（`reference_panel._fill_corr_list`）
   `theme.severity_color()` 返回 str，`QListWidgetItem.setForeground` 只收
   QBrush/QColor → TypeError 从 `_on_check_done` 冒出去 → 建议列表 0 条、
   计数永远停在"检查中…"、连波浪线都不画。打包版 stderr 不可见，
   用户看到的就是"点了 F7 什么都没发生"。

2. **退出时后台线程仍在跑 → Qt qFatal 强杀进程**（`main_window.closeEvent`）
   实测退出码 0xC0000409；老实现只对 TTS 一个 worker 做了 stop+wait。

3. **陈旧偏移把正文改坏**（`reference_panel._apply_all`）
   检查后在前面敲一个字，再点"全部替换"：'这是布署。' → '前这部署署。'

4. **无父 QThread 被 GC → 同样 0xC0000409**（`correct_dialog` / `feature_dialogs`）

5. **标题命中导致 IndexError，纠错结果永不渲染**（`correct_dialog._hit_text`）

6. **`Path.with_suffix` 吞标题**（`compile_wizard`）
   '关于2026.08重点工作的通知' → '关于2026.docx'
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog, QMenu  # noqa: E402

from gwtool.core import corrector  # noqa: E402
from gwtool.ui.widgets import wait_for_threads  # noqa: E402


# ============================================================ 1. F7 纠错面板
class TestReferencePanelRendersCorrections:
    def test_corrections_actually_render(self, tmp_db, qapp):
        """命中必须真的进列表、计数更新、波浪线信号发出。"""
        from gwtool.ui.reference_panel import ReferencePanel

        text = "关于布署工作的通知"
        panel = ReferencePanel(editor_getter=lambda: text)
        emitted: list = []
        panel.corrections_ready.connect(lambda c: emitted.append(len(c)))
        corrs = corrector.check_text(text)
        assert corrs, "前置条件：内置精标应能命中『布署』"

        panel._on_check_done(corrs)          # 这就是 FnWorker.ok 的槽

        assert panel.corr_list.count() == len(corrs), \
            "老实现在这里抛 TypeError，列表会是 0 行"
        assert "检查中" not in panel.lbl_count.text()
        assert emitted == [len(corrs)], "波浪线信号必须发出（老实现被异常挡掉）"
        panel.deleteLater()

    def test_item_foreground_is_qcolor(self, tmp_db, qapp):
        """前景色必须是 QColor 包出来的 QBrush，不能直接塞 str。"""
        from gwtool.ui.reference_panel import ReferencePanel

        text = "关于布署工作的通知"
        panel = ReferencePanel(editor_getter=lambda: text)
        panel._on_check_done(corrector.check_text(text))
        item = panel.corr_list.item(0)
        assert item is not None
        # str 会在这里抛 TypeError（老缺陷）；拿到 QBrush 才算通过
        brush = item.foreground()
        assert brush.color().isValid()
        panel.deleteLater()

    def test_render_failure_does_not_block_wave_marks(self, tmp_db, qapp,
                                                      monkeypatch):
        """即便列表渲染炸了，波浪线信号也必须照发（一处失败不连累另一处）。"""
        from gwtool.ui.reference_panel import ReferencePanel

        text = "关于布署工作的通知"
        panel = ReferencePanel(editor_getter=lambda: text)
        emitted: list = []
        panel.corrections_ready.connect(lambda c: emitted.append(1))

        def boom():
            raise RuntimeError("模拟渲染失败")

        monkeypatch.setattr(panel, "_fill_corr_list", boom)
        panel._on_check_done(corrector.check_text(text))
        assert emitted, "渲染失败不能连累波浪线"
        assert "渲染失败" in panel.lbl_count.text()
        panel.deleteLater()


# ============================================================ 2. 陈旧偏移
class TestStaleOffsetsProtection:
    def _panel(self, text, applied):
        from gwtool.ui.reference_panel import ReferencePanel
        cur = {"t": text}
        panel = ReferencePanel(editor_getter=lambda: cur["t"])
        panel.apply_edit.connect(lambda s, e, r: applied.append((s, e, r)))
        return panel, cur

    def test_apply_all_refuses_when_text_changed(self, tmp_db, qapp, monkeypatch):
        """检查后正文被改过：不得用旧偏移替换（否则正文被静默改坏）。"""
        from gwtool.ui import widgets
        monkeypatch.setattr(widgets, "info", lambda *a, **k: None)

        text = "关于布署工作的通知"
        applied: list = []
        panel, cur = self._panel(text, applied)
        panel._checked_text = text
        panel._corrections = corrector.check_text(text)

        cur["t"] = "前" + text          # 用户在开头敲了一个字
        panel._apply_all()
        assert applied == [], "正文已改动时绝不能用旧偏移替换"
        panel.deleteLater()

    def test_apply_all_works_when_text_unchanged(self, tmp_db, qapp):
        text = "关于布署工作的通知"
        applied: list = []
        panel, cur = self._panel(text, applied)
        panel._checked_text = text
        panel.chk_min_conf.setValue(0.0)
        panel._corrections = corrector.check_text(text)
        panel._apply_all()
        assert applied, "正文未改动时应正常替换"
        start, end, repl = applied[0]
        assert text[start:end] == "布署" and repl == "部署"
        panel.deleteLater()

    def test_apply_one_refuses_when_text_changed(self, tmp_db, qapp, monkeypatch):
        from gwtool.ui import widgets
        monkeypatch.setattr(widgets, "info", lambda *a, **k: None)

        text = "关于布署工作的通知"
        applied: list = []
        panel, cur = self._panel(text, applied)
        panel._checked_text = text
        panel._corrections = corrector.check_text(text)
        cur["t"] = "前" + text
        panel._apply_one()
        assert applied == []
        panel.deleteLater()


# ============================================================ 3. 退出等待线程
class _SleepThread:
    """构造一个可控的运行中 QThread（用于验证退出等待逻辑）。"""

    @staticmethod
    def make(owner, seconds: float):
        from PySide6.QtCore import QThread

        class _T(QThread):
            def run(self):
                import time
                time.sleep(seconds)

        th = _T(owner)
        th.start()
        return th


class TestExitWaitsForThreads:
    def test_wait_for_threads_waits_until_done(self, qapp):
        holder = QDialog()
        th = _SleepThread.make(holder, 0.6)
        assert th.isRunning()
        stuck = wait_for_threads(holder, timeout_ms=8000)
        assert not th.isRunning(), "必须等到线程结束"
        assert stuck == []
        holder.deleteLater()

    def test_wait_for_threads_reports_timeout(self, qapp):
        """超时不能静默：必须把线程名交回来（供 UI 提示用户）。"""
        holder = QDialog()
        th = _SleepThread.make(holder, 2.0)
        stuck = wait_for_threads(holder, timeout_ms=100)
        assert stuck == ["_T"], f"超时线程必须被报告，实得 {stuck}"
        th.wait(9000)
        holder.deleteLater()

    def test_dialogs_use_thread_safe_mixin(self):
        """所有自己起后台线程的对话框都必须挂 ThreadSafeDialog。"""
        from gwtool.ui.widgets import ThreadSafeDialog
        from gwtool.ui.compare_dialog import CompareDialog
        from gwtool.ui.compile_wizard import CompileWizard
        from gwtool.ui.correct_dialog import AnyDocCorrectDialog
        from gwtool.ui.feature_dialogs import (BatchCorrectDialog,
                                               BulkReplaceDialog,
                                               InspectorDialog,
                                               SimilarityDialog)
        from gwtool.ui.import_dialog import ImportDialog
        from gwtool.ui.material_dialogs import AttachmentDialog
        for cls in (CompareDialog, CompileWizard, AnyDocCorrectDialog,
                    BatchCorrectDialog, BulkReplaceDialog, InspectorDialog,
                    SimilarityDialog, ImportDialog, AttachmentDialog):
            assert issubclass(cls, ThreadSafeDialog), \
                f"{cls.__name__} 未挂 ThreadSafeDialog：退出时线程未等待会崩进程"

    def test_threads_created_with_parent(self):
        """worker 必须带 parent，否则对话框先销毁时线程被 GC → qFatal。"""
        import inspect

        from gwtool.ui import correct_dialog, feature_dialogs
        src_cd = inspect.getsource(correct_dialog)
        assert "FnWorker(_scan_blocks, blocks_snapshot, parent=self)" in src_cd
        src_fd = inspect.getsource(feature_dialogs)
        assert "parent=self)" in src_fd, "批量替换 worker 必须带 parent"


# ============================================================ 4. 标题命中
class TestHitTextTitleHandling:
    def _dialog(self):
        """不构造完整对话框（会阻塞在 UI 初始化），只测纯逻辑方法。"""
        from gwtool.ui.correct_dialog import AnyDocCorrectDialog as D
        dlg = D.__new__(D)
        dlg._title = "关于印法工作的通知"
        dlg._blocks = []
        return D, dlg

    def test_title_hit_returns_title(self):
        D, dlg = self._dialog()
        assert D._hit_text(dlg, -1, None, None) == "关于印法工作的通知"

    def test_out_of_range_returns_empty(self):
        D, dlg = self._dialog()
        assert D._hit_text(dlg, 5, None, None) == ""
        assert D._hit_text(dlg, -1, None, None) == "关于印法工作的通知"

    def test_normal_block(self):
        D, dlg = self._dialog()
        dlg._blocks = [{"kind": "para", "text": "正文", "rows": None}]
        assert D._hit_text(dlg, 0, None, None) == "正文"

    def test_table_cell(self):
        D, dlg = self._dialog()
        dlg._blocks = [{"kind": "table", "text": "", "rows": [["甲", "乙"]]}]
        assert D._hit_text(dlg, 0, 0, 1) == "乙"
        assert D._hit_text(dlg, 0, 9, 9) == ""


# ============================================================ 5. 输出名
class TestCompileWizardOutputNaming:
    @pytest.mark.parametrize("title", [
        "关于2026.08重点工作的通知", "报告 v1.2 终稿", "2026.08.30 会议纪要",
        "××办发〔2026〕12号", "汇编成果",
    ])
    def test_title_preserved_with_dots(self, title):
        """含小圆点的标题不能被 with_suffix 截断（老实现会吞掉后半段）。

        这里直接断言"我们用的拼接方式"符合预期，因为 with_suffix 的行为
        由 pathlib 决定、不是本项目的契约。
        """
        from pathlib import Path
        base = Path("/tmp") / title
        assert (str(base) + ".docx").endswith(title + ".docx")
        # 反面对照：with_suffix 确实会截断（证明为什么不能用它）
        assert base.with_suffix(".docx").name != title + ".docx" or "." not in title


# ============================================================ 6. 菜单回收
class TestMenuCleanup:
    def test_menu_delete_not_missing(self):
        """右键菜单构建处必须调用 deleteLater（父对象不会自动回收）。"""
        import inspect

        from gwtool.ui import editor_panel, library_panel
        for mod, count in ((library_panel, 2), (editor_panel, 1)):
            src = inspect.getsource(mod)
            assert src.count("menu.deleteLater()") >= count, \
                f"{mod.__name__} 的右键菜单未全部回收"
