# -*- coding: utf-8 -*-
"""风格校验的**三层判定**测试。

最要紧的一条纪律：**只有硬冲突算错**。区间外不等于错 —— 真实优秀作品
个体差异极大（调研报告「一是二是」从 0 到 20 次都有），拿均值或区间当合格线
会把好作品判成不合格。这一组测试就是守住"不误判"这条线。

样例统一由 `_plan_text()` 提供，它**自带足够长的 filler**：短于 1200 字会先触发
"公文族篇幅"那条硬冲突，把被测的那条规则盖住，测出来的是别的东西。
"""
from __future__ import annotations

from gwtool.core import inspector
from gwtool.core import style_data as D
from gwtool.core import style_profile as SP

#: 用于把样例撑到 1200 字以上（公文族篇幅硬约束），本身不含任何被测特征词
_FILLER = "该部分按既定分工推进，责任到岗到人。"


def _plan_text(extra: str = "") -> str:
    """一份够长的典型工作方案草稿（三级标题「1．」、无「一是」）。"""
    return ("一、工作目标\n到今年年底，全市缺口基本补齐。\n"
            "二、重点任务\n"
            "（一）摸清底数，建好台账。\n1．开展全域拉网式排查，逐小区核清保有量。\n"
            "2．建立问题隐患台账，明确责任单位与整改时限。\n"
            "3．实行动态更新，台账每月更新一次。\n"
            "（二）补齐设施缺口，同步消除隐患。\n1．加快建设集中充停设施。\n"
            "2．加装电梯阻车系统，防止进楼入户。\n3．整治违规停放充电行为。\n"
            "三、保障措施\n（一）加强组织领导，成立工作专班。\n"
            "（二）强化资金保障，由市财政统筹安排。\n"
            "（三）严格督导考核，纳入年度考核。"
            + _FILLER * 60 + extra)


def test_plan_text_is_long_enough():
    """守样例本身：短了会把被测规则盖住，测试就变成假的。"""
    assert SP.analyze(_plan_text())["chars"] > 1200


class TestHardConflicts:
    def test_dangjian_material_with_h1_is_hard_conflict(self):
        """千字材料加一级标题就变成了压缩版公文 —— 判族只看篇幅与一级标题。"""
        text = "一、第一个标题\n二、第二个标题\n" + "叙述做法与成效。" * 100
        assert 700 <= SP.analyze(text)["chars"] <= 2000
        v = SP.verdict(text, "短经验材料")
        assert not v.ok
        assert any("一级标题" in f.message for f in v.hard)

    def test_dangjian_material_length_window_is_enforced(self):
        v = SP.verdict("叙述做法与成效。" * 20, "短经验材料")
        assert not v.ok
        assert any("700-2000" in f.message for f in v.hard)

    def test_experience_summary_must_not_use_percent(self):
        text = "一、总体成效\n完成率达到95%。" + "陈述做法与成效。" * 80
        v = SP.verdict(text, "经验总结")
        assert not v.ok
        assert any("百分比" in f.message for f in v.hard)

    def test_leader_speech_must_not_use_h3(self):
        text = ("一、认识\n" + "讲清形势与意义。" * 100
                + "\n1．三级标题不该出现\n继续讲。" * 5)
        v = SP.verdict(text, "领导讲话")
        assert not v.ok
        assert any("三级标题" in f.message for f in v.hard)

    def test_gongwen_family_needs_minimum_length(self):
        v = SP.verdict("一、短稿\n内容很少。", "工作方案")
        assert not v.ok
        assert any("篇幅不应低于" in f.message for f in v.hard)

    def test_hard_conflict_is_the_only_thing_that_fails(self):
        """区间外**不等于**错：一份三不像的稿子只要不碰硬冲突就算通过。"""
        text = "一、甲\n" + "普通叙述内容。" * 300
        v = SP.verdict(text, "调研报告")
        assert v.ok, [f.message for f in v.hard]
        assert v.soft or v.rows      # 但一定有提示或对照项

    def test_every_hard_rule_is_callable(self):
        for style, rules in D.HARD.items():
            for field_key, ok_fn, _msg, _basis in rules:
                assert callable(ok_fn), f"{style}.{field_key} 判据不可调用"


class TestSoftWarnings:
    def test_plan_without_h3_warns_but_passes(self):
        text = ("一、工作目标\n" + "目标内容。" * 60
                + "\n二、重点任务\n" + "任务内容。" * 200
                + "\n三、保障措施\n" + "保障内容。" * 60)
        v = SP.verdict(text, "工作方案")
        assert v.soft, "工作方案缺三级标题应有软提示"
        assert v.ok, "软提示不应导致判定失败"

    def test_opinion_without_h2_warns(self):
        text = "一、总体要求\n" + "要求内容。" * 120 + "\n二、重点任务\n" + "任务。" * 250
        assert SP.analyze(text)["chars"] >= 1200
        v = SP.verdict(text, "工作意见")
        assert any("二级标题" in f.message for f in v.soft)
        assert v.ok

    def test_research_report_without_yishi_warns(self):
        text = "一、缘起\n" + "背景叙述。" * 120 + "\n二、做法成效\n" + "做法。" * 200
        assert SP.analyze(text)["chars"] >= 1200
        v = SP.verdict(text, "调研报告")
        assert any("一是二是" in f.message for f in v.soft)
        assert v.ok


class TestComparisonRows:
    def test_rows_cover_every_field_of_that_style(self):
        v = SP.verdict(_plan_text(), "工作方案")
        labels = {r.label for r in v.rows}
        for field_key in D.REF["工作方案"]:
            if field_key == "n":
                continue
            assert D.FIELD_LABEL[field_key] in labels

    def test_in_range_flag_is_consistent(self):
        v = SP.verdict(_plan_text(), "工作方案")
        for r in v.rows:
            assert r.in_range == (r.lo <= r.value <= r.hi)

    def test_bar_marks_below_and_above(self):
        assert "低" in SP.bar_pos(0, 10, 20)
        assert "高" in SP.bar_pos(99, 10, 20)
        assert "●" in SP.bar_pos(15, 10, 20)


class TestFocusAndTitles:
    def test_focus_lists_sections_and_picks_heaviest(self):
        v = SP.verdict(_plan_text(), "工作方案")
        assert len(v.focus) >= 3
        assert v.focus_top is not None
        assert v.focus_top[2] == max(pct for _n, _c, pct in
                                     [s for s in v.focus if s[0] != "导语"])

    def test_focus_note_states_range_out_is_not_an_error(self):
        v = SP.verdict(_plan_text(), "工作方案")
        assert "不作为错误" in v.focus_note

    def test_title_length_target_differs_by_style_kind(self):
        biz = SP.verdict(_plan_text(), "工作方案")
        ana = SP.verdict("一、改造已经起步，热度却集中在头部企业\n" + "叙述。" * 400,
                         "调研报告")
        assert "业务类" in biz.title_target
        assert "分析类" in ana.title_target


class TestPunctuationAndWords:
    def test_multiple_dashes_flagged(self):
        v = SP.verdict(_plan_text("第一处——这里。" * 3), "工作方案")
        assert any("破折号" in p for p in v.punct)

    def test_ascii_quotes_flagged(self):
        v = SP.verdict(_plan_text('所谓"闭环"机制。'), "工作方案")
        assert any("ASCII" in p for p in v.punct)

    def test_meta_comment_words_flagged(self):
        v = SP.verdict(_plan_text("说到底，还是要抓落实。"), "工作方案")
        assert any("元评论词" in p for p in v.punct)

    def test_meta_words_inside_quotes_are_allowed(self):
        """引语里的口语要保留 —— 排除引号内内容后再判。"""
        v = SP.verdict(_plan_text("他说：“说到底还是要抓落实。”"), "工作方案")
        assert not any("元评论词" in p for p in v.punct)

    def test_bad_words_detected(self):
        v = SP.verdict(_plan_text("相关部门要尽快落实。"), "工作方案")
        words = [w for w, _why in v.bad_words]
        assert "相关部门" in words and "尽快" in words

    def test_quota_warning_when_strength_words_exceed(self):
        v = SP.verdict(_plan_text("必须" * 40), "工作方案")
        assert v.quota_warn
        assert "顶破上限" in v.quota_warn


class TestFamilyMismatch:
    def test_declaring_dangjian_but_writing_gongwen_is_flagged(self):
        text = ("一、背景\n" + "内容。" * 200 + "\n二、做法\n" + "内容。" * 200
                + "\n三、保障\n" + "内容。" * 200)
        v = SP.verdict(text, "短经验材料")
        assert v.family_warn
        assert "党建族" in v.family_warn

    def test_same_family_mismatch_is_not_flagged(self):
        v = SP.verdict(_plan_text(), "工作方案")
        assert not v.family_warn


class TestReportContract:
    def test_report_has_the_ten_sections(self):
        v = SP.verdict(_plan_text("相关部门要尽快落实。"), "工作方案")
        text = "\n".join(SP.report_lines(v))
        for section in ("文体：", "身份证参数：", "【硬冲突检查】", "【参数对照】",
                        "【重心分布】", "【标点修辞检查】", "【避坑词】",
                        "一级标题平均"):
            assert section in text, f"报告缺少「{section}」"

    def test_report_states_pass_when_no_hard_conflict(self):
        v = SP.verdict(_plan_text(), "工作方案")
        assert v.ok
        text = "\n".join(SP.report_lines(v))
        assert "✓ 通过" in text

    def test_clean_report_still_prints_all_sections(self):
        """报告是"十项契约"：没问题也要显式说"未检出"。

        缺项会让读者以为"这一项没检查"，而实际是"检查了、没问题" ——
        两者语义完全不同。
        """
        v = SP.verdict(_plan_text(), "工作方案")
        text = "\n".join(SP.report_lines(v))
        assert "✓ 未检出" in text
        assert "✓ 未命中" in text

    def test_report_shows_failure_with_value_and_basis(self):
        v = SP.verdict("太短。", "调研报告")
        text = "\n".join(SP.report_lines(v))
        assert "✗" in text and "实测" in text and "依据" in text

    def test_empty_document_reports_not_crashes(self):
        v = SP.verdict("", "调研报告")
        assert not v.ok
        assert "为空" in v.hard[0].message
        assert SP.report_lines(v)


class TestInspectorIntegration:
    def test_style_findings_are_a_separate_item(self):
        """风格检查必须是独立一类，不并入版式检查（GB/T 9704）。"""
        out = inspector.style_findings(_plan_text(), "工作方案")
        assert out and all(f.item == "文体风格" for f in out)

    def test_hard_conflict_maps_to_error(self):
        out = inspector.style_findings("太短。", "调研报告")
        assert any(f.severity == "error" for f in out)

    def test_soft_warning_maps_to_warn(self):
        text = "一、总体要求\n" + "要求内容。" * 120 + "\n二、重点任务\n" + "任务。" * 250
        out = inspector.style_findings(text, "工作意见")
        assert any(f.severity == "warn" for f in out)

    def test_clean_text_gives_info_summary(self):
        out = inspector.style_findings(_plan_text(), "工作方案")
        assert out and out[0].severity == "info"
        assert D.ID_PARAM["工作方案"] in out[0].detail

    def test_style_findings_do_not_leak_into_inspect_text(self):
        """`inspect_text` 的输出里不应混入文体风格项（两者是不同性质的问题）。"""
        out = inspector.inspect_text(_plan_text())
        assert not any(f.item == "文体风格" for f in out)
