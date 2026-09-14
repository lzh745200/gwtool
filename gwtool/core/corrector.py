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
    category: str      # 错别字 / 易混词 / 禁用词 / 标点 / 数字用法 /
                       # 用户词库 / 神经纠错 / 语法纠错
    reason: str
    confidence: float
    # 改动类型：replace（替换，默认）/ insert（增字）/ delete（删字）。
    # 默认值保证历史构造点零改动；L5 Seq2Seq 层需用它区分变长输出。
    kind: str = "replace"

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
    try:                       # 顺带失效神经层的开关缓存（默认关闭时零成本）
        from . import csc_gec, csc_neural
        csc_neural.invalidate_cache()
        csc_gec.invalidate_cache()
    except Exception:
        pass


# ------------------------------------------------------------------ 引擎
def check_text(text: str) -> list[Correction]:
    if not text:
        return []
    found: list[Correction] = []
    found.extend(_check_exact(text))
    found.extend(_check_regex_group(text, CONTEXT_RULES, "易混词"))
    found.extend(_check_regex_group(text, PUNCT_RULES, "标点"))
    found.extend(_check_regex_group(text, NUMBER_RULES, "数字用法"))
    found = _apply_neural_layer(text, _dedupe(found))     # L4 单字替换精排
    return _apply_gec_layer(text, found)                  # L5 语法纠错


def _apply_neural_layer(text: str,
                        corrections: list[Correction]) -> list[Correction]:
    """可选第 4 级：神经网络精排（默认关闭，需导入增强包并手动开启）。

    设计约束：
      - 未安装/未开启时**零成本**：`enabled()` 只做一次内存布尔判断；
      - L4 只补前三层没有覆盖的位置，输出同样是 `Correction`，
        所以 UI 层（纠错面板 / 任意文档纠错 / 标记视图）无需任何改动；
      - **异常一律咽掉**：增强包损坏、依赖缺失、推理报错，都只回落三级
        流水线，绝不让纠错主流程失败。
    """
    try:
        from . import csc_neural
        if not csc_neural.enabled():
            return corrections
        extra = csc_neural.enhance(text, corrections)
        if not extra:
            return corrections
        return _dedupe(list(corrections) + list(extra))
    except Exception:
        return corrections


def _apply_gec_layer(text: str,
                     corrections: list[Correction]) -> list[Correction]:
    """可选第 5 级：Seq2Seq 语法纠错（默认关闭，需导入 cgec 增强包并开启）。

    与 L4 的分工：
      - L4 是「单字替换」分类器，逐位判定，补前三层漏掉的错字；
      - L5 是「变长输出」生成模型，能处理增字/删字/改序类语病。
    因此 L5 在 L4 之后运行，其输出仍挂在 `occupied` 之外——L4 优先、L5 补漏，
    同一位置不会双报。异常同样一律咽掉，未开启时只做一次内存布尔判断。
    """
    try:
        from . import csc_gec
        if not csc_gec.enabled():
            return corrections
        extra = csc_gec.enhance(text, corrections)
        if not extra:
            return corrections
        return _dedupe(list(corrections) + list(extra))
    except Exception:
        return corrections


def _buckets() -> dict[str, list[str]]:
    """首字 -> 词列表 的匹配桶（最长优先），随词库缓存避免每次 4 万条重建。"""
    buckets = _RULES_CACHE.get("buckets")
    if buckets is None:
        patterns = _load_patterns()
        buckets = {}
        for w in patterns:
            buckets.setdefault(w[0], []).append(w)
        for first in buckets:
            buckets[first].sort(key=len, reverse=True)  # 最长优先
        _RULES_CACHE["buckets"] = buckets
    return buckets


_NEG_COMPILED: dict[str, list] = {}


def _check_exact(text: str) -> list[Correction]:
    patterns = _load_patterns()
    ignore = _RULES_CACHE.get("ignore") or set()
    if not patterns:
        return []
    by_first = _buckets()
    raw: list[tuple[int, int, str, str, str, float]] = []
    i, n = 0, len(text)
    # 书名号前缀计数：O(n) 预计算，命中处 O(1) 判定（原为每命中 O(n) 全扫）
    q_open = q_close = 0   # text[:i] 中的书名号计数（随 i 推进更新）
    # 负向上下文预编译（当前仅 1 条规则，模式均为锚定短语）
    neg_rx: dict[str, list] = {}
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
                # 负向上下文：检查命中词之前的文本（窗口 120 字足够——
                # 模式均为紧邻短语；原实现对全前缀 re.search，O(命中×文本长)）
                neg = neg_rx.get(hit)
                if neg is None:
                    neg = neg_rx[hit] = [re.compile(rx) for rx
                                         in NEGATIVE_CONTEXTS.get(hit, [])]
                if any(rx.search(text[max(0, i - 120):i]) for rx in neg):
                    i += 1
                    continue
                # 书名号内的文件标题按原文引用，不作纠错提示
                if q_open > q_close:
                    i += len(hit)
                    continue
                raw.append((i, i + len(hit), hit, correct, cat, conf))
                i += len(hit)
                continue
        if text[i] == "《":
            q_open += 1
        elif text[i] == "》":
            q_close += 1
        i += 1
    return _suppress_inside_common_words(text, raw)


def _suppress_inside_common_words(
        text: str, raw: list[tuple[int, int, str, str, str, float]]) -> list[Correction]:
    """词边界保护：命中片段两端都不落在分词边界上（即横跨多个常用词的内部），
    判定为词内误报并抑制。例：'安全生产'（分词为 安全|生产）内的 '全生'
    不应报 '全生→全省'；而独立成词的 '布署' 两端均为边界，正常报出。

    低置信命中（如生成的混淆对 0.55）执行更严的门控：
      1) 两端都必须落在分词边界（原规则只要求"非双内"）——
         拦截跨词命中（如 '全厂生产' 分词 全厂|生产，'厂生' 右端在边界）；
      2) 命中不得严格位于任何分词 token 内部；
      3) 例外：命中本身是 jieba 高频词（词频≥30）时退回原规则，
         避免误伤"同为真实词的易混对"（如 度过/渡过 类）；
      4) 追加防线（v1.5.1）：命中若由**≥2 个高频常用字**组成
         （如 这是/没又/住要 —— 生成器的"同音错形"恰好落在常用字上），
         它在任何正常行文中随处可见，属结构性误报，一律抑制。
         实测 seed.db 的 4 万条生成对中有 925 条属此类。
         判据刻意用**逐字词频**而非分词结果：jieba 会把"这是"整体并成
         一个词、把"请驶用"并成一个词，依赖分词边界并不可靠。
         本条只对"低置信且命中非高频词"的命中生效，绝不触碰人工精标对。
    """
    boundaries: set[int] = set()
    inside_mask = bytearray(len(text) + 2)   # 位置 -> 是否严格位于某 token 内部
    has_tokens = False
    # 分词不可用（jieba 未装/词典损坏/运行期异常）时为 None：门控整体**降级为不过滤**。
    # 注意不能退化成空集 —— 空集会让 both_ends 恒为 False，把低置信命中全部吞掉，
    # 与"宁少报不误报的过滤器"这一设计意图正好相反（漏报比误报更不可接受）。
    tokenized: bool | None = None
    if raw:
        try:
            import jieba
            from ..db.tokenize import _ensure_jieba
            _ensure_jieba()
            for _w, a, b in jieba.tokenize(text):
                boundaries.add(a)
                boundaries.add(b)
                for k in range(a + 1, b):
                    inside_mask[k] = 1
                has_tokens = True
            tokenized = has_tokens
        except Exception:
            tokenized = None
    out: list[Correction] = []
    for s, e, hit, correct, cat, conf in raw:
        if tokenized is None:
            # 无分词信息：跳过词边界门控，只保留确定性的结构规则
            if conf < _LOW_CONF and jieba_freq(hit) < 30 \
                    and _is_common_char_sequence(hit):
                continue
            out.append(Correction(s, e, hit, correct, cat, "词库匹配", conf))
            continue
        both_ends = s in boundaries and e in boundaries
        inside = bool(inside_mask[s])
        if conf < _LOW_CONF and jieba_freq(hit) < 30:
            if not (both_ends and not inside):
                continue  # 低置信：跨词/词内命中 -> 误报
            if _is_common_char_sequence(hit):
                continue  # 逐字皆为高频常用字 -> 结构性误报
        else:
            if boundaries and s not in boundaries and e not in boundaries:
                continue  # 两端均在词内部 -> 误报
        out.append(Correction(s, e, hit, correct, cat, "词库匹配", conf))
    return out


_LOW_CONF = 0.7            # 低于此置信度走严格门控
# “高频常用字”门槛（jieba 单字词频）。取值依据实测权衡（语料：本仓库 2331 段
# /5.2 万字正常中文；样本：seed.db 4 万条生成对随机 600 条）：
#     门槛      生成层召回    每万字误报命中
#     关闭       85.3%        26.15
#     5000       70.3%         8.84   <- 采用
#     20000      82.5%        13.84
# 取 5000 的理由：公文篇幅长、以"零误报"为红线，宁可少报也不要在每 300 字
# 就弹一个假建议。**逃生通道**：用户手动添加的词条置信度会被抬到 0.99，
# 永不进入本门控——任何被误抑制的真错，用户加一次即永久生效。
_COMMON_CHAR_FREQ = 5000


def _is_common_char_sequence(hit: str) -> bool:
    """命中是否由 ≥2 个高频常用字逐一组成（结构性误报的特征）。

    例：'这是'(这 261791 / 是 796991)、'没又'、'住要' → True；
        '驶用'(驶 422) → False —— 真错别字得以保留。
    """
    if len(hit) < 2:
        return False
    return all(jieba_freq(ch) >= _COMMON_CHAR_FREQ for ch in hit)


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


# ------------------------------------------------------------------ 纠错标记（增强）
# 类别 -> (标记底色, 标记文字色)。中间调配色，深浅色主题下均可读。
_MARK_STYLE = {
    "错别字": ("#fdecea", "#8c1d18"),
    "易混词": ("#fff3e0", "#8a5300"),
    "标点": ("#e8eaf6", "#283593"),
    "数字用法": ("#e0f2f1", "#004d40"),
    "用户词库": ("#fdecea", "#8c1d18"),
    "神经纠错": ("#ede7f6", "#4527a0"),   # L4 神经精排（可选增强包）
    "语法纠错": ("#e3f2fd", "#0d47a1"),   # L5 Seq2Seq 语法纠错（可选增强包）
}
_MARK_FALLBACK = ("#eceff1", "#37474f")


def to_marked_html(text: str, corrections: list[Correction],
                   show_suggestion: bool = True,
                   anchor_prefix: str = "mk") -> str:
    """把原文渲染成带纠错标记的 HTML（QTextBrowser 直接 setHtml 可用）。

    每处错误按类别底色高亮 + 下划线 + 命名锚点（{anchor_prefix}{序号}，
    供 scrollToAnchor 定位），后随灰色〔建议〕；悬停显示完整说明。
    文本一律 HTML 转义，换行转 <br>。重叠区间（理论已被 _dedupe 消除）
    做跳过防御，绝不重复渲染。
    """
    import html as _html
    parts: list[str] = []
    pos = 0
    ordered = sorted(corrections, key=lambda c: (c.start, -(c.end - c.start)))
    for i, c in enumerate(ordered):
        if c.start < pos:
            continue
        if c.end <= c.start or c.end > len(text):
            continue
        parts.append(_html.escape(text[pos:c.start]).replace("\n", "<br>"))
        bg, fg = _MARK_STYLE.get(c.category, _MARK_FALLBACK)
        tip = _html.escape(f"{c.label}（{c.category}，置信度 {c.confidence:.2f}）")
        sug = (f'<span style="color:{_MARK_FALLBACK[1]};">'
               f'〔{_html.escape(c.suggestion)}〕</span>') if show_suggestion else ""
        parts.append(
            f'<a name="{anchor_prefix}{i}"></a>'
            f'<span style="background-color:{bg};color:{fg};'
            f'text-decoration:underline;" title="{tip}">'
            f'{_html.escape(c.wrong)}</span>{sug}')
        pos = c.end
    parts.append(_html.escape(text[pos:]).replace("\n", "<br>"))
    return "".join(parts)


def paragraph_no(text: str, pos: int) -> int:
    """pos 落在第几段（按换行计数，1 起）。"""
    return text.count("\n", 0, max(0, min(pos, len(text)))) + 1


def correct_block(text: str, skip_categories: "set[str] | tuple" = ("数字用法",)
                  ) -> "tuple[str, list[Correction]]":
    """单个文本块（段落/标题/单元格）的纠错：返回 (修正后文本, 命中明细)。

    偏移为块内局部偏移。默认跳过"数字用法"类——与纠错面板的口径一致
    （该类多为提示性规范建议，批量替换容易把编号、日期改坏）。
    """
    cs = [c for c in check_text(text) if c.category not in skip_categories]
    out = text
    for c in sorted(cs, key=lambda c: -c.start):
        out = out[:c.start] + c.suggestion + out[c.end:]
    return out, cs


def tree_to_blocks(tree) -> "list[dict]":
    """DocTree -> 轻量块列表（kind: heading/para/table），供任意文档纠错逐块处理。

    块里必须带上标题层级 ``level``：老实现只存 kind/text/rows，
    blocks_to_tree 只能给所有标题填 level=1，用户把一篇三级标题的公文
    纠错后导出，层级信息**不可逆丢失** —— docxgen 全部用 Heading 1，
    目录（TOC \\o "1-3"）与页码定位随之全错。level 只对 heading 有意义，
    para/table 存 0 即可（blocks_to_tree 只在 heading 分支读它）。
    """
    from .model import HEADING, TABLE
    # 标题不折进块列表：blocks_to_tree 的 title 是独立入参，
    # 折叠会导致导出时标题渲染两遍（DocTree.title + 正文 Heading1）。
    blocks = []
    for b in tree.blocks:
        if b.type == TABLE and b.rows:
            blocks.append({"kind": "table", "text": "", "rows": [list(r) for r in b.rows],
                           "level": 0})
        else:
            blocks.append({"kind": "heading" if b.type == HEADING else "para",
                           "text": b.text or "", "rows": None,
                           "level": int(b.level or 0) if b.type == HEADING else 0})
    return blocks


def blocks_to_tree(title: str, blocks) -> "object":
    """块列表 -> DocTree（表格行转 TABLE 块；标题沿用块内记录的层级）。"""
    from .model import Block, DocTree, HEADING, PARAGRAPH, TABLE
    out: list[Block] = []
    for b in blocks:
        if b["kind"] == "table" and b.get("rows"):
            out.append(Block(type=TABLE, rows=[list(r) for r in b["rows"]]))
        elif b["kind"] == "heading" and (b.get("text") or "").strip():
            # 缺 level（旧快照/外部构造的块）时退回 1 级，不让导出直接失败；
            # 上限 4 与 GB/T 9704 的标题层级一致，防止脏数据造出 Heading 7。
            level = int(b.get("level") or 1)
            out.append(Block(type=HEADING, level=max(1, min(level, 4)), text=b["text"]))
        elif (b.get("text") or "").strip():
            out.append(Block(type=PARAGRAPH, text=b["text"]))
    return DocTree(title=title or "", blocks=out)
