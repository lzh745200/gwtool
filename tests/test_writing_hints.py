# -*- coding: utf-8 -*-
"""写作辅助引擎（writing_hints）的契约测试。

守三件事：

1. **写作辅助永不改正文** —— 这是它与「文字纠错」的结构性边界。纠错命中是错误、
   可自动替换；写作建议不是错误、只能由人决定。本模块只产出 ``Hint``/``Idea``
   纯数据，不导入任何写库/写文件接口（下面用结构性断言把它钉住）。
2. **空数据不抛异常** —— 空草稿、未知文种、无资料库、检索异常，都必须降级成
   可展示的结果。写作提示是辅助，绝不能让界面崩。
3. **文种识别口径正确** —— ``detect_kind`` 复用 ``style_profile.match_genre``，
   返回的距离**越小越像**，必须保持升序（误当"越大越好"会把最不像的顶到首位）。
"""
import pytest

from gwtool.core import writing_hints as WH
from gwtool.db import dao


def _add_doc(title: str, text: str) -> int:
    return dao.add_document(dao.Document(title=title, content_text=text))


# ---------------------------------------------------------------- 骨架
def test_available_kinds_matches_skeleton_library(tmp_db):
    from gwtool.core import skeletons
    assert WH.available_kinds() == skeletons.kinds()
    assert "通知" in WH.available_kinds()


def test_outline_known_kind(tmp_db):
    o = WH.outline("通知")
    assert o is not None and o.kind == "通知"
    assert o.sections, "骨架应给出正文章节"
    assert all(s.strip() for s in o.sections), "正文要素不应含空串"
    assert o.closing == "特此通知。"


def test_outline_unknown_kind_is_none(tmp_db):
    assert WH.outline("不存在的文种") is None
    assert WH.outline("") is None


# ---------------------------------------------------------------- 写作提示
def test_hints_empty_draft_gives_scaffold(tmp_db):
    hs = WH.hints("", "通知")
    assert hs and hs[0].scope == "结构"
    assert "骨架" in hs[0].title or "结构" in hs[0].title


def test_hints_empty_draft_unknown_kind(tmp_db):
    hs = WH.hints("   ", "")
    assert len(hs) == 1


DRAFT_MISSING = (
    "关于进一步加强安全生产工作的通知\n"
    "各市人民政府：\n"
    "近年来，我市安全生产工作取得明显成效，但部分领域仍然存在薄弱环节，"
    "要抓紧抓好整改落实，确保各项措施落地见效。\n"
)


def test_hints_reports_missing_closing_and_date(tmp_db):
    hs = WH.hints(DRAFT_MISSING, "通知")
    titles = " ".join(h.title for h in hs)
    assert "结束语" in titles
    assert "成文日期" in titles
    # 结束语缺失是"缺口"，应为 warn 级
    assert any(h.level == "warn" and "结束语" in h.title for h in hs)


DRAFT_COMPLETE = (
    "关于进一步加强安全生产工作的通知\n"
    "各市人民政府：\n"
    "近年来，我市安全生产工作取得明显成效，但部分领域仍然存在薄弱环节，"
    "现将有关事项通知如下，请遵照执行。\n"
    "一、压实责任。各县区人民政府要落实属地责任，明确牵头单位和配合单位。\n"
    "二、强化监管。市应急管理局要加强督促检查，及时通报工作进展。\n"
    "特此通知。\n"
    "××市人民政府\n"
    "2026年10月3日\n"
)


def test_hints_complete_draft_has_no_missing_warn(tmp_db):
    hs = WH.hints(DRAFT_COMPLETE, "通知")
    assert not any(h.level == "warn" and "结束语" in h.title for h in hs)
    assert not any(h.level == "warn" and "成文日期" in h.title for h in hs)


def test_hints_reports_blank_words(tmp_db):
    hs = WH.hints("我觉得这项工作很重要，请相关部门尽快抓好落实。", "")
    titles = " ".join(h.title for h in hs)
    assert "我觉得" in titles and "尽快" in titles


def test_hints_long_single_paragraph(tmp_db):
    long_para = "各有关单位要高度重视此项工作，" * 25
    hs = WH.hints(long_para, "")
    assert any("单段过长" in h.title for h in hs)


def test_hints_never_raises_on_broken_style(tmp_db, monkeypatch):
    from gwtool.core import style_profile

    def boom(_text):
        raise RuntimeError("量化失败")
    monkeypatch.setattr(style_profile, "analyze", boom)
    hs = WH.hints("各单位要抓好落实。", "通知")
    assert isinstance(hs, list), "风格量化异常不应让写作提示整体失败"


def test_hints_never_raises_on_broken_slots(tmp_db, monkeypatch):
    from gwtool.core import paragraph_ref

    def boom(_text):
        raise RuntimeError("槽位抽取失败")
    monkeypatch.setattr(paragraph_ref, "extract_slots", boom)
    hs = WH.hints(DRAFT_MISSING, "通知")
    assert isinstance(hs, list)


# ---------------------------------------------------------------- 文种识别
def test_detect_kind_sorted_ascending(tmp_db):
    """距离越小越像：返回值必须升序（防止把最不像的当"最像"）。"""
    res = WH.detect_kind(DRAFT_COMPLETE)
    if len(res) >= 2:
        assert res[0][1] <= res[1][1], f"未升序：{res}"


def test_detect_kind_empty_text_safe(tmp_db):
    assert WH.detect_kind("") == []


# ---------------------------------------------------------------- 灵感建议
def test_inspiration_phrase_bank_only(tmp_db):
    ideas = WH.inspiration(kind="通知")
    assert ideas, "应至少给出句式库建议"
    assert all(i.source == "句式库" for i in ideas)
    assert any("通知如下" in i.text or "现将有关事项" in i.text for i in ideas)


def test_inspiration_unknown_kind_falls_back_to_all_groups(tmp_db):
    ideas = WH.inspiration(kind="未知文种")
    assert ideas and all(i.source == "句式库" for i in ideas)


def test_inspiration_topic_hits_library(tmp_db):
    _add_doc("关于安全生产工作的调查报告",
             "为深入贯彻安全生产工作要求，我们对全市安全生产情况进行了专题调研，"
             "现将有关情况报告如下。")
    ideas = WH.inspiration(topic="安全生产", kind="通知", limit=5)
    assert any(i.source != "句式库" for i in ideas), \
        f"应检索到资料库写法：{[(i.source, i.text[:12]) for i in ideas]}"


def test_inspiration_empty_db_does_not_raise(tmp_db):
    ideas = WH.inspiration(topic="不存在的主题词", kind="通知", limit=3)
    assert isinstance(ideas, list) and ideas


def test_inspiration_swallows_reference_error(tmp_db, monkeypatch):
    from gwtool.core import reference

    def boom(*_a, **_k):
        raise RuntimeError("检索炸了")
    monkeypatch.setattr(reference, "lookup", boom)
    ideas = WH.inspiration(topic="安全生产", kind="通知", limit=3)
    assert ideas, "检索异常应降级为「只有句式库」而不是空结果"
    assert all(i.source == "句式库" for i in ideas)


# ---------------------------------------------------------------- 报告
def test_report_lines_empty():
    lines = WH.report_lines([])
    assert len(lines) == 1 and "无明显偏离" in lines[0]


def test_report_lines_contains_scope():
    lines = WH.report_lines([WH.Hint("结构", "缺少结束语", "建议补上", "warn")])
    assert lines[0].startswith("【结构】")


# ---------------------------------------------------------------- 结构约束
def test_writing_hints_module_has_no_write_api():
    """写作辅助不得持有任何"改正文"的入口 —— 这是结构性边界，不是约定。

    纠错可以自动替换；写作建议只能由人决定是否采用。一旦本模块长出
    insert/apply/write 之类接口，这条边界就会被悄悄越过。
    """
    forbidden = ("insert", "apply_", "write", "save", "set_text",
                 "replace", "update_document")
    for name in dir(WH):
        low = name.lower()
        assert not (low.startswith("apply") or low.startswith("insert")), name
        assert not any(f == low or low.endswith(f) for f in forbidden), name


def test_hint_and_idea_are_plain_data():
    h = WH.Hint("结构", "标题", "详情")
    assert (h.scope, h.title, h.detail, h.level) == ("结构", "标题", "详情", "info")
    assert h.line.startswith("【结构】标题")
    i = WH.Idea("句式库", "结尾", "特此通知。", 0)
    assert i.source == "句式库" and i.ref_id == 0


@pytest.mark.parametrize("kind,group", [
    ("通知", "结尾（结束语）"),
    ("决定", "保障（组织与落实）"),
    ("报告", "背景（形势与问题）"),
])
def test_kind_groups_reference_existing_groups(kind, group):
    from gwtool.core import writing_data as W
    assert group in W.PHRASE_BANK
    assert kind in W.KIND_GROUPS
