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
        Block(type=PARAGRAPH, text=f"检查依据：GB/T 9704《党政机关公文格式》"),
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
