# -*- coding: utf-8 -*-
"""v1.9 写作三项修复的回归测试（P1-17 / P1-19 / P1-22）。

三组用例各自都带**正例与反例**：修掉假阳性的同时，原来该报的必须继续报——
只降误报、不放松判定。

  P1-17  「一是/二是」跨行列举不再被判跳号；同一父级内真跳号/重号仍报。
  P1-19  段落对齐的序号检查按父级复位；逐字相同的草稿 0 假阳性，
         同一父级内重复/跳号仍报。
  P1-22  完成时限识别补日期式写法（「9月20日前」「本月底前」「年底前」）；
         真正没写时限的仍报，且「前期/前往/目前/前后」这类词不得让提示静默消失。
"""
from __future__ import annotations

from gwtool.core import paragraph_ref as PR
from gwtool.core import writing_hints as WH


def _titles(text: str, kind: str = "") -> str:
    return " ".join(h.title for h in WH.hints(text, kind))


# ============================================================ P1-17 一是/二是
def test_yishi_across_paragraphs_not_reported():
    """经验材料式：每个「一是」独占一段，属规范写法，不得报跳号。"""
    text = ("关于基层治理的做法\n"
            "一、主要做法\n"
            "一是建强平台，把事项统起来。\n"
            "二是织密网格，把力量沉下去。\n"
            "三是打通数据，把负担减下来。\n"
            "四是健全机制，把闭环立起来。\n")
    assert "层次序号" not in _titles(text)


def test_yishi_cross_paragraph_real_jump_still_reported():
    """跨行不是免检：一是 → 三是 仍是跳号。"""
    text = "一、主要做法\n一是建强平台。\n三是打通数据。\n"
    assert "层次序号不规范：跳号" in _titles(text)


def test_yishi_cross_paragraph_duplicate_still_reported():
    """同一父级内连续两个「一是」是重号（不是"另起一组"）。"""
    text = "一、主要做法\n一是建强平台。\n一是织密网格。\n"
    assert "层次序号不规范：重号" in _titles(text)


def test_yishi_restart_in_same_parent_not_reported():
    """既有契约：同一父级内另起一组枚举（一是二是 → 一是二是）不算错。"""
    text = ("关于工作的通知\n"
            "一是加强领导，二是明确分工。\n"
            "一是压实责任，二是强化监管。")
    assert "层次序号" not in _titles(text)


def test_yishi_reset_after_new_parent_heading():
    """换一级标题后「一是」从 1 重新起算，不得把第二节判成重复/倒序。"""
    text = ("一、主要做法\n"
            "一是建强平台。\n二是织密网格。\n三是打通数据。\n"
            "二、主要成效\n"
            "一是治理效能提升。\n二是群众满意度提升。\n")
    assert "层次序号" not in _titles(text)


def test_yishi_single_line_jump_still_checked():
    """同一行内的「一是、二是、四是」仍要报（不能因为跨行修复而整体放松）。"""
    text = "一、工作要求\n一是加强领导，二是明确分工，四是严格考核。"
    assert "层次序号不规范：跳号" in _titles(text)


def test_layer_1_2_3_continuity_unaffected():
    """一二三正常续编仍零提示（回归）。"""
    text = "一、总体要求\n内容甲。\n二、重点任务\n内容乙。\n三、保障措施\n内容丙。"
    assert "层次序号" not in _titles(text)


def test_layer_1_2_3_jump_still_reported():
    """一级序号跳号仍要报（回归）。"""
    text = "一、总体要求\n内容甲。\n三、保障措施\n内容丙。"
    assert "层次序号不规范：跳号" in _titles(text)


# ============================================================ P1-19 对齐序号复位
NOTICE = ("一、总体要求\n"
          "（一）提高认识。\n（二）明确目标。\n（三）落实责任。\n"
          "二、重点任务\n"
          "（一）抓好落实。\n（二）强化保障。\n（三）严格考核。\n"
          "三、工作要求\n"
          "（一）加强领导。\n（二）严明纪律。\n（三）及时报送。\n")


def test_identical_draft_has_no_ordinal_issue():
    """参考与草稿逐字相同时，序号检查必须 0 命中（P1-19 的原始症状）。"""
    assert PR._ordinal_issues(PR.split_draft(NOTICE)) == []


def test_identical_draft_align_has_no_level_mismatch():
    """整条对齐链路上也不得出现 level_mismatch（假差异会被用户当成真差异）。"""
    refs = PR.split_draft(NOTICE)
    rels = [a.relation for a in PR.align_to_draft(NOTICE, refs)]
    assert "level_mismatch" not in rels
    assert rels.count("matched") == len(refs)


def test_sub_ordinals_reset_per_parent():
    """每个「一、」下重新用（一）（二），是规范写法。"""
    draft = PR.split_draft(
        "一、甲\n（一）子一\n（二）子二\n二、乙\n（一）子一\n（二）子二")
    assert PR._ordinal_issues(draft) == []


def test_arabic_sub_reset_per_parent():
    """（一）下重新用 1. 2. 同样是规范写法。"""
    draft = PR.split_draft(
        "（一）甲\n1.子一\n2.子二\n（二）乙\n1.子一\n2.子二")
    assert PR._ordinal_issues(draft) == []


def test_duplicate_ordinal_still_reported():
    """同一父级内真正的重号仍要报。"""
    issues = PR._ordinal_issues(PR.split_draft("一、甲\n一、又是甲"))
    assert issues and "重复" in issues[0].hint


def test_gap_ordinal_still_reported():
    """同一父级内真正的跳号仍要报。"""
    issues = PR._ordinal_issues(PR.split_draft("一、甲\n二、乙\n四、丁"))
    assert any("跳到" in a.hint for a in issues)


def test_correct_ordinals_no_issue():
    assert PR._ordinal_issues(PR.split_draft("一、甲\n二、乙\n三、丙")) == []


# ============================================================ P1-22 完成时限
DATE_STYLE = ("××市教育局关于开展校园安全排查的通知\n"
              "各区县教育局：\n"
              "请各单位于2026年9月20日前完成自查，并将台账报送市教育局。\n"
              "特此通知。\n"
              "××市教育局\n"
              "2026年9月10日\n")


def _has_deadline_hint(text: str, kind: str = "通知") -> bool:
    return any("完成时限" in h.title for h in WH._element_hints(text, kind))


def test_date_style_deadline_not_reported():
    """日期式时限（公文最标准写法）不得再报"未识别到完成时限"。"""
    assert not _has_deadline_hint(DATE_STYLE)


def test_deadline_variants_not_reported():
    """各种常见时限写法都要认。"""
    variants = (
        "2026年9月20日前", "9月20日以前", "9月20日之前",
        "9月底前", "本月底前", "年底前", "今年年底前",
        "3个工作日内", "三个工作日内", "3个工作日",
    )
    for tail in variants:
        text = f"各区县教育局：\n请各单位于{tail}完成自查。\n特此通知。"
        assert not _has_deadline_hint(text), tail


def test_missing_deadline_still_reported():
    """真没写时限的仍要报（只降误报，不放松判定）。"""
    text = ("××市教育局关于开展校园安全排查的通知\n"
            "各区县教育局：\n"
            "请各单位认真做好自查工作，建立隐患台账。\n"
            "特此通知。\n")
    assert _has_deadline_hint(text)


def test_unrelated_qian_words_do_not_suppress():
    """「前期/前往/目前/之前」不是时限，不得让提示消失（防漏报）。"""
    for noise in ("做好前期准备工作", "前往现场核查", "目前正在推进",
                  "在之前的工作基础上继续努力", "会议于9月20日前后召开"):
        text = f"各区县教育局：\n请各单位{noise}。\n特此通知。"
        assert _has_deadline_hint(text), noise


def test_deadline_only_required_for_task_kinds():
    """报告类不要求时限，保持既有口径。"""
    text = "请各单位认真做好自查工作。\n特此报告。"
    assert not _has_deadline_hint(text, "报告")


def test_hints_end_to_end_no_deadline_hint():
    """端到端：规范通知的完整提示里不再出现"未识别到完成时限"。"""
    assert "未识别到完成时限" not in _titles(DATE_STYLE, "通知")
