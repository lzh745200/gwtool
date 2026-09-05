# -*- coding: utf-8 -*-
"""附件管理：文件复制进数据目录、重名不覆盖、删除/彻底删除的联动、备份随带。

关键设计：附件本体必须落在数据目录内（paths.attachments_dir），库里只存相对路径。
只记用户选的原始绝对路径的话，备份恢复或便携模式换机器后附件全部失联。
"""
from pathlib import Path

import pytest

from gwtool.core import attachments
from gwtool.db import dao


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """把数据目录指到临时目录：附件测试绝不能碰真实的 %APPDATA%/gwtool。"""
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    import gwtool.paths as paths
    monkeypatch.setattr(paths, "app_data_dir", lambda: d)
    return d


@pytest.fixture()
def doc(tmp_db):
    return dao.add_document(dao.Document(title="关于季度工作的报告",
                                         content_text="一季度各项工作平稳推进。"))


def _make_file(tmp_path, name="附件一.pdf", content=b"%PDF-1.4 fake") -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


# ------------------------------------------------------------------ 落盘位置
def test_add_copies_file_into_data_dir(tmp_db, data_dir, doc, tmp_path):
    """添加附件 = 复制进数据目录，而不是只在库里记一条原路径。"""
    src = _make_file(tmp_path)
    att = attachments.add(doc, str(src))

    stored = attachments.resolve(att)
    assert stored is not None and stored.exists(), "附件文件没有真的落盘"
    assert stored.read_bytes() == src.read_bytes()
    # 落在数据目录的 attachments/ 子目录内，且库里存的是相对路径
    assert stored.parent == attachments.storage_dir()
    assert attachments.storage_dir() == data_dir / "attachments"
    assert not Path(att.stored_path).is_absolute(), "库里应存相对数据目录的路径"
    assert att.stored_path == f"attachments/{stored.name}"
    assert att.size == src.stat().st_size > 0
    assert att.file_name == "附件一.pdf"
    # 原件保持不动（是复制不是移动）
    assert src.exists()
    # 数据库记录一致
    assert dao.list_attachments(doc)[0].id == att.id
    assert dao.count_attachments(doc) == 1
    assert dao.count_attachments() == 1


def test_duplicate_name_is_not_overwritten(tmp_db, data_dir, doc, tmp_path):
    """同名不同内容的两个附件都要留住（加序号），绝不静默覆盖。"""
    a = _make_file(tmp_path, "报告.pdf", b"FIRST")
    b_dir = tmp_path / "elsewhere"
    b_dir.mkdir()
    b = _make_file(b_dir, "报告.pdf", b"SECOND-DIFFERENT")

    att1 = attachments.add(doc, str(a))
    att2 = attachments.add(doc, str(b))

    p1, p2 = attachments.resolve(att1), attachments.resolve(att2)
    assert p1 != p2, "重名附件被写到同一个文件上了"
    assert p1.read_bytes() == b"FIRST"
    assert p2.read_bytes() == b"SECOND-DIFFERENT", "原文件被覆盖了"
    assert p2.name != p1.name
    assert dao.count_attachments(doc) == 2
    # 第三次同名再落一个，序号继续往后走
    att3 = attachments.add(doc, str(b))
    assert attachments.resolve(att3).read_bytes() == b"SECOND-DIFFERENT"
    assert len({attachments.resolve(x).name for x in (att1, att2, att3)}) == 3


def test_stored_name_is_sanitized(tmp_db, data_dir):
    """文件名来自外部：路径分隔符、盘符、".." 一律清洗掉，不能决定落盘位置。"""
    assert attachments.safe_stored_name(r"..\..\evil.txt") == "evil.txt"
    assert attachments.safe_stored_name("a/b/c.txt") == "c.txt"
    assert attachments.safe_stored_name(r"C:\windows\system32\x.dll") == "x.dll"
    assert attachments.safe_stored_name("报:告?.pdf") == "报_告_.pdf"
    assert attachments.safe_stored_name("...") == "附件"
    assert attachments.safe_stored_name("") == "附件"
    assert len(attachments.safe_stored_name("长" * 500)) <= 120
    # 清洗后的名字落盘仍在附件目录内
    p = attachments.unique_path(attachments.storage_dir(), r"..\..\evil.txt")
    assert p.parent == attachments.storage_dir()


def test_add_rejects_bad_input(tmp_db, data_dir, doc, tmp_path):
    """没文档 / 文件不存在都要给出明确错误，由 UI 转成提示而不是崩。"""
    with pytest.raises(ValueError):
        attachments.add(0, str(_make_file(tmp_path)))
    with pytest.raises(FileNotFoundError):
        attachments.add(doc, str(tmp_path / "不存在的文件.pdf"))
    with pytest.raises(OSError):
        attachments.add(doc, str(tmp_path))            # 目录不是普通文件


def test_add_many_single_failure_does_not_abort(tmp_db, data_dir, doc, tmp_path):
    """批量添加：坏路径只进失败清单，其余照常入库。"""
    good1 = _make_file(tmp_path, "甲.docx", b"A")
    good2 = _make_file(tmp_path, "乙.docx", b"B")
    added, failures = attachments.add_many(
        doc, [str(good1), str(tmp_path / "没有这个文件.pdf"), str(good2)])
    assert [a.file_name for a in added] == ["甲.docx", "乙.docx"]
    assert len(failures) == 1 and "没有这个文件" in failures[0][0]
    assert dao.count_attachments(doc) == 2


# ------------------------------------------------------------------ 删除与联动
def test_remove_attachment_keeps_document(tmp_db, data_dir, doc, tmp_path):
    """删附件只删附件：文件与记录都没了，文档与正文一根毫毛都不能动。"""
    att = attachments.add(doc, str(_make_file(tmp_path)))
    stored = attachments.resolve(att)
    before = dao.get_document(doc).content_text

    assert attachments.remove(att) is True
    assert not stored.exists()
    assert dao.list_attachments(doc) == []
    assert dao.get_document(doc) is not None
    assert dao.get_document(doc).content_text == before


def test_remove_for_document(tmp_db, data_dir, doc, tmp_path):
    for name in ("一.pdf", "二.pdf"):
        attachments.add(doc, str(_make_file(tmp_path, name, name.encode())))
    ok, stuck = attachments.remove_for_document(doc)
    assert (ok, stuck) == (2, [])
    assert dao.count_attachments(doc) == 0
    assert list(attachments.storage_dir().iterdir()) == []
    assert dao.get_document(doc) is not None


def test_soft_delete_keeps_attachments_until_purge(tmp_db, data_dir, doc, tmp_path):
    """移入回收站不动附件（恢复后还得能用）；彻底删除才连文件一起清。"""
    att = attachments.add(doc, str(_make_file(tmp_path)))
    stored = attachments.resolve(att)

    dao.delete_document(doc)
    assert stored.exists(), "软删除不该删附件文件"
    assert dao.count_attachments(doc) == 1
    assert dao.restore_document(doc) is True
    assert attachments.exists(dao.list_attachments(doc)[0])

    stuck = attachments.purge_document(doc)
    assert stuck == []
    assert not stored.exists(), "彻底删除应连附件文件一起删"
    assert dao.count_attachments(doc) == 0
    assert dao.get_document(doc) is None


def test_missing_file_is_reported_not_crash(tmp_db, data_dir, doc, tmp_path):
    """用户手工挪走附件文件：列表标"已丢失"，删除记录仍能清干净。"""
    att = attachments.add(doc, str(_make_file(tmp_path)))
    attachments.resolve(att).unlink()

    assert attachments.exists(att) is False
    assert attachments.resolve(att) is not None        # 路径仍能给出，供界面显示
    assert attachments.remove(att) is True             # 文件不在也要能删记录
    assert dao.list_attachments(doc) == []


def test_resolve_supports_absolute_legacy_path(tmp_db, data_dir, doc, tmp_path):
    """历史/脏数据里存了绝对路径：能定位、能打开，但删除只清记录不动外部文件。"""
    outside = _make_file(tmp_path, "外部文件.pdf", b"OUT")
    att_id = dao.add_attachment(doc, outside.name, str(outside), outside.stat().st_size)
    att = dao.get_attachment(att_id)
    assert attachments.resolve(att) == outside
    assert attachments.exists(att) is True
    assert attachments.remove(att) is True
    assert outside.exists(), "绝不能删数据目录以外的用户文件"
    assert dao.list_attachments(doc) == []


def test_relative_path_escaping_data_dir_is_refused(tmp_db, data_dir, doc, tmp_path):
    """库里被塞进 "../" 这类越界相对路径时：拒绝定位、只清记录、外部文件不动。"""
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    att_id = dao.add_attachment(doc, "outside.txt", "../outside.txt", 6)
    att = dao.get_attachment(att_id)
    assert attachments.resolve(att) is None
    assert attachments.exists(att) is False
    assert attachments.remove(att) is True
    assert outside.exists(), "越界路径指向的文件被删了"
    assert dao.list_attachments(doc) == []


def test_backup_and_restore_carry_attachments(tmp_db, data_dir, doc, tmp_path):
    """备份包必须带着附件：恢复（或换机器）后附件仍在，不能失联。"""
    from gwtool.core import backup

    att = attachments.add(doc, str(_make_file(tmp_path, "随备份走的.pdf", b"PAYLOAD")))
    stored = attachments.resolve(att)
    z = backup.create_backup(note="带附件的备份")
    assert Path(z).exists()

    # 模拟换机器/数据目录丢失：附件文件与库都被清掉，再从备份恢复
    stored.unlink()
    assert not stored.exists()
    backup.restore_backup(z)

    back = attachments.resolve(dao.list_attachments(doc)[0])
    assert back.exists(), "恢复备份后附件文件没有回来"
    assert back.read_bytes() == b"PAYLOAD"


def test_human_size():
    assert attachments.human_size(0) == "0 B"
    assert attachments.human_size(512) == "512 B"
    assert attachments.human_size(2048) == "2.0 KB"
    assert attachments.human_size(5 * 1024 * 1024) == "5.0 MB"


def test_attachment_counts_avoids_n_plus_one(tmp_db, data_dir, tmp_path):
    ids = [dao.add_document(dao.Document(title=f"材料{i}", content_text=f"内容{i}"))
           for i in range(3)]
    src = _make_file(tmp_path)
    attachments.add(ids[0], str(src))
    attachments.add(ids[0], str(src))
    attachments.add(ids[2], str(src))
    assert dao.attachment_counts(ids) == {ids[0]: 2, ids[2]: 1}
    assert dao.attachment_counts([]) == {}


# ------------------------------------------------------------------ UI
@pytest.fixture(autouse=True)
def _no_modal(monkeypatch):
    """离屏测试屏蔽模态弹窗，否则 QMessageBox/QFileDialog 会永久挂住。"""
    from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: ("", "")))


def test_attachment_dialog_lists_and_deletes(tmp_db, qapp, data_dir, doc, tmp_path):
    from gwtool.ui.material_dialogs import AttachmentDialog

    att = attachments.add(doc, str(_make_file(tmp_path, "对话框附件.txt", b"X")))
    dlg = AttachmentDialog(doc, None, doc_title="关于季度工作的报告")
    try:
        assert dlg.table.rowCount() == 1
        assert dlg.table.item(0, 0).text() == "对话框附件.txt"
        assert dlg.table.item(0, 3).text() == "正常"
        assert "1 个附件" in dlg.lbl_count.text()

        dlg.table.selectRow(0)
        dlg.delete_selected()
        assert dlg.table.rowCount() == 0
        assert not attachments.resolve(att).exists()
        # 没有选中时各操作只提示，不抛异常
        dlg.delete_selected()
        dlg.open_selected()
        dlg.save_as()
        dlg.open_folder()
    finally:
        dlg.close()


def test_attachment_dialog_marks_missing_file(tmp_db, qapp, data_dir, doc, tmp_path):
    from gwtool.ui.material_dialogs import AttachmentDialog

    att = attachments.add(doc, str(_make_file(tmp_path)))
    attachments.resolve(att).unlink()
    dlg = AttachmentDialog(doc)
    try:
        assert dlg.table.item(0, 3).text() == "文件已丢失"
        dlg.table.selectRow(0)
        dlg.open_selected()          # 只提示，不崩
    finally:
        dlg.close()


def test_attachment_dialog_add_runs_in_worker(tmp_db, qapp, data_dir, doc,
                                              tmp_path, monkeypatch):
    """添加附件走后台线程复制（大文件不冻界面），完成后列表刷新。"""
    from PySide6.QtWidgets import QFileDialog
    from gwtool.ui.material_dialogs import AttachmentDialog

    src = _make_file(tmp_path, "后台复制.pdf", b"WORKER")
    monkeypatch.setattr(QFileDialog, "getOpenFileNames",
                        staticmethod(lambda *a, **k: ([str(src)], "")))
    dlg = AttachmentDialog(doc)
    try:
        dlg.add_files()
        worker = dlg._worker
        assert worker is not None, "附件复制没有走后台线程"
        assert worker.wait(20000), "后台复制线程未在时限内结束"
        qapp.processEvents()          # 让 ok 信号回到主线程执行刷新
        assert dlg.table.rowCount() == 1
        assert dao.count_attachments(doc) == 1
        assert attachments.resolve(dao.list_attachments(doc)[0]).read_bytes() == b"WORKER"
    finally:
        dlg.close()


def test_attachment_dialog_without_doc_only_warns(tmp_db, qapp, data_dir):
    """未关联文档时构造与添加都不能崩（对话框会被无参构造测试覆盖）。"""
    from gwtool.ui.material_dialogs import AttachmentDialog

    dlg = AttachmentDialog()
    try:
        assert dlg.table.rowCount() == 0
        dlg.add_files()               # 只 warn 后 return
        dlg.reload()
    finally:
        dlg.close()


def test_library_panel_shows_attachment_marker(tmp_db, qapp, data_dir, doc, tmp_path):
    """资料库列表上标出附件数，用户才知道哪篇材料挂了附件。"""
    from gwtool.ui.library_panel import LibraryPanel

    attachments.add(doc, str(_make_file(tmp_path)))
    panel = LibraryPanel()
    try:
        assert panel.doc_list.count() == 1
        assert "附件1" in panel.doc_list.item(0).text()
        panel.doc_list.item(0).setSelected(True)
        panel._open_attachments()     # 入口可用（exec 已屏蔽）
    finally:
        panel.close()

def test_data_dir_isolation_under_tmp_db(tmp_db):
    """tmp_db 夹具必须同时隔离数据目录：附件/备份不得写进真实 %APPDATA%。"""
    import os as _os
    from gwtool import paths

    app_dir = paths.app_data_dir()
    assert app_dir == tmp_db.parent
    assert paths.attachments_dir().parent == app_dir
    assert paths.backup_dir().parent == app_dir
    real = _os.environ.get("APPDATA", "")
    if real:
        assert real.lower() not in str(app_dir).lower()
