# -*- coding: utf-8 -*-
"""写作辅助引擎增强检查（v1.7.4）的回归测试。

覆盖十项只读检查的正/反例、规范范文不新增 warn 级误报、空/超长文本健壮性，
以及公文句式库扩充后的分组映射完整性。

与 `test_writing_hints.py` 的分工：那份守既有公开契约（Hint/Idea/Outline、
永不写正文、空数据不抛异常）；这份守新增检查的行为边界 —— 一律 info、
逐类兜底、阈值取自 ``writing_data``、且不为"改稿腔"制造假警告。
"""
from gwtool.core import writing_data as W
from gwtool.core import writing_hints as WH


def _titles(text: str, kind: str = "") -> str:
    return " ".join(h.title for h in WH.hints(text, kind))


def _lines(text: str, kind: str = "") -> str:
    return "\n".join(h.line for h in WH.hints(text, kind))


#: 规范范文：要素齐全、层次清楚、语气克制、无口语与重复用语。
REGULATED = (
    "××市人民政府关于进一步加强安全生产工作的通知\n"
    "各区县人民政府，市政府各部门：\n"
    "为深入贯彻落实上级关于安全生产工作的部署要求，切实防范化解重大安全风险，"
    "现将有关事项通知如下。\n"
    "一、压实安全生产责任。各区县人民政府要落实属地责任，明确牵头单位和配合单位，"
    "于2026年11月底前完成责任清单备案。\n"
    "二、强化重点领域监管。市应急管理局要组织开展专项检查，"
    "对发现的问题实行台账管理、限期整改。\n"
    "三、严格督导考核。市政府办公室要定期调度工作进展，"
    "对工作不力的单位进行通报。\n"
    "特此通知。\n"
    "××市人民政府\n"
    "2026年10月21日\n"
)

#: 两个段落各超过 400 字（用于"逐段检查"正例）
_LONG_P1 = "各单位要高度重视安全生产工作，切实落实各项措施，确保任务按期完成。" * 13
_LONG_P2 = ("市应急管理局要组织开展专项检查，对发现的问题实行台账管理，"
            "限期整改到位并报送整改情况。") * 11


# ---------------------------------------------------------------- 检查 1 长句
def test_long_sentence_reports_index_and_length():
    sent = "为深入贯彻落实上级关于进一步加强安全生产工作的部署要求，" * 5
    assert len(sent) >= W.SENT_TOO_LONG
    hs = WH.hints("关于安全生产工作的通知\n" + sent + "。", "")
    hit = [h for h in hs if "句偏长" in h.title]
    assert hit, [h.line for h in hs]
    assert str(len(sent)) in hit[0].title
    assert hit[0].scope == "文风" and hit[0].level == "info"


def test_short_sentences_no_long_sentence_hint():
    text = "各单位要高度重视。要明确责任分工。特此通知。"
    assert "偏长" not in _titles(text)


# ---------------------------------------------------------------- 检查 2 段落过长
def test_long_paragraphs_are_checked_one_by_one():
    text = "关于安全生产工作的通知\n" + _LONG_P1 + "\n" + _LONG_P2
    assert len(_LONG_P1) >= W.PARA_TOO_LONG and len(_LONG_P2) >= W.PARA_TOO_LONG
    titles = _titles(text)
    assert "段过长" in titles, titles
    assert "第 2 段" in titles and "第 3 段" in titles, titles
    assert all(h.level == "info" for h in WH.hints(text) if "段过长" in h.title)


def test_single_long_paragraph_keeps_legacy_hint_without_duplicate():
    text = "各单位要高度重视安全生产工作，切实落实各项措施，确保任务按期完成。" * 13
    titles = _titles(text)
    assert "单段过长" in titles
    assert "第 1 段过长" not in titles, "全文仅一段时既有提示已覆盖，不应重复报"


def test_normal_paragraphs_no_long_paragraph_hint():
    p1 = "各单位要高度重视安全生产工作，切实落实各项措施。" * 5
    p2 = "要明确责任分工，抓好督促检查，确保各项任务落地见效。" * 5
    assert len(p1) < W.PARA_TOO_LONG and len(p2) < W.PARA_TOO_LONG
    assert "段过长" not in _titles("关于工作的通知\n" + p1 + "\n" + p2)


# ---------------------------------------------------------------- 检查 3 句式疲劳
def test_repeated_sentence_opening_is_reported():
    text = ("关于加强安全生产工作的通知\n"
            "一、加强组织领导。要加强统筹协调。要明确责任分工。要严格督促检查。")
    hs = WH.hints(text)
    hit = [h for h in hs if "连续" in h.title and "开头" in h.title]
    assert hit, [h.title for h in hs]
    assert "要" in hit[0].title and hit[0].level == "info"


def test_varied_sentence_openings_not_reported():
    text = ("关于加强安全生产工作的通知\n"
            "要提高思想认识，压实工作责任。各单位要结合实际制定实施方案。"
            "市应急管理局负责督促检查，确保任务落实。")
    assert "连续" not in _titles(text)


# ---------------------------------------------------------------- 检查 4 层次序号
def test_layer_numbering_jump_reported():
    titles = _titles("关于工作的通知\n一、总体要求\n三、主要任务")
    assert "层次序号不规范：跳号" in titles
    assert "二、" in _lines("关于工作的通知\n一、总体要求\n三、主要任务")


def test_layer_numbering_duplicate_reported():
    titles = _titles("关于工作的通知\n一、总体要求\n二、主要任务\n二、工作要求")
    assert "层次序号不规范：重号" in titles


def test_layer_numbering_reversed_hierarchy_reported():
    titles = _titles("关于工作的通知\n（一）总体要求\n一、主要任务")
    assert "层次序号层级颠倒" in titles


def test_yishi_sequence_jump_reported():
    titles = _titles("关于工作的通知\n一、工作要求\n"
                     "一是加强领导，二是明确分工，四是严格考核。")
    assert "层次序号不规范：跳号" in titles


def test_layer_numbering_restart_not_reported():
    text = ("关于工作的通知\n"
            "一、总体要求\n"
            "（一）指导思想\n"
            "（二）基本原则\n"
            "二、主要任务\n"
            "（一）加强领导\n"
            "1. 制定方案\n"
            "2. 组织实施\n"
            "三、保障措施")
    assert "层次序号" not in _titles(text)


def test_yishi_restart_in_next_paragraph_not_reported():
    text = "关于工作的通知\n一是加强领导，二是明确分工。\n一是压实责任，二是强化监管。"
    assert "层次序号" not in _titles(text)


# ---------------------------------------------------------------- 检查 5 标点密度
def test_excessive_exclamation_reported():
    hs = WH.hints("各单位要高度重视！要狠抓落实！要务求实效！")
    assert any("感叹号" in h.title for h in hs)
    assert all(h.level == "info" for h in hs if "感叹号" in h.title)


def test_excessive_question_reported():
    hs = WH.hints("为什么会出现这种情况？原因是什么？下一步怎么办？")
    assert any("问号" in h.title for h in hs)


def test_excessive_ellipsis_reported():
    hs = WH.hints("一是……。二是……。三是……。")
    assert any("省略号" in h.title for h in hs)


def test_normal_punctuation_not_reported():
    titles = _titles(REGULATED)
    assert "感叹号" not in titles and "问号" not in titles and "省略号" not in titles


# ---------------------------------------------------------------- 检查 6 口语化
def test_colloquial_words_reported_with_advice():
    hs = WH.hints("这项工作要抓紧搞定，有啥问题及时反映，咱们一起想办法。")
    hits = [h for h in hs if "口语化" in h.title]
    assert hits, [h.title for h in hs]
    assert all(h.scope == "用语" and h.level == "info" for h in hits)
    assert any("搞定" in h.title for h in hits)
    assert any("完成" in h.detail for h in hits), "应带改写建议"


def test_formal_wording_not_reported_as_colloquial():
    titles = _titles("各单位要抓紧完成整改任务，及时报送整改台账。")
    assert "口语化" not in titles


def test_fixed_expression_allowlist_not_reported():
    titles = _titles("坚决整治弄虚作假行为，防止数据造假，严禁搞活过程中层层加码。")
    assert "口语化" not in titles


# ---------------------------------------------------------------- 检查 7 模糊表述
def test_vague_words_reported():
    hs = WH.hints("请有关部门进一步核实，适当调整，及时报送。")
    hits = [h for h in hs if "用语偏空泛" in h.title]
    assert hits and all(h.level == "info" for h in hits)
    assert any("有关" in h.title for h in hits)


def test_style_data_bad_words_still_reported():
    hs = WH.hints("我觉得这项工作很重要，请相关部门尽快抓好落实。", "")
    titles = " ".join(h.title for h in hs)
    assert "我觉得" in titles and "相关部门" in titles and "尽快" in titles
    assert all(h.level == "info" for h in hs if "用语偏空泛" in h.title)


def test_concrete_wording_not_reported_as_vague():
    text = "请市应急管理局于2026年10月30日前完成整改并报送台账。"
    assert "用语偏空泛" not in _titles(text)


# ---------------------------------------------------------------- 检查 8 重复短语
def test_repeated_phrase_in_paragraph_reported():
    hs = WH.hints("安全生产是底线。抓好安全生产是关键。安全生产责任重于泰山。")
    hit = [h for h in hs if "重复用语" in h.title]
    assert hit, [h.title for h in hs]
    assert "安全生产" in hit[0].title
    assert hit[0].scope == "用语" and hit[0].level == "info"


def test_repeat_across_paragraphs_not_reported():
    text = "安全生产是底线。\n安全生产是关键。"
    assert "重复用语" not in _titles(text)


def test_latin_identifiers_not_reported_as_repeat():
    text = ("gwtool 与 test 目录的说明：gwtool 是程序包，test 是测试目录，"
            "gwtool 与 test 各自独立。")
    assert "重复用语" not in _titles(text)


# ---------------------------------------------------------------- 检查 9 自称一致
def test_self_reference_mix_reported():
    hs = WH.hints("我单位高度重视。本单位已成立工作专班。")
    hit = [h for h in hs if "自称不统一" in h.title]
    assert hit, [h.title for h in hs]
    assert "我单位" in hit[0].title and hit[0].scope == "一致性"
    assert hit[0].level == "info"


def test_consistent_self_reference_not_reported():
    text = "我单位高度重视，我单位主要负责同志牵头，我单位已成立工作专班。"
    assert "自称不统一" not in _titles(text)


def test_different_self_reference_suffixes_not_conflict():
    # 「我市」「我局」是不同对象（城市与单位），并存属正常，不应判混用
    text = "我市高度重视安全生产工作，我局已成立工作专班开展专项检查。"
    assert "自称不统一" not in _titles(text)


# ---------------------------------------------------------------- 检查 10 标题长度
def test_long_title_reported():
    title = ("关于进一步加强全市安全生产隐患排查整治坚决防范遏制重特大事故"
             "切实保障人民群众生命财产安全工作的紧急通知")
    assert len(title) > W.TITLE_TOO_LONG
    hs = WH.hints(title + "\n" + "正文。", "")
    hit = [h for h in hs if "标题偏长" in h.title]
    assert hit, [h.title for h in hs]
    assert str(len(title)) in hit[0].title and hit[0].level == "info"


def test_normal_title_not_reported():
    assert "标题偏长" not in _titles("关于进一步加强安全生产工作的通知\n正文。")


def test_long_first_line_ending_with_period_is_not_title():
    first = ("为深入贯彻落实上级关于进一步加强安全生产工作的部署要求，"
             "切实防范化解重大安全风险，现就有关事项通知如下。")
    assert len(first) > W.TITLE_TOO_LONG
    assert "标题偏长" not in _titles(first + "\n正文。")


# ---------------------------------------------------------------- 规范范文回归
def test_regulated_draft_has_no_warn_level_hint():
    hs = WH.hints(REGULATED, "通知")
    warns = [h.line for h in hs if h.level == "warn"]
    assert hs and not warns, f"规范范文不应新增 warn 级误报：{warns}"


def test_new_checks_are_all_info_level():
    messy = (
        "关于安全生产工作的通知\n"
        "努力搞好这项工作，进一步落实有关要求！\n"
        "一、总体要求\n三、主要任务\n"
        "我单位与本单位要抓紧落实。" + "\n"
        + "安全生产是底线。抓好安全生产是关键。安全生产责任重于泰山。"
    )
    hs = WH.hints(messy, "")
    new_titles = ("段过长", "偏长", "连续", "层次序号", "感叹号", "问号",
                  "省略号", "口语化", "用语偏空泛", "重复用语", "自称不统一",
                  "标题偏长")
    for h in hs:
        if any(t in h.title for t in new_titles):
            assert h.level == "info", h.line


# ---------------------------------------------------------------- 健壮性
def test_empty_and_whitespace_text_safe():
    assert len(WH.hints("", "通知")) == 1
    assert len(WH.hints("   \n\n  ", "")) == 1


def test_none_text_safe():
    assert isinstance(WH.hints(None), list)  # type: ignore[arg-type]


def test_huge_text_finishes_and_does_not_flood():
    huge = "各单位要抓好落实。" * 3000
    hs = WH.hints(huge, "通知")
    assert isinstance(hs, list)
    assert len(hs) <= 40, f"超长文本提示不应刷屏：{len(hs)} 条"


def test_one_broken_check_does_not_break_others(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("检查失败")

    monkeypatch.setattr(WH, "_layer_hints", boom)
    monkeypatch.setattr(WH, "_punct_hints", boom)
    monkeypatch.setattr(WH, "_repeat_hints", boom)
    hs = WH.hints("我觉得这项工作很重要，请相关部门尽快抓好落实。", "")
    assert isinstance(hs, list)
    assert any("我觉得" in h.title for h in hs), "单类检查异常不得连累其他类"


# ---------------------------------------------------------------- 句式库扩充
_NEW_GROUPS = (
    "请示（请求事项）", "批复（答复意见）", "纪要（议定事项）",
    "通报（表扬与批评）", "整改（措施与销号）", "函（商洽与答复）",
    "部署（分工与节点）",
)


def test_phrase_bank_expanded_by_at_least_30():
    total = sum(len(v) for v in W.PHRASE_BANK.values())
    assert total >= 65, f"句式库应扩充到 65 条以上，实际 {total}"
    for group in _NEW_GROUPS:
        assert group in W.PHRASE_BANK, group
        assert len(W.PHRASE_BANK[group]) >= 4, group
        assert all(s.strip() for s in W.PHRASE_BANK[group]), group


def test_kind_groups_reference_existing_groups():
    for kind, groups in W.KIND_GROUPS.items():
        assert groups, kind
        for group in groups:
            assert group in W.PHRASE_BANK, (kind, group)


def test_inspiration_maps_new_groups_to_kinds():
    cases = {
        "请示": "请示（请求事项）",
        "批复": "批复（答复意见）",
        "纪要": "纪要（议定事项）",
        "通报": "通报（表扬与批评）",
        "意见": "整改（措施与销号）",
    }
    for kind, group in cases.items():
        ideas = WH.inspiration(kind=kind)
        assert ideas and all(i.source == "句式库" for i in ideas)
        assert any(i.label == group for i in ideas), (kind, [i.label for i in ideas])


def test_all_thresholds_live_in_writing_data():
    for name in ("SENT_TOO_LONG", "SENT_MIN_LEN", "PARA_TOO_LONG",
                 "FATIGUE_MIN_RUN", "LAYER_MAX_HINTS", "PUNCT_EXCLAIM_TOTAL",
                 "PUNCT_PARA_MAX", "TITLE_TOO_LONG", "PHRASE_REPEAT_LEN",
                 "PHRASE_REPEAT_MIN_COUNT", "PHRASE_REPEAT_SHOW",
                 "COLLOQUIAL_WORDS", "VAGUE_WORDS", "SELF_REF_SUFFIXES"):
        assert hasattr(W, name), f"阈值/词表应落在 writing_data：{name}"
