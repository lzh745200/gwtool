# -*- coding: utf-8 -*-
"""UI 回归测试：遍历主窗口全部动作 + 逐个构造对话框 + v1.3.0 已修缺陷的守门用例。

现有 86 个用例全绿却漏掉了多个"点开就崩"的严重缺陷（口令锁启动崩溃、
新建公文崩溃、朗读高亮崩溃、句式删除失败、编辑区不随窗口缩放），
根因是测试只构造主窗口、从不触发其中的动作。本文件补上这一层。
"""
import pytest

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox


@pytest.fixture(autouse=True)
def block_modal_dialogs(monkeypatch):
    """全局屏蔽模态弹窗。

    多个 UI 模块各自 `from .widgets import info`，只 patch 主窗口那一份不够；
    未走 win fixture 的用例（如 import_dialog._failed、dict_manager._del_phrase_row）
    会弹出真实 QMessageBox，在无头环境下永久挂住。
    """
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))


@pytest.fixture()
def win(tmp_db, qapp, monkeypatch):
    """主窗口 + 屏蔽文件对话框与主窗口自带的提示函数。"""
    from gwtool import app
    app.ensure_database_seeded()
    import gwtool.ui.main_window as mw

    monkeypatch.setattr(mw, "missing_official_fonts", lambda: [])
    monkeypatch.setattr(mw, "info", lambda *a, **k: None)
    monkeypatch.setattr(mw, "warn", lambda *a, **k: None)
    monkeypatch.setattr(mw, "ask", lambda *a, **k: True)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: ([], "")))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: ""))

    w = mw.MainWindow()
    yield w
    w.close()


def test_all_menu_and_toolbar_actions_triggerable(win, qapp):
    """遍历触发全部 QAction：任何一个动作抛异常都视为"点开就崩"缺陷。

    回归覆盖：新建公文（曾 AttributeError: 'SkeletonDialog' has no 'Accepted'）、
    朗读校对（曾 AttributeError: Qt.KeepAnchor）等。
    """
    actions = [a for a in win.findChildren(QAction)
               if (a.text() or "").replace("&", "").strip() and a.isEnabled()]
    assert len(actions) >= 20, f"动作数量异常偏少：{len(actions)}"

    failed = []
    for act in actions:
        label = act.text().replace("&", "").strip()
        try:
            act.trigger()
            qapp.processEvents()
        except Exception as exc:                    # noqa: BLE001  逐个记录后统一断言
            failed.append(f"{label}: {type(exc).__name__}: {exc}")
    assert not failed, "以下动作触发即崩：\n" + "\n".join(failed)


DIALOG_SPECS = [
    ("correct_dialog", "AnyDocCorrectDialog"),
    ("compare_dialog", "CompareDialog"),
    ("compile_wizard", "CompileWizard"),
    ("dict_manager", "DictManager"),
    ("import_dialog", "ImportDialog"),
    ("template_editor", "TemplateEditor"),
    ("feature_dialogs", "SkeletonDialog"),
    ("feature_dialogs", "InspectorDialog"),
    ("feature_dialogs", "BulkReplaceDialog"),
    ("feature_dialogs", "BatchCorrectDialog"),
    ("feature_dialogs", "SnapshotsDialog"),
    ("feature_dialogs", "SimilarityDialog"),
    ("feature_dialogs", "SecurityDialog"),
    ("feature_dialogs", "LockDialog"),
    ("editor_panel", "EditorPanel"),
    ("library_panel", "LibraryPanel"),
    ("material_dialogs", "AttachmentDialog"),
    ("material_dialogs", "RecycleBinDialog"),
    ("reference_panel", "ReferencePanel"),
    ("registry_dialog", "RegistryDialog"),
    ("registry_dialog", "DispatchForm"),
]


@pytest.mark.parametrize("module_name,class_name", DIALOG_SPECS)
def test_dialog_constructible(tmp_db, qapp, monkeypatch, module_name, class_name):
    """每个对话框都能构造（回归：曾出现 QListWidget 漏导入的 NameError）。"""
    import importlib
    import inspect

    from gwtool import app
    app.ensure_database_seeded()
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)

    cls = getattr(importlib.import_module(f"gwtool.ui.{module_name}"), class_name)
    required = [p.name for p in list(inspect.signature(cls.__init__).parameters.values())[1:]
                if p.default is inspect.Parameter.empty
                and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                               inspect.Parameter.POSITIONAL_ONLY)]
    # 必填参数按名字给最小可用值：getter 类给 lambda，集合类给空，其余给 None
    args = []
    for name in required:
        if "getter" in name:
            args.append(lambda: "")
        elif name in ("doc_ids", "ids"):
            args.append([])
        elif name == "category_id":
            args.append(0)
        elif name == "current_text":
            args.append("")
        else:
            args.append(None)
    obj = cls(*args)
    assert obj is not None


def test_editor_page_layout_manages_editor_and_outline(tmp_db, qapp):
    """回归 A1：编辑页曾被同一控件上的两个布局搞坏，编辑器与大纲不受任何布局管理，
    几何固定在 428x480、永不随窗口缩放。"""
    from gwtool import app
    app.ensure_database_seeded()
    from gwtool.ui.editor_panel import EditorPanel

    panel = EditorPanel()
    page = panel.tabs.widget(0)
    outer = page.layout()
    assert outer is not None, "编辑页没有布局"

    # 第 0 项是查找栏，第 1 项应为容纳「大纲 + 编辑器」的水平子布局
    sub = outer.itemAt(1).layout() if outer.count() > 1 else None
    assert sub is not None, "编辑页缺少容纳编辑器与大纲的子布局"
    assert sub.indexOf(panel.editor) >= 0, "编辑器未被任何布局管理"
    assert sub.indexOf(panel.outline) >= 0, "大纲未被任何布局管理"

    panel.show()
    panel.resize(900, 600)
    qapp.processEvents()
    wide = panel.editor.width()
    panel.resize(400, 600)
    qapp.processEvents()
    narrow = panel.editor.width()
    assert wide != narrow, f"编辑器宽度未随窗口缩放（恒为 {wide}）"
    assert wide > narrow, "窗口变宽后编辑器反而变窄"
    panel.close()


def test_pass_lock_uses_class_level_dialog_code(tmp_db, qapp, monkeypatch):
    """回归 A2：口令锁曾写成 `dlg.Accepted`，PySide6 6.8+ 下实例无该属性，
    用户一启用口令锁程序就启动即崩、完全无法使用。"""
    from gwtool import app
    from gwtool.db import dao
    from gwtool.core import security

    dao.set_setting("lock_enabled", "1")
    monkeypatch.setattr(security, "has_password", lambda: True)

    results = iter([QDialog.DialogCode.Accepted, QDialog.DialogCode.Rejected])
    monkeypatch.setattr(app.LockDialog, "exec", lambda self: next(results))

    assert app._pass_lock() is True, "解锁成功应放行"
    assert app._pass_lock() is False, "解锁失败应拦下"

    dao.set_setting("lock_enabled", "0")
    assert app._pass_lock() is True, "未启用口令锁应直接放行"


def test_new_skeleton_doc_rejected_does_not_raise(win, monkeypatch):
    """回归 A2 的另一处：点击「新建公文」在用户取消时曾抛 AttributeError。"""
    import gwtool.ui.main_window as mw

    monkeypatch.setattr(mw.SkeletonDialog, "exec",
                        lambda self: QDialog.DialogCode.Rejected)
    win.new_skeleton_doc()          # 不应抛异常


def test_highlight_sentence_selects_text(win):
    """回归 A3：朗读高亮曾用 Qt.KeepAnchor（PySide6 无此属性），每句都抛异常。"""
    sentence = "一季度以来各项工作平稳有序推进。"
    win.editor.editor.setPlainText("前言。\n" + sentence + "\n结语。")
    win._highlight_sentence(0, 1, sentence)      # 不应抛异常
    assert win.editor.editor.textCursor().hasSelection(), "朗读句未被选中高亮"


def test_delete_phrase_row_actually_deletes(tmp_db, qapp, monkeypatch):
    """回归 A4：`DELETE ... LIMIT 1` 在 Python 内置 SQLite 是语法错误，
    「常用句式」页删除所选永远失败。"""
    from gwtool import app
    app.ensure_database_seeded()
    from gwtool.db import dao
    from gwtool.db.connection import get_conn
    from gwtool.ui.dict_manager import DictManager

    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    get_conn().execute("INSERT INTO user_phrases(phrase) VALUES('待删除句式')")
    get_conn().commit()

    dlg = DictManager()
    dlg.tabs.setCurrentIndex(2)          # 常用句式页
    rows = [(r, dlg.phr_table.item(r, 0).text())
            for r in range(dlg.phr_table.rowCount())]
    target = next(r for r, text in rows if text == "待删除句式")
    dlg.phr_table.selectRow(target)
    dlg._del_phrase_row()                # 曾抛 OperationalError

    remaining = [p.phrase for p in dao.list_phrases(limit=100000)]
    assert "待删除句式" not in remaining, "句式未被删除"
    fts = get_conn().execute(
        "SELECT count(*) FROM phrases_fts WHERE phrases_fts MATCH '待删除句式'"
    ).fetchone()[0]
    assert fts == 0, "FTS 索引未同步"
    dlg.close()


def test_import_dialog_failure_reenables_start_button(tmp_db, qapp, monkeypatch):
    """回归 A6b：导入失败后曾不复位按钮与进度条，对话框永久卡死无法重试。"""
    from gwtool import app
    app.ensure_database_seeded()
    from gwtool.ui.import_dialog import ImportDialog

    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    dlg = ImportDialog()
    dlg.btn_start.setEnabled(False)
    dlg.progress.setVisible(True)
    dlg._failed("模拟失败")
    assert dlg.btn_start.isEnabled(), "失败后开始按钮未恢复"
    assert not dlg.progress.isVisible(), "失败后进度条未隐藏"
    dlg.close()


def test_template_from_json_ignores_unknown_keys():
    """回归 A6d：模板 config_json 出现未知键曾抛 TypeError，
    跨版本读写（新增字段/字段改名）会让模板编辑器与汇编向导整个崩掉。"""
    import json

    from gwtool.core.template import DocTemplate, default_template

    tpl = default_template()
    data = json.loads(tpl.to_json())
    data["未来版本才有的字段"] = "任意值"
    data["red_header"]["已废弃字段"] = 1
    data["h1"]["未知键"] = True

    restored = DocTemplate.from_json(json.dumps(data, ensure_ascii=False))
    assert restored.name == tpl.name
    assert restored.red_header.org == tpl.red_header.org
    assert restored.h1.font == tpl.h1.font
    assert restored.margin_top_mm == tpl.margin_top_mm
