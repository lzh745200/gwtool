# -*- coding: utf-8 -*-
"""离屏冒烟测试：主窗口可构建、种子导入可执行。"""


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


def test_dict_manager_hides_legacy_curated_ruleset(tmp_db, qapp):
    """老用户库里残留的 `curated` 精标快照不得出现在规则集下拉里（v1.8）。

    内置精标表是唯一事实来源，且按设计不受规则集开关影响（`corrector._load_patterns`
    直接跳过库内 `source='curated'` 的行）。把这种"开关了也没效果"的分组列进
    下拉，只会误导用户去停用它 —— 所以 UI 层过滤掉；新装用户的种子库
    也不再带这类行（见 tests/test_seed_artifact.py）。
    """
    from gwtool import app
    app.ensure_database_seeded()
    from gwtool.db import dao
    from gwtool.db.connection import get_conn
    from gwtool.ui.dict_manager import DictManager

    # 模拟"老用户库"：里面还残留着快照行与真正的用户规则集
    dao.add_error_pair("甲甲", "甲", "精标", 0.95, source="curated")
    dao.add_error_pair("本单位写法", "规范写法", "内部规范", 0.9, source="本单位")
    assert "curated" in dict(dao.error_pair_sources()), "本用例前提：库里确有快照行"
    get_conn().commit()

    dlg = DictManager()
    try:
        items = [dlg.err_source.itemData(i) for i in range(dlg.err_source.count())]
    finally:
        dlg.close()
    assert "curated" not in items, f"遗留快照分组仍出现在下拉：{items}"
    assert "本单位" in items, f"真实规则集被误过滤：{items}"


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
