# -*- coding: utf-8 -*-
"""v1.9 A 批：数据安全与自检修复的回归锚点（主控负责的两项）。

覆盖：
  · P1-30 恢复备份时校验 `user_version` 方向：新版本备份必须被拒绝（否则
    "恢复成功 → 程序打不开"）；旧备份/当前版本必须放行；
  · P1-33 `dbhealth.consistency_check` 不再把回收站文档算进"索引一致性"：
    软删除后 FTS 少一行是**正常**的，旧实现会持续告警且维护也治不好。
"""
from __future__ import annotations

import sqlite3

import pytest

from gwtool.core import backup, dbhealth
from gwtool.db import connection as dbconn
from gwtool.db import dao
from gwtool.db.schema import SCHEMA_VERSION, init_schema


def _make_db(path, user_version: int) -> None:
    """造一个"结构完整"的 gwtool 库（校验只看核心表 + 完整性）。"""
    conn = sqlite3.connect(str(path))
    try:
        for ddl in init_schema_statements():
            conn.execute(ddl)
        conn.execute(f"PRAGMA user_version = {int(user_version)}")
        conn.commit()
    finally:
        conn.close()


def init_schema_statements():
    from gwtool.db.schema import FTS_TABLES, TABLES
    return list(TABLES) + list(FTS_TABLES)


def test_restore_rejects_backup_from_newer_schema(tmp_path):
    """来自更新版本的备份必须被拒绝，且提示升级程序（P1-30）。"""
    newer = tmp_path / "newer.db"
    _make_db(newer, SCHEMA_VERSION + 1)
    with pytest.raises(ValueError) as ei:
        backup._validate_restored_db(newer)
    msg = str(ei.value)
    assert "更新版本" in msg and "升级" in msg, msg
    assert "当前数据未被改动" in msg, msg


@pytest.mark.parametrize("version", [SCHEMA_VERSION, 0, 3])
def test_restore_accepts_current_or_older_schema(tmp_path, version):
    """当前版本与旧版本备份必须放行（旧备份交给启动迁移）。"""
    p = tmp_path / f"v{version}.db"
    _make_db(p, version)
    backup._validate_restored_db(p)          # 不抛异常即通过


def test_dbhealth_ignores_recycle_bin_documents(tmp_db):
    """有文档在回收站时，自检不得报"全文索引与文档数不一致"（P1-33）。"""
    init_schema(dbconn.get_conn())
    keep = dao.add_document(dao.Document(title="在库文档", content_text="安全生产"))
    trash = dao.add_document(dao.Document(title="回收站文档", content_text="排查隐患"))
    dao.delete_document(trash)               # 软删除：撤 FTS 行、保留 documents 行
    dbconn.get_conn().commit()

    problems, stats = dbhealth.consistency_check()
    assert stats["documents"] == 1, stats
    assert stats["documents_in_trash"] == 1, stats
    assert not [p for p in problems if "全文索引" in p], problems
    # 在库文档仍然可检索（索引没被误判后"顺手修坏"）
    assert dao.search_documents("安全生产"), "在库文档应仍可检索"
    assert keep and trash


def test_dbhealth_still_reports_real_mismatch(tmp_db):
    """反例：真删掉 FTS 行时必须报（不能把护栏一起改没了）。"""
    init_schema(dbconn.get_conn())
    doc_id = dao.add_document(dao.Document(title="正常文档", content_text="安全生产"))
    dbconn.get_conn().execute("DELETE FROM documents_fts WHERE ref_id=?", (doc_id,))
    dbconn.get_conn().commit()

    problems, _stats = dbhealth.consistency_check()
    assert [p for p in problems if "全文索引" in p], problems
