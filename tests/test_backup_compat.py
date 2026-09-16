# -*- coding: utf-8 -*-
"""备份恢复的**向后兼容**护栏。

背景（一次真实的踩坑）
----------------------
`_validate_restored_db` 原本的判据是"缺任意一张业务表就拒绝恢复"，而业务表清单
`required_table_names()` 是从 `TABLES` **自动推导**的。于是"给数据库加一张新表"
这个看似无害的动作，会让**所有旧备份立刻无法恢复** —— 用户升级后发现自己的
备份打不开了。症状极隐蔽：日常使用一切正常，只有去恢复旧备份时才暴露。

正确判据是区分两类表：
  · **核心表**（documents/settings/dictionary/error_pairs）—— 缺任意一张
    说明"这不是本程序创建的库"，必须拒绝；
  · **其余业务表** —— 属后续版本陆续新增，缺失是正常版本差异，恢复后由
    init_schema 建出空表即可，绝不能拒绝。
"""
from __future__ import annotations

import re
import sqlite3
import zipfile

import pytest

from gwtool.core import backup
from gwtool.db.schema import TABLES, core_table_names, required_table_names

_NAME_RE = re.compile(
    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE)


def _zip_of(tmp_path, db_path) -> str:
    zp = tmp_path / "pack.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.write(db_path, "gwtool.db")
    return str(zp)


def _make_zip(tmp_path, table_names) -> str:
    """按 schema 的**真实 DDL** 建出指定表并打包。

    必须用真实 DDL 而非 `CREATE TABLE x(id)` 之类的最小桩：表已存在时
    `CREATE TABLE IF NOT EXISTS` 会被跳过，随后 INDEXES 里针对真实列
    （如 documents.text_hash）建索引就会失败。用桩会测出假故障。
    """
    wanted = set(table_names)
    src = tmp_path / "src.db"
    conn = sqlite3.connect(str(src))
    for ddl in TABLES:
        m = _NAME_RE.search(ddl)
        if m and m.group(1) in wanted:
            conn.execute(ddl)
    conn.commit()
    conn.close()
    return _zip_of(tmp_path, src)


def _make_zip_raw(tmp_path, ddls) -> str:
    """用任意 DDL 建库并打包 —— 用于构造"不是本程序的库"。"""
    src = tmp_path / "raw.db"
    conn = sqlite3.connect(str(src))
    for ddl in ddls:
        conn.execute(ddl)
    conn.commit()
    conn.close()
    return _zip_of(tmp_path, src)


def _ensure_db(tmp_db) -> None:
    """tmp_db 只做重定向，不会真的建库；需要库文件存在时先触发一次初始化。"""
    from gwtool.db import connection as dbconn
    from gwtool.db.schema import init_schema
    init_schema(dbconn.get_conn())


class TestCoreTableDeclaration:
    def test_core_tables_are_real_tables(self):
        assert set(core_table_names()) <= required_table_names()

    def test_receive_register_is_not_core(self):
        """收文表是本次新增的，**绝不能**进核心清单 —— 否则旧备份立刻失效。"""
        assert "receive_register" not in core_table_names()

    def test_receive_register_is_registered_as_business_table(self):
        assert "receive_register" in required_table_names()


class TestRestoreCompatibility:
    def test_accepts_backup_missing_newer_tables(self, tmp_db, tmp_path):
        """老备份缺 receive_register 必须仍可恢复。"""
        zp = _make_zip(tmp_path, list(core_table_names()))
        backup.restore_backup_detailed(zp)      # 不得抛异常

    def test_missing_tables_are_created_after_restore(self, tmp_db, tmp_path):
        """恢复后 init_schema 会把缺的表补出来（空表），已有数据不受影响。"""
        zp = _make_zip(tmp_path, list(core_table_names()))
        backup.restore_backup_detailed(zp)
        from gwtool.db import connection as dbconn
        from gwtool.db.schema import init_schema

        # 恢复换掉了库文件，旧连接必须丢弃后重取
        dbconn.close_current_thread()
        conn = dbconn.get_conn()
        init_schema(conn)
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "receive_register" in names, "旧备份恢复后应补齐新表"

    def test_rejects_db_without_core_tables(self, tmp_db, tmp_path):
        """缺核心表 -> 判定非本程序的库，拒绝恢复。"""
        zp = _make_zip_raw(
            tmp_path, ["CREATE TABLE some_unrelated_table(id INTEGER)"])
        with pytest.raises(ValueError):
            backup.restore_backup_detailed(zp)

    def test_rejects_db_missing_one_core_table(self, tmp_db, tmp_path):
        """核心表缺一张也必须拒绝（防被无关库覆盖）。"""
        partial = [t for t in core_table_names() if t != "documents"]
        zp = _make_zip(tmp_path, partial)
        with pytest.raises(ValueError):
            backup.restore_backup_detailed(zp)

    def test_rejects_empty_db(self, tmp_db, tmp_path):
        zp = _make_zip_raw(tmp_path, [])
        with pytest.raises(ValueError):
            backup.restore_backup_detailed(zp)

    def test_recovery_message_mentions_core_tables(self, tmp_db, tmp_path):
        """报错要能自解释：告诉用户缺的是"核心业务表"而非笼统的"表"。"""
        zp = _make_zip_raw(tmp_path, ["CREATE TABLE unrelated(id INTEGER)"])
        with pytest.raises(ValueError) as ei:
            backup.restore_backup_detailed(zp)
        assert "核心业务表" in str(ei.value)


class TestRoundTripStillWorks:
    def test_real_backup_restores(self, tmp_db, tmp_path):
        """既有回环不能坏：真实备份仍可恢复（回归保护）。"""
        from gwtool.db import dao
        dao.add_document(dao.Document(title="回环测试", content_text="内容"))
        pack = backup.create_backup(note="兼容回环", mode=backup.MODE_MANUAL)
        backup.restore_backup_detailed(pack)
        assert dao.count_documents() >= 1

    def test_backup_contains_receive_table(self, tmp_db):
        """新表必须随备份一起走，否则恢复后收文台账会凭空消失。"""
        _ensure_db(tmp_db)
        pack = backup.create_backup(note="新表检查", mode=backup.MODE_MANUAL)
        with zipfile.ZipFile(pack) as zf:
            names = zf.namelist()
        assert any(n.endswith("gwtool.db") for n in names), names
        # 表结构随库文件走：恢复后由 init_schema 保证存在
        assert "receive_register" in required_table_names()
