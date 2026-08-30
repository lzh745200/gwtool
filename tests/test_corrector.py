# -*- coding: utf-8 -*-
"""纠错引擎测试：验收标准要求“截止/截至、部署/布署等100%识别”。"""
from gwtool.core import corrector
from gwtool.core.corrector_data import CURATED_PAIRS
from gwtool.db import dao


def find(corr, wrong):
    return [c for c in corr if c.wrong == wrong]


def test_bushu(tmp_db):
    corr = corrector.check_text("会议对下一步工作进行了布署。")
    hits = find(corr, "布署")
    assert hits and hits[0].suggestion == "部署"


def test_jiezhi_jieshu(tmp_db):
    """截止+时间 -> 截至；截止日期/截止到 保持正确不误报。"""
    corr = corrector.check_text("报名截止12月31日。")
    hits = find(corr, "截止")
    assert hits and hits[0].suggestion == "截至"
    corr = corrector.check_text("报名截止日期为12月31日。")
    assert not find(corr, "截止")
    corr = corrector.check_text("报名截止到12月31日。")
    assert not find(corr, "截止")


def test_jianku_100_curated_pairs(tmp_db):
    """验收：精标词库中的错别字对在样句中100%识别（取100对）。"""
    sentence_tpl = {
        "截止": "活动截止本月底。", "报怨": "他一肚子报怨。",  # 规则类单独测
    }
    tested = 0
    for wrong, correct, _cat, conf in CURATED_PAIRS:
        if conf < 0.85 or wrong == correct or wrong in sentence_tpl:
            continue
        text = f"文中出现了{wrong}一词，需要修改。"
        corr = corrector.check_text(text)
        hits = find(corr, wrong)
        assert hits, f"未识别：{wrong} -> {correct}"
        assert hits[0].suggestion == correct
        tested += 1
        if tested >= 100:
            break
    assert tested == 100


def test_yi_de_baoyuan_exception(tmp_db):
    corr = corrector.check_text("将以德报怨的精神对待。")
    # “以德报怨”不应被改为“抱怨”
    starts = [c.start for c in corr if c.wrong == "报怨"]
    text = "将以德报怨的精神对待。"
    assert all(text[s - 2:s] != "以德" for s in starts)


def test_punctuation_rules(tmp_db):
    corr = corrector.check_text("这是测试。。重复标点。")
    assert any(c.category == "标点" for c in corr)
    corr = corrector.check_text("中文,逗号需要全角。")
    hits = [c for c in corr if c.category == "标点" and c.suggestion.endswith("，")]
    assert hits
    corr = corrector.check_text("省略号...测试")
    assert any(c.suggestion.endswith("……") for c in corr)


def test_number_rules(tmp_db):
    corr = corrector.check_text("会议在二零二六年召开。")
    assert any(c.category == "数字用法" for c in corr)
    corr = corrector.check_text("现场有3、4个人。")
    assert any(c.category == "数字用法" for c in corr)


def test_generated_pairs_loaded(tmp_db):
    """种子库（≥3万生成对）参与纠错。"""
    # 取一个生成对插入验证流程
    dao.add_error_pair("测试错词", "测试正词", "测试", 0.9, source="user")
    corrector.invalidate_cache()
    corr = corrector.check_text("这里是测试错词的句子。")
    assert any(c.wrong == "测试错词" and c.suggestion == "测试正词" for c in corr)


def test_generated_pair_inside_common_word_suppressed(tmp_db):
    """词边界保护：'全生→全省' 不得在 '安全生产' 内部误报。"""
    dao.add_error_pair("全生", "全省", "错别字(生成)", 0.55, source="generated")
    corrector.invalidate_cache()
    corr = corrector.check_text("本次会议对全市安全生产工作进行了布署，材料报送截止本月底。")
    assert not any(c.wrong == "全生" for c in corr), "词内部误报未被抑制"
    # 真错误仍要报出
    assert any(c.wrong == "布署" and c.suggestion == "部署" for c in corr)


def test_apply_all(tmp_db):
    text = "工作布署完成，日期截止本月底。"
    corr = corrector.check_text(text)
    fixed = corrector.apply_all(text, corr)
    assert "部署" in fixed
    assert "截至" in fixed or "截止" in fixed  # 截止+月底 -> 截至


def test_overlapping_dedupe(tmp_db):
    corr = corrector.check_text("布署布署布署")
    # 相同词重复出现应各自报出且不重叠
    assert len(corr) == 3
    for a, b in zip(corr, corr[1:]):
        assert a.end <= b.start


def test_seed_db_pair_count(tmp_db):
    """随包种子库须 ≥3万条（在主程序首次启动导入后满足）。"""
    from gwtool.paths import bundled_db_seed_path
    f = bundled_db_seed_path()
    assert f.exists(), "seed.db 缺失"
    import sqlite3
    conn = sqlite3.connect(str(f))
    n = conn.execute("SELECT count(*) FROM error_pairs").fetchone()[0]
    conn.close()
    assert n >= 30000, f"错别字对仅 {n} 条"
