# -*- coding: utf-8 -*-
"""纠错规则集：CSV 导入导出 + 按来源批量启停。

场景是「单位内部规范词库」：某科室整理一份 CSV 导入为独立来源，之后能整体
停用/启用、整体导出给别的电脑。此前只有导入没有导出，词库入库就取不出来，
也没有任何入口能改 enabled，规则集无法整体开关。
"""
import csv

import pytest

from gwtool.core import corrector, ruleset
from gwtool.db import dao


def _seed():
    dao.add_error_pair("布署", "部署", category="精标", source="builtin")
    dao.add_error_pair("截止任务", "截至任务", category="精标", source="builtin")
    dao.add_error_pair("制发文件", "印发文件", category="内部规范", source="本单位")


# ------------------------------------------------------------ 导出
def test_export_csv_is_utf8_bom_with_full_columns(tmp_db, tmp_path):
    _seed()
    out = tmp_path / "rules.csv"
    n = ruleset.export_error_pairs(str(out))
    assert n == 3

    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "缺 UTF-8 BOM，Excel 打开会乱码"

    with open(out, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == list(ruleset.HEADER)
    assert len(rows[0]) == 6
    body = {r[0]: r for r in rows[1:]}
    assert body["布署"][1] == "部署"
    assert body["布署"][3] == "builtin"
    assert body["制发文件"][3] == "本单位"


def test_export_filters_by_source(tmp_db, tmp_path):
    """只导出选中的规则集，便于把本单位词库单独分发给同事。"""
    _seed()
    out = tmp_path / "only.csv"
    n = ruleset.export_error_pairs(str(out), source="本单位")
    assert n == 1
    with open(out, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    assert [r[0] for r in rows[1:]] == ["制发文件"]


# ------------------------------------------------------------ 导入
def test_import_two_column_minimal_csv(tmp_db, tmp_path):
    p = tmp_path / "two.csv"
    p.write_text("以经,已经\n既使,即使\n", encoding="utf-8")
    res = ruleset.import_error_pairs(str(p), default_source="测试来源")
    assert res["imported"] == 2 and res["skipped"] == 0
    pairs = {q.wrong: q for q in dao.all_error_pairs(only_enabled=False)}
    assert pairs["以经"].correct == "已经"
    assert pairs["以经"].source == "测试来源"


def test_import_six_column_csv_skips_header(tmp_db, tmp_path):
    """带完整表头的导出文件再导回，不应把表头当成一条纠错对。"""
    p = tmp_path / "six.csv"
    p.write_text("错误写法,正确写法,类别,来源,置信度,启用\n"
                 "以经,已经,内部规范,本单位,0.95,1\n", encoding="utf-8-sig")
    res = ruleset.import_error_pairs(str(p))
    assert res["imported"] == 1
    pairs = {q.wrong: q for q in dao.all_error_pairs(only_enabled=False)}
    assert "错误写法" not in pairs, "表头被当成了数据行"
    assert pairs["以经"].category == "内部规范"
    assert pairs["以经"].source == "本单位"
    assert pairs["以经"].confidence == pytest.approx(0.95)


def test_import_gbk_csv_from_excel(tmp_db, tmp_path):
    """中文 Windows 上 Excel 另存的 CSV 通常是 GBK，必须能读。"""
    p = tmp_path / "gbk.csv"
    p.write_bytes("以经,已经\n既使,即使\n".encode("gbk"))
    res = ruleset.import_error_pairs(str(p))
    assert res["imported"] == 2
    pairs = {q.wrong: q for q in dao.all_error_pairs(only_enabled=False)}
    assert pairs["以经"].correct == "已经"


def test_import_skips_blank_and_malformed_rows(tmp_db, tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("以经,已经\n\n,只有错列没有对列\n单独一列\n既使,即使\n",
                 encoding="utf-8")
    res = ruleset.import_error_pairs(str(p))
    assert res["imported"] == 2, f"应只导入 2 条有效数据，实际 {res}"
    assert res["skipped"] >= 2


def test_import_is_idempotent(tmp_db, tmp_path):
    """同一份文件导两次不应产生重复条目（唯一索引 wrong+correct）。"""
    p = tmp_path / "dup.csv"
    p.write_text("以经,已经\n", encoding="utf-8")
    ruleset.import_error_pairs(str(p))
    before = dao.count_error_pairs_by(source="用户导入")
    ruleset.import_error_pairs(str(p))
    assert dao.count_error_pairs_by(source="用户导入") == before


def test_roundtrip_preserves_disabled_state(tmp_db, tmp_path):
    """导出再导回，停用状态必须保持——否则一导出就悄悄把停用的规则全启用了。"""
    _seed()
    dao.set_error_pairs_enabled(False, source="本单位")
    assert dao.count_error_pairs_by(source="本单位", enabled=False) == 1

    out = tmp_path / "rt.csv"
    ruleset.export_error_pairs(str(out), source="本单位")
    dao.delete_error_pair(next(p.id for p in dao.all_error_pairs(only_enabled=False)
                               if p.wrong == "制发文件"))
    assert dao.count_error_pairs_by(source="本单位") == 0

    ruleset.import_error_pairs(str(out))
    restored = next(p for p in dao.all_error_pairs(only_enabled=False)
                    if p.wrong == "制发文件")
    assert restored.enabled == 0, "导回后停用状态丢失"


# ------------------------------------------------------------ 批量启停
def test_toggle_by_source_only_affects_that_source(tmp_db):
    _seed()
    n = dao.set_error_pairs_enabled(False, source="本单位")
    assert n == 1
    assert dao.count_error_pairs_by(source="本单位", enabled=False) == 1
    assert dao.count_error_pairs_by(source="builtin", enabled=True) == 2, \
        "停用本单位规则集误伤了 builtin"

    dao.set_error_pairs_enabled(True, source="本单位")
    assert dao.count_error_pairs_by(enabled=False) == 0


def test_toggle_by_category(tmp_db):
    _seed()
    n = dao.set_error_pairs_enabled(False, category="内部规范")
    assert n == 1
    assert dao.count_error_pairs_by(category="精标", enabled=True) == 2


def test_disabled_pairs_do_not_participate_in_correction(tmp_db):
    """启停必须真的影响纠错结果，否则这个开关只是摆设。

    用只存在于数据库的词对验证：布署→部署 同时在 corrector_data.CURATED_PAIRS
    的硬编码层里，拿它测启停必然失败。
    """
    dao.add_error_pair("以经", "已经", category="内部规范", source="本单位")
    text = "本项工作以经完成。"
    assert any(c.wrong == "以经" for c in corrector.check_text(text))

    dao.set_error_pairs_enabled(False, source="本单位")
    corrector.invalidate_cache()
    assert not any(c.wrong == "以经" for c in corrector.check_text(text)), \
        "停用后仍在纠错"

    dao.set_error_pairs_enabled(True, source="本单位")
    corrector.invalidate_cache()
    assert any(c.wrong == "以经" for c in corrector.check_text(text)), \
        "重新启用后未恢复"


def test_curated_pairs_are_not_affected_by_ruleset_toggle(tmp_db):
    """已知局限：内置精标对写死在 corrector_data.CURATED_PAIRS，
    _load_patterns 先加载它再加载数据库，因此规则集开关关不掉它们。

    这条测试把该行为固定下来：既防止有人误以为停用「全部」就能关掉所有纠错，
    也防止将来改动 corrector 加载顺序时悄悄破坏"精标对 100% 识别"的验收项。
    """
    dao.set_error_pairs_enabled(False)          # 停用数据库里的全部纠错对
    corrector.invalidate_cache()
    found = {c.wrong for c in corrector.check_text("工作布署已完成，报名截止本月底。")}
    assert "布署" in found, "内置精标对应始终生效，不受数据库启停影响"


# ------------------------------------------------------------ 规则集清单
def test_error_pair_sources_lists_all_with_counts(tmp_db):
    _seed()
    sources = dict(dao.error_pair_sources())
    assert sources["builtin"] == 2
    assert sources["本单位"] == 1


def test_error_pair_categories_scoped_to_source(tmp_db):
    _seed()
    assert dict(dao.error_pair_categories("本单位")) == {"内部规范": 1}
    all_cats = dict(dao.error_pair_categories())
    assert all_cats["精标"] == 2 and all_cats["内部规范"] == 1


def test_blank_source_grouped_as_unlabelled(tmp_db):
    """source 为空的历史数据要归入「未标注」，否则下拉里看不到、也无法整体启停。"""
    from gwtool.db.connection import get_conn
    get_conn().execute(
        "INSERT INTO error_pairs(wrong,correct,source) VALUES('老数据','新数据','')")
    get_conn().commit()
    assert dict(dao.error_pair_sources())["未标注"] == 1
    assert dao.set_error_pairs_enabled(False, source="未标注") == 1
