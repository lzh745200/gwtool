# -*- coding: utf-8 -*-
"""重复字检测：同字极大连 ``c^k`` 的规则化判定。

背景
----
中文里"同字两连/多连"既可能是叠词（爸爸、高高兴兴、红彤彤）或跨词边界的
自然重合（中国|国际、奇数页|页码），也可能是实实在在的多打一字（的的、
进行行）。朴素 ``(.)\1+`` 在 CC-CEDICT 全量词典上误报 1475 条（1.23%），
因此本模块**先放行后报警**：只有当一个同字连在所有解释规则下都无法被
合理解释时，才判定为疑似重复字。

判定单位是**同字极大连** ``c^k``（k≥2）。对每个连按下列顺序**短路**判定：

  ① AABB / ABAB  —— 四字窗口中"两两成对且成对的两字不同" → 放行；
  ② 词典词内     —— 本地下文存在长度 > k 的词典词完整覆盖该连 → 放行；
  ③ k ≥ 3        —— 连本身是词典词 → 放行；否则是拟声字 → 0.5；其余 → 0.9；
  ④ 两连是词典词 —— ``c²`` 本身是词 → 放行（爸爸、猩猩、等等…）；
  ⑤ 跨词边界（k=2）：
       ⑤a 左有词（以连首字结尾且左伸）且有词（以连第二字起始且右伸）→ 放行；
       ⑤b 仅左有词：连第二字之后是标点/EOS → 0.85；否则（自然延伸）→ 放行；
       ⑤c 仅右有词：连处于段首/标点后 → 0.85；否则（自然重合）→ 放行；
  ⑥ 虚词重复     —— ``c`` 是虚词 → 0.9；
  ⑦ 叠字白名单   —— ``c`` 出现在任一词典词的"同字两连"位置 → 放行；
  ⑧ 兜底         —— 无任何解释 → 0.5。

⑤b/⑤c 的**位置守卫**是精髓：``进行行``（后接标点）报，``直接接时间``
（"接"自然延伸）放行；``我我们``（段首）报，``奇数页|页码``（前有汉字）
放行。

另设**间隔重复**（cXc）专门通道：仅当"间隔恰为 1 字 + 中间字为虚词 +
删一字后落回词典 + 原形式不在词典"**四条全中**时给出恒为 0.5 的"仅提示"
（如 一对一/不得不 等 75 个词典词是合法的，故只能提示不能确认）。
"""
from __future__ import annotations

from .corrector import Correction

__all__ = ["check_repeat", "invalidate_cache"]

# 上下文缓存：词集合 / 最大词长 / 叠字白名单。随词典整体变化而失效。
_CACHE: dict = {}
# 词典窗口上限：真实词典最长词 8 字，留足余量并给 ② 的窗口扫描封顶。
_MAX_WIN = 16

# ⑥ 虚词集（用于"虚词重复"与 cXc 的"中间字须为虚词"两处判据）。
# 收窄为真正的高频虚词，避免把"如果/所以"一类实义字误纳（如 果、所）。
# 刻意不含"等"——"等等"是词典里合法的常用词，由 ④ 放行，这里也不把它当虚词。
FUNCTION_CHARS = frozenset(
    "的了是在和与及或也就而之其这那着过把被为并且则且兮矣吧吗呢啊嘛"
    "于因为对向从而"
)

# ③ 拟声字集：仅用于 k≥3 时把"哈哈哈/呵呵呵"一类拟声重复降为灰档。
# 这是一个**语言类别小集合**（非词表），不参与词典放行。
ONO_CHARS = frozenset(
    "哈呵嘻嘿呼轰哗嘟咕咚咔叽唧嘀滴嘭砰啪唰嗖呜哇咩喵汪呱咯嗷吼嗡咪"
    "吱嘎嘁嚓嘘砰"
)


def invalidate_cache() -> None:
    """词典变化后调用：清空派生缓存，下次判定重新派生。"""
    _CACHE.clear()


def _context() -> dict:
    """运行期从词典派生并缓存 {words, maxlen, white}。

    - ``words``   ：全部唯一词条集合，O(1) 判词；
    - ``maxlen``  ：最大词长（封顶 ``_MAX_WIN``），决定 ② 的扫描窗口；
    - ``white``   ：叠字白名单——出现在任一词典词"同字两连"位置的字。
    """
    ctx = _CACHE.get("ctx")
    if ctx is not None:
        return ctx
    from ..db import dao
    try:
        words = dao.all_dictionary_words()
    except Exception:
        words = []
    word_set = set(words)
    maxlen = 1
    white: set = set()
    for w in words:
        if len(w) > maxlen:
            maxlen = len(w)
        prev = ""
        for ch in w:
            if ch == prev:
                white.add(ch)
            prev = ch
    if maxlen > _MAX_WIN:
        maxlen = _MAX_WIN
    ctx = {"words": word_set, "maxlen": maxlen, "white": white}
    _CACHE["ctx"] = ctx
    return ctx


# ------------------------------------------------------------------ 判定
def _is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


# 书名号/引号配对（与 csc_gec._QUOTE_PAIRS 一致）：其内为原文引用，绝不纠。
_QUOTE_OPEN = {"“": "”", "‘": "’", "《": "》", "〈": "〉",
               "【": "】", "「": "」", "『": "』", "〔": "〕"}
_QUOTE_CLOSE = frozenset(_QUOTE_OPEN.values())


def _protected_mask(text: str) -> bytearray:
    """标记"书名号/引号内"的字符位（1 = 受保护，不参与重复字判定）。

    单趟 O(n)：用栈跟踪未闭合的成对符号，未闭合的起始符号一直保护到串尾。
    """
    mask = bytearray(len(text))
    stack: list[int] = []
    for i, ch in enumerate(text):
        if ch in _QUOTE_OPEN:
            stack.append(i)
            mask[i] = 1
        elif ch in _QUOTE_CLOSE:
            mask[i] = 1
            if stack:
                stack.pop()
        elif stack:
            mask[i] = 1
    return mask


def _iter_runs(text: str):
    """产出所有**汉字**同字极大连 (start, length, char)，仅 length≥2。

    只考察汉字：ASCII/数字/空白/标点（含 markdown 的 ``**``、``--``、``###``）
    的重复由既有标点规则负责，此处绝不介入，否则代码/表格/排版符号会大面积误报。
    """
    n = len(text)
    i = 0
    while i < n:
        c = text[i]
        if not _is_cjk(c):
            i += 1
            continue
        j = i + 1
        while j < n and text[j] == c:
            j += 1
        if j - i >= 2:
            yield i, j - i, c
        i = j


def _aabb_or_abab(text: str, i: int) -> bool:
    """① 四字窗口 w0w1w2w3：AABB(w0==w1≠w2==w3) 或 ABAB(w0w1==w2w3≠w0w1 形态)。

    仅在连首或连尾对齐的两个窗口上检查。ABAB 分支带"两字不同"守卫，
    故对同字连恒不成立（同字连纳入 ABAB 只会得到 c^4，属真重复）。
    """
    n = len(text)
    for s in (i, i - 2):
        if s < 0 or s + 4 > n:
            continue
        w0, w1, w2, w3 = text[s], text[s + 1], text[s + 2], text[s + 3]
        if w0 == w1 and w2 == w3 and w0 != w2:      # AABB
            return True
        if w0 + w1 == w2 + w3 and w0 != w1:          # ABAB（两字不同）
            return True
    return False


def _covered_by_longer_word(text: str, i: int, k: int, ctx: dict) -> bool:
    """② 是否存在长度 > k 的词典词完整覆盖区间 [i, i+k)。"""
    words: set = ctx["words"]
    maxlen: int = ctx["maxlen"]
    n = len(text)
    lo = max(0, i - maxlen + 1)
    for a in range(lo, i + 1):
        hi = min(n, a + maxlen)
        for b in range(a + k + 1, hi + 1):
            if text[a:b] in words:
                return True
    return False


def _left_word_exists(text: str, i: int, ctx: dict) -> bool:
    """⑤ 左侧证据：存在以连首字（下标 i）结尾、且左伸（长度≥2）的词典词。"""
    words: set = ctx["words"]
    maxlen: int = ctx["maxlen"]
    end = i + 1
    lo = max(0, end - maxlen)
    for a in range(lo, i):            # a ≤ i-1 ⇒ 长度≥2 且左伸
        if text[a:end] in words:
            return True
    return False


def _right_word_exists(text: str, i: int, ctx: dict) -> bool:
    """⑤ 右侧证据：存在以连第二字（下标 i+1）起始、且右伸（长度≥2）的词典词。"""
    words: set = ctx["words"]
    maxlen: int = ctx["maxlen"]
    start = i + 1
    hi = min(len(text), start + maxlen)
    for b in range(start + 2, hi + 1):   # b ≥ start+2 ⇒ 长度≥2 且右伸
        if text[start:b] in words:
            return True
    return False


def _trailing_boundary(text: str, p: int) -> bool:
    """连第二字之后是否为标点/EOS（非汉字即视为边界）。"""
    return p >= len(text) or not _is_cjk(text[p])


def _leading_boundary(text: str, i: int) -> bool:
    """连是否处于段首/标点后（前一字非汉字或不存在）。"""
    return i == 0 or not _is_cjk(text[i - 1])


def _judge_run(text: str, i: int, k: int, c: str, ctx: dict):
    """对单个同字连按序短路判定；返回 None（放行）或置信度 float（报告）。"""
    # ①② 放行类
    if _aabb_or_abab(text, i):
        return None
    if _covered_by_longer_word(text, i, k, ctx):
        return None
    # ③ k ≥ 3
    if k >= 3:
        if text[i:i + k] in ctx["words"]:
            return None
        if c in ONO_CHARS:
            return 0.5
        return 0.9
    # 此处 k == 2
    # ④ 两连本身是词
    if text[i:i + 2] in ctx["words"]:
        return None
    # ⑤ 跨词边界
    left = _left_word_exists(text, i, ctx)
    right = _right_word_exists(text, i, ctx)
    if left and right:
        return None                        # ⑤a
    if left:                               # ⑤b 仅左证据
        return 0.85 if _trailing_boundary(text, i + 2) else None
    if right:                              # ⑤c 仅右证据
        return 0.85 if _leading_boundary(text, i) else None
    # ⑥ 虚词重复
    if c in FUNCTION_CHARS:
        return 0.9
    # ⑦ 叠字白名单
    if c in ctx["white"]:
        return None
    # ⑧ 兜底
    return 0.5


def _reason_for(k: int, c: str, conf: float) -> str:
    if k >= 3:
        return f"“{c}”连续重复 {k} 次，疑似多打"
    if conf >= 0.9:
        return f"虚词“{c}”重复，疑似多打一字"
    if conf >= 0.85:
        return f"疑似多打了“{c}”，请核对"
    return "疑似重复字符"


def _check_runs(text: str, ctx: dict) -> list[Correction]:
    out: list[Correction] = []
    for i, k, c in _iter_runs(text):
        conf = _judge_run(text, i, k, c, ctx)
        if conf is None:
            continue
        out.append(Correction(
            start=i, end=i + k, wrong=text[i:i + k], suggestion=c,
            category="重复字", reason=_reason_for(k, c, conf),
            confidence=conf, kind="delete"))
    return out


# ------------------------------------------------------------------ cXc 间隔重复
def _segmentable(s: str, words: set) -> bool:
    """s 能否被切成若干词典词（覆盖整串，至少一段）。"""
    n = len(s)
    if n == 0:
        return False
    reach = [False] * (n + 1)
    reach[0] = True
    for i in range(n):
        if not reach[i]:
            continue
        for j in range(i + 1, n + 1):
            if s[i:j] in words:
                reach[j] = True
    return reach[n]


def _check_interval_repeat(text: str, ctx: dict) -> list[Correction]:
    """cXc 间隔重复：四条全中才报，且置信度**恒为 0.5**（仅提示、不进确认错）。

    条件：① 间隔恰 1 字（cXc）；② c 是虚词；③ 删去一字后落回词典
    （或可切成词典词）；④ 原形式 cXc 不在词典里。

    额外护栏（与 ② 同源）：cXc 若被**更长的词典词**完整覆盖则放行——
    否则"如火如荼、所作所为、得过且过、一了百了"等成语会被 3 字窗口误命中。
    """
    out: list[Correction] = []
    words: set = ctx["words"]
    n = len(text)
    i = 0
    while i + 2 < n:
        c = text[i]
        if c in FUNCTION_CHARS and text[i] == text[i + 2] and text[i] != text[i + 1]:
            # 极大化：避免与同字连、更长的间隔重复重叠
            if (i == 0 or text[i - 1] != c) and (i + 3 >= n or text[i + 3] != c):
                mid = text[i + 1]
                if _is_cjk(c) and _is_cjk(mid) and not \
                        _covered_by_longer_word(text, i, 3, ctx):
                    form = text[i:i + 3]
                    keep_left = text[i:i + 2]       # cX（删右侧 c）
                    keep_right = text[i + 1:i + 3]  # Xc（删左侧 c）
                    deletable = (keep_right in words or keep_left in words
                                 or _segmentable(keep_right, words)
                                 or _segmentable(keep_left, words))
                    if deletable and form not in words:
                        suggestion = keep_left if keep_left in words else keep_right
                        out.append(Correction(
                            start=i, end=i + 3, wrong=form, suggestion=suggestion,
                            category="重复字",
                            reason="疑似间隔重复（保留一字）", confidence=0.5,
                            kind="delete"))
                        i += 3
                        continue
        i += 1
    return out


def check_repeat(text: str) -> list[Correction]:
    """重复字检测主入口：返回全部命中（含 确认错 0.9/0.85 与 仅提示 0.5）。"""
    if not text:
        return []
    ctx = _context()
    if not ctx["words"]:
        # 词典不可用：无法做放行判定，宁少报不误报——直接不报。
        return []
    mask = _protected_mask(text)
    hits = _check_runs(text, ctx) + _check_interval_repeat(text, ctx)
    return [c for c in hits if not mask[c.start]]
