# -*- coding: utf-8 -*-
"""离屏冒烟测试：主窗口可构建、种子导入可执行。"""
import os


def test_first_run_seeding(tmp_db, monkeypatch):
    """模拟首次启动：seed.db 存在时，词典/纠错对/默认模板导入。"""
    from gwtool import app, paths
    assert paths.bundled_db_seed_path().exists()
    app.ensure_database_seeded()
    from gwtool.db import dao
    from gwtool.db.connection import get_conn
    n_pairs = get_conn().execute("SELECT count(*) FROM error_pairs").fetchone()[0]
    n_dict = get_conn().execute("SELECT count(*) FROM dictionary").fetchone()[0]
    assert n_pairs >= 30000
    assert n_dict >= 50000
    assert dao.list_templates(), "默认模板应已写入"
    # 幂等：再次执行不会重复导入
    app.ensure_database_seeded()
    n_pairs2 = get_conn().execute("SELECT count(*) FROM error_pairs").fetchone()[0]
    assert n_pairs2 == n_pairs


def test_main_window_smoke(tmp_db, monkeypatch, qapp):
    """主窗口构建与销毁（离屏）。弹窗类提示在离屏测试中屏蔽。"""
    from gwtool import app
    app.ensure_database_seeded()
    import gwtool.ui.main_window as mw
    monkeypatch.setattr(mw, "missing_official_fonts", lambda: [])
    win = mw.MainWindow()
    assert win.windowTitle()
    win.close()
