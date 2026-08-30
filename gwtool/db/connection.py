# -*- coding: utf-8 -*-
"""SQLite 连接管理。

单机桌面应用：主线程与后台工作线程各自持有连接（SQLite 连接不可跨线程共用）。
通过 thread-local 方式封装，写操作统一走 with 事务。
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .schema import init_schema

_local = threading.local()
_db_file: Path | None = None


def configure(db_file: Path) -> None:
    """设置数据库文件位置（测试可指向临时文件）。

    强制关闭当前线程已有连接，防止切换目标文件后仍复用旧连接
    （跨测试/跨配置数据泄漏的根源）。
    """
    global _db_file
    close_current_thread()
    _db_file = Path(db_file)


def get_conn() -> sqlite3.Connection:
    """获取当前线程的连接；首次调用时建库建表/迁移。"""
    if _db_file is None:
        from gwtool.paths import db_path
        configure(db_path())
    conn = getattr(_local, "conn", None)
    if conn is None:
        _db_file.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_db_file), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        init_schema(conn)
        _local.conn = conn
    return conn


def close_current_thread() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


def current_db_file() -> Path:
    """当前配置的数据库文件路径（供备份/恢复使用，测试可指向临时文件）。"""
    if _db_file is None:
        from gwtool.paths import db_path
        configure(db_path())
    return _db_file
