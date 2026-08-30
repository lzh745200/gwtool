# -*- coding: utf-8 -*-
"""PDF 解析（文字版）：PyMuPDF 逐页提取，按行合并段落。"""
from __future__ import annotations

import re

from ..model import Block, DocTree, HEADING, PARAGRAPH

# 允许 import pymupdf（新）或 fitz（旧别名）
try:
    import pymupdf as _fitz
except ImportError:  # pragma: no cover
    import fitz as _fitz  # type: ignore

_FONT_SIZE_HEADING_RATIO = 1.25


def parse_pdf(path: str) -> DocTree:
    tree = DocTree()
    doc = _fitz.open(path)
    try:
        lines: list[tuple[float, str]] = []  # (字号, 文本)
        for page in doc:
            d = page.get_text("dict")
            for blk in d.get("blocks", []):
                if blk.get("type") != 0:
                    continue
                for line in blk.get("lines", []):
                    buf = []
                    size = 0.0
                    for span in line.get("spans", []):
                        t = span.get("text", "")
                        if t.strip():
                            buf.append(t)
                            size = max(size, float(span.get("size", 10)))
                    text = "".join(buf).strip()
                    if text:
                        lines.append((size, text))
        # 估算正文字号 = 出现最多的字号
        from collections import Counter
        counter = Counter(round(s) for s, _ in lines)
        body_size = counter.most_common(1)[0][0] if counter else 12
        for size, text in lines:
            if size >= body_size * _FONT_SIZE_HEADING_RATIO and len(text) <= 50:
                tree.blocks.append(Block(type=HEADING, level=1, text=text))
            else:
                tree.blocks.append(Block(type=PARAGRAPH, text=text))
    finally:
        doc.close()
    if tree.blocks and tree.blocks[0].type == HEADING:
        tree.title = tree.blocks[0].text
    return tree
