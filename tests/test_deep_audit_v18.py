# -*- coding: utf-8 -*-
"""v1.8 深审（doc/深审报告_v1.8.md）P1/P2 修复的回归锚点。

这份文件覆盖的是**"会把正确正文改坏"**的那一类缺陷：每一条修复都必须有一个
能复现原缺陷的用例，否则下次重构就可能悄悄把闸门拆掉。用例按报告的编号组织：

  P1-1  机构沿革表里的自包含映射（`旅游局→文化和旅游局`）在**已正确的现行机构名**
        上再次命中，批量写回会得到「市文化和文化和旅游局」。
  P1-2  `行使权利→行使权力` 方向反了（权利=rights，权力=power）。
  P1-3  两字高置信精标对只要求"两端不同时在词内"，于是正常词的跨接被改
        （`按排放标准`→`安排放标准`）。
  P1-X  `_check_exact` 命中后整段跳过，起点更早的短命中会吞掉落在其内部的长命中
        （`了戒` 吞掉 `戒骄戒燥`）。
  P2-1..P2-14 逐条见各用例 docstring。
"""
from __future__ import annotations

import pytest

from gwtool.core import corrector, repeat_rules
from gwtool.core.corrector_data import CURATED_PAIRS


def _hits(text: str) -> list[tuple[str, str]]:
    return [(c.wrong, c.suggestion) for c in corrector.check_text(text)]


# ================================================================== P1-1
def test_org_rename_pairs_are_not_self_contained(tmp_db):
    """自包含映射必须在加载端被丢弃（否则现行机构名会被叠字改写）。"""
    patterns = corrector._load_patterns()
    bad = [(w, c) for w, (c, _cat, _conf) in patterns.items()
           if corrector._is_self_contained(w, c)]
    assert not bad, f"自包含映射仍在词表里：{bad[:5]}"


@pytest.mark.parametrize("text", [
    "市文化和旅游局负责本项工作。",
    "住房和城乡建设部印发了通知。",
    "住房和城乡建设局组织排查。",
])
def test_current_org_names_are_not_rewritten(tmp_db, text):
    """已经正确的现行机构名不得被机构沿革对照命中。"""
    assert not [h for h in _hits(text) if h[0] in
                ("旅游局", "建设部", "建设局")], _hits(text)


def test_historical_org_mention_is_not_rewritten(tmp_db):
    """历史引述里的旧机构名不改（`1953年，卫生部` / `原卫生部`）。

    机构沿革是"新稿误用旧名"的提示，不是历史改写器：把历史引述改成现名
    会把本来正确的引述改错（深审 P2-12）。
    """
    assert not [h for h in _hits("1953年，卫生部成立。") if h[0] == "卫生部"]
    assert not [h for h in _hits("原卫生部发布的文件仍然有效。") if h[0] == "卫生部"]


# ================================================================== P1-2
def test_shixhi_quanli_pair_removed(tmp_db):
    """`行使权利→行使权力` 已删除：公民/股东行使权利是规范表述。

    反向（该用"权力"的地方写成"权利"，如"权利机构"）由上下文规则覆盖，
    方向唯一，见 test_quanli_jigou_rule。
    """
    assert ("行使权利", "行使权力") not in {(w, c) for w, c, _cat, _conf
                                            in CURATED_PAIRS}
    assert not [h for h in _hits("股东依法行使权利，公司不得限制。") if "权利" in h[0]]


def test_quanli_jigou_rule(tmp_db):
    """「权利机构/权利机关」应改成「权力」（机关掌握的是权力）。

    注：`国家权利→国家权力` 本身是精标对，会以更长命中先胜出（同一语义），
    所以断言"建议文本以权力结尾"而不是钉死命中的字面长度。
    """
    hits = _hits("这是国家权利机关。")
    assert any(s.endswith("权力") for _w, s in hits), hits


# ================================================================== P1-3
@pytest.mark.parametrize("text", [
    "按排放标准执行。",
    "按装机容量核定。",
    "虽然烧了，但没有大碍。",
    "防害虫要用对方法。",
    "这是电影象。",
])
def test_two_char_pairs_need_word_boundaries(tmp_db, text):
    """两字精标对必须两端都在分词边界上（正常词跨接不得被报出）。"""
    assert _hits(text) == [], _hits(text)


def test_genuine_typo_still_reported_after_boundary_tightening(tmp_db):
    """收紧两字边界后，真错别字照报（不能把能力一起收紧没了）。"""
    assert ("布署", "部署") in _hits("现将有关工作布署如下。")
    assert ("反应情况", "反映情况") in _hits("群众反应情况属实。")


# ================================================================== P1-X 扫描器
def test_long_curated_pair_not_shadowed_by_earlier_short_hit(tmp_db):
    """起点更早的短命中不得吞掉落在它内部的长精标对。

    原实现命中后 `i += len(hit)`：生成对 `了戒→了解` 从下标 4 命中就把指针
    推到 6，4 字精标对 `戒骄戒燥`（0.9）根本不会被扫描到 —— 高置信长对被
    低置信短对**在扫描阶段**吃掉（`_dedupe` 根本没机会仲裁）。
    """
    pairs = _hits("文中出现了戒骄戒燥，需要修改。")
    assert ("戒骄戒燥", "戒骄戒躁") in pairs, pairs
    assert ("了戒", "了解") not in pairs, pairs


# ================================================================== P2-1
def test_cheng_yu_yide_baoyuan_not_corrected(tmp_db):
    """「以直报怨」是成语，不得改成「以直抱怨」。"""
    assert not [h for h in _hits("以直报怨，以德报德。") if h[0] == "报怨"]


# ================================================================== P2-2 / P2-3 / P2-4
def test_cuangai_only_in_clear_contexts(tmp_db):
    """`窜改`（改动原文）本身合法：只在数据/账目/历史这类明确语境才改 `篡改`。"""
    assert ("窜改", "篡改") in _hits("有人窜改数据。")
    assert not [h for h in _hits("请勿窜改文件格式说明。") if h[0] == "窜改"]


def test_zhencha_personnel_is_legal(tmp_db):
    """`侦察人员` 是军事语境合法写法，不得改成「侦查人员」。"""
    assert not [h for h in _hits("侦察人员已就位。") if h[0] == "侦察"]
    assert ("侦察", "侦查") in _hits("侦察案件正在办理。")


def test_yanjin_is_legal_word(tmp_db):
    """`严紧` 是《现代汉语词典》收录的规范词，不再被改成「严谨」。"""
    assert not [h for h in _hits("他对工作要求严紧。") if h[0] == "严紧"]


# ================================================================== P2-6
def test_yaba_not_replaced_with_wrong_terminology(tmp_db):
    """`哑巴→听障人士` 语义错配（哑=言语障碍，≠听力障碍），已移除。"""
    assert ("哑巴", "听障人士") not in {(w, c) for w, c, _cat, _conf in CURATED_PAIRS}


# ================================================================== P2-7
@pytest.mark.parametrize("text", [
    "档案保管期限30年。",
    "合同有效期10年。",
    "近20年来，我县变化很大。",
    "未来10年发展规划。",
])
def test_duration_not_treated_as_short_year(tmp_db, text):
    """时长（保管期限/有效期/近N年）不是两位年份简写。"""
    assert not [h for h in _hits(text) if h[1] == "年份简写"], _hits(text)


def test_short_year_still_flagged(tmp_db):
    """真正的年份简写仍然要提示（能力不能被收紧没了）。"""
    assert any(h[1] == "年份简写" for h in _hits("项目于26年启动。"))


# ================================================================== P2-8
def test_single_hyphen_between_chinese_not_dash(tmp_db):
    """`见附件-说明` 这类清单写法不得被改成破折号（会把清单改坏）。"""
    assert not [h for h in _hits("详见附件-说明。") if h[1] == "——"]


# ================================================================== P2-9
def test_correct_percent_not_flagged(tmp_db):
    """正确的小数百分数（25.5%）不再提示「百分数」。"""
    assert not [h for h in _hits("同比增长25.5%。") if h[1] == "百分数"]


# ================================================================== P2-10
@pytest.mark.parametrize("text,want", [
    ("会议做出了决定。", ("做出", "作出")),
    ("这是做出的部署。", ("做出", "作出")),
    ("给与大力支持。", ("给与", "给予")),
    ("发扬积极作用。", ("发扬", "发挥")),
    ("渡过了艰难时期。", ("渡过", "度过")),
    ("签定了合同。", ("签定", "签订")),
    ("记念活动如期举行。", ("记念", "纪念")),
    ("发挥了优良传统。", ("发挥", "发扬")),
    ("屡行职责不到位。", ("屡行", "履行")),
    ("一惯的作风要保持。", ("一惯", "一贯")),
    ("倍受关注。", ("倍受", "备受")),
])
def test_context_rules_tolerate_le_and_modifiers(tmp_db, text, want):
    """`了/的/固定修饰语` 不能挡住固定宾语规则（审计实测这些高频错写全漏）。"""
    assert want in _hits(text), _hits(text)


# ================================================================== P2-13
@pytest.mark.parametrize("text", [
    "达到预期的目的。",
    "这是一个很好的目的。",
])
def test_de_particle_not_interval_repeat(tmp_db, text):
    """`的X的` 不是间隔重复（参考文献面板默认阈值 0.5 会把它写回正文）。"""
    assert not [h for h in
                ((c.wrong, c.suggestion) for c in repeat_rules.check_repeat(text))
                if h[0].startswith("的")], text


# ================================================================== P2-14
@pytest.mark.parametrize("wrong,category", [
    ("图象", "规范用字"),
    ("狼籍", "规范用字"),
    ("妥贴", "规范用字"),
    ("丰采", "规范用字"),
])
def test_variant_words_are_style_not_typo(tmp_db, wrong, category):
    """异形词/旧字形是「规范用字」建议，不再当"错别字"且置信度压到 0.7。"""
    hit = corrector._load_patterns()[wrong]
    assert hit[1] == category, hit
    assert hit[2] <= 0.7, hit
