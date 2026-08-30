# -*- coding: utf-8 -*-
"""DOCX 解析：保留标题层级与段落结构。"""
from __future__ import annotations

from docx import Document as DocxDocument

from ..model import Block, DocTree, HEADING, LIST_ITEM, PARAGRAPH

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

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
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
            tree.blocks.append(Block(type=HEADING, level=min(level, 4), text=text))
        elif para.style.name and "list" in para.style.name.lower():
            tree.blocks.append(Block(type=LIST_ITEM, text=text))
        else:
            # 大纲级别也可能写在段落属性里（outlineLvl）
            lvl = _outline_level(para)
            if lvl:
                tree.blocks.append(Block(type=HEADING, level=lvl, text=text))
            else:
                tree.blocks.append(Block(type=PARAGRAPH, text=text))

    if not tree.title and tree.blocks and tree.blocks[0].type == HEADING:
        tree.title = tree.blocks[0].text
    return tree


def _outline_level(para) -> int:
    """读取段落 pPr/outlineLvl（0 基 -> 1 基级别）。"""
    try:
        pPr = para._p.pPr
        if pPr is not None and pPr.outlineLvl is not None:
            return int(pPr.outlineLvl.val) + 1
    except Exception:
        pass
    return 0
