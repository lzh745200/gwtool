# -*- coding: utf-8 -*-
"""GB/T 9704 格式体检报告导出。

体检结果原先只在对话框里看一眼就没了，无法归档、无法随文流转、也没法发给
拟稿人整改。本模块把 Finding 列表渲染成一份规范 DOCX 报告。

实现上不自己拼 docx：把报告组织成 DocTree（标题 + 摘要段 + 结果表格），
直接复用 docxgen.generate_docx，从而继承既有的公文字体、行距、页边距与
表格样式，避免另起一套排版逻辑与主文档不一致。
"""
from __future__ import annotations

from datetime import datetime

from .. import APP_NAME, __version__
from .corrector import ADVISORY_CATEGORIES, Correction
from .model import Block, DocTree, HEADING, PARAGRAPH, TABLE
from .inspector import Finding

_SEVERITY_LABEL = {"error": "不合规范", "warn": "建议修改", "info": "提示"}
_SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}


def summarize(findings: list[Finding]) -> dict[str, int]:
    """按严重度计数。"""
    out = {"error": 0, "warn": 0, "info": 0}
    for f in findings:
        out[f.severity] = out.get(f.severity, 0) + 1
    return out


def verdict(findings: list[Finding]) -> str:
    """一句话结论，用于报告开头与对话框标题。"""
    s = summarize(findings)
    if s["error"]:
        return f"发现 {s['error']} 项不合规范、{s['warn']} 项建议修改"
    if s["warn"]:
        return f"未发现硬性错误，有 {s['warn']} 项建议修改"
    if s["info"]:
        return f"基本符合规范，{s['info']} 项提示"
    return "未发现格式问题"


def build_report_tree(findings: list[Finding], source_name: str = "",
                      title: str = "公文格式体检报告") -> DocTree:
    """把体检结果组织成可交给 docxgen 渲染的 DocTree。"""
    ordered = sorted(findings,
                     key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.item))
    counts = summarize(findings)
    # 不要把中文写进 strftime 的格式串：Windows 会按 C 运行时的 locale 编码处理它，
    # 英文区域设置的机器上「年月日」无法编码，直接抛 UnicodeEncodeError。
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    stamp = f"{now[:4]}年{now[5:7]}月{now[8:10]}日 {now[11:]}"

    blocks: list[Block] = [
        Block(type=HEADING, level=1, text=title),
        Block(type=PARAGRAPH, text=f"体检对象：{source_name or '（未指定）'}"),
        Block(type=PARAGRAPH, text=f"体检时间：{stamp}"),
        Block(type=PARAGRAPH, text="检查依据：GB/T 9704《党政机关公文格式》"),
        Block(type=PARAGRAPH,
              text=f"体检结论：{verdict(findings)}"
                   f"（不合规范 {counts['error']} 项、建议修改 {counts['warn']} 项、"
                   f"提示 {counts['info']} 项）。"),
    ]

    if ordered:
        blocks.append(Block(type=HEADING, level=2, text="一、检查明细"))
        rows = [["序号", "结论", "检查项", "说明"]]
        for i, f in enumerate(ordered, 1):
            rows.append([str(i), _SEVERITY_LABEL.get(f.severity, f.severity),
                         f.item, f.detail])
        blocks.append(Block(type=TABLE, rows=rows))
    else:
        blocks.append(Block(type=PARAGRAPH,
                            text="本次体检未发现问题，无需整改。"))

    return DocTree(title=title, blocks=blocks)


def export_report(findings: list[Finding], out_path: str,
                  source_name: str = "", tpl=None) -> str:
    """导出体检报告为 DOCX，返回输出路径。

    tpl 为空时用默认公文模板；调用方也可传入当前正在用的模板，
    让报告与正文保持同一套版式。
    """
    from .docxgen import generate_docx
    from .template import default_template

    tree = build_report_tree(findings, source_name=source_name)
    return generate_docx([tree], tpl or default_template(), out_path)


def export_report_xlsx(findings: list[Finding], out_path: str,
                       source_name: str = "") -> int:
    """导出体检结果为 xlsx **整改清单**，返回写出的条目数。

    与 DOCX 报告并存：DOCX 适合归档与流转（有正式版式），
    xlsx 适合**逐条整改销号**——拟稿人可以在表里直接加"已改/待改"列筛选。
    两者面向的使用场景不同，故都保留。
    """
    from .xlsx import write_table

    ordered = sorted(findings,
                     key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.item))
    rows = [[_SEVERITY_LABEL.get(f.severity, f.severity), f.item, f.detail,
             "", ""] for f in ordered]
    title = f"{source_name}：体检整改清单" if source_name else "体检整改清单"
    return write_table(out_path,
                       ["严重程度", "检查项", "问题说明", "处理意见", "备注"],
                       rows, sheet_name=title)


# ================================================================== 校对报告
# 提示类条目的「建议」是规范提示标签，不是可替换文本（见
# corrector.ADVISORY_CATEGORIES）。报告是归档件、会被转交给执行修改的人，
# 若不在明细里显式标注，读者会把“建议”直接当成替换文本写回正文。
_ADVISORY_NOTE = "提示类，建议人工确认"

# 分节序号（最多三节）。与既有报告一致用中文序号，不用阿拉伯数字。
_SECTION_NO = ("一", "二", "三", "四", "五")


def _as_hit_list(hits) -> list[Correction]:
    """把命中拍平成一维 ``list[Correction]``。

    纠错结果天然是按块分组的（``AnyDocCorrectDialog._corrs`` 就是
    ``list[list[Correction]]``），但报告只关心“逐处明细”，块内偏移没有
    全局意义。这里同时接受一维列表与两层列表，调用方不必各自拍平 ——
    否则把 list 当命中渲染进表格，会变成 ``[Correction(...)]`` 这样的文本。
    """
    flat: list[Correction] = []
    for item in hits or []:
        if isinstance(item, (list, tuple)):
            flat.extend(_as_hit_list(item))
        elif item is not None:
            flat.append(item)
    return flat


def _category_counts(items: list[Correction]) -> list[tuple[str, int]]:
    """按类别计数，数量降序 + 类别名升序（保证报告可复现）。"""
    counts: dict[str, int] = {}
    for c in items:
        counts[c.category] = counts.get(c.category, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def _confidence_counts(items: list[Correction]) -> tuple[int, int, int, int]:
    """置信度分档：确认错误 / 疑似 / 提示类 / 低置信。

    与 ``AnyDocCorrectDialog._show_banner`` 同一口径：提示类（其 suggestion
    是标签）单独一档，不再按置信度重复计入“确认错误/疑似”。
    """
    sure = maybe = advice = low = 0
    for c in items:
        if c.category in ADVISORY_CATEGORIES:
            advice += 1
        elif c.confidence >= 0.85:
            sure += 1
        elif c.confidence >= 0.55:
            maybe += 1
        else:
            low += 1
    return sure, maybe, advice, low


def _correction_verdict(items: list[Correction]) -> str:
    """报告开头的一句话结论（与材料体检报告的 `verdict` 同一形态）。"""
    if not items:
        return "未发现问题"
    sure, maybe, advice, low = _confidence_counts(items)
    parts = [f"确认错误 {sure} 处", f"疑似 {maybe} 处", f"提示类 {advice} 处"]
    if low:
        parts.append(f"低置信 {low} 处")
    return f"共发现 {len(items)} 处问题（" + "、".join(parts) + "）"


def _section(no: int, text: str) -> Block:
    label = _SECTION_NO[no - 1] if 0 < no <= len(_SECTION_NO) else str(no)
    return Block(type=HEADING, level=2, text=f"{label}、{text}")


def build_correction_report_tree(hits, source_name: str = "",
                                 title: str = "公文校对报告",
                                 fixed_text: str = "") -> DocTree:
    """把一次校对的结果组织成可交给 docxgen 渲染的“校对留痕” DocTree。

    hits 接受一维 ``list[Correction]``，也接受按块分组的
    ``list[list[Correction]]``（见 `_as_hit_list`）。只报“第几处”，
    不报块内偏移 —— 命中可来自多个块/单元格，偏移没有全局意义。

    fixed_text 非空时追加“修正后正文”节：内容是调用方手上最新的正文，
    通常已包含用户应用过的修正。空 hits 也能出报告（结论“未发现问题”）。
    """
    items = _as_hit_list(hits)
    sure, maybe, advice, low = _confidence_counts(items)
    # 不要把中文写进 strftime 的格式串（Windows 按 C 运行时 locale 编码处理，
    # 英文区域设置机器上“年月日”无法编码，直接抛 UnicodeEncodeError）。
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    stamp = f"{now[:4]}年{now[5:7]}月{now[8:10]}日 {now[11:]}"

    blocks: list[Block] = [
        Block(type=HEADING, level=1, text=title),
        Block(type=PARAGRAPH, text=f"校对对象：{source_name or '（未指定）'}"),
        Block(type=PARAGRAPH, text=f"校对时间：{stamp}"),
        Block(type=PARAGRAPH,
              text="校对依据：《党政机关公文格式》GB/T 9704 与内置词库/规则"
                   f"（{APP_NAME} v{__version__}）。"),
        Block(type=PARAGRAPH, text=f"校对结论：{_correction_verdict(items)}。"),
        _section(1, "结论摘要"),
    ]

    if items:
        cat_text = "、".join(f"{cat} {n} 处" for cat, n in _category_counts(items))
        blocks.append(Block(type=PARAGRAPH, text=f"按类别：{cat_text}。"))
        conf = (f"按置信度：确认错误（≥0.85）{sure} 处、"
                f"疑似（0.55–0.85）{maybe} 处、提示类 {advice} 处")
        if low:
            conf += f"、低置信（<0.55）{low} 处"
        blocks.append(Block(type=PARAGRAPH, text=conf + "。"))
    else:
        blocks.append(Block(type=PARAGRAPH, text="按类别：无命中。"))
        blocks.append(Block(type=PARAGRAPH, text="按置信度：无命中。"))

    if items:
        blocks.append(_section(2, "校对明细"))
        rows = [["序号", "类别", "原文", "建议", "置信度", "依据"]]
        for i, c in enumerate(items, 1):
            suggestion = c.suggestion
            if c.category in ADVISORY_CATEGORIES:
                suggestion = f"〔{_ADVISORY_NOTE}〕{c.suggestion}"
            rows.append([str(i), c.category, c.wrong, suggestion,
                         f"{c.confidence:.2f}", c.reason or "—"])
        blocks.append(Block(type=TABLE, rows=rows))
        if advice:
            blocks.append(Block(
                type=PARAGRAPH,
                text=("注：标有「提示类，建议人工确认」的条目，其“建议”是规范性"
                      "提示标签，不是可直接替换的文本，请人工判断后修改。")))
        else:
            blocks.append(Block(
                type=PARAGRAPH,
                text="注：“原文/建议”为块内定位结果，请结合文意逐处确认后修改。"))
    else:
        blocks.append(Block(
            type=PARAGRAPH,
            text="本次校对未发现问题（未命中内置词库与规则），无需整改。"))

    if (fixed_text or "").strip():
        blocks.append(_section(3 if items else 2, "修正后正文"))
        blocks.append(Block(
            type=PARAGRAPH,
            text="以下为导出时正文的最新状态，已应用的修正均已包含在内。"))
        for line in fixed_text.splitlines():
            if line.strip():          # 空行不生成空段落，避免版面被拉散
                blocks.append(Block(type=PARAGRAPH, text=line))

    return DocTree(title=title, blocks=blocks)


def export_correction_report(hits, out_path: str, source_name: str = "",
                             tpl=None, fixed_text: str = "") -> str:
    """导出校对报告为 DOCX，返回输出路径。

    与 `export_report` 完全同一套路：DocTree -> docxgen.generate_docx，
    继承公文字体/行距/页边距与表格样式，不另起排版逻辑。
    tpl 为空时用默认公文模板；调用方可传入当前正文字体所用模板。
    """
    from .docxgen import generate_docx
    from .template import default_template

    tree = build_correction_report_tree(hits, source_name=source_name,
                                        fixed_text=fixed_text)
    return generate_docx([tree], tpl or default_template(), out_path)
