# -*- coding: utf-8 -*-
"""P0 保底修复回归：口令锁启动路径、脏文档保护、另存为新文档、撤销栈保留。"""


def test_lock_dialog_importable_from_app(tmp_db):
    """回归：app.run 口令锁分支的导入路径必须有效（曾指向不存在的 ui.lock_dialog，
    导致设置口令锁后程序下次启动必崩）。"""
    from gwtool import app
    assert app.LockDialog is not None


def test_load_document_dirty_guard(tmp_db, qapp, monkeypatch):
    """切换文档前须询问未保存修改：取消则不切换，确认则切换。"""
    from gwtool import app as appmod
    from gwtool.db import dao
    from gwtool.ui.editor_panel import EditorPanel

    appmod.ensure_database_seeded()
    ed = EditorPanel()
    try:
        did_a = dao.add_document(dao.Document(title="A", content_text="旧内容"))
        ed.load_document(did_a)
        ed.editor.setPlainText("新修改内容")
        assert ed._dirty
        did_b = dao.add_document(dao.Document(title="B", content_text="另一篇"))
        monkeypatch.setattr(ed, "confirm_discard_changes", lambda: False)
        ed.load_document(did_b)
        assert ed.doc_id == did_a, "用户取消后不应切换文档"
        monkeypatch.setattr(ed, "confirm_discard_changes", lambda: True)
        ed.load_document(did_b)
        assert ed.doc_id == did_b
    finally:
        ed.close()


def test_save_as_new_document(tmp_db, qapp, monkeypatch):
    """未关联文档的内容可另存为新文档入库并拿到 doc_id。"""
    from gwtool.db import dao
    from gwtool.ui.editor_panel import EditorPanel

    ed = EditorPanel()
    try:
        ed.editor.setPlainText("关于××事项的通知\n正文内容……")
        monkeypatch.setattr("gwtool.ui.widgets.ask", lambda *a, **k: True)
        ed.save_to_db()
        assert ed.doc_id is not None
        d = dao.get_document(ed.doc_id)
        assert d is not None
        assert d.title.startswith("关于××事项的通知")
        assert not ed._dirty
    finally:
        ed.close()


def test_replace_document_text_keeps_undo(tmp_db, qapp):
    """程序化全文替换须保留撤销栈（一个撤销块，Ctrl+Z 一次还原）。"""
    from gwtool.ui.editor_panel import EditorPanel

    ed = EditorPanel()
    try:
        ed.editor.setPlainText("AAA\nBBB")
        ed.editor.textCursor().setPosition(0)
        ed.replace_document_text("CCC")
        assert ed.editor.toPlainText() == "CCC"
        assert ed.editor.document().isUndoAvailable()
        ed.editor.undo()
        assert ed.editor.toPlainText() == "AAA\nBBB"
    finally:
        ed.close()


def test_word_count_in_status(tmp_db, qapp):
    """编辑器状态栏显示字数。"""
    from gwtool.ui.editor_panel import EditorPanel

    ed = EditorPanel()
    try:
        ed.editor.setPlainText("关于××事项的通知\n  正文 两行。")
        ed._update_status("已保存")
        text = ed.lbl_status.text()
        assert "字" in text and "已保存" in text
    finally:
        ed.close()
