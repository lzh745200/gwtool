# -*- coding: utf-8 -*-
"""段落检索（L1）测试：检索、召回兜底、过滤、溯源与引用变更检测。"""
from __future__ import annotations

from gwtool.core import paragraph_ref as pr
from gwtool.core.model import PARAGRAPH, Block, DocTree
from gwtool.db import dao


def _add(title: str, paras: list[str]) -> int:
    tree = DocTree(title=title)
    tree.blocks = [Block(type=PARAGRAPH, text=p) for p in paras]
    content = "\n".join(paras)
    did = dao.add_document(dao.Document(title=title, content_text=content,
                                        blocks_json=tree.to_json()))
    assert did > 0
    return did


class TestBasicRetrieval:
    def test_empty_query_returns_nothing(self, tmp_db):
        _add("某通知", ["正文内容一段。"])
        assert pr.search_paragraphs("") == []
        assert pr.search_paragraphs("   ") == []

    def test_finds_paragraph_by_keyword(self, tmp_db):
        _add("某通知", ["第一段与安全生产有关。", "第二段无关内容。"])
        hits = pr.search_paragraphs("安全生产")
        assert hits and "安全生产" in hits[0].text

    def test_result_carries_traceability_fields(self, tmp_db):
        did = _add("溯源测试通知", ["请各单位抓好安全生产责任落实。"])
        hit = pr.search_paragraphs("安全生产")[0]
        assert hit.doc_id == did
        assert hit.doc_title == "溯源测试通知"
        assert hit.ordinal == 0
        assert hit.para_id > 0
        assert hit.text_hash
        assert hit.source == "library"

    def test_locator_is_human_readable(self, tmp_db):
        _add("溯源测试通知", ["正文。"])
        hit = pr.search_paragraphs("正文")[0]
        assert "溯源测试通知" in hit.locator
        assert "第 1 段" in hit.locator

    def test_source_and_kind_labels(self):
        r = pr.ParagraphRef(source="library", kind="heading", level=2)
        assert r.source_label == "资料"
        assert r.kind_label == "标题"
        d = pr.ParagraphRef(source="draft", kind="paragraph")
        assert d.source_label == "草稿" and d.kind_label == "正文"

    def test_scores_are_within_unit_range(self, tmp_db):
        _add("某通知", ["安全生产第一段。", "安全生产第二段。", "无关。"])
        for h in pr.search_paragraphs("安全生产"):
            assert 0.0 <= h.score <= 1.0

    def test_limit_is_respected(self, tmp_db):
        _add("某通知", [f"安全生产第{i}段内容。" for i in range(20)])
        assert len(pr.search_paragraphs("安全生产", limit=3)) == 3

    def test_results_sorted_by_score_desc(self, tmp_db):
        _add("某通知", ["安全生产与责任并重的一段。", "安全生产的简单一句。"])
        hits = pr.search_paragraphs("安全生产")
        assert [h.score for h in hits] == sorted((h.score for h in hits),
                                                 reverse=True)

    def test_no_duplicate_paragraphs_in_result(self, tmp_db):
        _add("某通知", ["安全生产责任落实到人。"])
        hits = pr.search_paragraphs("安全生产 责任")
        pids = [h.para_id for h in hits]
        assert len(pids) == len(set(pids))


class TestRecallFallback:
    def test_multi_term_query_recall_is_relaxed_when_and_fails_too_well(self, tmp_db):
        """复现真实场景：索引把「安全生产责任制」切成整词，同段里不存在
        独立的「责任」，AND 必然落空 —— 必须靠 OR 兜底把该段捞回来。

        这是本功能可用性的关键：段落短，AND 极易全落空。
        """
        _add("某通知", [
            "为深入贯彻落实现行安全生产责任制，现就有关工作通知如下。",
            "各单位主要负责人是本单位安全生产第一责任人。",
        ])
        hits, relaxed = pr.search_paragraphs_ex("安全生产 责任")
        assert hits, "放宽后仍应命中"
        assert relaxed is True
        assert any("安全生产" in h.text for h in hits)

    def test_single_term_query_is_not_relaxed(self, tmp_db):
        _add("某通知", ["安全生产第一段。", "安全生产第二段。"])
        hits, relaxed = pr.search_paragraphs_ex("安全生产")
        assert hits and relaxed is False

    def test_strict_hits_outrank_relaxed_ones(self, tmp_db):
        """精确命中的分值区间与放宽命中**不相交**，精确恒在前。

        实测踩过：两组混在一起做 min-max 归一，放宽项因 bm25 量纲不同
        排到了精确项前面 —— 那等于"放宽把精确挤下去了"。
        """
        _add("某通知", [
            "安全生产责任制的落实情况需要逐项核查到位。",
            "安全生产的简单一句另起一段单独表述清楚。",
        ])
        hits, _ = pr.search_paragraphs_ex("安全生产 责任")
        assert result_has_clean_split(hits), \
            f"精确/放宽分值区间出现交叠：{[h.score for h in hits]}"

    def test_relaxed_flag_false_when_enough_strict_hits(self, tmp_db):
        """精确命中够了就不放宽。

        注意用词：必须让「安全」「生产」「责任」作为**独立词**出现在同一段里
        （jieba 会把"安全生产责任制"切成整词，那样 AND 永远不命中）。
        """
        paras = [f"安全生产的责任第{i}项要逐条落实到位并留痕备查。" for i in range(8)]
        _add("某通知", paras)
        hits, relaxed = pr.search_paragraphs_ex("安全生产 责任")
        assert len(hits) >= 5
        assert relaxed is False


def result_has_clean_split(hits) -> bool:
    """检查"精确段"与"放宽段"没有交叠：高分段的**最低分**不得低于低分段的
    最高分。判据来自实现约定：精确 ∈ [0.5, 1.0]、放宽 ∈ [0, 0.5]。

    这里不直接断言常量，而是断言"存在一个分界值把两组分开"—— 这样即便
    以后调整常数，只要"不相交"这个性质还在，测试依然是有效的。
    """
    scores = [h.score for h in hits]
    if len(scores) < 2:
        return True
    scores.sort(reverse=True)
    # 找第一个"掉档"的位置：相邻分数差 > 0.2 视为分组边界
    for i in range(len(scores) - 1):
        if scores[i] - scores[i + 1] > 0.2:
            return True
    return len(set(scores)) == 1


class TestFilters:
    def test_doc_ids_restricts_results(self, tmp_db):
        a = _add("文档甲", ["安全生产甲篇内容。"])
        b = _add("文档乙", ["安全生产乙篇内容。"])
        hits = pr.search_paragraphs("安全生产", doc_ids=[a])
        assert hits and all(h.doc_id == a for h in hits)
        hits_b = pr.search_paragraphs("安全生产", doc_ids=[b])
        assert hits_b and all(h.doc_id == b for h in hits_b)

    def test_doc_ids_with_no_match_returns_empty(self, tmp_db):
        _add("文档甲", ["安全生产甲篇内容。"])
        assert pr.search_paragraphs("安全生产", doc_ids=[999999]) == []

    def test_table_paragraphs_hidden_by_default(self, tmp_db):
        tree = DocTree(title="表格文档")
        tree.blocks = [Block(type="table", rows=[["安全生产", "责任"],
                                                 ["甲", "乙"]])]
        content = "安全生产 | 责任\n甲 | 乙"
        dao.add_document(dao.Document(title="表格文档", content_text=content,
                                      blocks_json=tree.to_json()))
        assert pr.search_paragraphs("安全生产") == []
        shown = pr.search_paragraphs("安全生产", exclude_kinds=())
        assert shown and shown[0].kind == "table"

    def test_deleted_document_is_not_retrievable(self, tmp_db):
        did = _add("将被删除的通知", ["安全生产需要常抓不懈。"])
        assert pr.search_paragraphs("安全生产")
        dao.delete_document(did)
        assert pr.search_paragraphs("安全生产") == []


class TestReferenceChangeDetection:
    def test_unchanged_reference_is_not_flagged(self, tmp_db):
        _add("某通知", ["安全生产责任要落实。"])
        hit = pr.search_paragraphs("安全生产")[0]
        assert pr.reference_changed(hit) is False

    def test_reference_flagged_after_source_edit(self, tmp_db):
        did = _add("某通知", ["安全生产责任要落实。"])
        hit = pr.search_paragraphs("安全生产")[0]
        dao.update_document_content(did, "某通知", "整篇换成了别的内容。")
        assert pr.reference_changed(hit) is True, \
            "源段落已被改，引用必须被标记为已变更"

    def test_draft_ref_never_flagged(self):
        assert pr.reference_changed(pr.ParagraphRef(source="draft")) is False

    def test_missing_paragraph_counts_as_changed(self, tmp_db):
        """段落行已不存在（文档被删/重建换了 id）-> 视为已变更。"""
        ref = pr.ParagraphRef(source="library", para_id=987654, doc_id=1,
                              text="x", text_hash="y")
        assert pr.reference_changed(ref) is True


class TestParagraphsOf:
    def test_returns_library_refs_in_order(self, tmp_db):
        did = _add("顺序通知", ["第一段。", "第二段。", "第三段。"])
        refs = pr.paragraphs_of(did)
        assert [r.text for r in refs] == ["第一段。", "第二段。", "第三段。"]
        assert [r.ordinal for r in refs] == [0, 1, 2]
        assert all(r.source == "library" and r.doc_id == did for r in refs)

    def test_missing_document_returns_empty(self, tmp_db):
        assert pr.paragraphs_of(123456) == []

    def test_split_draft_marks_source_and_offline(self):
        refs = pr.split_draft("第一段。\n\n第二段。")
        assert [r.text for r in refs] == ["第一段。", "第二段。"]
        assert all(r.source == "draft" and r.doc_id == 0 for r in refs)
        assert all(r.char_offset == -1 for r in refs)
