# -*- coding: utf-8 -*-
"""回收站（软删除）：迁移升级路径、软删除语义、恢复、彻底删除与索引同步。

这一组里最要紧的是 test_migration_v2_to_v3_*：迁移写错会在用户机器上改坏已有
资料库，所以必须手工造一个 user_version=2 且没有 deleted_time 列的旧库实测。
"""
import sqlite3

import pytest

from gwtool.db import connection as dbconn
from gwtool.db import dao
from gwtool.db.schema import SCHEMA_VERSION, init_schema

# v1.3.1（schema v2）时的 documents 建表语句：有 simhash，没有 deleted_time
_V2_DOCUMENTS_DDL = (
    "CREATE TABLE documents(id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " title TEXT NOT NULL, content_text TEXT NOT NULL DEFAULT '',"
    " blocks_json TEXT NOT NULL DEFAULT '[]', file_path TEXT DEFAULT '',"
    " file_type TEXT DEFAULT '', tags TEXT DEFAULT '',"
    " category_id INTEGER NOT NULL DEFAULT 0, text_hash TEXT NOT NULL DEFAULT '',"
    " word_count INTEGER NOT NULL DEFAULT 0,"
    " import_time TEXT NOT NULL DEFAULT '', updated_time TEXT NOT NULL DEFAULT '',"
    " simhash INTEGER);"
    "CREATE TABLE categories(id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " parent_id INTEGER NOT NULL DEFAULT 0, name TEXT NOT NULL,"
    " sort INTEGER NOT NULL DEFAULT 0, created_time TEXT NOT NULL DEFAULT '');"
    "CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);"
)


def _make_v2_db(db_file) -> None:
    """造一个 v1.3.1 的老库：user_version=2、documents 无 deleted_time 列、有数据。"""
    conn = sqlite3.connect(str(db_file))
    conn.executescript(_V2_DOCUMENTS_DDL)
    conn.execute("PRAGMA user_version=2")
    conn.execute(
        "INSERT INTO documents(title,content_text,tags,word_count,text_hash,category_id)"
        " VALUES('旧库材料','安全生产责任制的老内容','安全',12,'hash-old',0)")
    conn.execute("INSERT INTO categories(name) VALUES('旧分类')")
    conn.execute("INSERT INTO settings(key,value) VALUES('seeded_version','1.3.1')")
    conn.commit()
    conn.close()


def _columns(conn, table="documents") -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


# ------------------------------------------------------------------ 迁移
def test_migration_v2_to_v3_adds_column_and_keeps_data(tmp_path):
    """老库升级：列加上、原有数据一字不改、附件表建好、迁移前有自动备份。"""
    dbf = tmp_path / "old.db"
    _make_v2_db(dbf)
    dbconn.configure(dbf)
    try:
        conn = dbconn.get_conn()          # 首次连接即触发 init_schema
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 3
        assert "deleted_time" in _columns(conn), "迁移未补上 deleted_time 列"
        assert "simhash" in _columns(conn), "v2 的列不能丢"

        row = conn.execute(
            "SELECT title,content_text,tags,word_count,text_hash,deleted_time"
            " FROM documents").fetchone()
        assert tuple(row)[:5] == ("旧库材料", "安全生产责任制的老内容", "安全", 12,
                                 "hash-old"), "原有数据被迁移改坏了"
        assert row["deleted_time"] == "", "老数据升级后必须默认未删除"

        # 老库升级后 DAO 各条路径都要能正常用
        assert dao.count_documents() == 1
        assert [d.title for d in dao.list_documents()] == ["旧库材料"]
        # 本测试造的旧库没有 documents_fts 行（真实老库有），重建后应能检索
        dao.rebuild_fts()
        assert dao.search_documents("安全生产"), "升级后全文检索应仍可用"
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "attachments" in tables, "附件表应随建表语句一起创建"
        assert list((tmp_path / "backups").glob("*_premigrate_v2.zip")), \
            "老库升级前应有原始文件备份作为安全网"
    finally:
        dbconn.close_current_thread()


def test_migration_is_idempotent(tmp_path):
    """同一个库反复 init_schema 不应报错，也不应重复加列。"""
    dbf = tmp_path / "twice.db"
    _make_v2_db(dbf)
    conn = sqlite3.connect(str(dbf))
    init_schema(conn)
    init_schema(conn)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(documents)").fetchall()]
    assert cols.count("deleted_time") == 1
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_fresh_db_has_deleted_time_and_index(tmp_db):
    """全新库建表即带新列与索引（不依赖迁移），否则新装用户拿不到回收站。"""
    conn = dbconn.get_conn()
    assert "deleted_time" in _columns(conn)
    indexes = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
        " AND tbl_name='documents'").fetchall()}
    assert "idx_documents_deleted" in indexes


# ------------------------------------------------------------------ 软删除语义
def _mk(title="安全生产通知", text="安全生产人人有责，压实安全生产责任。", **kw):
    return dao.add_document(dao.Document(title=title, content_text=text, **kw))


def test_soft_delete_hides_from_list_count_and_search(tmp_db):
    did = _mk()
    other = _mk(title="乡村振兴要点", text="坚持农业农村优先发展。")
    assert dao.search_documents("安全生产")

    dao.delete_document(did)

    assert [d.id for d in dao.list_documents()] == [other], "列表仍看得到已删除文档"
    assert dao.count_documents() == 1, "计数没排除已删除文档"
    assert dao.search_documents("安全生产") == [], "全文检索仍命中已删除文档"
    assert dao.count_deleted_documents() == 1
    deleted = dao.list_deleted_documents()
    assert [d.id for d in deleted] == [did]
    assert deleted[0].deleted_time, "回收站列表应带删除时间"
    # 行还在（可恢复），且 get_document 仍能取到
    assert dao.get_document(did) is not None
    assert dao.get_document(did).deleted_time


def test_soft_delete_keeps_content_snapshots_and_category(tmp_db):
    cat = dao.add_category("通知类")
    did = _mk(category_id=cat)
    dao.add_snapshot(did, "安全生产通知", "旧内容", reason="auto")
    dao.delete_document(did)
    d = dao.get_document(did)
    assert d.content_text.startswith("安全生产"), "软删除不该动正文"
    assert d.category_id == cat
    assert len(dao.list_snapshots(did)) == 1
    assert dao.list_documents(category_id=cat) == []


def test_restore_brings_back_everything(tmp_db):
    did = _mk()
    dao.delete_document(did)
    assert dao.restore_document(did) is True
    assert [d.id for d in dao.list_documents()] == [did]
    assert dao.count_documents() == 1
    assert dao.search_documents("安全生产"), "恢复后必须重新可检索（FTS 行要补回）"
    assert dao.count_deleted_documents() == 0
    assert dao.get_document(did).deleted_time == ""
    # 未在回收站的文档再恢复一次应返回 False，而不是报错
    assert dao.restore_document(did) is False
    assert dao.restore_document(999999) is False


def test_deleted_docs_excluded_from_simhash_and_rebuild(tmp_db):
    """查重与重建索引都不能把回收站里的材料算进来。"""
    did = _mk()
    _mk(title="乡村振兴要点", text="坚持农业农村优先发展。")
    dao.delete_document(did)
    assert did not in dao.all_simhashes()

    dao.rebuild_fts()
    assert dao.search_documents("安全生产") == [], "重建索引把已删除文档又放回来了"
    assert dao.search_documents("乡村振兴")


def test_reimport_of_deleted_doc_resurrects_it(tmp_db):
    """text_hash 上有唯一索引：回收站里的同内容文档不恢复就再也导不进来。

    用户重新导入同一份材料时应把它从回收站捞回来，而不是提示"重复"却在
    资料库里找不到。
    """
    text = "安全生产人人有责，压实安全生产责任。"
    did = _mk(text=text)
    dao.delete_document(did)
    cat = dao.add_category("重新导入")
    again = dao.add_document(dao.Document(title="安全生产通知", content_text=text,
                                          category_id=cat))
    assert again == did, "应复用并恢复原文档，而不是报重复"
    assert dao.get_document(did).deleted_time == ""
    assert dao.get_document(did).category_id == cat
    assert dao.count_documents() == 1
    assert dao.search_documents("安全生产")


def test_duplicate_check_still_rejects_undeleted(tmp_db):
    """未删除的重复内容仍然返回 -1（原有去重行为不能变）。"""
    did = _mk(text=" identical content for dedup check ")
    assert dao.add_document(dao.Document(title="另一篇",
                                         content_text=" identical content for dedup check ")) == -1
    assert dao.count_documents() == 1
    assert dao.get_document(did) is not None


# ------------------------------------------------------------------ 彻底删除
def test_purge_removes_row_fts_and_attachments(tmp_db, monkeypatch, tmp_path):
    """彻底删除：真删行、删 FTS 行、删附件记录与磁盘文件。"""
    from gwtool.core import attachments
    import gwtool.paths as paths

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(paths, "app_data_dir", lambda: data)

    did = _mk()
    src = tmp_path / "附件.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    att = attachments.add(did, str(src))
    stored = attachments.resolve(att)
    assert stored.exists()

    dao.delete_document(did)
    attachments.purge_document(did)

    assert dao.get_document(did) is None, "彻底删除后行应真的没了"
    assert dao.list_deleted_documents() == []
    assert dao.search_documents("安全生产") == []
    assert dao.count_attachments(did) == 0
    assert not stored.exists(), "附件文件应一并删除"


def test_purge_documents_batch_and_orphan_sweep(tmp_db, monkeypatch, tmp_path):
    from gwtool.core import attachments
    import gwtool.paths as paths

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(paths, "app_data_dir", lambda: data)

    ids = [_mk(title=f"材料{i}", text=f"第{i}篇材料的布署内容。") for i in range(3)]
    src = tmp_path / "共同附件.txt"
    src.write_text("附件", encoding="utf-8")
    for did in ids:
        attachments.add(did, str(src))
    for did in ids:
        dao.delete_document(did)

    # 先确认附件文件确实复制进了数据目录，再验证彻底删除把它们清掉
    first_file = attachments.resolve(dao.list_attachments(ids[0])[0])
    assert first_file.exists()
    attachments.purge_documents(ids)
    assert not first_file.exists()
    assert dao.count_documents() == 0
    assert dao.count_attachments() == 0
    orphan = attachments.storage_dir() / "没人引用的文件.txt"
    orphan.write_text("x", encoding="utf-8")
    assert attachments.sweep_orphans() == 1
    assert not orphan.exists()


def test_deleted_document_ids_and_empty_bin(tmp_db):
    ids = [_mk(title=f"材料{i}", text=f"内容{i}号，安全生产。") for i in range(3)]
    dao.delete_document(ids[0])
    dao.delete_document(ids[2])
    assert sorted(dao.deleted_document_ids()) == sorted([ids[0], ids[2]])
    for did in dao.deleted_document_ids():
        dao.purge_document(did)
    assert dao.deleted_document_ids() == []
    assert dao.count_documents() == 1


# ------------------------------------------------------------------ UI
@pytest.fixture(autouse=True)
def _no_modal(monkeypatch):
    """离屏测试里屏蔽模态弹窗，否则 QMessageBox 会永久挂住。"""
    from PySide6.QtWidgets import QDialog, QMessageBox
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))


def test_recycle_bin_dialog_restore_and_purge(tmp_db, qapp):
    from gwtool.ui.material_dialogs import RecycleBinDialog

    kept = _mk(title="留着的材料", text="安全生产常抓不懈。")
    gone = _mk(title="要删的材料", text="乡村振兴全面推进。")
    dao.delete_document(gone)

    dlg = RecycleBinDialog()
    try:
        assert dlg.table.rowCount() == 1
        assert dlg.table.item(0, 0).text() == "要删的材料"
        assert dlg.table.item(0, 4).text(), "应显示删除时间"

        dlg.table.selectRow(0)
        dlg.restore_selected()
        assert dlg.table.rowCount() == 0
        assert sorted(d.id for d in dao.list_documents()) == sorted([kept, gone])
        assert dao.search_documents("乡村振兴"), "界面恢复后也应可检索"

        dao.delete_document(gone)
        dlg.reload()
        dlg.table.selectRow(0)
        dlg.purge_selected()
        assert dlg.table.rowCount() == 0
        assert dao.get_document(gone) is None
        # 没选中时操作只提示，不抛异常
        dlg.restore_selected()
        dlg.purge_selected()
        dlg.empty_bin()
    finally:
        dlg.close()


def test_library_panel_delete_goes_to_bin(tmp_db, qapp, monkeypatch):
    """资料库右键删除 = 移入回收站，面板上随即消失。"""
    from gwtool.ui.library_panel import LibraryPanel

    did = _mk(title="面板材料", text="安全生产人人有责。")
    panel = LibraryPanel()
    try:
        assert panel.doc_list.count() == 1
        panel.doc_list.item(0).setSelected(True)
        panel._delete_selected()
        assert panel.doc_list.count() == 0, "删除后仍显示在列表里"
        assert dao.count_deleted_documents() == 1
        assert dao.get_document(did) is not None
        panel.open_recycle_bin()          # 入口可用（exec 已屏蔽）
    finally:
        panel.close()
