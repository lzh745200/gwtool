# -*- coding: utf-8 -*-
"""公文风格量化引擎：把文本量成指标，再对照文体指纹做三层判定。

**三个层次，只有第一层判错**：

1. **硬冲突** —— 判为*文种识别错误*（例如给工作方案写出了调研报告的层级结构）；
2. **软提示** —— 提醒但不判错（真实作品存在合法变体）；
3. **对照** —— 仅展示实测 / 中位 / 常见区间。

**为什么不拿均值当合格线**：真实优秀作品个体差异极大 —— 调研报告的「一是二是」
从 0 到 20 次都有，四分之一的作品完全不用；语料中场面写得最好的几篇逐篇跑自检，
**没有一篇全项落在常见区间内**。好文章不整齐，整齐的往往是平庸作品。
为了消掉提示把稿子改成最匀速的样子，是本模块明确反对的做法。

量法口径与上游 `check_params.py` 保持一致（参数表才有意义），但有两处已知弱环
已在代码里就地标注，不做「顺手修正」—— 改了量法就必须重算整张参数表。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import style_data as D

# ---------------------------------------------------------------- 统计口径
# 段落的判定。与上游一致：长度 > 40 且不是任何层级的标题行。
_PARA_MIN = 40
_PARA_SKIP = re.compile(r"^[一二三四五六七八九十]+、|^（[一二三四五六七八九十]+）|^\d+[．.]")
_H1_RE = re.compile(r"^\**([一二三四五六七八九十]+、)([^\n]{2,80})", re.M)
_H2_RE = re.compile(r"^\**（[一二三四五六七八九十]+）", re.M)
_H3_RE = re.compile(r"^\**\d+[．.]\s*\D", re.M)
_YISHI_RE = re.compile(r"[一二三四五六七八九]是[，、]?")
_PCT_RE = re.compile(r"\d+(?:\.\d+)?%")
_SHORT_QUOTE_RE = re.compile(r"[“\"][^”\"]{1,8}[”\"]")
#: 长引语只统计**中文弯引号**：ASCII 直角引号无法配对，
#: 会把相邻两个引号之间的正文整段误判为引语。
_LONG_QUOTE_RE = re.compile(r"[“][^”]{9,}[”]")
_PLACEHOLDER_RE = re.compile(r"【[^】]*】")
_STRIP_MD_RE = re.compile(r"\*\*|__|`")
_QUOTED_RE = re.compile(r"[“\"][^”\"]{0,20}[”\"]")

_QUOTE_WORDS = ("应当", "必须", "不得", "严禁")
_YQ_WORDS = ("要", "切实", "确保", "务必")
_JY_WORDS = ("建议", "可以", "鼓励", "支持")


def analyze(text: str) -> dict | None:
    """把文本量成指标字典；无有效正文时返回 None。

    字段含义见 `style_data.FIELD_LABEL`。所有"每千字"类指标单位是 ‰，
    其余是**每篇个数**（与参数表一致）。
    """
    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
    body_lines: list[str] = []
    for ln in lines:
        if re.match(r"^#{1,6}\s", ln):
            body_lines.append(re.sub(r"^#{1,6}\s*", "", ln))
        elif re.match(r"^(\||-{3,}|>)", ln):
            continue
        else:
            body_lines.append(ln)
    body = "\n".join(body_lines)
    plain = _STRIP_MD_RE.sub("", body)
    chars = len(plain.replace("\n", ""))
    if chars == 0:
        return None

    sents = [s for s in re.split(r"[。！？；]", plain) if len(s.strip()) >= 4]
    paras = [ln for ln in body_lines
             if len(ln) > _PARA_MIN and not _PARA_SKIP.match(ln)]

    h1_titles: list[str] = []
    for m in _H1_RE.finditer(body):
        t = re.split(r"[。！？；]", m.group(2))[0]
        t = re.split(r"　{2,}|\s{3,}", t)[0]
        h1_titles.append(t.strip()[:40])

    # 力度词统计前先剔除引号内内容：自造概念常带力度字样（如需求分类的
    # 「必须改」「可以缓」），计入后会顶破配额。经验总结的强制词配额只有
    # 0-1 个，任何误判都是致命的。
    depunct = _QUOTED_RE.sub("", plain)
    # 「不得不」是"只能"义，不是禁止义，需从「不得」中排除
    qz_text = depunct.replace("不得不", "＃＃＃")

    return dict(
        chars=chars,
        sent=round(sum(len(s) for s in sents) / len(sents), 1) if sents else 0,
        h1=len(h1_titles), h1_titles=h1_titles,
        h2=len(_H2_RE.findall(body)),
        h3=len(_H3_RE.findall(body)),
        yishi=len(_YISHI_RE.findall(plain)),
        dun=round(1000 * plain.count("、") / chars, 1),
        # ⚠ 已知识别弱环：`yq` 用子串计数，「要」会命中"需要/要求/重要"等词，
        # 故要求词的实测值系统性偏高。上游口径如此，改了就必须重算参数表。
        qz=sum(qz_text.count(w) for w in _QUOTE_WORDS),
        yq=sum(depunct.count(w) for w in _YQ_WORDS),
        jy=sum(depunct.count(w) for w in _JY_WORDS),
        # 占位符内的 % 不是真实数据（如【待补：xx%】），先剔除
        pct=len(_PCT_RE.findall(_PLACEHOLDER_RE.sub("", plain))),
        quote=len(_SHORT_QUOTE_RE.findall(plain)),
        quote_long=round(1000 * len(_LONG_QUOTE_RE.findall(plain)) / chars, 1),
        paras=len(paras),
        para_len=round(sum(len(p) for p in paras) / len(paras)) if paras else 0,
        ascii_quote=plain.count('"'),
        dash=plain.count("——"),
        sections=section_weights(body_lines),
        plain=plain,
    )


def section_weights(body_lines: list[str]) -> list[tuple[str, int, float]]:
    """按一级标题切分正文，返回各段 (名称, 字数, 占比%)。

    「重心分布」参数表测不到，而它恰是区分「单段独大型」与「均衡型」的关键 ——
    参数可以全部达标而文种写错（把调研报告写成单段独大，等于写成了经验材料）。
    """
    secs: list[list] = []
    cur: list = ["导语", 0]
    for ln in body_lines:
        if re.match(r"^\**[一二三四五六七八九十]+、", ln):
            secs.append(cur)
            cur = [re.sub(r"^\**", "", ln)[:16], 0]
        cur[1] += len(ln)
    secs.append(cur)
    tot = sum(n for _name, n in secs) or 1
    return [(name, n, round(100 * n / tot, 1)) for name, n in secs]


#: 参与文种匹配的指标（与上游 KEYS 一致）。
#: `quote_long` 只定义在党建族那一行，公文族各文种没有该字段而被跳过 ——
#: 因此实测长引语高会拉近党建族、拉远公文族，这正是辨族所需的方向。
_MATCH_KEYS = ("chars", "sent", "h1", "h2", "h3", "yishi", "dun", "qz", "yq",
               "jy", "pct", "quote_long")


def match_genre(a: dict) -> list[tuple[str, float]]:
    """按归一化距离给七个文体排序，返回 [(文体, 距离), ...]（越小越像）。

    距离 = 各指标 |实测-中位| / 尺度 的均值。尺度取 max(p75-p25, |中位|*0.3, 1)，
    这样"区间宽"的指标不会因为量纲大就主导结果。
    """
    out: list[tuple[str, float]] = []
    for g, r in D.REF.items():
        d, cnt = 0.0, 0
        for k in _MATCH_KEYS:
            if k not in r:
                continue
            lo, hi, mid = r[k]
            scale = max(hi - lo, abs(mid) * 0.3, 1)
            d += abs(a[k] - mid) / scale
            cnt += 1
        out.append((g, d / cnt))
    return sorted(out, key=lambda x: x[1])


def bar_pos(val: float, lo: float, hi: float) -> str:
    """把实测值画成"在 p25-p75 区间里的位置"示意。"""
    if hi <= lo:
        return "│" if val == lo else ("←低" if val < lo else "→高")
    pos = (val - lo) / (hi - lo)
    if pos < -0.15:
        return "←低"
    if pos > 1.15:
        return "高→"
    slot = max(0, min(9, int(pos * 9)))
    return "·" * slot + "●" + "·" * (9 - slot)


# ---------------------------------------------------------------- 判定结果
@dataclass
class Finding:
    """一条硬冲突或软提示。"""
    field_key: str = ""
    value: float = 0.0
    message: str = ""
    basis: str = ""


@dataclass
class Row:
    """一行参数对照。"""
    label: str = ""
    value: float = 0.0
    mid: float = 0.0
    lo: float = 0.0
    hi: float = 0.0
    bar: str = ""
    in_range: bool = True


@dataclass
class Verdict:
    """一次风格校验的完整结果。"""
    style: str = ""
    n: int = 0
    id_param: str = ""
    metrics: dict = field(default_factory=dict)
    hard: list[Finding] = field(default_factory=list)
    soft: list[Finding] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    focus: list[tuple[str, int, float]] = field(default_factory=list)
    focus_top: tuple[str, int, float] | None = None
    focus_note: str = ""
    title_avg: float = 0.0
    title_target: str = ""
    punct: list[str] = field(default_factory=list)
    bad_words: list[tuple[str, str]] = field(default_factory=list)
    quota_warn: str = ""
    ranking: list[tuple[str, float]] = field(default_factory=list)
    family_warn: str = ""

    @property
    def ok(self) -> bool:
        """是否通过硬冲突检查（**唯一判定"错"的依据**）。"""
        return not self.hard


def _label(style: str) -> str:
    return "党建族" if D.FAMILY_OF.get(style) == D.FAMILY_DANGJIAN else "公文族"


def verdict(text: str, style: str) -> Verdict:
    """对文本做指定文体的风格校验。

    `style` 必须是 `style_data.STYLES` 之一 —— 调用方（UI）已按此约束提供选项，
    此处不重复做合法性兜底，让越界立刻暴露而不是静默给出空报告。
    """
    a = analyze(text)
    if a is None:
        v = Verdict(style=style, n=D.REF[style]["n"], id_param=D.ID_PARAM[style])
        v.hard.append(Finding("chars", 0, "文件为空或无法解析", "需有正文才能校验"))
        return v

    r = D.REF[style]
    v = Verdict(style=style, n=r["n"], id_param=D.ID_PARAM[style], metrics=a)

    # ① 硬冲突：只有这一层判错
    for key, ok_fn, msg, basis in D.HARD.get(style, []):
        if not ok_fn(a[key]):
            v.hard.append(Finding(key, a[key], msg, basis))

    # ② 软提示
    for key, ok_fn, msg, basis in D.SOFT.get(style, []):
        if not ok_fn(a[key]):
            v.soft.append(Finding(key, a[key], msg, basis))

    # ③ 参数对照
    for key, label in D.FIELD_LABEL.items():
        if key not in r:
            continue
        lo, hi, mid = r[key]
        val = a[key]
        v.rows.append(Row(label, val, mid, lo, hi,
                          bar_pos(val, lo, hi), lo <= val <= hi))

    # ③b 重心分布（仅对照，不判错）
    if style in D.FOCUS and len(a["sections"]) > 1:
        lo, hi, mid, note = D.FOCUS[style]
        v.focus = list(a["sections"])
        body_secs = [s for s in a["sections"] if s[0] != "导语"]
        if body_secs:
            v.focus_top = max(body_secs, key=lambda s: s[2])
        v.focus_note = (f"最重一段语料中位 {mid}%，常见 {lo}-{hi}%（{note}）；"
                        f"个体离散度大，此项区间外不作为错误")

    # ③c 一级标题字数
    titles = a.get("h1_titles") or []
    if titles:
        v.title_avg = round(sum(len(t) for t in titles) / len(titles), 1)
        # 目标区间**取自参数表**（`style_data.TITLE_LENGTH`）—— 此前这里硬编码
        # "4-12/14-24"，与参数表及《公文风格量化指标与验收标准》的 "4-8/17-18"
        # 对不上：界面与文档互相矛盾，且两处维护必然继续漂移。
        _tl = dict(D.TITLE_LENGTH)
        v.title_target = (
            f"{_tl.get('一级标题·业务类（方案/意见）', '')}（业务类）"
            if style in ("工作意见", "工作方案")
            else f"{_tl.get('一级标题·分析类（调研/讲话/经验）', '')}（分析类）")

    # ③d 标点修辞
    punct: list[str] = []
    if a["dash"] > 1:
        punct.append(f"正文破折号 {a['dash']} 个"
                     f"（语料平均 0.2-0.9 个/篇，建议不超过 1 个）")
    non_quote_colons = []
    for cm in re.finditer(r"([^\n]{0,12})：", a["plain"]):
        before = cm.group(1).strip()
        if re.search(r"[一二三四五六七八九十]+、|（[一二三四五六七八九十]）|\d+[．.]", before):
            continue
        if re.search(r"说|问|讲|提|指出|反映|回忆|告诉|表示|答", before):
            continue
        if re.search(r"人民政府|部门|单位|书记|镇长|同志|负责人", before):
            continue
        if "待补" in before or "待核" in before:
            continue
        non_quote_colons.append(cm.group(0)[:25])
    if non_quote_colons:
        punct.append(f"非引语冒号 {len(non_quote_colons)} 处"
                     f"（冒号应只用于引语引入、主送机关、层次标题）")
    # 元评论词：排除引号内（引语里的口语保留）
    meta = re.findall(r"其实[是就]|说到底|归根到底|本质上",
                      re.sub(r"[“][^”]*[”]", "", a["plain"]))
    if meta:
        punct.append(f"元评论词 {len(meta)} 处（{', '.join(sorted(set(meta)))}）")
    if a["ascii_quote"]:
        punct.append(f"检出 {a['ascii_quote']} 个 ASCII 直角引号（\"）。"
                     f"语料使用中文弯引号，混用会使引号概念与长引语统计失真")
    v.punct = punct

    # ⑤ 避坑词
    v.bad_words = [(w, why) for w, why in D.BAD_WORDS.items() if w in a["plain"]]

    # ⑥ 力度词配额：改稿时最易踩的坑
    if "qz" in r and a["qz"] > r["qz"][1]:
        v.quota_warn = (f"强制词超配额（{a['qz']} > {r['qz'][1]}）。"
                        f"「要」属要求词、「必须/应当」属强制词，两者配额独立"
                        f"且相差一个量级 —— 把「要」整批替换成「必须」会直接顶破上限。")

    # ④ 文种匹配度：跨族误配信号最强，同族内只作参考
    v.ranking = match_genre(a)
    top = v.ranking[0][0]
    if _label(style) != _label(top):
        v.family_warn = (f"你声明「{style}」（{_label(style)}），但参数上更像"
                         f"「{top}」（{_label(top)}）。两族参数互斥，判错族后"
                         f"所有参数都会偏。")
    return v


def skeleton_draft(style: str) -> str:
    """按该文体的骨架公式生成**结构草稿**（L3：结构复用，不编造内容）。

    做法：把 `style_data.SKELETON[style]["formula"]` 里的 `[段名]` 逐一取出，
    落成一级标题占位 + `【待补：本节内容】` 正文位；再附上重心占比、段内主力标记
    与生成清单，供作者照着填。

    **为什么正文位一律写【待补】而不是先写点像样的句子**：生成侧铁律 G1 —— 无法
    核实的内容一律占位、绝不编造。参数表也证明这条路径可行：51%-100% 的真实公文
    完全不用百分比，靠逻辑深度撑起文章是被语料验证过的写法。先塞套话反而会把
    "还没想清楚"掩盖过去。
    """
    info = D.SKELETON.get(style)
    if not info:
        return ""
    segments = re.findall(r"\[([^\[\]]+)\]", info["formula"])
    lines: list[str] = ["【待补：标题】", ""]
    if segments:
        cn = "一二三四五六七八九十"
        for i, seg in enumerate(segments):
            num = cn[i] if i < len(cn) else str(i + 1)
            lines.append(f"{num}、{seg}")
            lines.append("")
            lines.append(f"【待补：{seg}部分内容】")
            lines.append("")
    if info.get("variant"):
        lines.append(f"（备选结构：{info['variant']}）")
        lines.append("")
    lines.append("—— 写作约束（照此填写）——")
    lines.append(f"· 重心：{info['focus']}")
    lines.append(f"· 段内主力标记：{info['marker']}")
    lines.append(f"· 该文种意图：{info['intent']}")
    if info.get("note"):
        lines.append(f"· 结构要点：{info['note']}")
    if style in D.REF:
        r = D.REF[style]
        lines.append(f"· 篇幅目标：约 {r['chars'][2]} 字"
                     f"（常见 {r['chars'][0]}-{r['chars'][1]}）")
    lines.append("· 身份证参数：" + D.ID_PARAM[style])
    for tag, rule in D.GENERATE_RULES:
        lines.append(f"· {tag}：{rule}")
    return "\n".join(lines)


def report_lines(v: Verdict) -> list[str]:
    """把判定结果整成**可直接展示的报告文本**（输出契约的十项）。"""
    out: list[str] = []
    out.append(f"文体：{v.style}　语料样本 n={v.n}")
    out.append(f"身份证参数：{v.id_param}")
    out.append("")

    out.append("【硬冲突检查】判文种识别错误，非风格差异")
    if not v.hard:
        total = len(D.HARD.get(v.style, []))
        out.append(f"✓ 通过（{total} 项）")
    else:
        for f in v.hard:
            out.append(f"✗ {f.message}")
            out.append(f"   实测 {f.field_key}={f.value}   依据：{f.basis}")
    out.append("")

    if v.soft:
        for f in v.soft:
            out.append(f"⚠ {f.message}")
            out.append(f"   实测 {f.field_key}={f.value}   {f.basis}")
        out.append("")

    out.append("【参数对照】仅供参考，偏离不等于错误")
    out.append(f"{'指标':<10}{'实测':>9}{'中位':>9}{'常见区间':>14}   位置")
    for row in v.rows:
        flag = "" if row.in_range else "  ⚠偏离"
        out.append(f"{row.label:<10}{row.value:>9}{row.mid:>9}"
                   f"{f'{row.lo}-{row.hi}':>14}   {row.bar}{flag}")
    out.append("")

    if v.focus_top:
        out.append("【重心分布】仅供对照，偏离不等于错误")
        for name, n, pct in v.focus:
            mark = " ←最重" if v.focus_top and name == v.focus_top[0] else ""
            out.append(f"{pct:>6.1f}%  {n:>5}字  {name}{mark}")
        out.append(f"   {v.focus_note}")
        out.append("")

    if v.title_avg:
        out.append(f"一级标题平均 {v.title_avg} 字，目标 {v.title_target}")

    # 标点修辞与避坑词**恒定输出**：报告是"十项契约"，缺项会让读者以为
    # "这一项没检查"，而实际是"检查了、没问题"——两者语义完全不同。
    out.append("")
    out.append("【标点修辞检查】")
    if v.punct:
        for p in v.punct:
            out.append(f"⚠ {p}")
    else:
        out.append("✓ 未检出（破折号 / 非引语冒号 / 元评论词 / ASCII 引号均正常）")

    out.append("")
    out.append("【避坑词】")
    if v.bad_words:
        for w, why in v.bad_words:
            out.append(f"⚠ 「{w}」— {why}")
    else:
        out.append("✓ 未命中")

    if v.quota_warn:
        out.append("")
        out.append(f"⚠ {v.quota_warn}")

    if v.family_warn:
        out.append("")
        out.append("【文体族检查】")
        out.append(f"⚠ {v.family_warn}")
        out.append("   距离：" + "  ".join(f"{g} {d:.2f}" for g, d in v.ranking[:3]))
    elif v.style in [g for g, _ in v.ranking[3:]]:
        rank = [g for g, _ in v.ranking]
        out.append("")
        out.append("【结构习惯提示】")
        out.append(f"ⓘ 参数上「{v.style}」排在第 {rank.index(v.style) + 1} 位，"
                   f"前三位是 {'、'.join(rank[:3])}")
        out.append("   同族文种参数重叠大，此信号仅供参考，不代表文种判错。")
    return out
