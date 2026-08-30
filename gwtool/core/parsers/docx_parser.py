# -*- coding: utf-8 -*-
"""DOCX 解析：按文档顺序保留标题层级、段落与表格结构。"""
from __future__ import annotations

from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from ..model import Block, DocTree, HEADING, LIST_ITEM, PARAGRAPH, TABLE

# Word 内置标题样式名（中文 Word 可能是“标题 1”，英文是 Heading 1）
_HEADING_STYLES = {"标题", "heading"}


def parse_docx(path: str) -> DocTree:
    doc = DocxDocument(path)
    tree = DocTree()

    # 标题：优先 core_properties.title
    try:
        t = (doc.core_properties.title or "").strip()
    except Exception:
        t = ""
    tree.title = t

    # 按文档顺序遍历 body（doc.paragraphs 不含表格，会让表格内容整块丢失）
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            blk = _paragraph_block(Paragraph(child, doc))
            if blk is not None:
                tree.blocks.append(blk)
        elif child.tag == qn("w:tbl"):
            rows = _table_rows(Table(child, doc))
            if rows:
                tree.blocks.append(Block(type=TABLE, rows=rows))

    if not tree.title and tree.blocks and tree.blocks[0].type == HEADING:
        tree.title = tree.blocks[0].text
    return tree


def _paragraph_block(para: Paragraph) -> Block | None:
    text = para.text.strip()
    if not text:
        return None
    style_name = ""
    try:
        style_name = (para.style.name or "").lower()
    except Exception:
        pass
    level = 0
    if any(k in style_name for k in _HEADING_STYLES):
        # 标题 1 / heading 1 -> level 1
        digits = "".join(ch for ch in style_name if ch.isdigit())
        level = int(digits) if digits.isdigit() else 1
    if level:
        return Block(type=HEADING, level=min(level, 4), text=text)
    if para.style.name and "list" in para.style.name.lower():
        return Block(type=LIST_ITEM, text=text)
    # 大纲级别也可能写在段落属性里（outlineLvl）
    lvl = _outline_level(para)
    if lvl:
        return Block(type=HEADING, level=lvl, text=text)
    return Block(type=PARAGRAPH, text=text)


def _table_rows(table: Table) -> list[list[str]]:
    """表格 -> 二维文本（合并单元格会重复取值，保文本完整优先）。"""
    out: list[list[str]] = []
    try:
        for row in table.rows:
            out.append([" ".join(cell.text.split()) for cell in row.cells])
    except Exception:
        return []
    return out


def _outline_level(para) -> int:
    """读取段落 pPr/outlineLvl（0 基 -> 1 基级别）。"""
    try:
        pPr = para._p.pPr
        if pPr is not None and pPr.outlineLvl is not None:
            return int(pPr.outlineLvl.val) + 1
    except Exception:
        pass
    return 0
