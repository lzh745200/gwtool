# -*- coding: utf-8 -*-
"""异常鲁棒性回归（第 20 轮逐模块探测产出）：损坏库启动防护、脏数据容忍。"""
from __future__ import annotations

import sqlite3

from pathlib import Path

from gwtool.db import connection as dbconn
from gwtool.db import dao


def test_corrupted_db_startup_guard(tmp_db, qapp, monkeypatch):
    """库文件损坏时 run() 返回 2 并落诊断日志，绝不带栈崩溃、不自动覆盖。"""
    import gwtool.app as appmod
    from gwtool import paths

    # 把当前数据目录的库文件破坏（魔数对、内容烂）
    db_file = paths.db_path()
    db_file.write_bytes(b"SQLite format 3\x00" + b"\x00" * 2048)

    # 造一个"历史备份"，验证指引中包含它
    import zipfile
    bk_dir = paths.backup_dir()
    bk = bk_dir / "gwtool_backup_test.zip"
    with zipfile.ZipFile(bk, "w") as zf:
        zf.writestr("gwtool.db", "占位")

    rc = appmod.run()
    assert rc == 2
    log = Path.home() / "gwtool_启动诊断.log"
    assert log.exists()
    text = log.read_text(encoding="utf-8")
    assert "数据库文件损坏" in text and "gwtool_backup_test.zip" in text
    # 被破坏的库文件未被静默覆盖（用户数据安全第一）
    assert db_file.read_bytes().startswith(b"SQLite format 3\x00")


def test_dirty_blocks_json_falls_back(tmp_db):
    """手工脏库（blocks_json 非法）：汇编树兜底为纯文本段，不崩溃。"""
    conn = dbconn.get_conn()
    conn.execute("INSERT INTO documents(title, content_text, blocks_json,"
                 " text_hash, word_count) VALUES('脏JSON', 'FTS可见内容',"
                 " '不是JSON{{{', 'hd1', 6)")
    conn.commit()
    from gwtool.core.compiler import load_trees
    from gwtool.core.model import PARAGRAPH
    tree = load_trees([conn.execute(
        "SELECT id FROM documents WHERE title='脏JSON'").fetchone()[0]], [])[0]
    assert len(tree.blocks) == 1 and tree.blocks[0].type == PARAGRAPH
    assert "FTS可见内容" in tree.blocks[0].text


def test_lock_recovered_writes(tmp_db):
    """外部 EXCLUSIVE 锁：写入超时抛干净错误；锁释放后恢复（WAL 读取不受影响）。"""
    conn = dbconn.get_conn()
    dao.add_document(dao.Document(title="锁前文档", content_text="锁前内容"))
    lock = sqlite3.connect(str(tmp_db))
    lock.execute("BEGIN EXCLUSIVE")
    try:
        dao.add_document(dao.Document(title="锁期间", content_text="x"))
        raise AssertionError("持锁期间写入不应成功")
    except sqlite3.OperationalError:
        pass
    rs = dao.search_documents("锁前内容")     # 锁期间读取正常（WAL）
    assert isinstance(rs, list)
    lock.rollback()
    lock.close()
    assert dao.add_document(dao.Document(title="锁后文档", content_text="锁后")) > 0
