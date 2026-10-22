# -*- coding: utf-8 -*-
"""写作辅助引擎：写作提示（结构/要素/衔接/文风/用语/一致性）与灵感建议（句式库+资料库）。

与「文字纠错」的分工（这条边界必须守住）
---------------------------------------
  · 纠错（``corrector``）= 判定"**写错了**"，命中是错误，可自动替换；
  · 写作辅助（本模块）= 判定"**还缺什么 / 可以怎么写**"，全部是建议，
    **永不自动写回正文**，也**不生成任何未被用户提供的内容**。

因此本模块只读取文本、只输出 ``Hint`` / ``Idea`` 两类纯数据对象，
调用方（对话框）负责展示。这样"写作提示"不可能污染正文，
"不引入副作用"是结构性的而不是靠自觉。

能力
----
1. **写作提示** —— 只读、只提示，按三个层面组织：
   · 结构：骨架要素对照、层次序号（跳号/重号/层级颠倒）、标题长度；
   · 要素：单位/日期/时限等关键要素抽取；
   · 衔接：正文未分层、逐段过长；
   · 文风：平均句长、单句过长、句式疲劳、标点密度、模糊表述；
   · 用语：口语化/网络化表达、同段重复短语；
   · 一致性：自称（我单位/本单位）混用、文内写法前后不一致
     （数字/日期/单位/机构名/标点形式，复用 ``core.consistency``）。
   **warn 只留给"骨架要求的要素确实缺失"**，其余一律 info —— 正常公文不应被刷警告。
2. **文种识别** —— 复用 ``style_profile`` 的量化指纹，回答"这篇更像哪个文种"；
3. **灵感建议** —— 离线句式库（``writing_data``，自撰、许可干净）
   ＋ 资料库/词典检索（``reference``），给可套用的句式骨架与可借鉴的既有写法。

离线与零新依赖：只用本仓库既有模块与标准库 ``re``；每一项检查各自兜底，
任何一类异常都不得连累其他类，更不得抛给界面。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import writing_data as W

# ---------------------------------------------------------------- 启发式正则
# 层级序号：一、/（一）/1. /1、/一是，（与 style_profile 的口径保持一致）
_LAYER_RE = re.compile(
    r"(?:^\s*[一二三四五六七八九十]+\s*、|^\s*（[一二三四五六七八九十]+）"
    r"|^\s*\d+\s*[．.、]|[一二三四五六七八九]是\s*[，、])", re.M)
# 成文日期（阿拉伯或汉字年份均算）
_DATE_RE = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日")
# 主送机关行：句末冒号且不含句号（"各市人民政府："这类）
_RECIPIENT_RE = re.compile(r"^\s*[^\n。；]{2,30}[：:]\s*$", re.M)

# 段落/句子切分：中文句末标点与换行都算断句（与 style_profile 同口径）
_SENT_SPLIT_RE = re.compile(r"[。！？；!?;]+")
_LAYER_LEAD_RE = re.compile(
    r"^(?:[一二三四五六七八九十]+\s*、|（[一二三四五六七八九十]+）"
    r"|\([一二三四五六七八九十]+\)|\d+\s*[．.、]|[一二三四五六七八九]是\s*[，、]?)\s*")
# 行首序号（用于层次序号连续性检查）
_LAYER_LINE_RES: tuple[tuple[int, re.Pattern], ...] = (
    (1, re.compile(r"^([一二三四五六七八九十]+)\s*、")),
    (2, re.compile(r"^[（(]([一二三四五六七八九十]+)[）)]")),
    # 尾部 (?!\d) 排除「2.1」这类章节号，避免把版本号/小节号误判成层次序号
    (3, re.compile(r"^(\d+)\s*[．.、](?!\d)")),
)
# 「一是」只在句首/标点之后才算层次标记，避免「统一是」这类词内误命
_YISHI_RE = re.compile(r"(?<![\u4e00-\u9fff])([一二三四五六七八九])是\s*[，、]?")

# Markdown 文本的过滤（真实长文/笔记常带表格、代码块、列表，不能当正文量）
_MD_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_MD_TABLE_RE = re.compile(r"^\s*\|")
_MD_BULLET_RE = re.compile(r"^[-*+]\s+")
_MD_HR_RE = re.compile(r"^\s*[-*_]{3,}\s*$")
_MD_HEAD_RE = re.compile(r"^#{1,6}\s*")
_MD_MARK_RE = re.compile(r"\*\*|__|`")
# 重复短语扫描时只保留汉字（标点/空白切断 n-gram；英文/数字/代码标识符不算
# "用语贫乏"，保留会造成技术文档里「test」「gwtool」这类假提示）
_NGRAM_KEEP_RE = re.compile(r"[^\u4e00-\u9fff]")

_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
_CN_CHARS = "零一二三四五六七八九"


@dataclass
class Hint:
    """一条写作提示（只读建议，永不写回正文）。"""
    scope: str                 # 结构 / 要素 / 衔接 / 文风 / 用语 / 一致性
    title: str                 # 一行结论
    detail: str = ""           # 为什么 + 怎么做
    level: str = "info"        # info（建议）/ warn（骨架要求项缺失）

    @property
    def line(self) -> str:
        return f"【{self.scope}】{self.title}" + (f"：{self.detail}" if self.detail else "")


@dataclass
class Idea:
    """一条灵感建议（可套用的句式或可借鉴的既有写法）。"""
    source: str = ""           # 句式库 / 资料 / 词典 / 段落
    label: str = ""            # 用途（开头/过渡/结尾…）或来源标签
    text: str = ""
    ref_id: int = 0


@dataclass
class Outline:
    """文种骨架大纲（写作前的"该写哪几块"）。"""
    kind: str = ""
    title_hint: str = ""
    recipient: str = ""
    sections: list[str] = field(default_factory=list)
    closing: str = ""
    signature: str = ""
    note: str = ""

    def lines(self) -> list[str]:
        out = [f"文种：{self.kind}"]
        if self.title_hint:
            out.append(f"标题模板：{self.title_hint}")
        if self.recipient:
            out.append(f"主送机关：{self.recipient}")
        if self.sections:
            out.append("正文要素：")
            out.extend(f"  {i}. {s}" for i, s in enumerate(self.sections, 1))
        if self.closing:
            out.append(f"结束语：{self.closing}")
        if self.signature:
            out.append(f"落款：{self.signature}")
        if self.note:
            out.append(f"提示：{self.note}")
        return out


# ---------------------------------------------------------------- 骨架
def available_kinds() -> list[str]:
    """可用文种（骨架库清单）。"""
    try:
        from . import skeletons
        return skeletons.kinds()
    except Exception:
        return []


def outline(kind: str) -> Outline | None:
    """取某文种的骨架大纲；无此骨架返回 None（不抛异常）。"""
    if not kind:
        return None
    try:
        from . import skeletons
        sk = skeletons.get(kind)
    except Exception:
        return None
    return Outline(kind=sk.kind, title_hint=sk.title_hint, recipient=sk.recipient,
                   # 骨架 body 里的空串是段落分隔符（渲染用），展示时去掉
                   sections=[s for s in (sk.body or []) if s.strip()],
                   closing=sk.closing, signature=sk.signature, note=sk.note)


# ---------------------------------------------------------------- 文种识别
def detect_kind(text: str, limit: int = 3) -> list[tuple[str, float]]:
    """按量化指纹给出"更像哪个文种"的排序。

    返回 ``[(文种, 距离), ...]`` —— **距离越小越像**，列表已按此升序排列
    （与 ``style_profile.match_genre`` 同口径，勿按"分值越大越好"理解）。
    无法量化时返回 ``[]``。
    """
    try:
        from . import style_profile
        a = style_profile.analyze(text or "")
        if not a:
            return []
        return list(style_profile.match_genre(a))[:limit]
    except Exception:
        return []


# ---------------------------------------------------------------- 写作提示
def hints(text: str, kind: str = "") -> list[Hint]:
    """对草稿给出写作提示（结构/要素/衔接/文风/用语/一致性）。

    空稿给"先搭骨架"引导。**每一类检查都单独兜底**：某一类算不出来只能少一类
    提示，绝不影响其他类，也不向调用方抛异常。
    """
    text = text or ""
    if not text.strip():
        return [_empty_hint(kind)]

    checks = (
        (_structure_hints, (text, kind)),
        (_layer_hints, (text,)),
        (_title_hints, (text,)),
        (_element_hints, (text, kind)),
        (_cohesion_hints, (text,)),
        (_paragraph_hints, (text,)),
        (_style_hints, (text, kind)),
        (_vague_hints, (text,)),
        (_sentence_hints, (text,)),
        (_fatigue_hints, (text,)),
        (_punct_hints, (text,)),
        (_colloquial_hints, (text,)),
        (_repeat_hints, (text,)),
        (_consistency_hints, (text,)),
        (_doc_consistency_hints, (text,)),
    )
    out: list[Hint] = []
    for check, args in checks:
        try:
            out.extend(check(*args))
        except Exception:
            continue
    return out


def _empty_hint(kind: str) -> Hint:
    o = outline(kind)
    if o and o.sections:
        return Hint("结构", "先按骨架搭好结构再落笔",
                    "可参照：" + "；".join(o.sections[:4]), "info")
    return Hint("结构", "先确定文种再落笔",
                "选择文种后，可查看该文种的骨架要素（标题/主送/正文/结束语/落款）",
                "info")


def _structure_hints(text: str, kind: str) -> list[Hint]:
    """骨架对照：只在**该文种骨架确实要求**的要素缺失时才提示。"""
    out: list[Hint] = []
    o = outline(kind)
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    head = lines[0] if lines else ""

    if o:
        if o.title_hint and kind not in head:
            out.append(Hint("结构", "标题未包含文种",
                            f"该文种标题通常写作「{o.title_hint}」", "info"))
        if o.closing and o.closing not in text:
            out.append(Hint("结构", "缺少结束语",
                            f"建议补上「{o.closing}」", "warn"))
        if o.signature and not _DATE_RE.search(text):
            out.append(Hint("结构", "缺少成文日期",
                            "落款处应写明成文日期（如「2026年10月3日」）", "warn"))
        if o.recipient and not _RECIPIENT_RE.search("\n".join(lines[:4])):
            out.append(Hint("结构", "未识别到主送机关",
                            "标题下方应顶格写主送机关并以冒号收尾（如「各市人民政府：」）",
                            "info"))
    return out


def _element_hints(text: str, kind: str) -> list[Hint]:
    """要素抽取：复用 paragraph_ref 的槽位规则，缺关键要素时提示。"""
    try:
        from . import paragraph_ref
        slots = paragraph_ref.extract_slots(text)
    except Exception:
        return []
    names = {s.name.rstrip("0123456789") for s in slots}
    out: list[Hint] = []
    if "org" not in names:
        out.append(Hint("要素", "未识别到单位名称",
                        "业务性公文通常需写明发文/收文单位", "info"))
    if "date" not in names and not _DATE_RE.search(text):
        out.append(Hint("要素", "未识别到日期",
                        "涉及成文日期、印发日期时建议写明到日", "info"))
    # 时限只在"布置任务"的文种里才要求，避免对报告类无谓提醒
    if kind in ("通知", "意见", "指示", "决定", "方案") and "deadline" not in names:
        out.append(Hint("要素", "未识别到完成时限",
                        "布置工作宜明确时间节点（如「于本月底前完成」）", "info"))
    return out


def _cohesion_hints(text: str) -> list[Hint]:
    """衔接与结构：长段未分层、全文仅一段且过长（逐段检查见 ``_paragraph_hints``）。"""
    out: list[Hint] = []
    paras = [ln for ln in text.split("\n") if len(ln.strip()) > 0]
    body = [ln for ln in paras if len(ln.strip()) > 40]
    if len(body) >= W.PARA_LAYER_MIN and not _LAYER_RE.search(text):
        out.append(Hint("衔接", "正文分层不明显",
                        "段落较多时建议用「一、」「（一）」「1.」「一是」等层级词组织",
                        "info"))
    if len(body) == 1 and len(body[0].strip()) >= W.PARA_LONG:
        out.append(Hint("衔接", "单段过长，建议拆分",
                        f"该段 {len(body[0].strip())} 字，宜按「背景—任务—要求」分层",
                        "info"))
    return out


def _style_hints(text: str, kind: str) -> list[Hint]:
    """文风：平均句长、文种匹配度。全部只提示。"""
    out: list[Hint] = []
    try:
        from . import style_profile
        a = style_profile.analyze(text)
    except Exception:
        return out
    if a:
        sent = float(a.get("sent") or 0)
        if sent > W.SENT_LONG:
            out.append(Hint("文风", "平均句长偏长",
                            f"实测 {sent:.0f} 字/句，超过 {W.SENT_LONG} 字，建议拆分长句",
                            "info"))
        top = detect_kind(text, limit=1)
        if kind and top and top[0][0] != kind:
            out.append(Hint("文风", f"本文更像「{top[0][0]}」",
                            f"与所选文种「{kind}」不一致，请确认文种是否选对",
                            "info"))
    return out


# ---------------------------------------------------------------- 新检查：文本切分helper
def _body_lines(text: str) -> list[str]:
    """取可量化的正文行：去掉空行、Markdown 表格/代码块/列表符号与强调标记。

    真实长文常带表格与代码块，它们不是公文正文；不滤掉会把整行表格当成
    一个超长句子/段落，制造大量假提示。
    """
    out: list[str] = []
    for raw in (text or "").split("\n"):
        ln = raw.strip()
        if not ln:
            continue
        if (_MD_FENCE_RE.match(ln) or _MD_TABLE_RE.match(ln)
                or _MD_BULLET_RE.match(ln) or _MD_HR_RE.match(ln)):
            continue
        ln = _MD_HEAD_RE.sub("", ln)
        ln = _MD_MARK_RE.sub("", ln).strip()
        if ln:
            out.append(ln)
    return out


def _split_sentences(line: str) -> list[str]:
    """把一行按句末标点切断；过短的片段（序号、标题短语）不参与句级统计。"""
    return [s.strip() for s in _SENT_SPLIT_RE.split(line)
            if len(s.strip()) >= W.SENT_MIN_LEN]


def _is_han(ch: str) -> bool:
    return bool(ch) and "\u4e00" <= ch <= "\u9fff"


def _cn_to_int(raw: str) -> int:
    """中文数字/阿拉伯数字转整数；无法识别返回 0（调用方跳过）。"""
    if not raw:
        return 0
    if raw.isdigit():
        return int(raw)
    if "十" in raw:
        left, _sep, right = raw.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    return _CN_DIGITS.get(raw, 0)


def _int_to_cn(n: int) -> str:
    if n <= 0:
        return ""
    if n < 10:
        return _CN_CHARS[n]
    if n < 20:
        return "十" + (_CN_CHARS[n % 10] if n % 10 else "")
    if n < 100:
        return _CN_CHARS[n // 10] + "十" + (_CN_CHARS[n % 10] if n % 10 else "")
    return str(n)


def _marker_label(level: int, value: int) -> str:
    """把 (层级, 序号值) 还原成公文里常见的写法，便于提示里对照。"""
    if level == 1:
        return f"{_int_to_cn(value)}、"
    if level == 2:
        return f"（{_int_to_cn(value)}）"
    if level == 3:
        return f"{value}."
    return f"{_int_to_cn(value)}是"


# ---------------------------------------------------------------- 检查 1：长句
def _sentence_hints(text: str) -> list[Hint]:
    """单句超过阈值 → 指出第几句、多少字、建议拆分。"""
    out: list[Hint] = []
    idx = 0
    for line in _body_lines(text):
        for sent in _split_sentences(line):
            idx += 1
            if len(sent) >= W.SENT_TOO_LONG:
                out.append(Hint(
                    "文风", f"第 {idx} 句偏长（{len(sent)} 字）",
                    f"超过 {W.SENT_TOO_LONG} 字；建议在「，」「；」处分句，"
                    "或拆成两句各自表达一个意思", "info"))
                if len(out) >= W.SENT_TOO_LONG_MAX_HINTS:
                    return out
    return out


# ---------------------------------------------------------------- 检查 2：段落过长
def _paragraph_hints(text: str) -> list[Hint]:
    """逐段检查超长段落，建议按层次拆段。

    "全文仅一段且过长"由既有 ``_cohesion_hints`` 负责（阈值 PARA_LONG），
    这里只处理多段文本，避免同一段报两次。
    """
    out: list[Hint] = []
    paras = _body_lines(text)
    body = [p for p in paras if len(p) > 40]
    if len(body) <= 1:
        return out
    for i, para in enumerate(paras, 1):
        if len(para) >= W.PARA_TOO_LONG:
            out.append(Hint(
                "衔接", f"第 {i} 段过长（{len(para)} 字）",
                f"超过 {W.PARA_TOO_LONG} 字，建议按「背景—任务—要求」拆段，"
                "或用层级词（一、/（一）/一是）分层", "info"))
            if len(out) >= W.PARA_TOO_LONG_MAX_HINTS:
                break
    return out


# ---------------------------------------------------------------- 检查 3：句式疲劳
def _opening_char(sent: str) -> str:
    """句子的"开头字"：先剥掉行首序号词，再看第一个汉字。"""
    s = _LAYER_LEAD_RE.sub("", sent).lstrip()
    return s[0] if s and _is_han(s[0]) else ""


def _fatigue_hints(text: str) -> list[Hint]:
    """同段内连续多句以同一字开头 → 句式疲劳，建议变化。"""
    out: list[Hint] = []
    for i, para in enumerate(_body_lines(text), 1):
        opens = [_opening_char(s) for s in _split_sentences(para)]
        j = 0
        while j < len(opens):
            k = j
            while k + 1 < len(opens) and opens[k + 1] and opens[k + 1] == opens[j]:
                k += 1
            run = k - j + 1
            if opens[j] and run >= W.FATIGUE_MIN_RUN:
                out.append(Hint(
                    "文风", f"第 {i} 段连续 {run} 句以「{opens[j]}」开头",
                    "句式单一，建议变换句子开头、调整语序，或合并相邻分句", "info"))
                if len(out) >= W.FATIGUE_MAX_HINTS:
                    return out
                j = k + 1
                continue
            j += 1
    return out


# ---------------------------------------------------------------- 检查 4：层次序号
def _collect_layer_markers(text: str) -> list[tuple[int, int, int, int, str]]:
    """收集层次标记，按出现顺序返回 ``(行号, 层级, 序号值, 列位置, 原写法)``。"""
    markers: list[tuple[int, int, int, int, str]] = []
    for line_no, line in enumerate(_body_lines(text), 1):
        found: list[tuple[int, int, int, int, str]] = []
        for level, rx in _LAYER_LINE_RES:
            m = rx.match(line)
            if not m:
                continue
            value = _cn_to_int(m.group(1))
            found.append((m.start(), level, value, m.start(), m.group(0).strip()))
        for m in _YISHI_RE.finditer(line):
            value = _cn_to_int(m.group(1))
            found.append((m.start(), 4, value, m.start(), m.group(0).strip()))
        found.sort(key=lambda it: it[0])
        for pos, level, value, _pos, label in found:
            markers.append((line_no, level, value, pos, label))
    return markers


def _layer_hints(text: str) -> list[Hint]:
    """层次序号：跳号、重号、层级颠倒。

    序号可以随上级标题重新开始（每个「一、」下重新用「（一）」「1.」），
    因此出现更浅层级的序号时，把更深层级的期望值复位为 1；没有更浅层标题可
    依据时，「（一）」「1.」从 1 重新起算视为合法重启（分层文档的常态）。
    「一是」是段内枚举，下段可重新从「一是」开始，故按行复位。
    """
    out: list[Hint] = []
    markers = _collect_layer_markers(text)
    if not markers:
        return out
    next_val = {1: 1, 2: 1, 3: 1, 4: 1}
    last_val = {1: 0, 2: 0, 3: 0, 4: 0}
    last_label = {1: "", 2: "", 3: "", 4: ""}
    first_seen: dict[int, int] = {}
    prev_line = -1

    for order, (line_no, level, value, _pos, label) in enumerate(markers):
        first_seen.setdefault(level, order)
        if value <= 0:
            continue
        # 「一是」是段内枚举，允许下一段重新从「一是」起，故按行复位
        if level == 4 and line_no != prev_line:
            next_val[4] = 1
            last_val[4] = 0
            last_label[4] = ""
        prev_line = line_no
        # 出现更浅层级序号 → 更深一层的序号重新从 1 开始
        for deeper in range(level + 1, 5):
            next_val[deeper] = 1
            last_val[deeper] = 0
            last_label[deeper] = ""
        expected = next_val[level]
        if value == expected:
            issue = ""
        elif last_val[level] and value == last_val[level]:
            issue = "重号"
        elif level in (2, 3) and value == 1 and expected > 1:
            # 「（一）」「1.」常随新章节重新起序号；没有更浅层标题可依据时
            # 把它当合法重启，避免对分层文档刷假提示
            issue = ""
        elif value > expected:
            issue = "跳号"
        else:
            issue = "倒序"
        if issue:
            prev = last_label[level] or "无"
            out.append(Hint(
                "结构", f"层次序号不规范：{issue}",
                f"第 {line_no} 行的「{label}」：前一处为「{prev}」，"
                f"按顺序应为「{_marker_label(level, expected)}」", "info"))
            if len(out) >= W.LAYER_MAX_HINTS:
                return out
        last_val[level] = value
        last_label[level] = label
        next_val[level] = value + 1

    # 层级顺序：浅层序号必须先于深层序号出现
    if len(out) < W.LAYER_MAX_HINTS:
        l1 = first_seen.get(1)
        l2 = first_seen.get(2)
        l3 = first_seen.get(3)
        if l1 is not None and l2 is not None and l2 < l1:
            out.append(Hint("结构", "层次序号层级颠倒",
                            "「（一）」出现在「一、」之前，应先列一级序号再列二级序号",
                            "info"))
        elif l1 is not None and l3 is not None and l3 < l1:
            out.append(Hint("结构", "层次序号层级颠倒",
                            "「1.」出现在「一、」之前，应先列一级序号再列三级序号",
                            "info"))
        elif l2 is not None and l3 is not None and l3 < l2:
            out.append(Hint("结构", "层次序号层级颠倒",
                            "「1.」出现在「（一）」之前，应先列二级序号再列三级序号",
                            "info"))
    return out


# ---------------------------------------------------------------- 检查 5：标点密度
def _punct_hints(text: str) -> list[Hint]:
    """感叹号/问号/省略号过量提示（公文以陈述为主）。"""
    out: list[Hint] = []
    lines = _body_lines(text)
    body = "\n".join(lines)

    exclaim = body.count("！") + body.count("!")
    if exclaim:
        max_para = max((ln.count("！") + ln.count("!") for ln in lines), default=0)
        if exclaim >= W.PUNCT_EXCLAIM_TOTAL or max_para >= W.PUNCT_PARA_MAX:
            out.append(Hint(
                "文风", "感叹号使用偏多",
                f"全文 {exclaim} 个、单段最多 {max_para} 个；公文以陈述为主，"
                "建议改用句号或调整语气", "info"))

    question = body.count("？") + body.count("?")
    if question >= W.PUNCT_QUESTION_TOTAL:
        out.append(Hint(
            "文风", "问号使用偏多",
            f"全文 {question} 个；除讲话稿的有意设问外，建议改为陈述句", "info"))

    ellipsis = len(re.findall(r"…{2,}|\.{3,}|。{3,}", body))
    if ellipsis >= W.PUNCT_ELLIPSIS_TOTAL:
        out.append(Hint(
            "文风", "省略号使用偏多",
            f"全文 {ellipsis} 处；公文一般不用省略号（引文省略除外），建议写全", "info"))
    return out


# ---------------------------------------------------------------- 检查 6/7：用语
def _vague_hints(text: str) -> list[Hint]:
    """模糊表述：复用 style_data.BAD_WORDS，并补充公文高频模糊词。

    只提示"可进一步具体化"，不判错；BAD_WORDS 的既有文案（「用语偏空泛：X」）
    与文风口径保持不变。
    """
    out: list[Hint] = []
    seen: set[str] = set()
    try:
        from . import style_data
        bad_words = dict(style_data.BAD_WORDS)
    except Exception:
        bad_words = {}
    # 既有契约：style_data.BAD_WORDS 的词全部提示（不设上限，保持原行为）
    for word, advice in bad_words.items():
        if word and word in text:
            seen.add(word)
            out.append(Hint("文风", f"用语偏空泛：{word}", advice, "info"))
    # 新增：公文高频模糊词（每种一次，避免「进一步」这类极常用词刷屏）
    extra = 0
    for word, advice in W.VAGUE_WORDS.items():
        if extra >= W.VAGUE_MAX_HINTS:
            break
        if word in seen or not word or word not in text:
            continue
        seen.add(word)
        extra += 1
        out.append(Hint("文风", f"用语偏空泛：{word}", advice, "info"))
    return out


def _colloquial_hints(text: str) -> list[Hint]:
    """口语化/网络化表达 → 提示改写（长词优先匹配，避免「搞好」重复命中「搞」）。"""
    body = "\n".join(_body_lines(text))
    # 固定搭配先整体剔除，避免「弄虚作假」「东西部」这类正式表述误报
    for allowed in W.COLLOQUIAL_ALLOW:
        body = body.replace(allowed, "")
    spans: list[tuple[int, int]] = []
    positions: list[tuple[int, str]] = []
    for word in sorted(W.COLLOQUIAL_WORDS, key=len, reverse=True):
        if not word:
            continue
        start = 0
        while True:
            at = body.find(word, start)
            if at < 0:
                break
            end = at + len(word)
            if not any(at < span_end and span_start < end
                       for span_start, span_end in spans):
                spans.append((at, end))
                positions.append((at, word))
            start = end
    positions.sort(key=lambda it: it[0])
    out: list[Hint] = []
    emitted: set[str] = set()
    for _at, word in positions:
        if word in emitted:
            continue
        emitted.add(word)
        out.append(Hint("用语", f"口语化表达：{word}",
                        W.COLLOQUIAL_WORDS[word], "info"))
        if len(out) >= W.COLLOQUIAL_MAX_HINTS:
            break
    return out


# ---------------------------------------------------------------- 检查 8：重复短语
def _longest_stable_phrase(plain: str, gram: str,
                           spots: list[int]) -> tuple[str, int]:
    """把 n-gram 向两侧扩展成"出现次数不减少"的最长短语。

    只保留所有出现位置都共有的扩展：这样报出的短语次数是准确的，也不会
    因为某两处恰好同头就把 4 字搭配扩成"安全生产是"这类半截搭配。
    """
    phrase = gram
    active = list(spots)
    count = len(active)
    # 向右扩展：所有出现位置的下一字符必须相同
    while count >= 2:
        width = len(phrase)
        nxt = [p for p in active if p + width < len(plain)]
        if len(nxt) != count:
            break
        ch = plain[active[0] + width]
        if any(plain[p + width] != ch for p in nxt):
            break
        phrase += ch
        active = [p for p in active if p + width < len(plain)]
    # 向左扩展：所有出现位置的前一字符必须相同
    while count >= 2:
        prev = [p for p in active if p > 0]
        if len(prev) != count:
            break
        ch = plain[active[0] - 1]
        if any(plain[p - 1] != ch for p in prev):
            break
        phrase = ch + phrase
        active = [p - 1 for p in active]
    return phrase, count


def _repeat_hints(text: str) -> list[Hint]:
    """同一段内 4 字以上短语重复出现 ≥2 次 → 用语贫乏提示。"""
    out: list[Hint] = []
    length = W.PHRASE_REPEAT_LEN
    for para_no, para in enumerate(_body_lines(text), 1):
        if len(para) < length * 2:
            continue
        plain = _NGRAM_KEEP_RE.sub("", para)[: W.PHRASE_SCAN_MAX_CHARS]
        if len(plain) < length * 2:
            continue
        positions: dict[str, list[int]] = {}
        for i in range(len(plain) - length + 1):
            positions.setdefault(plain[i:i + length], []).append(i)
        candidates: dict[str, int] = {}
        for gram, spots in positions.items():
            if len(spots) < W.PHRASE_REPEAT_MIN_COUNT:
                continue
            if any(gram in old for old in candidates):
                continue
            phrase, count = _longest_stable_phrase(plain, gram, spots)
            if len(phrase) >= length and count >= W.PHRASE_REPEAT_MIN_COUNT:
                # 「项项项项」这类同字重复多半来自表格/版式，不是用语贫乏
                if len(set(phrase)) <= 1:
                    continue
                candidates[phrase] = count
        if not candidates:
            continue
        chosen: list[str] = []
        for phrase in sorted(candidates, key=lambda p: (-candidates[p], -len(p))):
            if any(phrase in old or old in phrase for old in chosen):
                continue
            chosen.append(phrase)
            shown = (phrase if len(phrase) <= W.PHRASE_REPEAT_SHOW
                     else phrase[: W.PHRASE_REPEAT_SHOW] + "…")
            out.append(Hint(
                "用语", f"第 {para_no} 段重复用语：{shown}",
                f"该短语在本段出现 {candidates[phrase]} 次，建议换一种说法或合并表述",
                "info"))
            if len(out) >= W.PHRASE_REPEAT_MAX_HINTS:
                return out
    return out


# ---------------------------------------------------------------- 检查 9：自称一致
def _self_ref_re() -> re.Pattern:
    # 长后缀优先，避免「单位」被更短的「单」抢先匹配（当前无此冲突，留作保险）
    suffixes = sorted(W.SELF_REF_SUFFIXES, key=len, reverse=True)
    return re.compile(r"(我|本)(" + "|".join(map(re.escape, suffixes)) + ")")


_SELF_REF_RE = _self_ref_re()


def _consistency_hints(text: str) -> list[Hint]:
    """自称一致性：同一后缀既用「我X」又用「本X」时提示统一。"""
    counts: dict[str, dict[str, int]] = {}
    for m in _SELF_REF_RE.finditer(text or ""):
        prefix, suffix = m.group(1), m.group(2)
        counts.setdefault(suffix, {})
        counts[suffix][prefix] = counts[suffix].get(prefix, 0) + 1
    out: list[Hint] = []
    for suffix, by_prefix in counts.items():
        if "我" not in by_prefix or "本" not in by_prefix:
            continue
        out.append(Hint(
            "一致性", f"自称不统一：我{suffix}/本{suffix}",
            f"「我{suffix}」出现 {by_prefix['我']} 处、「本{suffix}」出现 "
            f"{by_prefix['本']} 处，建议全文统一为一种写法", "info"))
        if len(out) >= W.SELF_REF_MAX_HINTS:
            break
    return out


# ------------------------------------------------- 检查 11：文内写法一致性（core.consistency）
def _doc_consistency_hints(text: str) -> list[Hint]:
    """文内写法一致性：同一实体在全文出现两种写法（提示级，不产生替换文本）。

    与检查 9（自称一致）的分工：检查 9 只看「我单位 / 本单位」一种称谓习惯；
    本检查复用 ``core.consistency`` 的全量横向比对——数字/数量（3个工作日 vs
    三个工作日）、日期格式（2026年10月3日 vs 2026.10.3）、计量单位（公里/千米）、
    机构新旧名称（环保局/生态环境局）、标点全半角。

    **为什么不挂在 corrector.check_text 上**：纠错流水线产出的是 ``Correction``
    （有替换文本、可批量写回），而本类结论的 suggestion 是**一整句描述**，
    没有可替换文本。混进纠错流水线会让「全部应用 / 批量写回」拿到不是替换
    文本的建议（与 ``ADVISORY_CATEGORIES`` 踩过的坑同源）。所以它只出现在
    写作提示这一侧，且由对话框只读展示。

    单类异常/依赖缺失一律降级为空——写作提示不能因为这个模块而整体失败。
    """
    try:
        from . import consistency
        items = consistency.check(text or "")
    except Exception:
        return []
    out: list[Hint] = []
    for inc in items[:W.DOC_INCONSISTENCY_MAX_HINTS]:
        out.append(Hint("一致性", inc.label, inc.detail, "info"))
    hidden = len(items) - W.DOC_INCONSISTENCY_MAX_HINTS
    if hidden > 0:
        out.append(Hint("一致性", f"另有 {hidden} 处写法不一致",
                        "建议先通读全文统一写法，再逐处校对", "info"))
    return out


# ---------------------------------------------------------------- 检查 10：标题长度
def _title_hints(text: str) -> list[Hint]:
    """首行标题过长提示（标题宜简明，事由不宜写成摘要）。"""
    for raw in (text or "").split("\n"):
        ln = raw.strip()
        if not ln:
            continue
        if (_MD_FENCE_RE.match(ln) or _MD_TABLE_RE.match(ln)
                or _MD_HR_RE.match(ln)):
            return []
        ln = _MD_HEAD_RE.sub("", ln)
        ln = _MD_MARK_RE.sub("", ln).strip()
        if not ln:
            continue
        # 过长或以句末标点收尾 → 更像正文首段，不当标题判
        if len(ln) > W.TITLE_MAX_SCAN or ln.endswith(("。", "！", "？", "；")):
            return []
        if len(ln) > W.TITLE_TOO_LONG:
            return [Hint(
                "结构", f"标题偏长（{len(ln)} 字）",
                f"超过 {W.TITLE_TOO_LONG} 字；公文标题宜简明，"
                "可把具体事由压缩后移到正文首段", "info")]
        return []
    return []


# ---------------------------------------------------------------- 灵感建议
def inspiration(topic: str = "", kind: str = "",
                limit: int = 8) -> list[Idea]:
    """灵感建议：句式库（按文种选环节）＋ 资料库/词典相关写法。

    `topic` 非空时追加两路离线检索（资料库全文/词典），全部失败即降级为
    "只有句式库" —— 检索异常绝不冒泡到界面。
    """
    out: list[Idea] = []
    groups = W.KIND_GROUPS.get(kind) or tuple(W.PHRASE_BANK.keys())
    for g in groups:
        for s in W.PHRASE_BANK.get(g, []):
            out.append(Idea("句式库", g, s))
    if topic.strip():
        out.extend(_library_ideas(topic.strip(), limit))
    return out[: max(1, limit * 3)]


def _library_ideas(topic: str, limit: int) -> list[Idea]:
    """资料库/词典/句式表的相关写法（复用既有归一化检索，零新依赖）。"""
    out: list[Idea] = []
    try:
        from . import reference
        for it in reference.lookup(topic, limit_each=max(3, limit)):
            out.append(Idea(it.source_label, it.title or "", it.snippet,
                            it.ref_id))
    except Exception:
        return []
    return out


# ---------------------------------------------------------------- 输出
def report_lines(hs: list[Hint]) -> list[str]:
    """把提示整理成可展示/可导出的文本行。"""
    if not hs:
        return ["未发现需要提示的问题：结构、要素、衔接、文风、用语、一致性均无明显偏离。"]
    return [h.line for h in hs]
