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
from .. import logs

_local = threading.local()
_db_file: Path | None = None

# ------------------------------------------------------------------ 连接登记表
# 记录"哪些线程当前持有打开的连接"。用途只有一个：**恢复备份**前判断库是不是
# 正被别的线程占用（Windows 上 os.replace 覆盖被打开的文件必失败）。
#
# 为什么不能只靠捕获 os.replace 的异常：那样报出来的只有一句 "拒绝访问 /
# 文件正被另一进程使用"，用户在界面上根本不知道是**哪个**后台任务在占用，
# 也就无法"等它结束后重试"。登记表让错误文案能点名具体任务。
#
# 键是线程 ident，值是线程对象（用 is_alive() 过滤已退出的线程，
# 避免线程被回收后登记项残留、把一次正常恢复误报成"被占用"）。
_CONN_LOCK = threading.Lock()
_OPEN_CONNS: dict[int, threading.Thread] = {}


def live_connection_threads() -> list[str]:
    """除当前线程外，仍持有打开连接的线程名（已退出的线程即时剔除）。

    返回的是线程名的有序去重列表，供恢复备份时生成可操作的错误提示。
    """
    cur = threading.get_ident()
    names: list[str] = []
    with _CONN_LOCK:
        for tid, th in list(_OPEN_CONNS.items()):
            if tid == cur:
                continue
            if th is None or not th.is_alive():
                _OPEN_CONNS.pop(tid, None)   # 线程已退出，登记项作废
                continue
            names.append(th.name)
    return sorted(set(names))


def _register_conn() -> None:
    with _CONN_LOCK:
        _OPEN_CONNS[threading.get_ident()] = threading.current_thread()


def _unregister_conn() -> None:
    with _CONN_LOCK:
        _OPEN_CONNS.pop(threading.get_ident(), None)


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
        try:
            init_schema(conn)
        except Exception:
            # 初始化失败（库版本过新 / 迁移失败）：连接必须在这里关掉 ——
            # 它既没登记进 _local，也不会被 close_current_thread 收走，
            # 每次重试都会泄漏一个句柄与一对 -wal/-shm 文件。
            try:
                conn.close()
            except sqlite3.Error:
                pass
            raise
        _local.conn = conn
        _register_conn()
    return conn


def _pre_migrate_backup() -> None:
    """打开库之前做一次自洽备份，作为安全网。

    两种场景共用：① 老库即将迁移（`*_premigrate_v{n}.zip`）；
    ② 库版本比程序新（`*_newer_v{n}.zip`）—— 程序会拒绝打开，但先留原件。

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
    if ver <= 0 or ver == SCHEMA_VERSION:
        return          # 新库（无版本）与当前版本都无需备份
    # ver < SCHEMA_VERSION：即将迁移，先留安全网。
    # ver > SCHEMA_VERSION（D5 新增）：库比程序**新**，本程序不会打开它，
    # 但更要留一份原件 —— 用户接下来无论换版本打开还是误操作，都有退路。
    newer = ver > SCHEMA_VERSION
    try:
        import datetime
        import zipfile
        backups = _db_file.parent / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        tag = f"newer_v{ver}" if newer else f"premigrate_v{ver}"
        dest = backups / f"gwtool_backup_{stamp}_{tag}.zip"
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
    except (OSError, sqlite3.Error) as exc:
        # 备份失败 = 本次升级/拒绝打开都失去安全网。绝不能静默：一旦迁移出问题，
        # 用户既没有回退点、也拿不到任何解释。必须留痕。
        logs.get_logger("db").warning(
            "打开库之前的自动备份失败（无安全网，库版本 v%d）：%s", ver, exc)


def close_current_thread() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
        _unregister_conn()


def current_db_file() -> Path:
    """当前配置的数据库文件路径（供备份/恢复使用，测试可指向临时文件）。"""
    if _db_file is None:
        from gwtool.paths import db_path
        configure(db_path())
    return _db_file
