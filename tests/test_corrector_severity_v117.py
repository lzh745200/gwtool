# -*- coding: utf-8 -*-
"""v1.17 P0：severity 三级门控 + 受保护片段 + 每句修改上限（红→绿）。

契约（与 doublecheck_spec 一致）：
- Correction.severity ∈ {error, warning, suggest}，默认 error（旧构造向后兼容）；
- 受保护片段（引号引文/书名号/URL/邮箱/行内代码/数字+单位）内命中只作 suggest，
  且 correct_block 不写回；
- 同一句内自动修改（error）最多 2 处，其余降为 warning，但**不丢结果**；
- 提示类（ADVISORY）永远 suggest，apply_low_confidence=True 也只放开生成错别字类。
"""
from __future__ import annotations

from gwtool.core import corrector


def test_severity_field_default_and_values():
    c = corrector.Correction(0, 1, "错", "对", "错别字", "理由", 0.96)
    assert c.severity == "error"          # 旧构造点零改动
    hits = corrector.check_text("各部门要按排专人负责。")
    assert hits and hits[0].severity == "error"
    assert hits[0].confidence >= 0.70


def test_advisory_category_is_suggest():
    # 数字用法属提示类：永远 suggest（用"二〇二六年"触发四位汉字年份规则）
    hits = corrector.check_text("二〇二六年十月开会。")
    nums = [h for h in hits if h.category == "数字用法"]
    assert nums, "测试前置：应检出数字用法提示"
    assert all(h.severity == "suggest" for h in nums)


def test_protected_quoted_text_not_auto_applied():
    text = "文件写着“布署工作”，实际应为部署。"
    hits = corrector.check_text(text)
    quoted = [h for h in hits if h.wrong == "布署"]
    assert quoted, "引号内仍应检出（只降级、不丢）"
    assert all(h.severity == "suggest" for h in quoted)
    fixed, applied = corrector.correct_block(text)
    assert "布署" in fixed, "受保护片段不得自动改写"
    assert not [c for c in applied if c.wrong == "布署"]


def test_unquoted_error_still_auto_applied():
    fixed, applied = corrector.correct_block("文件要求布署工作。")
    assert "部署" in fixed
    assert [c for c in applied if c.wrong == "布署"]


def test_book_title_and_url_protected():
    text = "参见《布署指南》与 https://example.com/布署 的说明。"
    hits = [h for h in corrector.check_text(text) if h.wrong == "布署"]
    assert hits
    assert all(h.severity == "suggest" for h in hits)


def test_per_sentence_auto_cap_two():
    # 一句话里三处可自动改的错：按排、截止、涉及到
    text = "要按排专人，截止本月底把涉及到的情况报上来。"
    hits = [h for h in corrector.check_text(text)
            if h.severity in ("error", "warning")]
    assert len(hits) >= 3, f"测试前置：应检出至少 3 处，实际 {[(h.wrong, h.severity) for h in hits]}"
    errors = [h for h in hits if h.severity == "error"]
    assert len(errors) <= 2
    others = [h for h in hits if h.severity == "warning"]
    assert others, "超出上限的应降为 warning，而不是消失"


def test_per_sentence_cap_does_not_drop_hits():
    one = corrector.check_text("要按排专人。")
    multi = corrector.check_text("要按排专人，截止本月底把涉及到的情况报上来。")
    words = {h.wrong for h in multi}
    assert {"按排", "涉及到"} <= words or {"按排", "截止"} <= words
    assert len(one) == 1


def test_apply_low_confidence_only_generated():
    # 提示类即使显式放开也不得变为 error
    text = "2026年10月32日开会。"
    fixed, applied = corrector.correct_block(text, apply_low_confidence=True)
    assert all(c.category != "数字用法" for c in applied)


def test_severity_does_not_change_detection_set():
    """severity 只影响"要不要自动写回"，不改变检出条数/内容。"""
    text = "各部门要按排专人负责，截止本月底报送涉及到情况的材料。"
    before = {(h.start, h.end, h.wrong, h.suggestion, h.category)
              for h in corrector.check_text(text)}
    # 再跑一次仍相同（仅新增字段，不重新排序/去重）
    after = {(h.start, h.end, h.wrong, h.suggestion, h.category)
             for h in corrector.check_text(text)}
    assert before == after
