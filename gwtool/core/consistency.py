# -*- coding: utf-8 -*-
"""文内一致性校验：同一份公文里同一实体的两种写法并存。

为什么必须单独一层：五层纠错引擎（corrector / grammar_rules / csc_*）逐词逐句
判断「这一处写错了没有」，天然看不到全文矛盾。而「3个工作日」与「三个工作日」、
「2026年10月3日」与「2026.10.3」、「公里」与「千米」、机构新旧名称并存，
**每一处单看都完全合法**，只有横向比对才能发现。这类问题在公文校对里最痛：
作者自己通读一遍也看不出来，却是最容易被上级挑出来的毛病。

设计约束（改动前请先读）：
  * **只提示、不改正文**：不产生任何替换文本，避免被「批量写回 / 整篇应用」当成
    替换建议写进正文（本项目已被提示级标签误写回坑过，见 corrector.ADVISORY_CATEGORIES）。
  * **精度优先、宁少勿滥**：每一类都要求「同一实体 + 两种写法」同时成立；
    只有一种写法、数值不同、只出现一次的文本必须零命中（见 tests 反例）。
    宁可漏报十处，不可误报一处——误报会让用户永久关掉这个功能。
  * **纯函数**：除静态导入 corrector_data 的机构沿革表外不依赖本项目任何模块
    （不 import logs，避免循环依赖与日志副作用），不读写文件/网络/DB，
    不修改入参，可重复调用（幂等）。
  * **异常降级为少报而不是崩溃**：单类检测内部异常只跳过该类；整篇异常返回 []。
    校对是 UI 主线程上的辅助能力，抛异常比少报一条严重得多。

positions 语义：该条结论涉及的全部写法在全文中的起始偏移（升序、去重，
最多 _MAX_POSITIONS 个），供 UI 逐处高亮。故意不给字符区间：同一实体可能有
两种以上写法，「一个区间」表达不了。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .corrector_data import ORG_RENAME_PAIRS

__all__ = ["Inconsistency", "check"]

# 单类最多报多少条：全文有 200 处数量写法不一致时，用户真正要的是「去看一眼」，
# 不是 200 条刷屏。截断按首次出现位置，保证结果确定。
_MAX_PER_KIND = 20
# 单条结论最多带多少个位置，避免长文里 positions 无限膨胀。
_MAX_POSITIONS = 50


@dataclass
class Inconsistency:
    """一条「文内写法不一致」结论（提示级，不含替换文本）。"""

    kind: str        # "数字" | "日期" | "单位" | "机构名称" | "标点形式"
    label: str       # 一行用户可读结论
    detail: str      # 一句「为什么 + 怎么改」
    first: str       # 先出现的写法
    variant: str     # 另一种写法
    positions: list[int] = field(default_factory=list)


# ============================================================ 通用小工具
def _find_all(text: str, needle: str) -> list[int]:
    """needle 在 text 中所有出现位置（允许重叠，机构名做包含判断需要）。"""
    out: list[int] = []
    start = 0
    while True:
        i = text.find(needle, start)
        if i < 0:
            return out
        out.append(i)
        start = i + 1


def _unique_positions(positions) -> list[int]:
    """升序、去重、截断——positions 的统一收口，保证 UI 侧可以无条件信任。"""
    seen = set()
    out: list[int] = []
    for p in sorted(positions):
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
        if len(out) >= _MAX_POSITIONS:
            break
    return out


def _make(kind: str, label: str, detail: str, first: str, variant: str,
          positions) -> Inconsistency:
    return Inconsistency(kind=kind, label=label, detail=detail,
                         first=first, variant=variant,
                         positions=_unique_positions(positions))


# ============================================================ 一、数字 / 数量
# ---------------------------------------------------------------- 汉字数字
_CN_DIGIT: dict[str, int] = {
    "〇": 0, "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_SMALL_UNIT: dict[str, int] = {"十": 10, "百": 100, "千": 1000}
_CN_BIG_UNIT: dict[str, int] = {"万": 10000, "亿": 100000000}
_CN_NUMERAL_CHARS = "".join(_CN_DIGIT) + "".join(_CN_SMALL_UNIT) + "".join(_CN_BIG_UNIT)


def _cn_int(token: str) -> int | None:
    """汉字整数 → int；无法可靠解释的写法返回 None（宁可不报）。

    支持「十二 / 二十 / 一百零五 / 三万 / 二〇二六」这类公文常见写法。
    刻意不追求 亿 以上的严格分段语义：算错只会漏报（数值对不上），不会误报。
    """
    if not token:
        return None
    # 逐位数字写法（年份「二〇二六」即属此类）
    if all(ch in _CN_DIGIT for ch in token):
        return int("".join(str(_CN_DIGIT[ch]) for ch in token))
    total = 0
    section = 0
    number = 0
    seen = False
    for ch in token:
        if ch in _CN_DIGIT:
            number = _CN_DIGIT[ch]
            seen = True
        elif ch in _CN_SMALL_UNIT:
            unit = _CN_SMALL_UNIT[ch]
            if not seen:
                # 「十二」开头的十：前面没有数字，按 1 计
                number = 1
            section += number * unit
            number = 0
            seen = True
        elif ch == "万":
            section += number
            number = 0
            total += (section or 1) * _CN_BIG_UNIT["万"]
            section = 0
            seen = True
        elif ch == "亿":
            section += number
            number = 0
            total = (total + section) * _CN_BIG_UNIT["亿"]
            section = 0
            seen = True
        else:
            return None
    return total + section + number


def _cn_number(token: str) -> float | None:
    """汉字数字 → float（支持「三点五」这类带小数点的写法）。"""
    if not token:
        return None
    if "点" in token:
        int_part, _, frac = token.partition("点")
        if not frac or not all(ch in _CN_DIGIT for ch in frac):
            return None
        base = _cn_int(int_part) if int_part else 0
        if base is None:
            return None
        return float(f"{base}.{''.join(str(_CN_DIGIT[ch]) for ch in frac)}")
    return _cn_int(token)


# ---------------------------------------------------------------- 数量单位
# 只收「公文里可计量、且阿拉伯数字写法合规」的单位/量词。
# **刻意不收「年 / 月 / 日」**：那是日期域的坑（2026年10月3日 与 3个月 必须分开判），
# 混进来会把日期当数量、把数量当日期，两边都误报。
_QUANTITY_UNITS: tuple[str, ...] = (
    "个工作日", "个自然日", "个百分点", "个星期",
    "工作日", "自然日", "百分点", "星期", "个月", "季度",
    "小时", "分钟", "人次", "万元", "亿元", "平方公里",
    "平方米", "立方米", "公里", "千米", "公斤", "千克",
    "厘米", "毫米", "毫升", "公顷", "元", "吨", "天", "周",
    "秒", "米", "升", "克", "斤", "亩", "台", "套", "辆",
    "艘", "架", "件", "项", "条", "页", "篇", "份", "次",
    "批", "笔", "轮", "家", "户", "人", "名", "位", "所",
    "门", "期", "届", "倍", "个",
)
# 万元 / 亿元 归一化到「元」再比较，否则「3万元」与「三万元」永远对不上：
# 前者是 (3, 万元)，后者是 (30000, 元)。
_MAG_UNIT_BASE: dict[str, tuple[str, float]] = {
    "万元": ("元", 10000.0),
    "亿元": ("元", 100000000.0),
}
_MAG_VALUE: dict[str, float] = {"万": 10000.0, "亿": 100000000.0}

# 汉字数字紧跟在「第 / 其 / 同 / 单」之后时是序数或固定词的一部分，不是「同一数量」，
# 一律不猜：「第一项」「同一项目」「单一项目」里的「一」都不是一个计数。
# 不再扩大这个集合：加「另 / 各 / 划」会把「另三个工作日」「各三项」「计划一天」
# 这类真实数量一起挡掉（此前的 另 就漏掉了「另三个工作日」，由 tests 锁住）。
_CN_BLOCK_PREFIX = frozenset("第其同单")
# 数量后紧跟「多/余/来」是概数（「一个多小时」「五十多人」），不是确定数量，
# 拿去和「1 个」比属于跨语义误报。
_APPROX_SUFFIX = frozenset("多余来")
# 区间上限（「3—5个工作日」「1-2 个」「三至五个」）里的数字不是独立数量，
# 公文里区间写法极多，不排除会把「五个」和区间尾巴「5个」凑成一条误报。
_RANGE_SEP = frozenset("-–—~～至到")


def _is_range_tail(text: str, start: int) -> bool:
    """start 处的数字是否是「A—B」里的上限 B（A 必须是数字/汉字数字）。"""
    i = start - 1
    while i >= 0 and text[i] in " \u3000":
        i -= 1
    if i < 0 or text[i] not in _RANGE_SEP:
        return False
    j = i - 1
    while j >= 0 and text[j] in " \u3000":
        j -= 1
    return j >= 0 and (text[j].isdigit() or text[j] in _CN_NUMERAL_CHARS)


_UNIT_ALTERNATION = "|".join(_QUANTITY_UNITS)
# 阿拉伯数字侧的三重门槛：
#   (?<![\d.０-９A-Za-z]) —— 不能是更长数字/版本号的一部分（v1.2、P2 期、3.5.1）
#   (?<!第)             —— 序数「第3项」不参与数量比对（与汉字侧对称）
#   (?<!第 )            —— 容忍「第 8 项」这种带空格写法
_QTY_RE = re.compile(
    r"(?<![\d.０-９A-Za-z])(?<!第)(?<!第 )"
    r"(?P<anum>\d+(?:\.\d+)?)\s*(?P<amag>[万亿])?\s*"
    r"(?P<aunit>" + _UNIT_ALTERNATION + r")"
    r"|(?P<cnum>[〇零一二两三四五六七八九十百千万亿]+"
    r"(?:点[〇零一二三四五六七八九]+)?)"
    r"(?P<cunit>" + _UNIT_ALTERNATION + r")"
)


def _cn_quantity_value(token: str) -> float | None:
    """把「汉字数量」解析成数值；不确定的写法返回 None。

    例外：「三五天」「一二个」这类相邻数字表示概数，本身不是精确数量，
    归一化后拿去和「35天」比必然误报，直接放弃。
    """
    if "点" not in token and len(token) > 1:
        if not any(ch in _CN_SMALL_UNIT or ch in _CN_BIG_UNIT for ch in token):
            return None
    return _cn_number(token)


def _check_numbers(text: str) -> list[Inconsistency]:
    """同一「单位 + 归一化数值」在同一文本里既有阿拉伯数字又有汉字数字写法。"""
    # key: (单位基底, 数值) -> [(start, 写法, 书写体系)]
    buckets: dict[tuple[str, float], list[tuple[int, str, str]]] = {}
    for m in _QTY_RE.finditer(text):
        tail = text[m.end()] if m.end() < len(text) else ""
        if tail in _APPROX_SUFFIX:
            continue  # 概数不算确定数量
        if _is_range_tail(text, m.start()):
            continue  # 区间上限不算独立数量
        if m.group("anum") is not None:
            raw = m.group("anum")
            try:
                value = float(raw)
            except ValueError:  # 理论上不会发生，防御一下
                continue
            mag = m.group("amag") or ""
            unit = m.group("aunit")
            value *= _MAG_VALUE.get(mag, 1.0)
            style = "阿拉伯数字"
            writing = m.group(0)  # 原文片段，保证是 text 的子串，UI 可直接高亮
        else:
            token = m.group("cnum")
            unit = m.group("cunit")
            start = m.start()
            if start > 0 and text[start - 1] in _CN_BLOCK_PREFIX:
                continue
            value = _cn_quantity_value(token)
            if value is None:
                continue
            style = "汉字数字"
            writing = m.group(0)
        base_unit, mult = _MAG_UNIT_BASE.get(unit, (unit, 1.0))
        value *= mult
        buckets.setdefault((base_unit, round(value, 6)), []).append(
            (m.start(), writing, style))

    findings: list[Inconsistency] = []
    for (unit, _value), occ in buckets.items():
        occ.sort()
        styles = {o[2] for o in occ}
        if len(styles) < 2:
            continue  # 只有一种书写体系 → 不是问题
        first_occ = occ[0]
        variant_occ = next(o for o in occ if o[2] != first_occ[2])
        findings.append(_make(
            "数字",
            f"同一数量写作不一致：{first_occ[1]} / {variant_occ[1]}",
            f"「{unit}」的同一数值在全文既有阿拉伯数字又有汉字数字写法，"
            f"建议按 GB/T 15835 统一（一般计量、统计数字用阿拉伯数字）。",
            first_occ[1], variant_occ[1], [o[0] for o in occ]))
    findings.sort(key=lambda f: f.positions[0])
    return findings[:_MAX_PER_KIND]


# ============================================================ 二、日期
# 五套格式：汉字日期 / 数字+年月日 / 短横线 / 斜线 / 点号。
# 先各自识别，再把「归一化到同一 (年,月,日)」的归为一组，格式不同才报。
_DATE_CN_RE = re.compile(
    r"(?P<y>[〇零一二三四五六七八九]{4})年"
    r"(?P<m>[一二三四五六七八九十]{1,2})月"
    r"(?:(?P<d>[一二三四五六七八九十]{1,3})[日号])?"
)
_DATE_YUE_RE = re.compile(
    r"(?<!\d)(?P<y>\d{4})年(?P<m>\d{1,2})月(?:(?P<d>\d{1,2})[日号])?"
)
_DATE_DASH_RE = re.compile(
    r"(?<![\d.])(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})(?![\d.])"
)
_DATE_SLASH_RE = re.compile(
    r"(?<![\d.])(?P<y>\d{4})/(?P<m>\d{1,2})/(?P<d>\d{1,2})(?![\d.])"
)
_DATE_DOT_RE = re.compile(
    r"(?<![\d.])(?P<y>\d{4})[.．](?P<m>\d{1,2})[.．](?P<d>\d{1,2})(?![\d.])"
)
_DATE_STYLES = (
    (_DATE_CN_RE, "汉字日期"),
    (_DATE_YUE_RE, "年月日式"),
    (_DATE_DASH_RE, "短横线式"),
    (_DATE_SLASH_RE, "斜线式"),
    (_DATE_DOT_RE, "点号式"),
)


def _valid_date(year: int, month: int, day: int) -> bool:
    """只做粗略合法性校验：挡掉版本号/编号（如 2026.99.99）冒充日期。"""
    return 1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31


def _to_int(token: str) -> int | None:
    """数字串或汉字数字 → int（日期各段两种写法都要吃）。"""
    if token.isdigit():
        return int(token)
    return _cn_int(token)


def _collect_dates(text: str) -> list[tuple[int, str, tuple[int, int, int], str]]:
    """-> [(start, 原文, (年,月,日), 格式名)]；日期精度不足时日=0。"""
    found: list[tuple[int, str, tuple[int, int, int], str]] = []
    for pattern, style in _DATE_STYLES:
        for m in pattern.finditer(text):
            year = _to_int(m.group("y"))
            month = _to_int(m.group("m"))
            day_group = m.groupdict().get("d")
            day = _to_int(day_group) if day_group else 0
            if year is None or month is None or day is None:
                continue
            if not _valid_date(year, month, day or 1):
                continue
            found.append((m.start(), m.group(0), (year, month, day), style))
    # 同一位置可能被多套正则命中（理论上不会），保留最长的一条
    found.sort(key=lambda item: (item[0], -len(item[1])))
    merged: list[tuple[int, str, tuple[int, int, int], str]] = []
    for item in found:
        if merged and item[0] < merged[-1][0] + len(merged[-1][1]):
            continue
        merged.append(item)
    return merged


# 年份简写：只有「看起来真的是年份」的两位数才猜，避免把「成立26年」当时简写。
_SHORT_YEAR_DATE_RE = re.compile(
    r"(?<![\d.年])(?P<y>\d{2})年(?P<m>\d{1,2})月(?:(?P<d>\d{1,2})[日号])?"
)
_FULL_YEAR_RE = re.compile(r"(?<!\d)(?P<y>(?:19|20)\d{2})年")
_SHORT_YEAR_RE = re.compile(r"(?<![\d.年])(?P<y>\d{2})年")
# 年份简写的合法性信号：前面是介词/时间介词，或后面接「度/初/底/末」等。
# **不含句读与「前后」**：中文里「成立26年，」「工作26年以来」「26年前」和
# 「于26年，」长得一模一样，靠逗号/邻居字判不出来；宁可漏掉孤立年份，
# 也不要把时长误报成简写。
_SHORT_YEAR_PREFIX = frozenset("于在自至从到即系现按据由与同对向截止")
_SHORT_YEAR_SUFFIX = frozenset("度初底末）")


def _check_dates(text: str) -> list[Inconsistency]:
    dates = _collect_dates(text)
    findings: list[Inconsistency] = []

    # ---- a) 同一日期两种格式 ----
    by_canon: dict[tuple[int, int, int], list[tuple[int, str, str]]] = {}
    for start, surface, canon, style in dates:
        by_canon.setdefault(canon, []).append((start, surface, style))
    for _canon, occ in by_canon.items():
        occ.sort()
        styles = {o[2] for o in occ}
        if len(styles) < 2:
            continue
        first_occ = occ[0]
        variant_occ = next(o for o in occ if o[2] != first_occ[2])
        findings.append(_make(
            "日期",
            f"同一日期写法不一致：{first_occ[1]} / {variant_occ[1]}",
            "同一个日期在全文出现了两种格式，建议统一为「2026年10月3日」"
            "一种写法（GB/T 9704 公文格式）。",
            first_occ[1], variant_occ[1], [o[0] for o in occ]))

    # ---- b) 年份简写与全写混用 ----
    full_years: dict[int, list[int]] = {}
    for m in _FULL_YEAR_RE.finditer(text):
        full_years.setdefault(int(m.group("y")), []).append(m.start())
    if not full_years:
        return findings
    # 只有 20xx 的全写年份才接受两位数简写（「26年」在公文里指 2026 年；
    # 若写成 1926 年，配对 26年 只会有害）
    short_full = {y % 100: y for y in full_years if 2000 <= y <= 2099}

    # 汇总「哪一年的简写」：同一年份的简写只报一条（同一实体只报一次），
    # 简写出现在完整日期里（26年10月）还是独立出现（于26年）都归到同一条。
    short_occ: dict[int, list[tuple[int, str]]] = {}
    date_like_starts: set[int] = set()
    # b1) 年份简写出现在完整日期里（26年10月3日）——最可靠的一种
    for m in _SHORT_YEAR_DATE_RE.finditer(text):
        full = short_full.get(int(m.group("y")))
        if full is None:
            continue
        date_like_starts.add(m.start())
        short_occ.setdefault(full, []).append((m.start(), m.group(0)))
    # b2) 独立的两位年份（于26年 / 26年度）——靠前后文判定，不猜时长
    for m in _SHORT_YEAR_RE.finditer(text):
        if m.start() in date_like_starts:
            continue
        full = short_full.get(int(m.group("y")))
        if full is None:
            continue
        before = text[m.start() - 1] if m.start() > 0 else ""
        end = m.end()
        after = text[end] if end < len(text) else ""
        if before not in _SHORT_YEAR_PREFIX and after not in _SHORT_YEAR_SUFFIX:
            continue
        short_occ.setdefault(full, []).append((m.start(), m.group(0)))
    for full, occ in short_occ.items():
        occ.sort()
        findings.append(_make(
            "日期",
            f"年份简写与全写混用：{occ[0][1]} / {full}年",
            "同一公文里年份出现两位数简写与四位全写两种，建议统一写全四位"
            "（如「2026年」）。",
            occ[0][1], f"{full}年",
            [p for p, _w in occ] + full_years[full]))
    findings.sort(key=lambda f: f.positions[0])
    return findings[:_MAX_PER_KIND]


# ============================================================ 三、计量单位
# 只有「法定写法」与「俗称/非法定写法」**同时出现**才报；概念表是模块级常量，
# 新增单位只需要往这里加一行（成对出现才有意义，单边单位不会命中）。
_UNIT_CONCEPTS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    # (概念, 法定写法, 俗称 / 非法定写法)
    ("千米", ("千米",), ("公里",)),
    ("平方千米", ("平方千米",), ("平方公里",)),
    ("米", ("米",), ("公尺",)),
    ("厘米", ("厘米",), ("公分",)),
    ("平方米", ("平方米",), ("平米", "平方公尺")),
    ("立方米", ("立方米",), ("立方公尺", "方")),
    ("千克", ("千克",), ("公斤",)),
    ("克", ("克",), ("公克",)),
    ("升", ("升",), ("公升",)),
    ("吨", ("吨",), ("公吨",)),
)
# 这些词里含单位字，但不是该单位本身；先整体消费掉，避免「平方米」里的「米」
# 被当成 concept=米 记一次（否则文中再有「公尺」就会误报）。
_NEUTRAL_UNIT_ATOMS: tuple[str, ...] = (
    "平方公里", "平方英尺", "平方英里", "平方厘米", "平方毫米", "平方分米",
    "立方厘米", "立方毫米", "立方分米", "立方英尺",
    "毫米", "微米", "纳米", "分米", "毫升", "微升", "分升",
)
# 单字单位必须紧跟数量（3米 / 五十米 / 3.5升），否则「大米」「提升」「克服」
# 都会被算成一次单位出现——这是本类最容易误报的地方。
_UNIT_NEEDS_QUANTITY = frozenset({"米", "克", "升", "吨", "方"})
# 「方」还必须是「3方」这种体积口语，后面不能接「案/式/面」等构词成分
_FANG_FOLLOW_BLOCK = frozenset("案式面法向针便程略正圆针位形")


def _build_unit_atoms() -> dict[str, list[tuple[str, str, str]]]:
    """首字 -> [(atom, 概念, 法定/俗称/中性)]，同首字内按长度降序（最长匹配）。"""
    atoms: dict[str, list[tuple[str, str, str]]] = {}
    for concept, legal, colloquial in _UNIT_CONCEPTS:
        for atom in legal:
            atoms.setdefault(atom[0], []).append((atom, concept, "法定"))
        for atom in colloquial:
            atoms.setdefault(atom[0], []).append((atom, concept, "俗称"))
    for atom in _NEUTRAL_UNIT_ATOMS:
        atoms.setdefault(atom[0], []).append((atom, "", "中性"))
    for key in atoms:
        atoms[key].sort(key=lambda item: -len(item[0]))
    return atoms


_UNIT_ATOMS = _build_unit_atoms()


def _unit_atom_allowed(text: str, start: int, atom: str) -> bool:
    """单字单位的上下文门槛：必须紧跟在数量之后，且不是构词成分。"""
    if atom not in _UNIT_NEEDS_QUANTITY:
        return True
    if start == 0:
        return False
    prev = text[start - 1]
    if not (prev.isdigit() or prev in _CN_NUMERAL_CHARS or prev in "多数余半"):
        return False
    if atom == "方":
        nxt = text[start + 1] if start + 1 < len(text) else ""
        if nxt in _FANG_FOLLOW_BLOCK:
            return False
    return True


def _iter_units(text: str):
    """最长匹配扫全文，产出 (start, atom, 概念, 法定/俗称)。中性 atom 直接跳过。"""
    i = 0
    n = len(text)
    while i < n:
        candidates = _UNIT_ATOMS.get(text[i])
        if not candidates:
            i += 1
            continue
        for atom, concept, variant in candidates:
            if text.startswith(atom, i):
                if not _unit_atom_allowed(text, i, atom):
                    continue
                if concept:
                    yield i, atom, concept, variant
                i += len(atom)
                break
        else:
            i += 1


def _check_units(text: str) -> list[Inconsistency]:
    """同一概念下「法定写法」与「俗称写法」并存 → 报（每种概念只报一次）。"""
    # 概念 -> [(start, atom, 法定/俗称)]
    by_concept: dict[str, list[tuple[int, str, str]]] = {}
    for start, atom, concept, variant in _iter_units(text):
        by_concept.setdefault(concept, []).append((start, atom, variant))

    findings: list[Inconsistency] = []
    for concept, occ in by_concept.items():
        kinds = {o[2] for o in occ}
        if "法定" not in kinds or "俗称" not in kinds:
            continue  # 只有一种写法（或只有俗称）→ 不是「混用」
        occ.sort()
        first_occ = occ[0]
        variant_occ = next(o for o in occ if o[2] != first_occ[2])
        findings.append(_make(
            "单位",
            f"计量单位混用：{first_occ[1]} / {variant_occ[1]}",
            f"同一公文里「{concept}」出现了法定计量单位与俗称两种写法，"
            f"建议统一采用法定单位（GB 3100）。",
            first_occ[1], variant_occ[1], [o[0] for o in occ]))
    findings.sort(key=lambda f: f.positions[0])
    return findings[:_MAX_PER_KIND]


# ============================================================ 四、机构名称
def _check_orgs(text: str) -> list[Inconsistency]:
    """同一公文里机构旧称与新称并存（查 corrector_data.ORG_RENAME_PAIRS）。

    精度关键：新称常常**包含**旧称（「住房和城乡建设局」包含「建设局」），
    若不做包含过滤，正常使用新称的公文会被整篇误报。这里把落在新称内部的
    旧称出现全部丢弃；同理新旧对照里「卫生部 / 国家卫生部」两个旧称并存时，
    只保留更长的那个，避免同一实体报两条。
    """
    # 新称 -> [(旧称, 旧称位置, 新称位置)]
    groups: dict[str, list[tuple[str, list[int], list[int]]]] = {}
    for old, new in ORG_RENAME_PAIRS:
        if not old or not new or old == new:
            continue
        old_starts = _find_all(text, old)
        if not old_starts:
            continue
        new_starts = _find_all(text, new)
        if not new_starts:
            continue
        old_spans = []
        for p in old_starts:
            if any(s <= p < s + len(new) for s in new_starts):
                continue  # 旧称是新称的一部分，不是「并存」
            old_spans.append(p)
        new_spans = [p for p in new_starts
                     if not any(s <= p < s + len(old) for s in old_starts)]
        if old_spans and new_spans:
            groups.setdefault(new, []).append((old, old_spans, new_spans))

    findings: list[Inconsistency] = []
    for new, entries in groups.items():
        kept: list[tuple[str, list[int]]] = []
        for old, old_spans, _new_spans in entries:
            shadowed = False
            for other_old, other_spans, _osp in entries:
                if other_old == old or len(other_old) <= len(old):
                    continue
                if old in other_old and all(
                        any(s <= p < s + len(other_old) for s in other_spans)
                        for p in old_spans):
                    shadowed = True
                    break
            if not shadowed:
                kept.append((old, old_spans))
        if not kept:
            continue
        # 旧称一侧取最早出现，新称一侧取最早出现；first/variant 按位置先后定
        old_occ = [(p, old) for old, spans in kept for p in spans]
        new_occ = [(p, new) for _old, _spans, news in entries for p in news]
        old_first = min(old_occ)
        new_first = min(new_occ)
        if old_first[0] <= new_first[0]:
            first, variant = old_first[1], new_first[1]
        else:
            first, variant = new_first[1], old_first[1]
        positions = [p for p, _w in old_occ] + [p for p, _w in new_occ]
        findings.append(_make(
            "机构名称",
            f"机构新旧名称并存：{first} / {variant}",
            "同一公文里出现了机构的旧称与新称，建议核实机构名称并全文统一为现称"
            "（机构沿革信息仅供参考，请以现行文件为准）。",
            first, variant, positions))
    findings.sort(key=lambda f: f.positions[0])
    return findings[:_MAX_PER_KIND]


# ============================================================ 五、标点形式
# 只报「中文语境里的 ASCII 标点」：ASCII 标点两侧都是汉字（括号则要求括号内
# 含汉字），才可能是全角误写。英文/数字/代码里的半角标点一律不管。
_PUNCT_ASCII_SPECS: tuple[tuple[str, str, str, "re.Pattern[str]"], ...] = (
    ("逗号", ",", "，",
     re.compile(r"(?<=[\u4e00-\u9fff]),(?=[\u4e00-\u9fff])")),
    ("分号", ";", "；",
     re.compile(r"(?<=[\u4e00-\u9fff]);(?=[\u4e00-\u9fff])")),
    ("冒号", ":", "：",
     re.compile(r"(?<=[\u4e00-\u9fff]):(?=[\u4e00-\u9fff\d])")),
)
_ASCII_PAREN_RE = re.compile(r"\(([^()\n]{1,40})\)")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# 中文标点邻居：ASCII 符号两侧是汉字或中文标点，才可能是在中文正文里误用了半角。
# 这条同时挡掉 markdown 链接 `[说明](路径)`（前面是「]」）和英文括号。
_CN_PUNCT_NEIGHBORS = frozenset("。；：，、！？…》）】」』“”‘’《》")


def _is_cn_context(ch: str) -> bool:
    return bool(ch) and (bool(_CJK_RE.match(ch)) or ch in _CN_PUNCT_NEIGHBORS)


def _check_punct(text: str) -> list[Inconsistency]:
    """全角/半角同类标点在同一文本里混用。"""
    findings: list[Inconsistency] = []

    for name, ascii_char, full_char, pattern in _PUNCT_ASCII_SPECS:
        ascii_starts = [m.start() for m in pattern.finditer(text)]
        if not ascii_starts or full_char not in text:
            continue
        first_is_ascii = ascii_starts[0] < text.find(full_char)
        first = ascii_char if first_is_ascii else full_char
        variant = full_char if first_is_ascii else ascii_char
        findings.append(_make(
            "标点形式",
            f"标点全角/半角混用：{first} / {variant}",
            f"同一公文里{name}同时出现全角与半角两种写法，中文正文建议统一使用"
            f"全角标点（{full_char}）。",
            first, variant,
            ascii_starts + _find_all(text, full_char)))

    # 括号按「一对」判定：括号内含汉字、且括号外侧是中文语境，才算中文正文里的
    # 半角括号（英文括号、代码括号、markdown 链接一律不碰）
    ascii_parens: list[int] = []
    for m in _ASCII_PAREN_RE.finditer(text):
        if not _CJK_RE.search(m.group(1)):
            continue
        prev = text[m.start() - 1] if m.start() > 0 else ""
        nxt = text[m.end()] if m.end() < len(text) else ""
        if prev and not _is_cn_context(prev):
            continue
        if nxt and not _is_cn_context(nxt):
            continue
        ascii_parens.append(m.start())
    if ascii_parens and "（" in text:
        ascii_starts = sorted(set(ascii_parens))
        full_starts = _find_all(text, "（")
        first_is_ascii = ascii_starts[0] < full_starts[0]
        findings.append(_make(
            "标点形式",
            f"标点全角/半角混用：{'(' if first_is_ascii else '（'} / "
            f"{'（' if first_is_ascii else '('}",
            "同一公文里括号同时出现全角与半角两种写法，中文正文建议统一使用"
            "全角括号（（））。",
            "(" if first_is_ascii else "（", "（" if first_is_ascii else "(",
            ascii_starts + full_starts))

    findings.sort(key=lambda f: f.positions[0])
    return findings[:_MAX_PER_KIND]


# ============================================================ 对外入口
def check(text: str) -> list[Inconsistency]:
    """检查一段文本（通常是一整篇公文）里的文内写法不一致。

    返回按首次出现位置排序的结论列表；空文本 / 纯空白 / None 返回 []；
    任何内部异常都降级为「少报」而不是抛给 UI。
    """
    try:
        if not isinstance(text, str) or not text.strip():
            return []
        findings: list[Inconsistency] = []
        # 逐类独立兜底：某一类正则写炸了，其它类照报
        try:
            findings.extend(_check_numbers(text))
        except Exception:
            pass
        try:
            findings.extend(_check_dates(text))
        except Exception:
            pass
        try:
            findings.extend(_check_units(text))
        except Exception:
            pass
        try:
            findings.extend(_check_orgs(text))
        except Exception:
            pass
        try:
            findings.extend(_check_punct(text))
        except Exception:
            pass
        findings.sort(key=lambda f: (f.positions[0] if f.positions else 0, f.kind))
        return findings
    except Exception:      # 兜底：校对辅助能力绝不把异常抛到 UI 主线程
        return []
