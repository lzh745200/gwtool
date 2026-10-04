# -*- coding: utf-8 -*-
"""写作辅助引擎：写作提示（结构/要素/衔接/文风）与灵感建议（句式库+资料库）。

与「文字纠错」的分工（这条边界必须守住）
----------------------------------------
  · 纠错（``corrector``）= 判定"**写错了**"，命中是错误，可自动替换；
  · 写作辅助（本模块）= 判定"**还缺什么 / 可以怎么写**"，全部是建议，
    **永不自动写回正文**，也**不生成任何未被用户提供的内容**。

因此本模块只读取文本、只输出 ``Hint`` / ``Idea`` 两类纯数据对象，
调用方（对话框）负责展示。这样"写作提示"不可能污染正文，
"不引入副作用"是结构性的而不是靠自觉。

能力
----
1. **写作提示** —— 对照文种骨架（``skeletons`` 的 12 个军队机关文种）与草稿实际内容，
   指出缺失要素（标题/主送/结束语/落款/时限）、结构问题（长段未分层）、文风偏离；
2. **文种识别** —— 复用 ``style_profile`` 的量化指纹，回答"这篇更像哪个文种"；
3. **灵感建议** —— 离线句式库（``writing_data``，自撰、许可干净）
   ＋ 资料库/词典检索（``reference``），给可套用的句式骨架与可借鉴的既有写法。

离线与零新依赖：只用本仓库既有模块；检索失败一律降级为空结果，绝不抛给界面。
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


@dataclass
class Hint:
    """一条写作提示（只读建议，永不写回正文）。"""
    scope: str                 # 结构 / 要素 / 衔接 / 文风
    title: str                 # 一行结论
    detail: str = ""           # 为什么 + 怎么做
    level: str = "info"        # info（建议）/ warn（缺失或明显偏离）

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
    """对草稿给出写作提示（结构/要素/衔接/文风）。空稿给"先搭骨架"引导。"""
    text = text or ""
    if not text.strip():
        return [_empty_hint(kind)]

    out: list[Hint] = []
    out.extend(_structure_hints(text, kind))
    out.extend(_element_hints(text, kind))
    out.extend(_cohesion_hints(text))
    out.extend(_style_hints(text, kind))
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
    """衔接与结构：长段未分层、单段过长。"""
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
    """文风：句长、空泛词、文种匹配度。全部只提示。"""
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
    # 空泛词/模糊表述：直接扫 style_data.BAD_WORDS（词->建议）
    try:
        from . import style_data
        for word, advice in style_data.BAD_WORDS.items():
            if word in text:
                out.append(Hint("文风", f"用语偏空泛：{word}", advice, "info"))
    except Exception:
        pass
    return out


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
        return ["未发现需要提示的问题：结构、要素、衔接、文风均无明显偏离。"]
    return [h.line for h in hs]
