# -*- coding: utf-8 -*-
"""P2/P3 回归：迁移框架、SimHash 持久化、重建索引、纠错门控、参考排序、
体检新规则、后台工作器、图标渲染、TTS 设置。"""
import sqlite3


def test_migration_v1_to_v2(tmp_path):
    """老库（user_version=1）启动时自动迁移到当前版本并做迁移前备份。"""
    from gwtool.db import connection as dbconn
    from gwtool.db.schema import SCHEMA_VERSION
    dbf = tmp_path / "old.db"
    conn = sqlite3.connect(str(dbf))
    conn.executescript(
        "CREATE TABLE documents(id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " title TEXT NOT NULL, content_text TEXT NOT NULL DEFAULT '',"
        " blocks_json TEXT NOT NULL DEFAULT '[]', file_path TEXT DEFAULT '',"
        " file_type TEXT DEFAULT '', tags TEXT DEFAULT '',"
        " category_id INTEGER NOT NULL DEFAULT 0, text_hash TEXT NOT NULL DEFAULT '',"
        " word_count INTEGER NOT NULL DEFAULT 0,"
        " import_time TEXT NOT NULL DEFAULT '', updated_time TEXT NOT NULL DEFAULT '');"
        "CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);")
    conn.execute("PRAGMA user_version=1")
    conn.execute("INSERT INTO documents(title) VALUES('旧库文档')")
    conn.commit()
    conn.close()

    dbconn.configure(dbf)
    try:
        from gwtool.db.connection import get_conn
        assert get_conn().execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        cols = {r[1] for r in get_conn().execute(
            "PRAGMA table_info(documents)").fetchall()}
        assert "simhash" in cols
        assert "deleted_time" in cols, "v3 迁移应补上回收站标记列"
        assert list((tmp_path / "backups").glob("*_premigrate_v1.zip")), \
            "迁移前应有原始库备份"
        from gwtool.db import dao
        assert dao.get_document(1).title == "旧库文档"
    finally:
        dbconn.close_current_thread()


def test_fresh_db_schema_version(tmp_db):
    from gwtool.db.connection import get_conn
    from gwtool.db.schema import SCHEMA_VERSION
    assert get_conn().execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_simhash_persisted(tmp_db):
    """入库/更新时计算 SimHash，查重可直接查表。"""
    from gwtool.core.simhash import find_similar
    from gwtool.db import dao
    text = "完全不同的另一段内容，用于重算哈希值。"
    did = dao.add_document(dao.Document(
        title="a", content_text="这是用来计算simhash的内容文本，内容足够长。"))
    assert dao.get_document(did).simhash
    dao.update_document_content(did, "a", text)
    assert dao.all_simhashes()[did]
    # texts 的第二个条目无需真实入库（find_similar 只按 dict 工作）
    pairs = find_similar({did: text, 99999: text}, hashes=dao.all_simhashes())
    assert pairs and pairs[0][2] == 1.0


def test_rebuild_fts(tmp_db):
    """FTS 索引失配后可经 rebuild_fts 自救。"""
    from gwtool.db import dao
    from gwtool.db.connection import get_conn
    dao.add_document(dao.Document(title="安全生产通知",
                                  content_text="安全生产人人有责。"))
    get_conn().execute("DELETE FROM documents_fts")
    get_conn().commit()
    assert not dao.search_documents("安全生产")
    counts = dao.rebuild_fts()
    assert counts["documents"] >= 1
    assert dao.search_documents("安全生产")


def test_fn_worker(qapp):
    from gwtool.ui.workers import FnWorker
    results = []
    w = FnWorker(lambda a, b: a + b, 2, 3)
    w.ok.connect(results.append)
    w.run()      # 测试中直接同步执行 run()
    assert results == [5]


def test_corrector_book_title_guard(tmp_db):
    """书名号内引用的文件标题不纠错；正文正常提示。"""
    from gwtool.core.corrector import check_text
    assert not [c for c in check_text("《关于加强布署的通知》") if c.wrong == "布署"]
    assert any(c.wrong == "布署" for c in check_text("做好今年的布署工作。"))


def test_reference_lookup_normalized(tmp_db):
    """三源结果归一到 [0,1]，且资料/词典/句式可同时命中。"""
    from gwtool.core import reference
    from gwtool.db import dao
    dao.add_document(dao.Document(
        title="安全生产通知",
        content_text="关于加强安全生产工作的通知，安全生产责任重大。", tags="安全"))
    dao.add_phrase("安全生产责任制", context="落实安全生产责任制")
    dao.add_dictionary_entry("安全生产", "ānquán shēngchǎn", "生产过程安全")
    items = reference.lookup("安全生产")
    assert items
    assert all(0.0 <= it.rank <= 1.0 for it in items)
    sources = {it.source for it in items}
    assert "documents" in sources


def test_inspector_classification_and_attachment():
    """体检新规则：密级标注格式、附件说明格式。"""
    from gwtool.core.inspector import inspect_text
    f1 = inspect_text("秘密\n关于××的通知\n\n各科室：\n正文。")
    assert any(x.item == "密级标注" and x.severity == "error" for x in f1)
    f2 = inspect_text("关于××的通知\n\n各科室：\n正文。\n\n附件 保洁方案。")
    assert any(x.item == "附件说明" for x in f2)
    f3 = inspect_text("关于××的通知\n\n各科室：\n正文。")
    assert not any(x.item == "密级标注" for x in f3)


def test_differ_tokens_preserve_text():
    """词级切分必须保证 join 还原原文。"""
    from gwtool.core.differ import _tokens
    s = "关于加强安全生产工作 的通知（试行）。"
    assert "".join(_tokens(s)) == s
    assert "".join(_tokens("ab")) == "ab"     # 短串退回字符级


def test_tts_settings_default(tmp_db):
    from gwtool.core.tts import _settings
    assert _settings() == (0, "")


def test_icons_render(qapp):
    """SVG 图标渲染可用（PySide6 qsvg 插件随装随用）。"""
    from gwtool.ui import icons
    assert not icons.icon("new_doc").isNull()
