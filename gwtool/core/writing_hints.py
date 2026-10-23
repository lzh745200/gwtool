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
   7 种无固定模板文体为常序，文本标题/结束语带法定文种标记时并入伍种基线
   （``style_data.LEGAL_REF``，P1-18），避免通知/请示/报告恒被判成"经验总结"；
3. **灵感建议** —— 离线句式库（``writing_data``，自撰、许可干净）
   ＋ 资料库/词典检索（``reference``），给可套用的句式骨架与可借鉴的既有写法。

离线与零新依赖：只用本仓库既有模块与标准库 ``re``；每一项检查各自兜底，
任何一类异常都不得连累其他类，更不得抛给界面。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import style_data as D
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
# 发文字号（「国办发〔2026〕28号」）：公文形态的硬标志之一
_DOC_NO_RE = re.compile(r"〔\s*\d{4}\s*〕\s*\d+\s*号")
# 引号内内容（弯引号/直角引号成对）。引语是**原话**，口语化判定要跳过它 ——
# 新闻/讲话里的「道路一定要搞清楚」是引用，不是作者用语贫乏（v1.14-220）。
_QUOTED_TEXT_RE = re.compile(r"[“「『][^”」』\n]{0,300}[”」』]")
# 《…》内的文件名（引用既有文件时的规范重复，不算用语贫乏）
_DOC_TITLE_RE = re.compile(r"《([^》\n]{2,60})》")
# 完成时限的**文本级**判据（P1-22）。槽位规则只认「N 个工作日内」，而公文
# 布置任务时更常用日期式写法（「9月20日前」「本月底前」「年底前」）—— 只认
# 前一种会把最规范的通知判成"未识别到完成时限"。
# 刻意收得紧：必须有「日＋前/以前/之前」或「月底/年底＋前」，不拿单字「前」
# 兜底，否则「前期」「前往」「目前」都会让提示静默消失（那是漏报，比误报更坏）。
_DEADLINE_RE = re.compile(
    r"(?:(?:\d{4}\s*年\s*)?\d{1,2}\s*月\s*\d{1,2}\s*日"
    r"|(?:\d{1,2}|本|下|当|今)?\s*月\s*底"
    r"|(?:今|本)?\s*年\s*底)\s*(?:以前|之前|前(?!后))"
    r"|[0-9〇零一二三四五六七八九十两]+\s*个\s*工作日",
    re.M)

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
#: 与 `style_profile._MATCH_KEYS` 同口径（该常量是私有的，这里镜像一份；
#: 漂移由 tests/test_genre_v19b.py 的一致性断言兜住）。
_LEGAL_MATCH_KEYS = ("chars", "sent", "h1", "h2", "h3", "yishi", "dun",
                     "qz", "yq", "jy", "pct")
#: 法定基线并入排序的门槛：只有法定基线**明显**优于 7 文体 top1 才参与。
#: 目的：无固定模板样张的标题/结尾偶尔夹带法定套语（如文末引用「特此报告」）
#: 时不至于被"抢位"——实测法定样张 legal≈0.0，而同文本 style≥0.70。
_LEGAL_MERGE_MARGIN = 0.8


def _legal_marker(text: str) -> tuple[str, str]:
    """文本暗示的法定文种，返回 ``(文种, 来源)``；没有迹象返回 ``("", "")``。

    来源分两档（置信度不同，排序时用法也不同）：

    * ``"title"`` —— 标题后缀直接写明文种（「…的通知」「…的请示」
      「…的实施意见」「会议纪要」等）。这是作者**自报**的文种，属确定性证据；
      真实公文粘贴后前几行常带发文机关标志/发文字号，因此扫**前 3 行**；
    * ``"closing"`` —— 只有文末结束语套语（「特此通知」「妥否，请批示」
      「此复」…）。只是线索：正文引用别家公文时也可能出现，需与统计距离配合。

    两处词表都在 ``style_data.LEGAL_TITLE_MARKS / LEGAL_CLOSINGS``，不在本模块
    硬编码——加一个法定文种只改数据层。
    """
    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
    for line in lines[:3]:
        head = _MD_HEAD_RE.sub("", line)[:80]
        for genre, suffixes in D.LEGAL_TITLE_MARKS:
            if any(head.endswith(suf) for suf in suffixes):
                return genre, "title"
    tail = (text or "")[-400:]
    for genre, closings in D.LEGAL_CLOSINGS.items():
        if any(closing in tail for closing in closings):
            return genre, "closing"
    return "", ""


def _legal_rank(a: dict) -> list[tuple[str, float]]:
    """按法定文种识别基线排序（距离口径与 ``style_profile.match_genre`` 一致）。"""
    out: list[tuple[str, float]] = []
    for genre, row in D.LEGAL_REF.items():
        dist = 0.0
        count = 0
        for key in _LEGAL_MATCH_KEYS:
            spec = row.get(key)
            if not spec or key not in a:      # 指标缺失就跳过，不猜也不抛
                continue
            lo, hi, mid = spec
            scale = max(hi - lo, abs(mid) * 0.3, 1)
            dist += abs(a[key] - mid) / scale
            count += 1
        if count:
            out.append((genre, dist / count))
    return sorted(out, key=lambda item: item[1])


def _is_legal_kind(kind: str) -> bool:
    """是否法定文种（含未设识别基线的条例/通告/通令/指示，一律按法定处理）。

    法定文种**不出**"本文更像 X"提示：它们的结构由制度锁定，识别基线是
    自撰示意样本，据此在法定文种之间判"更像"没有精度保证（P1-18）。
    """
    return bool(kind) and (kind in D.LEGAL_STYLES or kind in D.NO_PARAM
                           or kind in ("通令", "指示"))


def detect_kind(text: str, limit: int = 3) -> list[tuple[str, float]]:
    """按量化指纹给出"更像哪个文种"的排序。

    返回 ``[(文种, 距离), ...]`` —— **距离越小越像**，列表已按此升序排列
    （与 ``style_profile.match_genre`` 同口径，勿按"分值越大越好"理解）。
    无法量化时返回 ``[]``。

    排序对象平时是 7 种无固定模板文体；文本带法定文种标记时并入 10 种法定
    文种的识别基线，否则通知/请示/报告会被 7 文体指纹判成「经验总结」
    （P1-18 实测 top1=经验总结 0.751）。两档合并策略：

    * 标题已写明文种（``"title"``）：该文种距离记为 **0.0**——标题是作者自报
      的确定性证据，优先于统计相似度（「的通知」就是通知，不需要再猜）；
      其余文种仍按统计距离排在后面，列表整体仍升序。
    * 只有结束语线索（``"closing"``）：受 ``_LEGAL_MERGE_MARGIN`` 约束，法定
      基线必须明显优于 7 文体 top1 才并入，避免正文引用别家公文套语时抢位。
    """
    try:
        from . import style_profile
        a = style_profile.analyze(text or "")
        if not a:
            return []
        ranking = list(style_profile.match_genre(a))
        genre, source = _legal_marker(text)
        if genre:
            legal = _legal_rank(a)
            if source == "title":
                legal = [(g, 0.0 if g == genre else d) for g, d in legal]
                ranking = sorted(ranking + legal, key=lambda item: item[1])
            elif legal and (not ranking
                            or legal[0][1] < ranking[0][1] * _LEGAL_MERGE_MARGIN):
                ranking = sorted(ranking + legal, key=lambda item: item[1])
        return ranking[:limit]
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
        (_cohesion_hints, (text, kind)),
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


def _has_closing(text: str, head: str, closing: str) -> bool:
    """正文是否已有结束语（v1.14）。

    判据从"骨架那一句是否逐字出现"放宽为"任一等价写法"：真实公文的收束句
    远不止一种（「请认真贯彻执行」「妥否，请批示」），只认骨架句会把规范公文
    判成"缺少结束语"（warn 级误报，校准前 6/35 篇全部命中此项）。

    「印发/转发类通知」直接视作已有：这类公文的功能是印发附件，正文以落款＋
    附件收束，本来就不写结束语（真实国办文件即如此），要求补「特此通知」是错的。
    """
    if closing and closing in text:
        return True
    if any(mark in head for mark in W.PRINT_NOTICE_MARKS):
        return True
    if any(mark in text[-300:] for mark in W.CLOSING_OPTIONAL_TAIL):
        return True                     # 以附件收束，结束语非必需
    return any(word in text for word in W.CLOSING_ACCEPT)


def _looks_formal(head: str, text: str, kind: str) -> bool:
    """文本是否具备公文形态 —— 只用来决定**要不要按骨架扣要素**（warn）。

    判据取"标题自报文种"或"有发文字号"：真实公文标题必含文种（「…的通知」），
    发文字号与之互为佐证。**不能用"有主送机关行"**：新闻报道里的
    「他说：」这种对话行也满足主送机关的行形态，会把非公文判成公文。
    """
    return bool((kind and kind in head) or _DOC_NO_RE.search(text))


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
        # warn 只在"确实是公文"时发：语料里一篇军报通讯的副标题是
        # 「——来自北部战区海军某潜艇部队的报告」，detect_kind 据此判成"报告"，
        # 于是被要求补「特此报告。」与成文日期 —— 两条 warn 都是纯误报。
        formal = _looks_formal(head, text, kind)
        if formal and o.closing and not _has_closing(text, head, o.closing):
            out.append(Hint("结构", "缺少结束语",
                            f"建议补上「{o.closing}」（或「请遵照执行」等等价写法）",
                            "warn"))
        if formal and o.signature and not _DATE_RE.search(text):
            out.append(Hint("结构", "缺少成文日期",
                            "落款处应写明成文日期（如「2026年10月3日」）", "warn"))
        if o.recipient and not _RECIPIENT_RE.search("\n".join(lines[:4])):
            out.append(Hint("结构", "未识别到主送机关",
                            "标题下方应顶格写主送机关并以冒号收尾（如「各市人民政府：」）",
                            "info"))
    return out


def _element_hints(text: str, kind: str) -> list[Hint]:
    """要素抽取：复用 paragraph_ref 的槽位规则，缺关键要素时提示。

    时限判定是"槽位 ＋ 文本级正则"双判据：槽位只认「N 个工作日内」，日期式
    写法（「9月20日前」「本月底前」「年底前」）由 ``_DEADLINE_RE`` 兜底。
    两者都判据明确，不拿单字「前」兜底，避免把真缺失静默成"已写"。

    **只对公文做要素审查**（v1.14）：kind 明确是 7 种无固定模板文体（讲话、
    经验材料、理论文章…）时直接返回空 —— 成文日期/主送机关/完成时限本就不是
    这些文体的必需要素。校准实测：35 篇真实语料里 9 篇被报「未识别到日期」、
    3 篇「未识别到单位名称」、4 篇「未识别到完成时限」，全部来自新闻稿与
    理论文章，属纯噪声。kind 为空（用户尚未选文种）时维持原行为，
    因为那正是"起草阶段该补要素"的提示场景。
    """
    if kind and not _is_legal_kind(kind):
        return []
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
    if (kind in ("通知", "意见", "指示", "决定", "方案")
            and "deadline" not in names
            and not _DEADLINE_RE.search(text)):
        out.append(Hint("要素", "未识别到完成时限",
                        "布置工作宜明确时间节点（如「于本月底前完成」）", "info"))
    return out


def _cohesion_hints(text: str, kind: str = "") -> list[Hint]:
    """衔接与结构：长段未分层、全文仅一段且过长（逐段检查见 ``_paragraph_hints``）。

    「正文分层不明显」加**文体门槛**（v1.14-220）：kind 明确是 7 种无固定模板
    文体（新闻通讯、理论文章、讲话整理稿…）时不出这条。语料依据：220 篇真实
    语料里这条命中 175 篇（80%），且**全部**来自军报/求是（junbao 114、
    qstheory 61），国务院公文 18 篇 0 命中 —— 因为媒体文章本来就不用
    「一、（一）1.」组织，提示"用层级词组织"对它们不成立。
    kind 为空（起草阶段尚未选文种）时仍然提示：那正是最需要它的场景。
    """
    out: list[Hint] = []
    paras = [ln for ln in text.split("\n") if len(ln.strip()) > 0]
    body = [ln for ln in paras if len(ln.strip()) > 40]
    gate_open = (not kind) or _is_legal_kind(kind)
    if (gate_open and len(body) >= W.PARA_LAYER_MIN
            and not _LAYER_RE.search(text)):
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
        # 文种匹配度：只在**同族**内比较（P1-18）。法定文种不出这条提示——
        # 其结构由制度锁定，识别基线是自撰示意样本，不足以在法定文种间判
        # "更像"；否则任何通知/请示/报告都会收到"更像经验总结"的误导性提示。
        # 7 种无固定模板文体维持原口径（直接与 match_genre 比，不经过
        # detect_kind，避免标题夹带法定套语时被法定基线抢位）。
        if kind and not _is_legal_kind(kind):
            top: list[tuple[str, float]] = []
            try:
                top = list(style_profile.match_genre(a))[:1]
            except Exception:
                top = []
            if top and top[0][0] != kind:
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
    依据时，同层级从 1 重新起算视为合法重启（分层文档的常态）。

    **「一是」（level 4）必须跨行连续判定**（P1-17）：一个「一是」在公文里
    通常独占一段，按行复位会把「一是…／二是…／三是…」整组判成跳号 —— 那是
    经验材料、调研报告、经验总结最主要的写法。现在只在出现更浅层级序号
    （即换了父级）时复位；同一父级内重新从「一是」起算按合法重启处理，
    与「（一）」「1.」一致，避免对"另起一组枚举"刷假提示。

    **不再因换父级而硬复位**（v1.14 校准修）：真实「国办发」意见常把二级
    序号（一）～（十七）**跨一级标题连续编号**（二、下面（一）～（四），
    三、下面接着（五）），硬复位会把（五）判成"跳号"。现在只保留"从 1
    重起算"这条放行规则，连续编号自然满足 expected。
    """
    out: list[Hint] = []
    markers = _collect_layer_markers(text)
    if not markers:
        return out
    next_val = {1: 1, 2: 1, 3: 1, 4: 1}
    last_val = {1: 0, 2: 0, 3: 0, 4: 0}
    last_label = {1: "", 2: "", 3: "", 4: ""}
    first_seen: dict[int, int] = {}

    for order, (line_no, level, value, _pos, label) in enumerate(markers):
        first_seen.setdefault(level, order)
        if value <= 0:
            continue
        expected = next_val[level]
        if value == expected:
            issue = ""
        elif last_val[level] and value == last_val[level]:
            issue = "重号"
        elif level in (2, 3, 4) and value == 1 and expected > 1:
            # 「（一）」「1.」「一是」都可随新章节/新一组枚举重新从 1 起；
            # 没有更浅层标题可依据时，把它当合法重启，避免对分层文档刷假提示。
            # 真重号在上一个分支已拦下（重号必是"与上一处相同的值"）。
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
    """感叹号/问号/省略号过量提示（公文以陈述为主）。

    两条判据取或（v1.14-220）：
      * **总量**：超过 ``W.PUNCT_*_TOTAL``（长文里偶发的引语/设问不触发）；
      * **密度**：超过 ``W.PUNCT_DENSITY`` 个/千字（短稿里 3 个问号也是语气成灾，
        只看总量会漏掉它——真实语料既有 24 字的短稿，也有 3000 字的长文）。
    """
    out: list[Hint] = []
    lines = _body_lines(text)
    body = "\n".join(lines)
    chars = max(1, len(body))

    def _over(count: int, total: int) -> bool:
        return count >= total or count * 1000.0 / chars >= W.PUNCT_DENSITY

    exclaim = body.count("！") + body.count("!")
    if exclaim:
        max_para = max((ln.count("！") + ln.count("!") for ln in lines), default=0)
        if _over(exclaim, W.PUNCT_EXCLAIM_TOTAL) or max_para >= W.PUNCT_PARA_MAX:
            out.append(Hint(
                "文风", "感叹号使用偏多",
                f"全文 {exclaim} 个、单段最多 {max_para} 个；公文以陈述为主，"
                "建议改用句号或调整语气", "info"))

    question = body.count("？") + body.count("?")
    if _over(question, W.PUNCT_QUESTION_TOTAL):
        out.append(Hint(
            "文风", "问号使用偏多",
            f"全文 {question} 个；除讲话稿的有意设问外，建议改为陈述句", "info"))

    ellipsis = len(re.findall(r"…{2,}|\.{3,}|。{3,}", body))
    if _over(ellipsis, W.PUNCT_ELLIPSIS_TOTAL):
        out.append(Hint(
            "文风", "省略号使用偏多",
            f"全文 {ellipsis} 处；公文一般不用省略号（引文省略除外），建议写全", "info"))
    return out


# ---------------------------------------------------------------- 检查 6/7：用语
def _vague_hints(text: str) -> list[Hint]:
    """模糊表述：复用 style_data.BAD_WORDS，并补充公文高频模糊词。

    只提示"可进一步具体化"，不判错；BAD_WORDS 的既有文案（「用语偏空泛：X」）
    与文风口径保持不变（不设门槛、不设上限）。

    ``W.VAGUE_WORDS`` 那张表**加出现门槛**（v1.14 降噪）：单次出现的「有关」
    「进一步」在真实公文里是规范用法（「国务院各有关部门」「进一步全面深化
    改革」），逐词必报等于每篇刷 2–5 条无关痛痒的建议。门槛取
    「≥ VAGUE_MIN_COUNT 次 或 密度 ≥ VAGUE_DENSITY‰」——重复出现才叫"偏空泛"。
    短稿因密度天然高仍会提示，起草阶段的逐条提醒不受影响。
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
    chars = max(1, len(text))
    extra = 0
    for word, advice in W.VAGUE_WORDS.items():
        if extra >= W.VAGUE_MAX_HINTS:
            break
        if word in seen or not word or word not in text:
            continue
        count = text.count(word)
        if count < W.VAGUE_MIN_COUNT and count * 1000.0 / chars < W.VAGUE_DENSITY:
            continue                    # 只出现一两次 → 属正常用法，不打扰
        seen.add(word)
        extra += 1
        out.append(Hint("文风", f"用语偏空泛：{word}", advice, "info"))
    return out


def _colloquial_hints(text: str) -> list[Hint]:
    """口语化/网络化表达 → 提示改写（长词优先匹配，避免「搞好」重复命中「搞」）。

    v1.14-220：**引号内的原话跳过** —— 新闻/讲话引用群众原话（「道路一定要
    搞清楚」）时，口语是**引语**不是作者的用语习惯，改它等于改别人的话。
    只在引号外出现才算"用语口语化"。
    """
    body = "\n".join(_body_lines(text))
    body = _QUOTED_TEXT_RE.sub("", body)          # 引语保留原话，不参与判定
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
        # 单字口语词（搞/弄/啥…）要求不止一次：一处「搞训练」「搞清楚」不足以
        # 判"用语口语化"，实测 220 篇里 15/23 的「搞」只是单次出现。
        if len(word) == 1 and body.count(word) < W.COLLOQUIAL_SINGLE_MIN_COUNT:
            continue
        emitted.add(word)
        out.append(Hint("用语", f"口语化表达：{word}",
                        W.COLLOQUIAL_WORDS[word], "info"))
        if len(out) >= W.COLLOQUIAL_MAX_HINTS:
            break
    return out


#: n-gram 扫描时把非汉字替换成的**断点**标记。**不能直接删掉**（v1.14 修）：
#: 删掉会把被数字/标点隔开的汉字段拼成假短语 —— 实测条例末尾的
#: 「2026年8月30日中华人民共和国国务院令第XXX号」被拼成
#: 「年月日中华人民共和国国务院令第号」，成了 16 字的"重复短语"。
_NGRAM_BREAK = "\u0000"


# ---------------------------------------------------------------- 检查 8：重复短语
def _longest_stable_phrase(plain: str, gram: str,
                           spots: list[int]) -> tuple[str, int]:
    """把 n-gram 向两侧扩展成"出现次数不减少"的最长短语。

    只保留所有出现位置都共有的扩展：这样报出的短语次数是准确的，也不会
    因为某两处恰好同头就把 4 字搭配扩成"安全生产是"这类半截搭配。
    扩展遇到 ``_NGRAM_BREAK``（数字/标点断点）即停止，不跨字段拼接。
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
        if ch == _NGRAM_BREAK or any(plain[p + width] != ch for p in nxt):
            break
        phrase += ch
        active = [p for p in active if p + width < len(plain)]
    # 向左扩展：所有出现位置的前一字符必须相同
    while count >= 2:
        prev = [p for p in active if p > 0]
        if len(prev) != count:
            break
        ch = plain[active[0] - 1]
        if ch == _NGRAM_BREAK or any(plain[p - 1] != ch for p in prev):
            break
        phrase = ch + phrase
        active = [p - 1 for p in active]
    return phrase, count


def _heading_terms(text: str) -> str:
    """取首行标题（去掉 Markdown 标记）—— 标题里的词组视为主题词。

    只把**标题形态**的首行当标题：不以句末标点收尾、长度在扫描上限内。
    否则单行草稿（整段就是一行）会把整篇当"标题"，所有短语都因"出现在标题里"
    被豁免——那样重复用语检查会静默失效（实测：一条用例因此从"报"变"不报"）。
    """
    for raw in (text or "").split("\n"):
        ln = _MD_HEAD_RE.sub("", raw.strip())
        ln = _MD_MARK_RE.sub("", ln).strip()
        if not ln:
            continue
        if len(ln) > W.TITLE_MAX_SCAN or ln.endswith(("。", "！", "？", "；")):
            return ""                    # 更像正文首段，不是标题
        return ln
    return ""


def _document_titles(text: str) -> set[str]:
    """文本里引用过的文件名（《…》内的书名/文件名）。

    公文引用既有文件时会把文件名（《政府信息公开条例》《全民健身计划…》）
    在一段里写两三遍，那是**规范的引用写法**，不是用语贫乏。校准实测：
    220 篇里重复用语残留 179 条，抽样后大半是文件名的反复引用。
    """
    return {m.group(1).strip() for m in _DOC_TITLE_RE.finditer(text or "")}


def _repeat_hints(text: str) -> list[Hint]:
    """同一段内 4 字以上短语重复 ≥``PHRASE_REPEAT_MIN_COUNT`` 次 → 用语贫乏提示。

    v1.14 校准后的四条收窄（每条都有真实语料依据）：
      1. **断点不复用**：n-gram 只在被数字/标点隔开的汉字段内算（``_NGRAM_BREAK``），
         否则会被拼出「年月日中华人民共和国国务院令第号」这类假短语；
      2. **标题主题词不报**：公文要求主题词全文统一（「政府信息」「伟大长征精神」
         本就是全文反复使用的对象），出现在标题里的短语不判"用语贫乏"；
      3. **首尾虚词不报**：「和国务院」「了中国共产党」「上闪耀在」是切词事故，
         不是重复短语（``W.PHRASE_EDGE_BLOCK``）；
      4. **≥3 次才报**：一段内出现两次在公文里是常态（专名＋回指）。
    """
    out: list[Hint] = []
    length = W.PHRASE_REPEAT_LEN
    head = _heading_terms(text)
    doc_titles = _document_titles(text)
    for para_no, para in enumerate(_body_lines(text), 1):
        if len(para) < length * 2:
            continue
        # 非汉字**替换**为断点（1:1 替换，长度不变），不删除 —— 删除会跨字段拼短语
        plain = _NGRAM_KEEP_RE.sub(_NGRAM_BREAK, para)[: W.PHRASE_SCAN_MAX_CHARS]
        if len(plain) < length * 2:
            continue
        positions: dict[str, list[int]] = {}
        for i in range(len(plain) - length + 1):
            gram = plain[i:i + length]
            if _NGRAM_BREAK in gram:
                continue
            positions.setdefault(gram, []).append(i)
        candidates: dict[str, int] = {}
        for gram, spots in positions.items():
            if len(spots) < W.PHRASE_REPEAT_MIN_COUNT:
                continue
            if any(gram in old for old in candidates):
                continue
            phrase, count = _longest_stable_phrase(plain, gram, spots)
            if len(phrase) < length or count < W.PHRASE_REPEAT_MIN_COUNT:
                continue
            # 「项项项项」这类同字重复多半来自表格/版式，不是用语贫乏
            if len(set(phrase)) <= 1:
                continue
            # 首尾虚词 → 半截搭配（切词事故），不是"短语重复"
            if phrase[0] in W.PHRASE_EDGE_BLOCK or phrase[-1] in W.PHRASE_EDGE_BLOCK:
                continue
            # 标题里的主题词 → 公文要求统一用词，不判贫乏
            if head and phrase in head:
                continue
            # 《…》内的文件名 → 引用既有文件时的规范重复，不判贫乏
            if doc_titles and any(phrase in t for t in doc_titles):
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
