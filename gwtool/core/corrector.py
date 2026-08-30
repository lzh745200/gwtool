# -*- coding: utf-8 -*-
"""文字纠错引擎：三级流水线。

  ① 精确匹配：error_pairs（含精标对、生成的混淆对、用户自定义对）最长优先；
  ② 上下文规则：同词不同境（截止/截至、爆发/暴发）；
  ③ 标点/数字规则（GB/T 15835）。

输出 Correction 列表：不重叠、按位置排序；同名命中按置信度取最高。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .corrector_data import (CONTEXT_RULES, CURATED_PAIRS, NEGATIVE_CONTEXTS,
                             NUMBER_RULES, ORG_RENAME_PAIRS, PUNCT_RULES)
from ..db import dao

_RULES_CACHE: dict = {}


@dataclass
class Correction:
    start: int
    end: int
    wrong: str
    suggestion: str
    category: str      # 错别字 / 易混词 / 禁用词 / 标点 / 数字用法 / 用户词库
    reason: str
    confidence: float

    @property
    def label(self) -> str:
        return f"{self.wrong} → {self.suggestion}"


def _is_valid_pair(wrong: str, correct: str) -> bool:
    """加载端防御：过滤同词、空串、含非中文噪声。"""
    if not wrong or not correct or wrong == correct:
        return False
    if not re.search(r"[\u4e00-\u9fff]", wrong):
        return False
    if any(ch in wrong for ch in "\n\r\t "):
        return False
    return True


def _load_patterns() -> dict[str, tuple[str, str, float]]:
    """wrong -> (correct, category, confidence)；用户自定义优先。"""
    if "patterns" in _RULES_CACHE:
        return _RULES_CACHE["patterns"]
    patterns: dict[str, tuple[str, str, float]] = {}
    # 内置精标
    for wrong, correct, cat, conf in CURATED_PAIRS:
        if conf <= 0 or not _is_valid_pair(wrong, correct):
            continue
        old = patterns.get(wrong)
        if old is None or conf > old[2]:
            patterns[wrong] = (correct, cat, conf)
    # 数据库（生成数据 + 用户添加，用户添加 source=user 置信度高）
    try:
        for p in dao.all_error_pairs():
            if not _is_valid_pair(p.wrong, p.correct) or not p.enabled:
                continue
            conf = p.confidence if p.source != "user" else max(p.confidence, 0.99)
            old = patterns.get(p.wrong)
            if old is None or conf > old[2]:
                patterns[p.wrong] = (p.correct, p.category or "错别字", conf)
    except Exception:
        pass  # 数据库不可用时退化为纯内置数据
    # 机构沿革对照（提示级，建议核实后替换）
    for wrong, correct in ORG_RENAME_PAIRS:
        old = patterns.get(wrong)
        if old is None or 0.7 > old[2]:
            patterns[wrong] = (correct, "机构沿革", 0.7)
    _RULES_CACHE["patterns"] = patterns
    # 持久忽略名单（用户确认无需提示的词）
    try:
        _RULES_CACHE["ignore"] = dao.all_ignore_words()
    except Exception:
        _RULES_CACHE["ignore"] = set()
    return patterns


def invalidate_cache() -> None:
    """用户增删词库后调用，使缓存失效。"""
    _RULES_CACHE.clear()


# ------------------------------------------------------------------ 引擎
def check_text(text: str) -> list[Correction]:
    if not text:
        return []
    found: list[Correction] = []
    found.extend(_check_exact(text))
    found.extend(_check_regex_group(text, CONTEXT_RULES, "易混词"))
    found.extend(_check_regex_group(text, PUNCT_RULES, "标点"))
    found.extend(_check_regex_group(text, NUMBER_RULES, "数字用法"))
    return _dedupe(found)


def _check_exact(text: str) -> list[Correction]:
    patterns = _load_patterns()
    ignore = _RULES_CACHE.get("ignore") or set()
    if not patterns:
        return []
    by_first: dict[str, list[str]] = {}
    for w in patterns:
        by_first.setdefault(w[0], []).append(w)
    for first in by_first:
        by_first[first].sort(key=len, reverse=True)  # 最长优先
    raw: list[tuple[int, int, str, str, str, float]] = []
    i, n = 0, len(text)
    while i < n:
        cands = by_first.get(text[i])
        if cands:
            hit = None
            for w in cands:
                if text.startswith(w, i):
                    hit = w
                    break
            if hit:
                if hit in ignore:
                    i += len(hit)
                    continue
                correct, cat, conf = patterns[hit]
                # 负向上下文：检查命中词之前的文本
                neg = NEGATIVE_CONTEXTS.get(hit, [])
                if any(re.search(rx, text[:i]) for rx in neg):
                    i += 1
                    continue
                raw.append((i, i + len(hit), hit, correct, cat, conf))
                i += len(hit)
                continue
        i += 1
    return _suppress_inside_common_words(text, raw)


def _suppress_inside_common_words(
        text: str, raw: list[tuple[int, int, str, str, str, float]]) -> list[Correction]:
    """词边界保护：命中片段两端都不落在分词边界上（即横跨多个常用词的内部），
    判定为词内误报并抑制。例：'安全生产'（分词为 安全|生产）内的 '全生'
    不应报 '全生→全省'；而独立成词的 '布署' 两端均为边界，正常报出。
    """
    boundaries: set[int] = set()
    if raw:
        try:
            import jieba
            from ..db.tokenize import _ensure_jieba
            _ensure_jieba()
            for _w, a, b in jieba.tokenize(text):
                boundaries.add(a)
                boundaries.add(b)
        except Exception:
            boundaries = set()
    out: list[Correction] = []
    for s, e, hit, correct, cat, conf in raw:
        if boundaries and s not in boundaries and e not in boundaries:
            continue  # 两端均在词内部 -> 误报
        out.append(Correction(s, e, hit, correct, cat, "词库匹配", conf))
    return out


def jieba_freq(word: str) -> int:
    try:
        import jieba
        return int(jieba.dt.FREQ.get(word, 0))
    except Exception:
        return 0


def _check_regex_group(text: str, rules, category: str) -> list[Correction]:
    out: list[Correction] = []
    for rx, repl, reason, conf in rules:
        if conf <= 0:
            continue
        try:
            for m in re.finditer(rx, text):
                if category == "数字用法":
                    # 数字规则的 repl 是提示标签而非替换文本
                    out.append(Correction(m.start(), m.end(), m.group(0), repl,
                                          category, reason, conf))
                else:
                    out.append(Correction(m.start(), m.end(), m.group(0),
                                          m.expand(repl), category, reason, conf))
        except re.error:
            continue
    return out


def _dedupe(items: list[Correction]) -> list[Correction]:
    """重叠去重：按 (confidence desc, length desc) 贪心保留。"""
    items = sorted(items, key=lambda c: (c.confidence, c.end - c.start), reverse=True)
    taken: list[Correction] = []
    for c in items:
        if any(not (c.end <= t.start or c.start >= t.end) for t in taken):
            continue
        taken.append(c)
    return sorted(taken, key=lambda c: c.start)


def apply_correction(text: str, c: Correction) -> str:
    return text[:c.start] + c.suggestion + text[c.end:]


def apply_all(text: str, corrections: list[Correction], skip: set[int] | None = None) -> str:
    """从后往前批量应用（skip 为要忽略的 Correction id 集合，用 start 标识）。"""
    skip = skip or set()
    for c in sorted(corrections, key=lambda x: x.start, reverse=True):
        if c.start in skip:
            continue
        text = text[:c.start] + c.suggestion + text[c.end:]
    return text
