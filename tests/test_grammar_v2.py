# -*- coding: utf-8 -*-
"""P1 语法规则库 v2：规则规模、病句集召回、干净文本零自动误报、FP 守卫。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from gwtool.core import corrector, grammar_v2


def test_rule_count_meets_target():
    assert grammar_v2.rule_count() >= 120, "P1 目标：语法规则 ≥120 条"


def test_all_patterns_compile():
    for rule in grammar_v2._RULES:
        import re
        re.compile(rule.pattern)


def test_bad_sentence_recall():
    import eval_grammar

    rows = json.loads((ROOT / "tests/data/grammar_bad_sentences_v119.json")
                      .read_text(encoding="utf-8"))
    assert len(rows) == 120
    rec, per, miss = eval_grammar.recall(
        ROOT / "tests/data/grammar_bad_sentences_v119.json")
    assert rec >= 0.60, f"病句集召回 {rec:.1%} 低于 60%，漏检 {miss[:5]}"
    # 六类都必须有覆盖（防止某类规则整体失效）
    assert set(per) == set(grammar_v2.CATEGORIES)


def test_clean_text_zero_auto_edits():
    """正确写法的句子不得被语法 v2 自动修改（advisory 也不应误报）。"""
    sample = (ROOT / "tests/data/clean_grammar_sample.txt").read_text(
        encoding="utf-8")
    cats = set(grammar_v2.CATEGORIES)
    bad = []
    for raw in sample.splitlines():
        line = raw.strip()
        if not line:
            continue
        for h in corrector.check_text(line):
            if h.category in cats:
                bad.append((line, h.category, h.wrong, h.severity))
    assert not bad, f"干净文本被语法层命中：{bad[:5]}"


def test_known_false_positive_guards():
    """真实语料里已确认的 4 类误报必须不再报。"""
    cases = [
        "之所以能够发展壮大，正是因为依靠了人民。",
        "到达陕北时人数不但未减，反而增加。",
        "他们只有一个思想，就是无论如何要克服困难。",
        "伟大长征精神充分彰显了忠诚的力量，作为重要组成部分。",
        "日本社保费用已占预算三分之一左右。",
    ]
    cats = set(grammar_v2.CATEGORIES)
    for text in cases:
        hits = [h for h in corrector.check_text(text) if h.category in cats]
        assert not hits, f"{text} 仍被语法层误报：{[(h.wrong, h.severity) for h in hits]}"


def test_structural_rules_are_advisory_only():
    """成分残缺/结构杂糅只提示，不自动写回（v1.17 分级契约）。"""
    for category in (grammar_v2.CAT_MISSING, grammar_v2.CAT_STRUCT):
        assert category in corrector.ADVISORY_CATEGORIES


def test_structural_hit_never_auto_applied():
    text = "通过加强管理，使工作效率明显提高。"
    fixed, applied = corrector.correct_block(text)
    assert "使" in fixed, "成分残缺不得自动改写"
    assert not applied
    hits = [h for h in corrector.check_text(text)
            if h.category == grammar_v2.CAT_MISSING]
    assert hits and all(h.severity == "suggest" for h in hits)


def test_real_corpus_auto_fp_gate():
    """真实语料上语法 v2 自动误报 ≤0.2 处/万字；CI 无语料时跳过。"""
    import eval_grammar
    import pytest

    corpus = ROOT / "doc/corpus/real_all"
    if not corpus.is_dir():
        pytest.skip("CI/无本地语料：doc/corpus/real_all 不存在（gitignored）")
    auto, _adv, chars, _paras = eval_grammar.false_positives(corpus)
    per_wan = auto / max(1, chars) * 10000
    assert per_wan <= 0.20, f"真实语料自动误报 {auto} 处 = {per_wan:.3f}/万字 > 0.2"
