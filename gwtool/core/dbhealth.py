# -*- coding: utf-8 -*-
"""数据库健康自检与压缩维护。

为什么需要它
------------
单机 SQLite 长期使用（大量增删文档、反复重建 FTS 索引）会产生碎片；异常断电
可能损坏数据库文件。而**用户不会知道**，直到某天程序打不开 —— 此时自动备份
的轮转（保留 20 份）很可能已经把损坏**之前**的好数据挤掉了。

本模块把这件事提前：
  · `quick_check()` 成本远低于 `integrity_check`，可在启动时低频跑一次，
    **正常时完全静默**，只有异常才提示——不打扰是它能长期留在启动路径上的前提；
  · `maintenance()` 提供手动的 VACUUM + REINDEX + FTS 重建，回收磁盘、
    消除"越用越慢"。

为什么 VACUUM 前要预检磁盘空间
------------------------------
VACUUM 的工作方式是**把整库重写到一份临时副本再替换**，因此峰值需要约
2 倍库体积的空闲空间。空间不足时 SQLite 会中途失败并留下半成品风险，
不如提前算出来、给出明确提示。
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import date, timedelta

from ..db import connection as dbconn
from .. import logs

SETTING_LAST_CHECK = "db_last_quick_check"
# 自检间隔：30 天。公文工具的数据变化是"月"级的，
# 更频繁没有收益，只会让每个用户每天启动时多付一次磁盘扫描。
CHECK_INTERVAL_DAYS = 30
# VACUUM 峰值空间倍数（临时副本 + 原库）
_VACUUM_SPACE_FACTOR = 2.2


def db_file_size() -> int:
    """数据库主文件字节数；不可用时返回 0。"""
    try:
        path = dbconn.current_db_file()
        return int(path.stat().st_size) if path.exists() else 0
    except Exception:
        return 0


def _all_file_size() -> int:
    """库文件 + WAL + SHM 的总占用（VACUUM 后的真实回收量以此为准）。"""
    try:
        path = dbconn.current_db_file()
    except Exception:
        return 0
    total = 0
    for suffix in ("", "-wal", "-shm"):
        p = path.with_name(path.name + suffix) if suffix else path
        try:
            if p.exists():
                total += int(p.stat().st_size)
        except OSError:
            pass
    return total


def free_space() -> int:
    """数据库所在磁盘的剩余字节数；测不出时返回 -1（表示"未知"而非"没空间"）。"""
    try:
        path = dbconn.current_db_file()
        return int(shutil.disk_usage(str(path.parent)).free)
    except Exception:
        return -1


def quick_check() -> "tuple[bool, str]":
    """快速完整性自检，返回 (是否正常, 详情)。

    `quick_check` 不验证索引与表数据的一致性细节，但能发现页级损坏，
    成本比 `integrity_check` 低一个量级——适合放在启动路径上。
    """
    try:
        conn = dbconn.get_conn()
        row = conn.execute("PRAGMA quick_check").fetchone()
    except sqlite3.DatabaseError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)
    result = str(row[0]) if row else ""
    return result.lower() == "ok", result


def last_check_date() -> str:
    from ..db import dao
    try:
        return str(dao.get_setting(SETTING_LAST_CHECK) or "")
    except Exception:
        return ""


def record_check(today: str = "") -> None:
    from ..db import dao
    try:
        dao.set_setting(SETTING_LAST_CHECK, today or date.today().isoformat())
    except Exception as exc:
        # 记录失败会让"下次是否该自检"的判断失真（可能每次启动都重跑），
        # 属可忍受的降级，但要有痕迹可查。
        logs.get_logger("dbhealth").warning("自检日期未能记录：%s", exc)


def should_check(today: str = "") -> bool:
    """距上次自检是否已超过间隔（从未检过 -> True）。"""
    last = last_check_date()
    base = today or date.today().isoformat()
    if not last:
        return True
    try:
        return date.fromisoformat(base) - date.fromisoformat(last) >= timedelta(
            days=CHECK_INTERVAL_DAYS)
    except ValueError:
        return True


def run_scheduled_check(today: str = "") -> "str | None":
    """启动时调用的低频自检。

    返回**需要告知用户的问题描述**；一切正常（或还没到检查时间）时返回 None
    ——"正常时完全静默"是这条检查能长期留在启动路径上的前提。
    """
    if not should_check(today):
        return None
    ok, detail = quick_check()
    record_check(today)
    if ok:
        return None
    return (f"数据库自检发现问题：{detail}\n\n"
            f"建议立即在「工具 → 数据库维护」中备份当前数据，"
            f"并从最近的备份恢复。数据目录：{_data_dir_text()}")


def _data_dir_text() -> str:
    try:
        return str(dbconn.current_db_file().parent)
    except Exception:
        return "（未知）"


def maintenance(rebuild_fts: bool = True) -> dict:
    """执行 VACUUM + REINDEX（+ 重建 FTS 索引），返回前后对比与结论。

    返回字典字段：
      ok       —— 是否成功执行
      reason   —— 失败原因（ok 为 False 时）
      before / after / saved —— 文件占用（字节）
      fts_rows —— 重建的 FTS 行数（未重建时为 -1）
    """
    before = _all_file_size()
    free = free_space()
    need = int(before * _VACUUM_SPACE_FACTOR)
    # free < 0 表示测不出空间，此时不拦（宁可尝试也不要因测量失败而拒绝服务）
    if free >= 0 and free < need:
        return {"ok": False, "reason":
                f"磁盘剩余空间不足：VACUUM 需要约 "
                f"{need / 1024 / 1024:.0f} MB（库体积的 "
                f"{_VACUUM_SPACE_FACTOR:.1f} 倍），当前仅剩 "
                f"{free / 1024 / 1024:.0f} MB", "before": before}

    conn = dbconn.get_conn()
    try:
        # VACUUM 不能在事务中执行，先确保没有未提交事务
        conn.commit()
        conn.execute("VACUUM")
        conn.commit()
        conn.execute("REINDEX")
        conn.commit()
    except sqlite3.DatabaseError as exc:
        return {"ok": False, "reason": f"维护失败：{exc}", "before": before}

    fts_rows = -1
    if rebuild_fts:
        try:
            from ..db import dao
            counts = dao.rebuild_fts()
            # rebuild_fts 返回 {源表: 条数}（如 {"documents": 12, "phrases": 3}），
            # 汇总成总条数——曾因误当作 int 而 int(dict) 抛错、又被宽 except 吞掉，
            # 表现为"维护成功但检索索引 0 条"，极难察觉。
            fts_rows = (int(sum(int(v) for v in counts.values()))
                        if isinstance(counts, dict) else int(counts))
        except Exception as exc:
            # FTS 重建失败不影响 VACUUM 的成果，但必须留痕：
            # 静默失败意味着"用户以为索引重建了、其实没有"
            logs.get_logger("dbhealth").warning("重建全文检索索引失败：%s", exc)
            fts_rows = -1

    # 统计前把 WAL 归零。
    # 不这么做的话"回收量"会失真——WAL 模式下 VACUUM 与重建索引的写入都先落
    # 在 -wal 里，实测出现过"VACUUM 后占用反而涨了 320 KB"的假象，
    # 用户会以为维护把数据搞大了。checkpoint(TRUNCATE) 把 -wal 截回 0 字节，
    # 这样 before/after 比较的才是真实占用。
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    except sqlite3.DatabaseError:
        # 有其他连接占用时 checkpoint 会 busy，此时只能接受近似值
        pass

    after = _all_file_size()
    return {"ok": True, "before": before, "after": after,
            "saved": max(0, before - after), "fts_rows": fts_rows}


def human_size(size: int) -> str:
    """字节 -> 人类可读（维护结果提示用）。"""
    try:
        value = float(size)
    except (TypeError, ValueError):
        return "未知"
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"
