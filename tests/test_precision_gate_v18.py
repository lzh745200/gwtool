# -*- coding: utf-8 -*-
"""v1.8 生成层误报闸门：错形本身是词典词 / 文档内重复出现 → 不报。

背景（这是本文件要锚定的缺陷）
----------------------------
生成层由 ``scripts/seed_data.py`` 用同音/近音字**程序化配对**，confidence 恒为
0.55，规模 4 万条。它的结构性弱点是：会把**合法的现代词**配成"错形" —

    ``显式 → 显示``、``一键 → 一件``、``重试 → 重视``、``一版 → 一般``

这类命中不是"写错了"，而是"同音异形真词"的歧义。在真实材料（含技术词、
单位内部提法）上，它们会把**正确的用词改成另一个词**——语义直接变掉。
实测仓库 doc/ 的 20 份真实中文长文：110 处低置信命中里绝大多数是这一类。

v1.8 加了两道**与分词无关**的结构性闸门（只对 ``confidence < 0.7`` 的生成层
命中生效，精标对/用户词条/规则层一律不受影响）：

1. 错形本身是 jieba 词典词 → 不报（同音异形歧义，无模型不可判定）；
2. 错形在同一文档内出现 ≥2 次 → 不报（作者的术语选择，不是笔误）。

本文件同时锚定**召回底线**：真错别字（生僻字构成的错形、只出现一次）必须照报，
用户手动加进词库的词条走 0.99 逃生通道，不吃这两道闸门。
"""
from __future__ import annotations

from unittest import mock

import pytest

from gwtool.core import corrector
from gwtool.db import dao


def _add(wrong: str, correct: str, conf: float = 0.55,
         source: str = "generated") -> None:
    """插入一条词库对并清缓存（与 test_corrector_extended 同一套路）。"""
    dao.add_error_pair(wrong, correct, "错别字(生成)", conf, source=source)
    corrector.invalidate_cache()


# --------------------------------------------------------------- 闸门 1：词典词
def test_dictionary_word_hit_suppressed(tmp_db):
    """'重试' 本身是词典词 → 不得被改成 '重视'。

    修复前实测：README/doc 里的"重试"被稳定报成"重视→重试"，属用户可见错误。
    """
    _add("重试", "重视")
    text = "系统会在超时后自动重试三次。"
    hits = [c for c in corrector.check_text(text) if c.wrong == "重试"]
    assert not hits, f"词典词被当成错形报出：{[(c.wrong, c.suggestion) for c in hits]}"


def test_dictionary_word_escape_hatch_for_user_pair(tmp_db):
    """用户手动添加的词条（置信度抬到 0.99）不受闸门影响 —— 逃生通道必须保留。

    这正是设计里"被误抑制的真错，用户加一次即永久生效"的机制。
    """
    _add("重试", "重启", source="user")
    text = "系统会在超时后自动重试三次。"
    assert any(c.wrong == "重试" and c.suggestion == "重启"
               for c in corrector.check_text(text)), "用户词条被误抑制"


# --------------------------------------------------------- 闸门 2：文档内重复
def test_doc_repeat_oov_word_suppressed(tmp_db):
    """词表外新词（'显式' 在 jieba 里不存在）→ 文档内重复出现即视为术语。"""
    _add("显式", "显示")
    text = "参数请显式传入，不要依赖隐式默认值；显式更符合可读性要求。"
    hits = [c for c in corrector.check_text(text) if c.wrong == "显式"]
    assert not hits, f"术语被当成错形报出：{[(c.wrong, c.suggestion) for c in hits]}"


def test_doc_repeat_needs_two_occurrences(tmp_db):
    """只出现一次时不走重复闸门 —— 门槛是 2，不是 1（避免把召回压光）。"""
    _add("显式", "显示")
    text = "参数请显式传入。"
    assert any(c.wrong == "显式" for c in corrector.check_text(text)), \
        "重复闸门被错当成『出现即抑制』"


# --------------------------------------------------------------- 召回底线
def test_genuine_oov_typo_still_reported(tmp_db):
    """生僻字构成的真错别字必须照报（'驶用' 的 '驶' 词频只有 422）。"""
    _add("驶用", "使用")
    hits = [c for c in corrector.check_text("文中出现了驶用一词，需要核对。")
            if c.wrong == "驶用"]
    assert hits, "真错别字被误抑制"


def test_curated_pair_unaffected(tmp_db):
    """精标对置信度 ≥0.7，完全不吃生成层闸门（布署→部署 必须照报）。"""
    hits = corrector.check_text("现将有关工作布署如下。")
    assert any(c.wrong == "布署" and c.suggestion == "部署" for c in hits)


# --------------------------------------------------------------- 降级路径
def test_structural_gates_survive_tokenize_failure():
    """分词不可用时两道新闸门仍然生效：降低误报的防线不该随分词一起降级。

    与 ``test_bugfix_regression.TestCorrectorTokenizeFallback`` 的分工：
    那两条管"分词不可用不能把命中全吞"，本条目管"分词不可用时**该吞的仍要吞**"。
    """
    from gwtool.db.tokenize import _ensure_jieba

    _ensure_jieba()          # jieba_freq 依赖已加载的词典
    raw = [(0, 2, "重试", "重视", "错别字(生成)", 0.55)]
    with mock.patch("jieba.tokenize", side_effect=RuntimeError("jieba 运行期错误")):
        out = corrector._suppress_inside_common_words("系统重试三次", raw)
    assert out == [], "分词失败时词典词闸门失效"


def test_structural_gates_do_not_touch_high_confidence():
    """高置信命中只走原来的词边界门控，不被两道新闸门拦截。"""
    from gwtool.db.tokenize import _ensure_jieba

    _ensure_jieba()
    # 错形 '重试' 是词典词，但置信度 0.99（用户/精标口径）必须保留
    raw = [(2, 4, "重试", "重启", "用户词库", 0.99)]
    out = corrector._suppress_inside_common_words("系统重试三次", raw)
    assert len(out) == 1, "高置信命中被低置信闸门误伤"

# ------------------------------------------------- 规则自身的精度修复（v1.8）
# 这两条不是"新能力"，而是把两条**既有**正则的误报修掉：它们都在正确的中文
# 上稳定命中，属"校对引擎自己制造噪声"。
def test_percent_rule_does_not_flag_correct_decimal():
    """正确写法「增长6.5%」不得被当成"小数点为全角"。

    修复前该规则正则写的是**半角点** ``(\\d+)\\.(\\d+)%``：于是每一个正确的
    百分数都会弹一条"请确认小数点为半角"——纯噪声；而真正要抓的全角写法
    （``25．5%``）反而不命中。
    """
    hits = [c for c in corrector.check_text("同比增长6.5%，占全年目标的12.25%。")
            if c.suggestion == "百分数"]
    assert not hits, f"正确的百分数被误报：{[(c.wrong, c.suggestion) for c in hits]}"


@pytest.mark.parametrize("text", ["完成率25．5%。", "增长25。5%。"])
def test_percent_rule_flags_fullwidth_decimal(text):
    """全角句点/逗号作小数分隔的百分数才是这条规则该抓的东西。"""
    assert [c for c in corrector.check_text(text) if c.suggestion == "百分数"], \
        "全角小数点未被报出"


def test_paren_rule_does_not_touch_code_snippets():
    """可自动替换（类别“标点”）的全角括号规则不得改动代码片段。

    否则「全部应用 / 批量写回」会把 ``(src[c.start:c.end] 说明)`` 这类可执行
    文本改成全角括号——把对的改错，比漏改严重。
    """
    text = "详见(src[c.start:c.end] 涉及数字位)，或看(1px `FOCUS_RING` 外圈)。"
    hits = [c for c in corrector.check_text(text) if c.category == "标点"]
    assert not hits, [(c.wrong, c.suggestion) for c in hits]


def test_paren_rule_still_converts_chinese_parens():
    """收紧代码判定之后，真实中文括号仍要能改（能力不能被一起收紧没了）。"""
    hits = [c for c in corrector.check_text("现将有关情况(附件1)报告如下。")
            if c.category == "标点"]
    assert any(c.suggestion == "（附件1）" for c in hits), hits

# ------------------------------------------- 闸门 3/4：词典词与"更长词典词覆盖"（v1.8 第二批）
def test_dictionary_word_veto(tmp_db):
    """错形在**随包词典/用户术语**里存在 → 不报（jieba 词表覆盖不到的合法词）。

    实测背景：jieba 词表是 1990 年代口径，`显式`、`一键` 这类当代词不在其中，
    但 CC-CEDICT 词典能兜住一部分（`一价`、`上疏`、`可恃` 等），
    词典全量探针里由此消掉约 1/3 的"合法词被当错形"。
    """
    from gwtool.db import dao

    dao.add_dictionary_entry("显式", source="user")
    _add("显式", "显示")
    assert not [c for c in corrector.check_text("请显式传入参数。") if c.wrong == "显式"]


def test_covered_by_longer_dictionary_word(tmp_db):
    """命中是更长词典词的一部分 → 不报（否则会把词拆坏）；独立出现时仍要报。"""
    from gwtool.db import dao

    dao.add_dictionary_entry("一矢中的", source="user")
    _add("一矢", "一时")
    assert not [c for c in corrector.check_text("他一矢中的。") if c.wrong == "一矢"], \
        "词典词内部的片段被当成错形"
    assert [c for c in corrector.check_text("一矢之地。") if c.wrong == "一矢"], \
        "独立出现的错形被误抑制"


def test_curated_snapshot_rows_in_db_are_ignored(tmp_db):
    """库里 source='curated' 的**构建期快照**不得覆盖/复活内置精标表。

    真实用户首启动即导入 seed.db，其中带着 v1.7.x 时代 220 条精标快照；
    若把这些当数据加载，内置表里删掉的错对会"复活"、降级的类别会被旧值盖回。
    """
    from gwtool.db import dao

    dao.add_error_pair("行使权利", "行使权力", "易混词", 0.8, source="curated")
    dao.add_error_pair("哑巴", "听障人士", "禁用词", 0.7, source="curated")
    corrector.invalidate_cache()
    patterns = corrector._load_patterns()
    assert "行使权利" not in patterns, "已被删除的精标对被库内快照复活"
    assert "哑巴" not in patterns, "已被删除的精标对被库内快照复活"
