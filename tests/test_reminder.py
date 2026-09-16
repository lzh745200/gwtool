# -*- coding: utf-8 -*-
"""办理时限与督办提醒（A2）的测试。

两个重点：
  · **默认必须克制** —— 只提醒"填了应办结日期且尚未办结"的记录。
    一旦无期限的收文也被催办，用户会把整个提醒关掉，等于没做；
  · **失败必须无害** —— 提醒功能坏掉不能影响主流程（它挂在启动路径上）。
"""
from __future__ import annotations

import pathlib

from gwtool.core import receive, reminder
from gwtool.db import dao

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _rec(**kw) -> dao.Receive:
    base = dict(reg_no="收〔2026〕1号", incoming_no="×政发〔2026〕12号",
                title="关于做好安全生产工作的通知", doc_type="通知",
                from_org="××市人民政府办公室", receive_date="2026-09-01",
                due_date="2026-09-05", status="承办", handler_dept="安全科")
    base.update(kw)
    return dao.Receive(**base)


class TestSettings:
    def test_enabled_by_default(self, tmp_db):
        assert reminder.enabled() is True

    def test_can_be_disabled(self, tmp_db):
        reminder.set_enabled(False)
        assert reminder.enabled() is False
        reminder.set_enabled(True)
        assert reminder.enabled() is True

    def test_default_due_soon_days(self, tmp_db):
        assert reminder.due_soon_days() == reminder.DEFAULT_DUE_SOON_DAYS

    def test_due_soon_days_roundtrip(self, tmp_db):
        reminder.set_due_soon_days(5)
        assert reminder.due_soon_days() == 5

    def test_due_soon_days_clamped(self, tmp_db):
        """异常配置不能算出离谱的窗口（比如 10000 天，等于全部催办）。"""
        reminder.set_due_soon_days(10000)
        assert reminder.due_soon_days() == 30
        reminder.set_due_soon_days(-5)
        assert reminder.due_soon_days() == 0

    def test_bad_setting_falls_back(self, tmp_db):
        dao.set_setting(reminder.SETTING_DUE_SOON, "abc")
        assert reminder.due_soon_days() == reminder.DEFAULT_DUE_SOON_DAYS


class TestPending:
    def test_disabled_returns_empty(self, tmp_db):
        dao.add_receive(_rec(due_date="2020-01-01"))
        reminder.set_enabled(False)
        assert reminder.pending(today="2026-09-01") == []

    def test_includes_overdue_and_due_soon(self, tmp_db):
        dao.add_receive(_rec(due_date="2026-08-01", title="已逾期件"))
        dao.add_receive(_rec(due_date="2026-09-02", title="将到期件"))
        got = reminder.pending(today="2026-09-01")
        assert len(got) == 2

    def test_excludes_far_future(self, tmp_db):
        dao.add_receive(_rec(due_date="2026-12-31", title="还早"))
        assert reminder.pending(today="2026-09-01") == []

    def test_excludes_closed(self, tmp_db):
        dao.add_receive(_rec(due_date="2026-08-01", status="已办结",
                             done_date="2026-08-02"))
        assert reminder.pending(today="2026-09-01") == []

    def test_excludes_records_without_due_date(self, tmp_db):
        """没有期限的记录不该被算法替用户判定为"该催办了"。"""
        dao.add_receive(_rec(due_date="", title="无期限"))
        assert reminder.pending(today="2026-09-01") == []

    def test_sorted_by_due_date(self, tmp_db):
        dao.add_receive(_rec(due_date="2026-09-10", title="较晚"))
        dao.add_receive(_rec(due_date="2026-08-01", title="最早"))
        got = reminder.pending(today="2026-09-01")
        assert [r.title for r in got][0] == "最早"

    def test_survives_db_error(self, tmp_db, monkeypatch):
        """提醒坏掉绝不能影响主流程。"""
        def boom(*_a, **_k):
            raise RuntimeError("模拟数据库故障")
        monkeypatch.setattr(dao, "pending_receive", boom)
        assert reminder.pending(today="2026-09-01") == []


class TestSplitAndText:
    def test_split(self):
        items = [_rec(due_date="2026-08-01"), _rec(due_date="2026-09-02")]
        overdue, soon = reminder.split(items, today="2026-09-01")
        assert len(overdue) == 1 and len(soon) == 1

    def test_empty_text(self):
        assert reminder.status_text([]) == ""

    def test_overdue_only(self):
        text = reminder.status_text([_rec(due_date="2026-08-01")],
                                    today="2026-09-01")
        assert "已逾期 1 件" in text

    def test_due_soon_only(self):
        text = reminder.status_text([_rec(due_date="2026-09-02")],
                                    today="2026-09-01")
        assert "到期 1 件" in text

    def test_both(self):
        items = [_rec(due_date="2026-08-01"), _rec(due_date="2026-09-02")]
        text = reminder.status_text(items, today="2026-09-01")
        assert "已逾期 1 件" in text and "到期 1 件" in text

    def test_detail_lines_include_days_and_org(self):
        lines = reminder.detail_lines([_rec(due_date="2026-08-30")],
                                      today="2026-09-01")
        assert len(lines) == 1
        assert "已逾期 2 天" in lines[0]
        assert "××市人民政府办公室" in lines[0]

    def test_detail_lines_tolerates_missing_title(self):
        lines = reminder.detail_lines(
            [_rec(title="", incoming_no="", from_org="")], today="2026-09-01")
        assert "（无标题）" in lines[0] and "未知来文机关" in lines[0]


class TestNoBackgroundTimer:
    def test_reminder_module_has_no_timer_or_thread(self):
        """设计约束：不做后台常驻轮询（伤续航，且对"天"级业务无意义）。"""
        src = (ROOT / "gwtool" / "core" / "reminder.py").read_text(
            encoding="utf-8")
        for bad in ("QTimer", "threading", "while True", "time.sleep"):
            assert bad not in src, f"reminder 不应引入 {bad}"


class TestWiring:
    def test_main_window_refreshes_on_startup(self):
        src = (ROOT / "gwtool" / "ui" / "main_window.py").read_text(
            encoding="utf-8")
        assert "self.refresh_reminders()" in src
        assert "reminder.pending()" in src

    def test_status_bar_badge_exists(self):
        src = (ROOT / "gwtool" / "ui" / "main_window.py").read_text(
            encoding="utf-8")
        assert "_reminder_btn" in src
        assert "addPermanentWidget(self._reminder_btn)" in src

    def test_badge_hidden_when_nothing_to_do(self):
        src = (ROOT / "gwtool" / "ui" / "main_window.py").read_text(
            encoding="utf-8")
        assert "self._reminder_btn.hide()" in src, (
            "无待办时必须隐藏角标，否则状态栏永远挂着一个空按钮")

    def test_receive_dialog_refreshes_badge_on_return(self):
        """从收文台账办结若干件回来后，角标必须跟着更新。"""
        src = (ROOT / "gwtool" / "ui" / "main_window.py").read_text(
            encoding="utf-8")
        assert "self.refresh_reminders()      # 从台账回来可能已办结若干件" in src


class TestIntegrationWithReceive:
    def test_integration_end_to_end(self, tmp_db):
        """登记 -> 承办 -> 到期 -> 出现在督办清单 -> 办结 -> 消失。"""
        rid = dao.add_receive(_rec(due_date="2026-09-03", status="承办"))
        assert len(reminder.pending(today="2026-09-01")) == 1

        r = dao.get_receive(rid)
        r.status = "已办结"
        r.done_date = "2026-09-02"
        dao.update_receive(r)
        assert reminder.pending(today="2026-09-01") == []

    def test_overdue_flag_matches_receive_module(self, tmp_db):
        r = _rec(due_date="2026-08-01")
        assert receive.is_overdue(r, today="2026-09-01")
        overdue, _soon = reminder.split([r], today="2026-09-01")
        assert len(overdue) == 1
