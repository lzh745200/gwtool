# -*- coding: utf-8 -*-
"""db 数据层逐模块深探（真实 SQLite 调用，禁 mock）。

覆盖此前从未执行过的 DAO/连接管理路径，每条用例注明探测动机：
  1. rename_category —— 历史上零覆盖；
  2. update_document_meta —— 三个可选参数组合 + FTS 同步零覆盖；
  3. rebuild_fts 的 phrases_fts 重建分支 —— 有短语数据时未测过；
  4. delete_dictionary_entry / delete_phrase / delete_attachments_of —— 零覆盖；
  5. list_error_pairs / list_phrases 的 keyword 过滤 —— 未测过；
  6. default_template_config 的回退分支（无默认模板/空库）—— 未测过；
  7. add_snapshot(doc_id=None) 的清理跳过分支 —— 未测过；
  8. search_dictionary 的空查询/三段命中/去重/limit —— 未测过；
  9. connection._pre_migrate_backup 的 -wal 打包与 OSError 吞掉分支 —— 未测过；
 10. connection 未配置时的 paths 回退分支 —— 未测过。
"""
from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path

import pytest

from gwtool.db import connection as dbconn
from gwtool.db import dao
from gwtool.db import schema as db_schema


# ---------------------------------------------------------------- categories
class TestRenameCategory:
    def test_rename_then_visible(self, tmp_db):
        cid = dao.add_category("旧名")
        dao.rename_category(cid, "新名")
        names = {c.id: c.name for c in dao.list_categories()}
        assert names[cid] == "新名"

    def test_rename_missing_id_is_noop(self, tmp_db):
        """对不存在的 id 改名：UPDATE 影响 0 行，不应抛异常。"""
        dao.rename_category(99999, "幽灵")
        assert all(c.id != 99999 for c in dao.list_categories())


# ---------------------------------------------------------------- doc meta
class TestUpdateDocumentMeta:
    def _mk_doc(self, title="原标题", content="乡村振兴政策文件"):
        return dao.Document(title=title, content_text=content)

    def test_tags_only(self, tmp_db):
        did = dao.add_document(self._mk_doc())
        dao.update_document_meta(did, tags="重要，2026")
        doc = dao.get_document(did)
        assert doc.tags == "重要，2026"
        assert doc.title == "原标题"  # 未传的列不动

    def test_category_only(self, tmp_db):
        did = dao.add_document(self._mk_doc())
        cid = dao.add_category("政策")
        dao.update_document_meta(did, category_id=cid)
        assert dao.get_document(did).category_id == cid

    def test_title_updates_fts_index(self, tmp_db):
        """改标题必须同步 FTS，否则新标题搜不到、旧标题仍命中。"""
        did = dao.add_document(self._mk_doc())
        dao.update_document_meta(did, title="新型城镇化要点")
        old_hits = [r.ref_id for r in dao.search_documents("原标题")]
        new_hits = [r.ref_id for r in dao.search_documents("城镇化要点")]
        assert did not in old_hits
        assert did in new_hits

    def test_missing_doc_id_is_noop(self, tmp_db):
        """对不存在的文档改 meta：不应抛异常，FTS 侧也不留脏行。"""
        dao.update_document_meta(424242, title="不存在")
        assert dao.get_document(424242) is None


# ---------------------------------------------------------------- rebuild_fts
class TestRebuildFtsWithPhrases:
    def test_phrases_rebuilt_and_searchable(self, tmp_db):
        did = dao.add_document(dao.Document(title="市局工作总结", content_text="全年安全生产形势稳定"))
        dao.add_phrase("特此报告", context="结尾惯用语")
        # 模拟索引静默失配：绕过 DAO 直插垃圾 + 删掉正常行
        conn = dbconn.get_conn()
        conn.execute("DELETE FROM phrases_fts")
        conn.execute("INSERT INTO documents_fts(title,tokenized,ref_id) VALUES('垃圾','垃圾',999)")
        conn.commit()
        counts = dao.rebuild_fts()
        assert counts["documents"] == 1
        assert counts["phrases"] == 1
        # 垃圾行被清掉
        remain = conn.execute("SELECT ref_id FROM documents_fts").fetchall()
        assert [r["ref_id"] for r in remain] == [did]
        # 短语重新可检索
        hits = dao.search_phrases("特此报告")
        assert len(hits) == 1 and hits[0].ref_id != 999

    def test_rebuild_skips_recycle_bin(self, tmp_db):
        did = dao.add_document(dao.Document(title="待删件", content_text="内容"))
        dao.delete_document(did)
        counts = dao.rebuild_fts()
        assert counts["documents"] == 0


# ---------------------------------------------------------------- dictionary
class TestDictionaryEntryDelete:
    def test_delete_then_lookup_empty(self, tmp_db):
        eid = dao.add_dictionary_entry("试行", definition="试验施行")
        assert dao.lookup_dictionary("试行")
        dao.delete_dictionary_entry(eid)
        assert dao.lookup_dictionary("试行") == []

    def test_delete_missing_id_is_noop(self, tmp_db):
        dao.delete_dictionary_entry(88888)  # 不应抛异常


# ---------------------------------------------------------------- error pairs
class TestListErrorPairsKeyword:
    def test_keyword_hits_wrong_and_correct(self, tmp_db):
        dao.add_error_pair("布署", "部署", source="测试源")
        dao.add_error_pair("按装", "安装", source="测试源")
        wrong_hit = {p.wrong for p in dao.list_error_pairs(keyword="布署")}
        correct_hit = {p.wrong for p in dao.list_error_pairs(keyword="安装")}
        assert wrong_hit == {"布署"}
        assert correct_hit == {"按装"}

    def test_limit_offset_paging(self, tmp_db):
        for i in range(5):
            dao.add_error_pair(f"错{i}", f"对{i}", source="分页源")
        page1 = dao.list_error_pairs(limit=2, offset=0)
        page2 = dao.list_error_pairs(limit=2, offset=2)
        ids1 = [p.id for p in page1]
        ids2 = [p.id for p in page2]
        assert len(page1) == 2 and len(page2) == 2
        assert not set(ids1) & set(ids2), "分页不得重叠"


# ---------------------------------------------------------------- phrases
class TestPhraseDeleteAndKeyword:
    def test_delete_removes_fts_row(self, tmp_db):
        pid = dao.add_phrase("特此函达", context="平行文结尾")
        assert dao.search_phrases("函达")
        dao.delete_phrase(pid)
        assert dao.list_phrases() == []
        assert dao.search_phrases("函达") == []  # FTS 行同步摘除

    def test_delete_missing_id_is_noop(self, tmp_db):
        dao.delete_phrase(77777)  # 不应抛异常

    def test_keyword_matches_context_too(self, tmp_db):
        dao.add_phrase("特此通知", context="普发性文件")
        dao.add_phrase("兹证明", context="介绍信专用")
        hit_a = {p.phrase for p in dao.list_phrases(keyword="介绍信")}
        hit_b = {p.phrase for p in dao.list_phrases(keyword="通知")}
        assert hit_a == {"兹证明"}
        assert hit_b == {"特此通知"}


# ---------------------------------------------------------------- templates
class TestDefaultTemplateFallback:
    def test_empty_db_returns_empty_string(self, tmp_db):
        assert dao.default_template_config() == ""

    def test_no_default_falls_back_to_first(self, tmp_db):
        """没有标记默认时回退到最早创建的模板，而非崩溃/空串。"""
        dao.save_template("甲", '{"m":1}')
        dao.save_template("乙", '{"m":2}')
        assert dao.default_template_config() == '{"m":1}'


# ---------------------------------------------------------------- snapshots
class TestSnapshotNullDoc:
    def test_doc_id_none_survives(self, tmp_db):
        """编辑器未保存（doc_id=None）时也可存快照，且不被清理逻辑波及。"""
        sid = dao.add_snapshot(None, "未保存稿", "草稿内容")
        row = dao.get_snapshot(sid)
        assert row is not None and row["doc_id"] is None

    def test_prune_keeps_latest_30(self, tmp_db):
        did = dao.add_document(dao.Document(title="快照源", content_text="x"))
        for i in range(35):
            dao.add_snapshot(did, "t", f"v{i}")
        snaps = dao.list_snapshots(did, limit=100)
        assert len(snaps) == dao.SNAPSHOT_KEEP
        assert snaps[0]["content"] == "v34"  # 最新的在最前


# ---------------------------------------------------------------- dictionary search
class TestSearchDictionaryStages:
    @pytest.fixture(autouse=True)
    def _seed(self, tmp_db):
        dao.add_dictionary_entry("乡村振兴", definition="三农战略")
        dao.add_dictionary_entry("乡村", definition="乡镇")
        dao.add_dictionary_entry("非乡村区域", definition="别的词")

    def test_empty_query_returns_empty(self, tmp_db):
        assert dao.search_dictionary("") == []
        assert dao.search_dictionary("   ") == []

    def test_exact_hit_ranks_first(self, tmp_db):
        hits = dao.search_dictionary("乡村")
        assert hits, "应有命中"
        assert hits[0].rank == 0.9 and hits[0].title == "乡村"

    def test_prefix_then_contains(self, tmp_db):
        hits = dao.search_dictionary("乡村")
        titles = [h.title for h in hits]
        assert "乡村振兴" in titles      # 前缀命中
        assert "非乡村区域" in titles     # 包含命中
        ranks = {h.title: h.rank for h in hits}
        assert ranks["乡村振兴"] == 0.8 > ranks["非乡村区域"] == 0.7

    def test_no_duplicate_rows(self, tmp_db):
        """一个词条只允许出现一次（三段 SQL 都可能命中同一条）。"""
        hits = dao.search_dictionary("乡村")
        titles = [h.title for h in hits]
        assert len(titles) == len(set(titles))


# ---------------------------------------------------------------- attachments dao
class TestDeleteAttachmentsOf:
    def test_bulk_delete_returns_count(self, tmp_db):
        did = dao.add_document(dao.Document(title="附件宿主", content_text="c"))
        dao.add_attachment(did, "a.pdf", "attachments/a.pdf", 10)
        dao.add_attachment(did, "b.pdf", "attachments/b.pdf", 20)
        other = dao.add_document(dao.Document(title="无关", content_text="c2"))
        dao.add_attachment(other, "c.pdf", "attachments/c.pdf", 30)
        n = dao.delete_attachments_of(did)
        assert n == 2
        assert dao.list_attachments(did) == []
        assert dao.count_attachments(other) == 1  # 无关文档不受影响

    def test_no_attachments_returns_zero(self, tmp_db):
        did = dao.add_document(dao.Document(title="无附件", content_text="c"))
        assert dao.delete_attachments_of(did) == 0


# ---------------------------------------------------------------- connection
class TestPreMigrateBackup:
    def _make_v1_db(self, db_file: Path) -> None:
        """构造一个 user_version=1 的老库：含 category_id（v1 即有），
        但缺 simhash(v2 补) 与 deleted_time(v3 补) —— 官方承诺的升级起点。"""
        conn = sqlite3.connect(str(db_file))
        conn.executescript("""
            CREATE TABLE documents(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content_text TEXT NOT NULL DEFAULT '',
                category_id INTEGER NOT NULL DEFAULT 0,
                text_hash TEXT NOT NULL DEFAULT '',
                word_count INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO documents(title, content_text, text_hash, word_count)
                VALUES('老文档', '老正文', 'h1', 3);
        """)
        conn.execute("PRAGMA user_version=1")
        conn.commit()
        conn.close()

    def test_backup_zip_contains_wal(self, tmp_path):
        """迁移前备份：zip 必须同时打包主库与 -wal 文件（第 76-78 行从未执行过）。"""
        db_file = tmp_path / "old" / "gwtool.db"
        db_file.parent.mkdir(parents=True)
        self._make_v1_db(db_file)
        # 制造真实 -wal：以 WAL 模式写入一条数据并保持连接打开——
        # SQLite 在最后一个连接关闭时才 checkpoint 删掉 -wal，
        # 用户机器上备份发生时正是"有连接持有、-wal 存在"的状态
        w = sqlite3.connect(str(db_file))
        w.execute("PRAGMA journal_mode=WAL")
        w.execute("INSERT INTO documents(title,content_text,text_hash) VALUES('wal内','数据','h2')")
        w.commit()
        assert db_file.with_name("gwtool.db-wal").exists()

        dbconn.configure(db_file)
        try:
            dbconn.get_conn()  # 触发 _pre_migrate_backup + v1→v3 迁移
            conn = dbconn.get_conn()
            ver = conn.execute("PRAGMA user_version").fetchone()[0]
            assert ver == db_schema.SCHEMA_VERSION
            # 老数据仍在（迁移不丢数据）
            n = conn.execute("SELECT count(*) FROM documents").fetchone()[0]
            assert n == 2
        finally:
            dbconn.close_current_thread()
            w.close()  # 收尾再放掉写连接，让 WAL 正常 checkpoint
        zips = list((tmp_path / "old" / "backups").glob("*premigrate_v1.zip"))
        assert len(zips) == 1, "迁移前必须自动留备份"
        with zipfile.ZipFile(zips[0]) as zf:
            names = zf.namelist()
        assert "gwtool.db" in names
        assert "gwtool.db-wal" in names, "-wal 也必须进备份包"

    def test_oserror_swallows_silently(self, tmp_path):
        """备份目录被同名文件占位时：迁移照常完成，只静默跳过备份（不崩启动）。"""
        db_file = tmp_path / "odd" / "gwtool.db"
        db_file.parent.mkdir(parents=True)
        self._make_v1_db(db_file)
        (db_file.parent / "backups").write_text("占位，非目录", encoding="utf-8")  # mkdir 必失败
        dbconn.configure(db_file)
        try:
            conn = dbconn.get_conn()
            ver = conn.execute("PRAGMA user_version").fetchone()[0]
            assert ver == db_schema.SCHEMA_VERSION, "备份失败不得阻断迁移"
        finally:
            dbconn.close_current_thread()


class TestConnectionFallback:
    def test_current_db_file_falls_back_to_paths(self, tmp_db, tmp_path):
        """configure 之前调用 current_db_file：回退到 paths.db_path() 而非崩溃。"""
        dbconn.close_current_thread()
        dbconn._db_file = None  # 模拟进程内未配置状态
        p = dbconn.current_db_file()
        assert p is not None and p.name.endswith(".db")
        # 回退目标必须落在隔离的数据目录内，绝不指向真实用户库
        from gwtool import paths
        assert str(p).startswith(str(paths._override))

    def test_get_conn_falls_back_to_paths(self, tmp_db):
        dbconn.close_current_thread()
        dbconn._db_file = None
        conn = dbconn.get_conn()
        conn.execute("SELECT 1")  # 能正常工作即可
        dbconn.close_current_thread()
