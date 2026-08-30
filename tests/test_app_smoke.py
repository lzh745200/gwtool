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


def test_dict_manager_smoke(tmp_db, qapp):
    """词典管理对话框可构建（回归：忽略名单页 QListWidget 未导入的 NameError）。"""
    from gwtool import app
    app.ensure_database_seeded()
    from gwtool.ui.dict_manager import DictManager
    dlg = DictManager()
    assert dlg.tabs.count() == 4
    dlg.close()


def test_default_template_retried_after_partial_init(tmp_db, monkeypatch):
    """回归：迁移标记已落库但默认模板缺失时，首启逻辑仍应补建模板。

    旧实现把模板创建嵌在 generated_conf_v2 迁移守卫内：若该标志先提交、
    模板写入随后失败，模板将永远不会重建。
    """
    from gwtool import app
    from gwtool.db import dao
    from gwtool.db.connection import get_conn
    from gwtool.db.schema import init_schema

    init_schema(get_conn())
    get_conn().execute("INSERT OR REPLACE INTO settings(key,value) "
                       "VALUES('generated_conf_v2','1')")
    get_conn().commit()
    # 模拟无 seed.db 的最小库，避免整库种子导入
    monkeypatch.setattr("gwtool.app.bundled_db_seed_path",
                        lambda: tmp_db.parent / "missing_seed.db")
    app.ensure_database_seeded()
    assert dao.list_templates(), "默认模板应在首启补建"
    # 幂等：再次执行不重复建模板
    app.ensure_database_seeded()
    assert len(dao.list_templates()) == 1
