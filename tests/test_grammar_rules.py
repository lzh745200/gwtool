# -*- coding: utf-8 -*-
"""语法与表达规则层（L2.5）的契约测试。

守四件事（对应四类可能引入的副作用）：

1. **没有死规则** —— 每条规则表里的规则都必须在某个正例上命中。
   词典法最怕"凑数规则"：永远不命中却进了表，让人以为覆盖了某类错误
   （`corrector_data.NEGATIVE_CONTEXTS` 的注释里已有同样的告诫）。
2. **正常公文体零误报** —— 新增的规则跑在词表之外，长句上是误报的高发区。
   这里用一批人工确认无错的公文句做门禁（与 `scripts/eval_corrector.py` 的
   E3 同一口径，但样本更广）。
3. **提示类类别绝不进自动写回** —— `suggestion` 是标签（"删其一"）而不是替换
   文本，一旦被批量纠错或"整篇应用"写回，正文就被改成了标签本身。
4. **规则层异常必须咽掉** —— 本层不能把纠错主流程拖垮。
"""
import re

import pytest

from gwtool.core import batch, corrector
from gwtool.core import grammar_rules as G

# ---------------------------------------------------------------- 正例表
# (样本文本, 期望类别, 期望 wrong, 期望 suggestion)
POSITIVE = [
    # 语义冗余（整段替换）
    ("会议于截至到本月底召开。", "语义冗余", "截至到", "截至"),
    ("各单位要涉及到安全生产的问题。", "语义冗余", "涉及到", "涉及"),
    ("该事件波及到周边三个县区。", "语义冗余", "波及到", "波及"),
    ("不能诉诸于暴力手段。", "语义冗余", "诉诸于", "诉诸"),
    ("这一新规见诸于报端。", "语义冗余", "见诸于", "见诸"),
    ("把想法付诸于行动。", "语义冗余", "付诸于", "付诸"),
    ("数据来自于基层统计。", "语义冗余", "来自于", "来自"),
    ("本次活动免费赠送纪念品。", "语义冗余", "免费赠送", "赠送"),
    ("奥运健儿凯旋归来。", "语义冗余", "凯旋归来", "凯旋"),
    ("我们亲眼目睹了这一刻。", "语义冗余", "亲眼目睹", "目睹"),
    ("他的目的是为了推动工作。", "语义冗余", "目的是为了", "目的是"),
    ("这是唯一一个可行的方案。", "语义冗余", "唯一一个", "唯一"),
    ("这一做法属首次首创。", "语义冗余", "首次首创", "首创"),
    ("双方达成一致共识。", "语义冗余", "一致共识", "共识"),
    ("两队的实力悬殊很大。", "语义冗余", "悬殊很大", "悬殊"),
    ("会议决定重新再次审议。", "语义冗余", "重新再次", "再次"),
    ("不得提前预支下月经费。", "语义冗余", "提前预支", "预支"),
    ("要求学生提前预习课文。", "语义冗余", "提前预习", "预习"),
    ("切忌不要违规操作。", "语义冗余", "切忌不要", "切忌"),
    ("各科室各自分别报送材料。", "语义冗余", "各自分别", "分别"),
    ("双方互相厮打起来。", "语义冗余", "互相厮打", "厮打"),
    ("该项目开始启动前期工作。", "语义冗余", "开始启动", "启动"),
    ("家长过分溺爱孩子。", "语义冗余", "过分溺爱", "溺爱"),
    ("他仔细端详着照片。", "语义冗余", "仔细端详", "端详"),
    ("这项技术已广泛普及。", "语义冗余", "广泛普及", "普及"),
    ("他十分酷爱书法。", "语义冗余", "十分酷爱", "酷爱"),
    ("他非常酷爱书法。", "语义冗余", "非常酷爱", "酷爱"),
    ("形势更加日趋严峻。", "语义冗余", "更加日趋", "日趋"),
    ("双方共同协商解决。", "语义冗余", "共同协商", "协商"),
    # 搭配不当
    ("各单位要加强力度抓落实。", "搭配", "加强力度", "加大力度"),
    ("要着力提高力度推进工作。", "搭配", "提高力度", "加大力度"),
    ("要加大意识抓好安全生产。", "搭配", "加大意识", "增强意识"),
    ("他担任责任，做了大量工作。", "搭配", "担任责任", "承担责任"),
    ("要认真执行职责。", "搭配", "执行职责", "履行职责"),
    ("着力加大水平推进改革。", "搭配", "加大水平", "提高水平"),
    ("提高步伐推进项目建设。", "搭配", "提高步伐", "加快步伐"),
    # 关联词呼应
    ("我们不但要抓好落实，但是要抓好监督。", "关联词", "但是", "而且"),
    ("不仅要有部署，可是要有检查。", "关联词", "可是", "而且"),
    ("只有深入调研，就能找到办法。", "关联词", "就", "才"),
    ("只要努力工作，才能取得成功。", "关联词", "才", "就"),
    # 提示类（suggestion 是标签）
    ("该项目大约需要三到五天左右完成。", "表达提示", None, None),
    ("工期约30天左右。", "表达提示", None, None),
    ("参会人数超过500人以上。", "表达提示", None, None),
    ('会议提出"提质增效"的要求。', "标点规范", None, None),
    ("会议提出“提质增效\"的要求。", "标点规范", None, None),
]


@pytest.mark.parametrize("text,category,wrong,suggestion", POSITIVE)
def test_positive_hits(tmp_db, text, category, wrong, suggestion):
    hits = corrector.check_text(text)
    matched = [h for h in hits if h.category == category]
    assert matched, f"未命中 {category}：{text} → {[h.category for h in hits]}"
    if wrong is not None:
        assert any(h.wrong == wrong and h.suggestion == suggestion
                   for h in matched), \
            f"{text} 期望 {wrong}→{suggestion}，实得 {[(h.wrong, h.suggestion) for h in matched]}"


def test_no_dead_rules(tmp_db):
    """每条规则都必须在正例上命中过（防"凑数规则"）。"""
    samples = [t for t, *_ in POSITIVE]
    tables = [
        ("语义冗余", [rx for rx, _r, _s, c in G.REDUNDANCY_RULES if c > 0]),
        ("搭配", [rx for rx, _r, _s, c in G.COLLOCATION_RULES if c > 0]),
        ("表达提示/标点规范", [rx for rx, _l, _c, _s, c in G.ADVISORY_RULES if c > 0]),
    ]
    for label, patterns in tables:
        dead = [rx for rx in patterns
                if not any(re.search(rx, s) for s in samples)]
        assert not dead, f"{label} 存在永不命中的死规则：{dead}"
    # 关联词规则是复合式（左项 …… 逗号 紧接 错配右项），单独校验
    dead_cj = []
    for left, wrong, _good, _reason, conf in G.CONJUNCTION_RULES:
        if conf <= 0:
            continue
        rx = re.compile(rf"(?:{left})[^，。；！？\n]{{0,40}}?[，,]\s*({wrong})")
        if not any(rx.search(s) for s in samples):
            dead_cj.append((left, wrong))
    assert not dead_cj, f"关联词存在永不命中的死规则：{dead_cj}"


# ---------------------------------------------------------------- 负样本
# 人工确认无错的公文句（含长句、并列结构、规范搭配、成对标点），
# 覆盖新增规则全部模式词面，确保"正常写法不误报"。
NEGATIVE = [
    "各部门要高度重视此项工作，严格落实责任制，确保各项任务按期完成。",
    "为进一步规范公文处理流程，现将有关事项通知如下，请遵照执行。",
    "各地区、各部门要结合实际，认真抓好贯彻落实，并及时报告有关情况。",
    "会议听取了关于上半年工作情况的汇报，研究部署了下半年重点任务。",
    "要坚持问题导向，深入基层开展调查研究，切实解决群众反映的突出问题。",
    "各单位要加强协调配合，形成工作合力，共同推动各项措施落地见效。",
    "经研究决定，同意你单位请示事项，请按有关规定办理相关手续。",
    "此项工作纳入年度考核，请各单位务必于本月底前完成材料报送。",
    "要进一步做好防汛抗旱工作，确保人民群众生命财产安全。",
    "他不仅工作认真，而且乐于助人，深受同事好评。",
    "即使遇到再大的困难，我们也要坚持到底。",
    "只有加强学习，才能不断提高业务能力。",
    "只要大家齐心协力，就一定能够完成任务。",
    "会议审议通过了《关于进一步加强安全生产工作的意见》。",
    "该方案自2026年10月1日起施行，有效期五年。",
    "截至2026年6月底，全市完成投资约120亿元。",
    "2026年，我市预计实现地区生产总值3000亿元。",
    "文件精神（见附件）要求各单位抓好落实。",
    "请各部门提高认识，增强责任感，加大工作力度。",
    "调研报告提出了三点建议，具有很强的针对性和可操作性。",
    "会议指出，要坚持稳中求进，统筹发展和安全。",
    "各部门要履行好职责，把各项工作抓实抓好。",
    "要加强督促检查，确保责任落实到位。",
    "他说：“这是一份重要文件。”",
    "该项工作进展有序，取得了阶段性成效。",
    "涉及多个部门的，由牵头单位负责协调。",
    "本通知自印发之日起执行。",
]


@pytest.mark.parametrize("text", NEGATIVE)
def test_negative_no_false_positive(tmp_db, text):
    hits = corrector.check_text(text)
    assert hits == [], f"正常句被误报：{text} → {[(h.category, h.wrong, h.suggestion) for h in hits]}"


def test_conjunction_not_across_sentence(tmp_db):
    """「只有」与「就」分属两个命题时不报 —— 这正是句内 + 逗号紧接门控的意义。"""
    hits = [h for h in corrector.check_text("只有一天时间，我们就要完成。")
            if h.category == "关联词"]
    assert hits == []


def test_conjunction_requires_comma_boundary(tmp_db):
    """「因为他来了，所以我就走了」这类正常句不报（「就」不在逗号紧接位置）。"""
    hits = [h for h in corrector.check_text("因为他来了，所以我就走了。")
            if h.category == "关联词"]
    assert hits == []


# ---------------------------------------------------------------- 成对标点
def test_punct_pair_unmatched_left(tmp_db):
    hits = [h for h in corrector.check_text("他说：“好的。") if h.category == "标点规范"]
    assert len(hits) == 1 and hits[0].wrong == "“"


def test_punct_pair_unmatched_right(tmp_db):
    hits = [h for h in corrector.check_text("这是好的”。") if h.category == "标点规范"]
    assert len(hits) == 1 and hits[0].wrong == "”"


def test_punct_pair_matched_ok(tmp_db):
    assert [h for h in corrector.check_text("他说：“好的。”") if h.category == "标点规范"] == []


def test_punct_pair_book_title(tmp_db):
    hits = [h for h in corrector.check_text("见《办法有关规定。") if h.category == "标点规范"]
    assert len(hits) == 1 and hits[0].wrong == "《"


# ---------------------------------------------------------------- 提示类别
def test_advisory_categories_single_source(tmp_db):
    """提示类清单只有一处定义（corrector），batch 与 UI 都从它取。"""
    assert batch.ADVISORY_CATEGORIES == corrector.ADVISORY_CATEGORIES
    declared = {c for _rx, _l, c, _s, conf in G.ADVISORY_RULES if conf > 0}
    assert declared <= set(corrector.ADVISORY_CATEGORIES)
    for cat in declared:
        assert cat in corrector.ADVISORY_CATEGORIES


def test_correct_block_skips_advisory_categories(tmp_db):
    """整篇应用必须跳过提示类：suggestion 是标签，写回会把正文改成标签。"""
    text = "该项目大约需要五天左右完成。"
    fixed, cs = corrector.correct_block(text)
    assert fixed == text, "提示类命中被写回了正文"
    assert all(c.category not in corrector.ADVISORY_CATEGORIES for c in cs)


def test_correct_block_applies_safe_rules(tmp_db):
    """可替换的规则（语义冗余/搭配/关联词）仍应正常写回。"""
    fixed, cs = corrector.correct_block("各单位要加强力度抓落实。")
    assert "加大力度" in fixed and "加强力度" not in fixed
    assert any(c.category == "搭配" for c in cs)


def test_apply_all_handles_advisory_label_safely(tmp_db):
    """apply_all 若被显式传入提示类命中，会把标签写进正文 —— 故调用方必须过滤；
    本用例固化"过滤前"的行为，提醒后人不要绕过 ADVISORY_CATEGORIES。"""
    text = "该项目大约需要五天左右完成。"
    adv = [c for c in corrector.check_text(text)
           if c.category in corrector.ADVISORY_CATEGORIES]
    assert adv, "样例应产生提示类命中"
    # 过滤后应用：正文不变
    safe = [c for c in corrector.check_text(text)
            if c.category not in corrector.ADVISORY_CATEGORIES]
    assert corrector.apply_all(text, safe) == text


# ---------------------------------------------------------------- 去重修正
def test_dedupe_prefers_lexicon_long_pair(tmp_db):
    """D3 修正：精标长对不被高置信短规则截胡（E1 由 99.4% → 100% 的原因）。"""
    hits = corrector.check_text("反应情况")
    assert hits, "应有命中"
    assert hits[0].wrong == "反应情况" and hits[0].suggestion == "反映情况"


def test_dedupe_still_keeps_rule_when_no_lexicon_parent(tmp_db):
    """没有 L1 长对包含时，规则命中照常保留（单向消解，不倒过来误伤）。"""
    hits = corrector.check_text("截止目前，工作进展顺利。")
    assert any(h.category == "易混词" and h.suggestion == "截至" for h in hits)


def test_dedupe_no_overlap(tmp_db):
    hits = corrector.check_text("各单位要加强力度，涉及到安全生产的问题。")
    for a, b in zip(hits, hits[1:]):
        assert a.end <= b.start, "命中区间重叠"


# ---------------------------------------------------------------- 异常安全
def test_grammar_layer_exception_is_swallowed(tmp_db, monkeypatch):
    """规则层抛错只丢本层，前几层的命中照常返回。"""
    def boom(_text):
        raise RuntimeError("规则数据损坏")
    monkeypatch.setattr(G, "all_hits", boom)
    hits = corrector.check_text("工作布署已完成。")
    assert any(h.wrong == "布署" for h in hits), "L1 命中不应受 L2.5 异常影响"


def test_grammar_hits_are_plain_tuples(tmp_db):
    """本层产出是纯元组：不依赖 corrector，便于单测与复用。"""
    for h in G.all_hits("各单位要涉及到安全生产工作。"):
        assert isinstance(h, tuple) and len(h) == 7
        start, end, wrong, sug, cat, reason, conf = h
        assert isinstance(start, int) and isinstance(end, int) and end > start
        assert wrong and cat and reason and 0 < conf <= 1
