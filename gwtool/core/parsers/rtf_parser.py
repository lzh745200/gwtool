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
        raw_para = para.strip()
        if not raw_para:
            continue
        if first:
            tree.title = raw_para
            first = False
            tree.blocks.append(Block(type=HEADING, level=1, text=raw_para))
            continue
        if _HEADING_RE.match(raw_para) and len(raw_para) <= 40:
            tree.blocks.append(Block(type=HEADING, level=2, text=raw_para))
        else:
            tree.blocks.append(Block(type=PARAGRAPH, text=raw_para))
    return tree
