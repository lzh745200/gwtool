# -*- coding: utf-8 -*-
"""办理时限与督办提醒。

为什么不做后台常驻定时器
------------------------
单机桌面工具**不应该**常驻轮询：
  · 常驻定时器会让系统难以进入低功耗状态（笔记本续航直接受影响）；
  · 公文办理以"天"为粒度，分钟级刷新毫无意义；
  · 单机程序没有多用户并发，不存在"别人刚改了我却看不到"的问题。
因此本模块只在**启动时算一次**、以及**用户手动刷新时**算一次，结果交给
状态栏常显角标；点开才拉清单。这是"够用且不打扰"的力度。

只提醒"填了应办结日期且尚未办结"的收文
--------------------------------------
没有期限的记录不该被算法替用户判定为"该催办了"——那只会制造噪音，
最后用户会把整个提醒关掉，等于没做。同理，默认只提醒**已逾期**与
**2 日内到期**两类，阈值可在设置里调。
"""
from __future__ import annotations

from .. import logs
from ..db import dao

log = logs.get_logger("reminder")

DEFAULT_DUE_SOON_DAYS = 2
SETTING_ENABLED = "reminder_enabled"
SETTING_DUE_SOON = "reminder_due_soon_days"


def enabled() -> bool:
    """提醒开关。默认开启（用户可在设置里关掉）。"""
    try:
        return dao.get_setting(SETTING_ENABLED) != "0"
    except Exception:
        return False


def set_enabled(flag: bool) -> None:
    try:
        dao.set_setting(SETTING_ENABLED, "1" if flag else "0")
    except Exception as exc:
        # 与 L4/L5 开关同型：写不进去 = 重启后回到旧值，用户会认为"设了不生效"
        log.warning("督办提醒开关未能持久化（%s），重启后将回到原值", exc)


def due_soon_days() -> int:
    """"即将到期"的提前量（天）。取值异常时回落到默认值。"""
    try:
        value = int(dao.get_setting(SETTING_DUE_SOON) or DEFAULT_DUE_SOON_DAYS)
    except (TypeError, ValueError):
        return DEFAULT_DUE_SOON_DAYS
    return max(0, min(30, value))


def set_due_soon_days(days: int) -> None:
    try:
        dao.set_setting(SETTING_DUE_SOON, str(max(0, min(30, int(days)))))
    except (TypeError, ValueError):
        pass


def pending(today: str = "") -> list:
    """需要关注的收文（未办结且已到期或将到期）。"""
    if not enabled():
        return []
    try:
        return dao.pending_receive(due_within_days=due_soon_days(), today=today)
    except Exception:
        # 提醒功能坏掉绝不能影响主流程——静默降级为"无提醒"
        return []


def split(items: list, today: str = "") -> tuple[list, list]:
    """把待关注清单拆成 (已逾期, 即将到期)，各自按应办结日期升序。"""
    from ..core import receive

    overdue, soon = [], []
    for r in items:
        left = receive.days_left(r, today=today)
        if left is None:
            continue
        (overdue if left < 0 else soon).append(r)
    return overdue, soon


def status_text(items: list, today: str = "") -> str:
    """状态栏文案；无需提醒时返回空串。"""
    if not items:
        return ""
    overdue, soon = split(items, today=today)
    parts = []
    if overdue:
        parts.append(f"已逾期 {len(overdue)} 件")
    if soon:
        parts.append(f"{due_soon_days()} 日内到期 {len(soon)} 件")
    return " · ".join(parts)


def detail_lines(items: list, today: str = "") -> list[str]:
    """清单文案：每条一行，标出剩余天数。"""
    from ..core import receive

    lines = []
    for r in items:
        left = receive.days_left(r, today=today) or 0
        when = f"已逾期 {abs(left)} 天" if left < 0 else f"剩 {left} 天"
        title = (r.title or r.incoming_no or "（无标题）")[:28]
        org = r.from_org or "未知来文机关"
        lines.append(f"[{when}] {title} —— {org}（应办结 {r.due_date}）")
    return lines
