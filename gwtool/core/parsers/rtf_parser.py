# -*- coding: utf-8 -*-
"""RTF 解析：striprtf 提取纯文本。"""
from __future__ import annotations

from ..model import Block, DocTree, HEADING, PARAGRAPH
from .txt_parser import _HEADING_RE


def parse_rtf(path: str) -> DocTree:
    from striprtf.striprtf import rtf_to_text
    raw = open(path, "rb").read()
    try:
        text = rtf_to_text(raw.decode("gb18030", errors="ignore"))
    except Exception:
        text = rtf_to_text(raw.decode("latin-1", errors="ignore"))
    tree = DocTree()
    first = True
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            continue
        if first:
            tree.title = para
            first = False
            tree.blocks.append(Block(type=HEADING, level=1, text=para))
            continue
        if _HEADING_RE.match(para) and len(para) <= 40:
            tree.blocks.append(Block(type=HEADING, level=2, text=para))
        else:
            tree.blocks.append(Block(type=PARAGRAPH, text=para))
    return tree
