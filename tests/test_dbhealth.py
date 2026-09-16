# -*- coding: utf-8 -*-
"""数据库健康自检与维护（D3）的测试。

核心约束是**正常时完全静默**：这条检查挂在启动路径上，只要它开始唠叨，
用户就会把整个功能当噪音。因此"健康库不返回任何问题"是重点用例。
"""
from __future__ import annotations

import pathlib
import sqlite3

import pytest

from gwtool.core import dbhealth
from gwtool.db import connection as dbconn
from gwtool.db import dao
from gwtool.db.schema import init_schema

ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestQuickCheck:
    def test_healthy_db_passes(self, tmp_db):
        ok, detail = dbhealth.quick_check()
        assert ok is True
        assert detail.lower() == "ok"

    def test_reports_failure_on_broken_connection(self, tmp_db, monkeypatch):
        """库读不了时必须如实报错，不能假装正常。"""
        def boom(*_a, **_k):
            raise sqlite3.DatabaseError("database disk image is malformed")
        monkeypatch.setattr(dbconn, "get_conn", boom)
        ok, detail = dbhealth.quick_check()
        assert ok is False
        assert "malformed" in detail

    def test_does_not_raise_on_unexpected_error(self, tmp_db, monkeypatch):
        def boom(*_a, **_k):
            raise RuntimeError("意料之外")
        monkeypatch.setattr(dbconn, "get_conn", boom)
        ok, _detail = dbhealth.quick_check()      # 不得抛
        assert ok is False


class TestSchedule:
    def test_first_run_should_check(self, tmp_db):
        assert dbhealth.should_check() is True

    def test_recently_checked_skips(self, tmp_db):
        dbhealth.record_check()
        assert dbhealth.should_check() is False

    def test_after_interval_should_check(self, tmp_db):
        dbhealth.record_check("2026-01-01")
        assert dbhealth.should_check("2026-09-01") is True

    def test_within_interval_skips(self, tmp_db):
        dbhealth.record_check("2026-08-20")
        assert dbhealth.should_check("2026-09-01") is False

    def test_corrupt_setting_triggers_check(self, tmp_db):
        dao.set_setting(dbhealth.SETTING_LAST_CHECK, "乱七八糟")
        assert dbhealth.should_check() is True

    def test_run_returns_none_when_healthy(self, tmp_db):
        """健康时**必须**返回 None —— 启动路径上不能有噪音。"""
        assert dbhealth.run_scheduled_check() is None

    def test_run_records_check_time(self, tmp_db):
        dbhealth.run_scheduled_check("2026-09-01")
        assert dbhealth.last_check_date() == "2026-09-01"

    def test_run_skips_when_not_due(self, tmp_db):
        dbhealth.record_check("2026-09-01")
        dao.set_setting(dbhealth.SETTING_LAST_CHECK, "2026-09-01")
        # 未到检查时间：即使把 quick_check 换成会炸的实现也不该被调用
        called = []
        original = dbhealth.quick_check

        def spy(*a, **k):
            called.append(1)
            return original(*a, **k)

        dbhealth.quick_check = spy
        try:
            assert dbhealth.run_scheduled_check("2026-09-02") is None
        finally:
            dbhealth.quick_check = original
        assert called == [], "未到检查时间却执行了自检"

    def test_run_reports_problem_when_broken(self, tmp_db, monkeypatch):
        monkeypatch.setattr(dbhealth, "quick_check",
                            lambda *a, **k: (False, "页校验失败"))
        problem = dbhealth.run_scheduled_check("2026-09-01")
        assert problem is not None
        assert "页校验失败" in problem
        assert "备份" in problem, "报错要给出可操作的建议"


class TestMaintenance:
    @pytest.fixture(autouse=True)
    def _ready(self, tmp_db):
        """tmp_db 只做重定向，不会真的建库；维护需要库文件真实存在，
        否则 `_all_file_size()` 为 0，磁盘空间预检会退化成"永远够用"。"""
        init_schema(dbconn.get_conn())

    def test_vacuum_succeeds(self, tmp_db):
        dao.add_document(dao.Document(title="维护测试", content_text="内容" * 50))
        rep = dbhealth.maintenance()
        assert rep["ok"] is True
        assert rep["after"] <= rep["before"] + 4096      # 不应变大
        assert rep["saved"] >= 0

    def test_fts_rebuilt(self, tmp_db):
        dao.add_document(dao.Document(title="检索测试", content_text="安全生产"))
        rep = dbhealth.maintenance(rebuild_fts=True)
        assert rep["ok"] is True
        assert rep["fts_rows"] >= 1

    def test_fts_skipped_when_disabled(self, tmp_db):
        rep = dbhealth.maintenance(rebuild_fts=False)
        assert rep["ok"] is True
        assert rep["fts_rows"] == -1

    def test_data_survives_maintenance(self, tmp_db):
        dao.add_document(dao.Document(title="不可丢失", content_text="内容"))
        dbhealth.maintenance()
        assert dao.count_documents() == 1

    def test_refuses_when_disk_space_insufficient(self, tmp_db, monkeypatch):
        """VACUUM 峰值需约 2 倍库体积；空间不够时应提前拒绝而非中途失败。"""
        monkeypatch.setattr(dbhealth, "free_space", lambda: 1)
        rep = dbhealth.maintenance()
        assert rep["ok"] is False
        assert "空间不足" in rep["reason"]

    def test_unknown_free_space_still_attempts(self, tmp_db, monkeypatch):
        """测不出空间时不拦——宁可尝试，也不要因测量失败而拒绝服务。"""
        monkeypatch.setattr(dbhealth, "free_space", lambda: -1)
        assert dbhealth.maintenance()["ok"] is True

    def test_fts_failure_does_not_fail_whole_run(self, tmp_db, monkeypatch):
        def boom():
            raise RuntimeError("FTS 炸了")
        monkeypatch.setattr(dao, "rebuild_fts", boom)
        rep = dbhealth.maintenance()
        assert rep["ok"] is True, "VACUUM 已成功，不该因 FTS 失败而整体判负"
        assert rep["fts_rows"] == -1


class TestSizeHelpers:
    def test_db_file_size_positive(self, tmp_db):
        dao.add_document(dao.Document(title="尺寸", content_text="x" * 100))
        assert dbhealth.db_file_size() > 0

    def test_human_size(self):
        assert dbhealth.human_size(0) == "0 B"
        assert dbhealth.human_size(1024) == "1.0 KB"
        assert dbhealth.human_size(1024 * 1024) == "1.0 MB"

    def test_human_size_tolerates_garbage(self):
        assert dbhealth.human_size(None) == "未知"


class TestWiring:
    def test_main_window_wires_health_check(self):
        src = (ROOT / "gwtool" / "ui" / "main_window.py").read_text(
            encoding="utf-8")
        assert "_maybe_db_health_check" in src
        assert "dbhealth.run_scheduled_check" in src

    def test_health_check_runs_in_background(self):
        """自检必须走后台线程：quick_check 扫全页，大库上会明显拖慢启动。"""
        src = (ROOT / "gwtool" / "ui" / "main_window.py").read_text(
            encoding="utf-8")
        idx = src.index("def _maybe_db_health_check")
        block = src[idx:idx + 900]
        assert "FnWorker" in block, "自检未放后台线程"
        assert "worker.start()" in block

    def test_maintenance_cursor_always_restored(self):
        """等待光标必须放在 try/finally 里复位，否则异常后界面永远转圈。"""
        src = (ROOT / "gwtool" / "ui" / "main_window.py").read_text(
            encoding="utf-8")
        idx = src.index("def open_db_maintenance")
        block = src[idx:idx + 1400]
        assert "setOverrideCursor" in block
        assert "finally:" in block and "restoreOverrideCursor" in block

    def test_maintenance_entry_in_menu(self):
        src = (ROOT / "gwtool" / "ui" / "main_window.py").read_text(
            encoding="utf-8")
        assert "数据库维护…" in src
