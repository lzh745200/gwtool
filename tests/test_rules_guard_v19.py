# -*- coding: utf-8 -*-
"""v1.9 回归：标点可自动替换规则的**语境守卫** + ``_dedupe`` 性能。

两条缺陷（来自 v1.9 审计 P1-12 / P1-16）：

1. **标点规则缺语境守卫**：`标点` 类别是 `ADVISORY_CATEGORIES` 之外的
   "可自动替换"类，会进「全部应用 / 批量写回 / 整篇应用」。原规则把
   ``print("恢复")`` 改成 ``print（"恢复"）``、把 ``f(...)`` 改成 ``f(……)``、
   把 ``(v1.8.0增强与修复记录.md)`` 改成全角括号 —— 一次点击就把代码、
   URL、文件名改坏。改法：括号规则加「左邻非 ASCII + 内容含引号 + 内容像
   代码/文件名」三重守卫；省略号规则只认带中文语境的半角点号。
   **反向要求**：真实中文括号/标点仍必须能改（能力不能被一起收紧没了）。

2. **``_dedupe`` 是 O(命中数²)**：实测 500/2000/4000 命中 = 8.1/139/549 ms，
   命中密集时占 ``check_text`` 的 95%。改成「按 start 排序 + 二分」后
   语义必须**逐条等价** —— 本文件用朴素参照实现对拍守着这一点。
"""
from __future__ import annotations

import random
import time

import pytest

from gwtool.core import corrector
from gwtool.core.corrector import Correction

# ------------------------------------------------------------------ 工具
_LEX = corrector._REASON_LEXICON


def _c(start: int, end: int, wrong: str = "错", sug: str = "对",
       conf: float = 0.9, reason: str = "规则") -> Correction:
    return Correction(start, end, wrong, sug, "错别字", reason, conf)


def _punct_hits(text: str):
    return [c for c in corrector.check_text(text) if c.category == "标点"]


def _fixed(text: str) -> str:
    """correct_block 就是「整篇应用 / 批量写回」的实现，必须按它断言。"""
    return corrector.correct_block(text)[0]


# ============================================================ 一、括号守卫
@pytest.mark.parametrize("text", [
    'print("恢复")',                 # 代码调用 + 字符串字面量
    "print('恢复')",                 # 单引号字符串
    "logger.info(f\"恢复 {x}\")",     # f-string（内容含 ASCII）
    "调用 f(...) 后返回",              # 省略号在括号里（代码）
    "详见 (v1.8.0增强与修复记录.md)",   # 文件名/扩展名
    "见 http://x/(说明)",             # URL 里的括号
    "若 a[i](说明) 成立",              # 下标调用后的括号
    "结果 (3px, 按 severity)",         # 数字+单位+ASCII 逗号
    "(48px 线性)",                    # 数字紧跟 ASCII 字母
    "(标题, 正文已改，但回滚快照未保存)",  # 内容含 ASCII 逗号
    "详见(src[c.start:c.end] 涉及数字位)",  # v1.8 已覆盖的代码切片
])
def test_paren_guard_leaves_code_alone(tmp_db, text):
    """代码/路径/文件名里的半角括号**不得**被改成全角。"""
    fixed, hits = corrector.correct_block(text)
    assert fixed == text, f"被改坏：{text!r} -> {fixed!r}（{[(c.wrong, c.suggestion) for c in hits]}）"
    assert not [c for c in hits if c.category == "标点"], hits


def test_paren_guard_still_converts_real_chinese_parens(tmp_db):
    """收紧守卫之后，真实中文括号必须仍能全角化（能力不能一起收紧没了）。"""
    assert _fixed("现将有关情况(附件1)报告如下。") == "现将有关情况（附件1）报告如下。"
    assert _fixed("依据(2026年10月3日)办理。") == "依据（2026年10月3日）办理。"
    assert _fixed("材料见 (PDF格式)。") == "材料见 （PDF格式）。"
    # 前接空白也属中文正文语境（公文里 "见 (附件1)" 很常见）
    assert _fixed("见 (附件1)。") == "见 （附件1）。"


def test_paren_guard_ignores_already_fullwidth(tmp_db):
    """已经是全角括号的文本不产生命中（不重复报）。"""
    text = "现依据（一）（二）两条办理。"
    assert _fixed(text) == text
    assert not [c for c in corrector.check_text(text) if c.category == "标点"]


def test_paren_rule_keeps_existing_v18_contract(tmp_db):
    """v1.8 的既有契约（代码片段不动、中文括号照改）不得被 v1.9 打破。"""
    code = "详见(src[c.start:c.end] 涉及数字位)，或看(1px `FOCUS_RING` 外圈)。"
    assert not [c for c in corrector.check_text(code) if c.category == "标点"]
    hits = [c for c in corrector.check_text("现将有关情况(附件1)报告如下。")
            if c.category == "标点"]
    assert any(c.suggestion == "（附件1）" for c in hits), hits


# ============================================================ 二、省略号守卫
@pytest.mark.parametrize("text", [
    "执行命令...",                   # 命令/代码行尾（前接汉字也不行：见规则说明）
    "调用 f(...) 后返回",             # 括号内、前接 ASCII
    "data = load(...)",             # 函数调用
    "https://example.com/a...",     # URL
    "path/to/file...",              # 路径
    "a ......",                     # 前有空格 + ASCII
    "x......",                      # 直接跟在 ASCII 后
])
def test_ellipsis_guard_leaves_code_alone(tmp_db, text):
    """代码/命令/URL 里的半角点号不是省略号，不得被替换。"""
    fixed, hits = corrector.correct_block(text)
    assert fixed == text, f"被改坏：{text!r} -> {fixed!r}"
    assert not [c for c in hits if c.category == "标点"], hits


@pytest.mark.parametrize("text,want", [
    ("省略号...测试", "省略号……测试"),          # 既有用例（句中的中文语境）
    ("等等......后面还有", "等等……后面还有"),      # 6 点中文语境
    # 全角句号连写：**标点重复**规则（0.95）优先于省略号规则（0.9），
    # 结果是"只保留一个句号"——v1.8 起就是这个语义，v1.9 不改变它。
    ("句子结束。。。", "句子结束。"),
    ("中文,逗号需要全角。", "中文，逗号需要全角。"),  # 既有用例：ASCII 逗号
    ("他说“等等...”吧", "他说“等等……”吧"),      # 引号内、后接中文
])
def test_punct_guard_keeps_chinese_conversion(tmp_db, text, want):
    """正常中文语境的标点仍必须能改（守卫不能把功能一起关掉）。"""
    assert _fixed(text) == want


def test_ellipsis_trailing_ascii_dots_are_left_alone(tmp_db):
    """句末的**三点**半角省略号不再自动替换 —— 与代码行尾无法区分，
    按"宁少报不误报"取不动（改法唯一性不足，误改代价高于漏改）。"""
    text = "下一步工作安排..."
    assert _fixed(text) == text


# ============================================================ 三、_dedupe 语义
def _naive_dedupe(items: list[Correction]) -> list[Correction]:
    """v1.8 的朴素实现（O(n²)），作为对拍参照 —— 逐字照抄旧代码。"""
    lexicon = [c for c in items if c.reason == _LEX]
    if lexicon:
        items = [c for c in items
                 if c.reason == _LEX or not any(
                     e.start <= c.start and c.end <= e.end
                     and (e.end - e.start) > (c.end - c.start) for e in lexicon)]
    items = sorted(items, key=lambda c: (c.confidence, c.end - c.start),
                   reverse=True)
    taken: list[Correction] = []
    for c in items:
        if any(not (c.end <= t.start or c.start >= t.end) for t in taken):
            continue
        taken.append(c)
    return sorted(taken, key=lambda c: c.start)


def _sig(items):
    return [(c.start, c.end, c.suggestion, c.confidence) for c in items]


def test_dedupe_same_as_reference_on_random_data():
    """随机数据（含重叠/包含/同分/重复区间）上与旧实现逐条一致。"""
    rnd = random.Random(20261023)
    for _round in range(60):
        n = rnd.randint(0, 60)
        items = []
        for _ in range(n):
            start = rnd.randint(0, 120)
            length = rnd.randint(1, 8)
            conf = rnd.choice([0.5, 0.55, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 0.98])
            reason = _LEX if rnd.random() < 0.35 else "规则"
            items.append(_c(start, start + length, sug=f"s{len(items)}",
                            conf=conf, reason=reason))
        assert _sig(corrector._dedupe(list(items))) == _sig(_naive_dedupe(list(items)))


def test_dedupe_reference_contracts():
    """三条既有语义的单点回归（长对优先、置信度优先、互不重叠）。"""
    # ① L1 长对吃掉内部短规则（D3）
    long_lex = _c(0, 6, sug="长对", conf=0.85, reason=_LEX)
    short_rule = _c(0, 2, sug="短规则", conf=0.9)
    got = corrector._dedupe([short_rule, long_lex])
    assert [c.suggestion for c in got] == ["长对"], [c.suggestion for c in got]

    # ② 高置信短命中裁掉低置信长命中（重叠时按置信度贪心）
    a = _c(0, 4, sug="长低", conf=0.55)
    b = _c(0, 2, sug="短高", conf=0.98)
    assert [c.suggestion for c in corrector._dedupe([a, b])] == ["短高"]

    # ③ 相邻不重叠都保留；输出按 start 升序
    got = corrector._dedupe([_c(8, 10, sug="右"), _c(0, 2, sug="左")])
    assert [c.suggestion for c in got] == ["左", "右"]

    # ④ 同分同长：保留输入顺序中靠前的那条（排序稳定性）
    x = _c(0, 2, sug="先", conf=0.9)
    y = _c(0, 2, sug="后", conf=0.9)
    assert [c.suggestion for c in corrector._dedupe([x, y])] == ["先"]

    # ⑤ 空输入
    assert corrector._dedupe([]) == []


def test_dedupe_lexicon_containment_many():
    """大量 L1 命中 + 大量内部候选时（旧实现最坏的 16M 次比较）语义不变。"""
    items = []
    for i in range(200):
        base = i * 10
        items.append(_c(base, base + 8, sug=f"lex{i}", conf=0.8, reason=_LEX))
        items.append(_c(base + 2, base + 4, sug=f"inner{i}", conf=0.99))
        items.append(_c(base + 8, base + 10, sug=f"after{i}", conf=0.99))
    assert _sig(corrector._dedupe(list(items))) == _sig(_naive_dedupe(list(items)))


def test_dedupe_4000_hits_under_100ms():
    """验收口径：4000 条命中 < 100 ms（v1.8 实测 549 ms）。"""
    items = [_c(i * 4, i * 4 + 2, sug=f"s{i}", conf=0.55) for i in range(4000)]
    corrector._dedupe(list(items))            # 预热，排除首次解释器开销
    best = None
    for _ in range(3):
        t0 = time.perf_counter()
        out = corrector._dedupe(list(items))
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    assert len(out) == 4000
    assert best < 0.100, f"_dedupe(4000) 耗时 {best * 1000:.1f} ms，超过 100 ms"


def test_dedupe_4000_mixed_fast():
    """混合场景（L1 + 规则 + 重叠）同样要快，且不会退化成 O(n²)。"""
    items = []
    for i in range(2000):
        base = i * 6
        items.append(_c(base, base + 4, sug=f"lex{i}", conf=0.85, reason=_LEX))
        items.append(_c(base + 1, base + 3, sug=f"rule{i}", conf=0.9))
    t0 = time.perf_counter()
    out = corrector._dedupe(items)
    dt = time.perf_counter() - t0
    assert len(out) == 2000          # 每条 L1 长对吃掉自己内部的短规则
    assert dt < 0.100, f"混合场景 {dt * 1000:.1f} ms，超过 100 ms"
