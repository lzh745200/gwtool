# -*- coding: utf-8 -*-
"""骨架生成（L3）测试：槽位抽取、骨架派生、填槽渲染、骨架持久化。

**这一组测试守住本功能最重要的一条边界**：系统只做"结构复用 + 槽位填充"，
不产出任何未被用户或参考原文提供的语义内容。因此断言的重点是
"替换进来的文本必须**原样取自参考**，不是新生成的"。
"""
from __future__ import annotations

import json

from gwtool.core import paragraph_ref as pr
from gwtool.db import dao


def _ref(text: str, ordinal: int = 0):
    return pr.ParagraphRef(source="library", para_id=ordinal + 1, doc_id=1,
                           doc_title="参考文献", ordinal=ordinal,
                           kind="paragraph", text=text,
                           char_offset=ordinal * 10, text_hash=str(ordinal))


# ---------------------------------------------------------------- 槽位抽取
class TestSlotExtraction:
    def test_org_slot_does_not_swallow_leading_verb(self):
        """单位名要切在词界上，不能把前面的动词吞进去。

        实测旧写法会把「请各单位报…」里的 `请各` + `单位` 拼成「请各单位」，
        槽位示例成了一句带谓语的短语 —— 拿它当骨架填出来的稿子是错的。
        """
        slots = pr.extract_slots("请各单位报××市教育局汇总。")
        orgs = [s.example for s in slots if s.name.startswith("org")]
        assert "××市教育局" in orgs, orgs
        assert not any(o.startswith("请") for o in orgs), orgs

    def test_org_with_placeholder_cross(self):
        """公文书里「××市教育局」这类占位写法极常见，必须能识别。"""
        slots = pr.extract_slots("××市教育局负责落实。")
        org = next(s for s in slots if s.name == "org")
        assert org.example == "××市教育局"

    def test_org_org_slot(self):
        slots = pr.extract_slots("现将有关情况报送长沙市教育局。")
        org = next(s for s in slots if s.name == "org")
        assert org.example.endswith("局")
        assert org.example == "长沙市教育局"

    def test_matter_slot_takes_inner_part_only(self):
        """事项名只取「关于…的」中间那段，不能把"关于""的"也抽象掉。"""
        slots = pr.extract_slots("××局关于安全生产的通知")
        matter = next(s for s in slots if s.name == "matter")
        assert matter.example == "安全生产"

    def test_date_slot_keeps_human_form(self):
        slots = pr.extract_slots("本通知自2026年8月15日起执行。")
        date = next(s for s in slots if s.name == "date")
        assert date.example == "2026年8月15日"

    def test_deadline_slot(self):
        slots = pr.extract_slots("请于3个工作日内反馈。")
        assert any(s.name == "deadline" for s in slots)

    def test_count_slot(self):
        slots = pr.extract_slots("共评选先进个人20名。")
        assert any(s.name == "count" for s in slots)

    def test_cited_document_slot(self):
        text = "依据《安全生产管理办法》（安办〔2026〕3号）执行。"
        slots = pr.extract_slots(text)
        cited = next(s for s in slots if s.name == "cited")
        assert "《安全生产管理办法》" in cited.example
        assert "〔2026〕3号" in cited.example

    def test_repeated_same_type_gets_suffix(self):
        text = "××市教育局与××市公安局联合印发。"
        names = [s.name for s in pr.extract_slots(text)]
        assert "org" in names and "org2" in names

    def test_span_matches_example_exactly(self):
        """槽位区间与示例必须自洽：否则替换会切错位置，正文被改坏。"""
        text = "请××市教育局于2026年8月15日前报送。"
        for s in pr.extract_slots(text):
            assert text[s.span[0]:s.span[1]] == s.example

    def test_overlapping_hits_are_resolved(self):
        """引用公文包着单位名时不能被切成互相嵌套的两个槽位。"""
        text = "《安全管理办法》（安全局〔2026〕3号）"
        slots = pr.extract_slots(text)
        spans = sorted(s.span for s in slots)
        for a, b in zip(spans, spans[1:]):
            assert a[1] <= b[0], f"槽位重叠：{a} 与 {b}"

    def test_no_rules_matched_returns_empty(self):
        assert pr.extract_slots("这是一句普通的话。") == []

    def test_slots_sorted_by_position(self):
        slots = pr.extract_slots("××市教育局于2026年8月15日印发。")
        assert [s.span[0] for s in slots] == sorted(s.span[0] for s in slots)


# ---------------------------------------------------------------- 骨架派生
class TestDeriveSkeleton:
    def test_empty_refs_gives_empty_skeleton(self):
        sk = pr.derive_skeleton([])
        assert sk.template == "" and sk.slots == []

    def test_template_is_exactly_reference_text_when_no_slots(self):
        sk = pr.derive_skeleton([_ref("这是一句普通的话。")])
        assert sk.template == "这是一句普通的话。"

    def test_abstracted_value_is_not_invented(self):
        """最关键的一条：槽位里的示例必须是**参考原文的切片**，不是新写的。"""
        ref = _ref("××市教育局关于安全生产的通知")
        sk = pr.derive_skeleton([ref])
        for s in sk.slots:
            assert s.example and s.example in ref.text

    def test_template_contains_placeholders(self):
        sk = pr.derive_skeleton([_ref("××市教育局关于安全生产的通知")])
        assert "{" in sk.template and "}" in sk.template
        assert "教育局" not in sk.template, "被抽象掉的具体值不该留在模板里"

    def test_paras_ordered_by_doc_then_ordinal(self):
        a = pr.ParagraphRef(source="library", para_id=2, doc_id=1, ordinal=1,
                            text="第二段。", text_hash="x")
        b = pr.ParagraphRef(source="library", para_id=1, doc_id=1, ordinal=0,
                            text="第一段。", text_hash="y")
        sk = pr.derive_skeleton([a, b])
        assert sk.template.startswith("第一段。")
        assert sk.source_paras == [1, 2]

    def test_source_paras_are_recorded_for_traceability(self):
        sk = pr.derive_skeleton([_ref("正文。", 0), _ref("结尾。", 1)])
        assert sk.source_paras == [1, 2]


# ---------------------------------------------------------------- 填槽渲染
class TestRenderDraft:
    def test_filled_values_replace_placeholders(self):
        out = pr.render_draft("关于{matter}的通知", {"matter": "防汛工作"})
        assert out == "关于防汛工作的通知"

    def test_unfilled_slot_keeps_placeholder(self):
        """未填的槽位**保留 {name}**，不静默清空 —— 清空会让用户以为
        这段本来就没有内容，从而直接采用一份缺内容的稿子。"""
        out = pr.render_draft("关于{matter}的通知", {})
        assert out == "关于{matter}的通知"

    def test_none_value_renders_as_empty_not_literal_none(self):
        out = pr.render_draft("值：{x}", {"x": None})
        assert out == "值：" and "None" not in out

    def test_accepts_plain_template_string(self):
        assert pr.render_draft("A{b}C", {"b": "X"}) == "AXC"

    def test_skeleton_roundtrip_from_template(self):
        tpl = "关于{matter}的通知\n{org}"
        sk = pr.skeleton_from_template(tpl)
        assert sk.slot_names() == ["matter", "org"]
        out = pr.render_draft(sk, {"matter": "防汛", "org": "××局"})
        assert out == "关于防汛的通知\n××局"

    def test_duplicate_placeholder_names_produce_one_slot(self):
        sk = pr.skeleton_from_template("{a}与{a}")
        assert sk.slot_names() == ["a"]


# ---------------------------------------------------------------- 序列化
class TestSlotSerialization:
    def test_json_roundtrip(self):
        slots = pr.extract_slots("请××市教育局于2026年8月15日前报送。")
        raw = pr.to_json_slots(slots)
        back = pr.from_json_slots(raw)
        assert [(s.name, s.example, s.span) for s in back] == \
            [(s.name, s.example, s.span) for s in slots]

    def test_broken_json_returns_empty(self):
        assert pr.from_json_slots("{不是 JSON") == []

    def test_json_is_valid_utf8_chinese(self):
        raw = pr.to_json_slots(pr.extract_slots("××市教育局"))
        assert "教育局" in raw
        assert json.loads(raw)[0]["name"] == "org"


# ---------------------------------------------------------------- 持久化
class TestUserSkeletonPersistence:
    def test_save_list_get_delete(self, tmp_db):
        sid = dao.save_user_skeleton(
            name="参考派生A", kind="通知", template="关于{matter}的通知",
            slots_json=pr.to_json_slots(pr.extract_slots("关于{matter}的通知")),
            source_paras="1,2", note="测试")
        assert sid > 0
        items = dao.list_user_skeletons()
        assert [s.name for s in items] == ["参考派生A"]
        got = dao.get_user_skeleton(sid)
        assert got is not None and got.template == "关于{matter}的通知"
        assert got.source_paras == "1,2"
        dao.delete_user_skeleton(sid)
        assert dao.list_user_skeletons() == []

    def test_same_name_overwrites_and_returns_real_id(self, tmp_db):
        """同名必须覆盖，且返回的 id 必须是**这次那条**。

        旧写法用 cur.lastrowid：upsert 走 DO UPDATE 分支时并没有发生 INSERT，
        lastrowid 可能是上一条语句留下的陈旧值，于是"刚保存的骨架 id"
        会指向另一条记录。
        """
        first = dao.save_user_skeleton(name="同名", kind="通知", template="A")
        second = dao.save_user_skeleton(name="同名", kind="报告", template="B")
        assert first == second
        assert len(dao.list_user_skeletons()) == 1
        got = dao.get_user_skeleton(second)
        assert got is not None and got.template == "B"

    def test_get_missing_returns_none(self, tmp_db):
        assert dao.get_user_skeleton(999999) is None

    def test_note_and_kind_are_persisted(self, tmp_db):
        sid = dao.save_user_skeleton(name="N", kind="函", template="T",
                                     note="备忘")
        got = dao.get_user_skeleton(sid)
        assert got.kind == "函" and got.note == "备忘"
        assert got.created_time, "创建时间应自动落库"
