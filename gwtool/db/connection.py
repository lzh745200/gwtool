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
        _pre_migrate_backup()
        conn = sqlite3.connect(str(_db_file), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        init_schema(conn)
        _local.conn = conn
    return conn


def _pre_migrate_backup() -> None:
    """老库升级（schema 迁移）前自动做一次备份，作为安全网。

    用 SQLite 在线备份 API 落一份**自洽**快照再打包，不走 get_conn
    （避免在初始化中递归触发迁移）。

    为什么不是"直接打包 db/-wal 原始文件"：老实现把 gwtool.db 与
    gwtool.db-wal 各作为一个 zip 成员塞进去，看似保住了 WAL，实际产出的
    是一个**解压后无法直接使用**的包——恢复端只会取包内的 gwtool.db
    覆盖主库，而 -wal 要么被忽略、要么与主库版本错配，SQLite 打开时报
    "database disk image is malformed"。在线备份 API 会把 WAL 中已提交的
    事务重放进快照，产出的单个 .db 文件自己就是完整可用的。
    """
    from .schema import SCHEMA_VERSION
    if not _db_file or not _db_file.exists():
        return
    try:
        raw = sqlite3.connect(f"file:{_db_file.as_posix()}?mode=ro", uri=True,
                              timeout=5)
        ver = raw.execute("PRAGMA user_version").fetchone()[0]
        raw.close()
    except sqlite3.Error:
        return
    if not (0 < ver < SCHEMA_VERSION):
        return
    try:
        import datetime
        import zipfile
        backups = _db_file.parent / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = backups / f"gwtool_backup_{stamp}_premigrate_v{ver}.zip"
        snap = _db_file.with_name(_db_file.name + ".premigrate-snap")
        src = sqlite3.connect(f"file:{_db_file.as_posix()}?mode=ro", uri=True,
                              timeout=30)
        dst = sqlite3.connect(str(snap), timeout=30)
        try:
            src.backup(dst)          # 含 WAL 中已提交事务，产出单文件自洽快照
            dst.commit()
        finally:
            dst.close()
            src.close()
        try:
            with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(snap, "gwtool.db")
        finally:
            try:
                snap.unlink()
            except OSError:
                pass
    except (OSError, sqlite3.Error):
        pass


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
