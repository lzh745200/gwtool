# -*- coding: utf-8 -*-
"""内容对齐（L2）规则测试。

对齐**只比较结构与角色**，不比较语义 —— 所以每条规则都可以用"确定的输入 ->
确定的输出"来验证，不存在需要人工判断的模糊地带。这一组测试就是把这套
确定性钉住：改了规则却没人发现，用户会在"差异列表"里看到凭空的提示。
"""
from __future__ import annotations

import pytest

from gwtool.core import paragraph_ref as pr


def _ref(text: str, ordinal: int, kind: str = "paragraph", level: int = 0):
    return pr.ParagraphRef(source="library", para_id=ordinal + 1, doc_id=1,
                           doc_title="参考文献", ordinal=ordinal, kind=kind,
                           level=level, text=text, char_offset=ordinal * 10,
                           text_hash=str(ordinal))


def _rel(aligns):
    return [a.relation for a in aligns]


class TestEmptyAndDegenerate:
    def test_no_refs_returns_empty(self):
        assert pr.align_to_draft("任意草稿", []) == []

    def test_empty_draft_reports_all_missing(self):
        refs = [_ref("标题", 0, "heading", 1), _ref("正文内容。", 1)]
        aligns = pr.align_to_draft("", refs)
        assert _rel(aligns).count("missing") == 2

    def test_blank_ref_text_is_tolerated(self):
        """空白段落不该让对齐崩掉（历史数据里确实有空块）。"""
        refs = [_ref("   ", 0), _ref("正文。", 1)]
        pr.align_to_draft("正文。", refs)   # 不抛即可


class TestRoleMatching:
    def test_identical_structure_all_matched(self):
        refs = [_ref("关于X的通知", 0, "heading", 1),
                _ref("各分局：", 1),
                _ref("为做好有关工作，现通知如下。", 2),
                _ref("特此通知。", 3)]
        draft = "\n".join(r.text for r in refs)
        aligns = pr.align_to_draft(draft, refs)
        assert _rel(aligns).count("matched") == 4
        assert "missing" not in _rel(aligns)

    def test_missing_closing_is_reported_with_role_label(self):
        refs = [_ref("关于X的通知", 0, "heading", 1),
                _ref("各分局：", 1),
                _ref("正文事项。", 2),
                _ref("特此通知。", 3)]
        draft = "关于X的通知\n各分局：\n正文事项。"
        aligns = pr.align_to_draft(draft, refs)
        missing = [a for a in aligns if a.relation == "missing"]
        assert len(missing) == 1
        assert "结束语" in missing[0].hint
        assert "特此通知" in missing[0].hint

    def test_missing_heading_hint_mentions_chapter(self):
        refs = [_ref("一级标题", 0, "heading", 1),
                _ref("二级标题", 1, "heading", 2)]
        aligns = pr.align_to_draft("一级标题", refs)
        missing = [a for a in aligns if a.relation == "missing"]
        assert missing and "缺对应章节" in missing[0].hint

    def test_extra_draft_role_is_reported(self):
        """草稿里有、参考里完全没有的角色 -> extra。"""
        refs = [_ref("正文事项。", 0)]
        draft = "正文事项。\n特此通知。"
        aligns = pr.align_to_draft(draft, refs)
        assert "extra" in _rel(aligns)

    def test_signature_role_detected_by_short_org_line(self):
        assert pr._role_of(2, 3, "paragraph", 0, "××市教育局") == "signature"

    def test_closing_role_detected_by_fixed_phrase(self):
        assert pr._role_of(2, 3, "paragraph", 0, "妥否，请批示。") == "closing"

    def test_plain_middle_paragraph_is_body(self):
        assert pr._role_of(1, 3, "paragraph", 0, "这里是一般正文内容。") == "body"


class TestOrderAndLevel:
    def test_reordered_text_is_flagged(self):
        refs = [_ref("第一部分内容。", 0), _ref("第二部分内容。", 1)]
        draft = "第二部分内容。\n第一部分内容。"
        aligns = pr.align_to_draft(draft, refs)
        assert "order_swap" in _rel(aligns)

    def test_same_role_different_text_is_not_order_swap(self):
        """顺序判定必须靠**文本相等**，不能因为"角色相同但内容不同"就报错位。"""
        refs = [_ref("参考正文甲。", 0), _ref("参考正文乙。", 1)]
        draft = "我的正文甲。\n我的正文乙。"
        aligns = pr.align_to_draft(draft, refs)
        assert "order_swap" not in _rel(aligns)


class TestOrdinalContinuity:
    def test_gap_in_ordinal_is_reported(self):
        draft = "一、第一项\n二、第二项\n四、第四项"
        aligns = pr._ordinal_issues(pr.split_draft(draft))
        assert any("跳到" in a.hint for a in aligns)

    def test_duplicate_ordinal_is_reported(self):
        draft = "一、第一项\n一、又是第一项"
        aligns = pr._ordinal_issues(pr.split_draft(draft))
        assert any("重复" in a.hint for a in aligns)

    def test_correct_ordinals_produce_no_issue(self):
        draft = "一、甲\n二、乙\n三、丙"
        assert pr._ordinal_issues(pr.split_draft(draft)) == []

    def test_sub_ordinals_tracked_separately(self):
        draft = "（一）甲\n（二）乙"
        assert pr._ordinal_issues(pr.split_draft(draft)) == []

    def test_arabic_ordinals_tracked(self):
        draft = "1.甲\n3.丙"
        aligns = pr._ordinal_issues(pr.split_draft(draft))
        assert aligns and "跳到" in aligns[0].hint


class TestCnToInt:
    @pytest.mark.parametrize("s,expect", [
        ("一", 1), ("二", 2), ("九", 9), ("十", 10), ("十一", 11),
        ("十九", 19), ("二十", 20), ("二十三", 23),
    ])
    def test_conversion(self, s, expect):
        assert pr._cn_to_int(s) == expect

    @pytest.mark.parametrize("bad", ["", "廿", "abc", "百"])
    def test_unparsable_returns_zero(self, bad):
        """转不了返回 0 —— 调用方据此跳过该项，**不猜**。"""
        assert pr._cn_to_int(bad) == 0
