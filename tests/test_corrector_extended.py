# -*- coding: utf-8 -*-
"""v1.5.1 扩充后的词库与规则层回归。

关注两类问题：
  1. 词库自身的**结构性缺陷**（自纠错、重复、自相矛盾）；
  2. 新增规则是否**方向正确**（该报的报、不该报的不报）。
"""
import collections
import re

import pytest

from gwtool.core import corrector
from gwtool.core.corrector_data import (CONTEXT_RULES, CURATED_PAIRS,
                                        NEGATIVE_CONTEXTS)


def _hits(text, wrong):
    return [c for c in corrector.check_text(text) if c.wrong == wrong]


def _sugg(text, wrong):
    hits = _hits(text, wrong)
    return hits[0].suggestion if hits else None


# ------------------------------------------------------------ 词库结构完整性
def test_no_self_pairs():
    """不得存在自纠错条目（错=对，会被 _is_valid_pair 静默丢弃）。"""
    bad = [(w, c) for w, c, _, _ in CURATED_PAIRS if w == c]
    assert not bad, f"自纠错死条目：{bad}"


def test_no_duplicate_keys():
    dup = [w for w, n in
           collections.Counter(w for w, _, _, _ in CURATED_PAIRS).items() if n > 1]
    assert not dup, f"重复词条：{dup}"


def test_no_conflicting_pairs():
    """错形不得同时是另一条的正确词，否则规则自相矛盾。"""
    corrects = {c for _, c, _, _ in CURATED_PAIRS}
    clash = sorted({w for w, _, _, _ in CURATED_PAIRS if w in corrects})
    assert not clash, f"自相矛盾词条：{clash}"


def test_pairs_are_chinese_words():
    for wrong, correct, _cat, _conf in CURATED_PAIRS:
        assert wrong and correct and wrong != correct
        assert re.search(r"[\u4e00-\u9fff]", wrong), f"非中文错形：{wrong}"
        assert not any(ch in wrong for ch in "\n\r\t "), f"含空白：{wrong}"


def test_wordbank_scale():
    """扩充后应显著大于原始 221 条。"""
    assert len(CURATED_PAIRS) >= 320


def test_rule_layer_scale():
    """上下文规则层曾是最大短板（原仅 3 条）。"""
    assert len(CONTEXT_RULES) >= 35
    assert len(NEGATIVE_CONTEXTS) >= 5


def test_all_rules_compile():
    for rx, _repl, _reason, _conf in CONTEXT_RULES:
        re.compile(rx)


def test_negative_contexts_keyed_by_real_pair():
    """负向上下文必须挂在真实存在的词条上，否则是永不命中的死规则。"""
    keys = {w for w, _, _, _ in CURATED_PAIRS}
    dead = [k for k in NEGATIVE_CONTEXTS if k not in keys]
    assert not dead, f"负向上下文挂在不存在词条上：{dead}"


# ------------------------------------------------------------ 新增规则方向性
def test_fayang_fahui_correct_usage_not_flagged(tmp_db):
    """正确搭配不得误报。"""
    assert corrector.check_text("我们要发扬优良传统，发挥积极作用。") == []


def test_fayang_fahui_directional(tmp_db):
    got = {(c.wrong, c.suggestion)
           for c in corrector.check_text("应当发扬作用，发挥传统。")}
    assert ("发扬", "发挥") in got
    assert ("发挥", "发扬") in got


def test_zhencha_vs_zhencha(tmp_db):
    """法律用“侦查”，军事用“侦察”。"""
    assert _sugg("公安机关开展侦察案件工作。", "侦察") == "侦查"
    assert _sugg("部队奉命侦查敌情。", "侦查") == "侦察"
    # 各自的正确用法不应被改动
    assert _hits("公安机关开展侦查工作。", "侦查") == []
    assert _hits("部队奉命侦察敌情。", "侦察") == []


def test_xingshi_quanli_rights_false_positive_fixed(tmp_db):
    """『依法行使权利』是规范表述，不得改『行使权力』。

    （原词库把『行使权利』一律提示为『行使权力』，是稳定的误报源；
      权利=rights，权力=power，两者并不等价。）
    """
    assert _hits("公民依法行使权利受法律保护。", "行使权利") == []
    assert _hits("这是合法行使权利的方式。", "行使权利") == []


def test_jiezhi_zaidao_kept_correct(tmp_db):
    """『截止到』是规范说法，不得改成『截至』（守住既有验收口径）。"""
    assert _hits("报名截止到12月31日。", "截止") == []


def test_new_pairs_detected(tmp_db):
    cases = {
        "共商国事": "共商国是",
        "不记其数": "不计其数",
        "以身作责": "以身作则",
        "严惩不待": "严惩不贷",
        "美仑美奂": "美轮美奂",
    }
    for wrong, good in cases.items():
        text = f"文中出现了{wrong}一词。"
        assert _sugg(text, wrong) == good, f"未识别：{wrong} -> {good}"


# ---------------------------------------------------- 结构性误报防线（v1.5.1）
def test_common_token_pair_false_positive_suppressed(tmp_db):
    """生成对里『错形=两个高频常用字相接』的条目必须被抑制。

    典型：这是/着个/没又/可已 —— 它们在正常行文里随处可见，
    若不抑制会在几乎每篇公文里弹出无意义的纠错建议。
    """
    from gwtool.db import dao
    for wrong, good in [("这是", "这时"), ("没又", "没有"), ("住要", "主要")]:
        dao.add_error_pair(wrong, good, "错别字(生成)", 0.55, source="generated")
    corrector.invalidate_cache()
    text = "这是必须解决的问题，会议认为没又必要再等，住要任务已经明确。"
    low = [c for c in corrector.check_text(text) if c.confidence < 0.6]
    assert not low, f"结构性误报未被抑制：{[(c.wrong, c.suggestion) for c in low]}"


def test_genuine_generated_typo_still_detected(tmp_db):
    """抑制误报不能把真错一起吃掉（'驶用'的'驶'是生僻字，必须照报）。"""
    from gwtool.db import dao
    dao.add_error_pair("驶用", "使用", "错别字(生成)", 0.55, source="generated")
    corrector.invalidate_cache()
    corr = corrector.check_text("文中出现了驶用一词，需要修改。")
    assert any(c.wrong == "驶用" for c in corr), "真错别字被误抑制"


@pytest.mark.parametrize("text", [
    "会议听取了汇报，反映情况，反应速度很快。",
    "必须坚持原则，这是生活必需品。",
    "他总是一贯作风，治学严谨。",
])
def test_new_rules_do_not_false_positive(tmp_db, text):
    assert corrector.check_text(text) == []
