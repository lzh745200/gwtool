# -*- coding: utf-8 -*-
"""TXT 解析：多编码自动探测。"""
from __future__ import annotations

from pathlib import Path

from ..model import Block, DocTree, HEADING, PARAGRAPH

import re

# 常见公文中可作为标题的行模式
_HEADING_RE = re.compile(
    r"^(第?[一二三四五六七八九十百]+[、章节部分．.]\S*)"
    r"|^（[一二三四五六七八九十]+）"
    r"|^\([一二三四五六七八九十]+\)"
    r"|^第[一二三四五六七八九十百\d]+章"
    r"|^\d{1,2}[、．.]"
)


def read_text_smart(path: str) -> str:
    """多编码探测读取：UTF-8/UTF-8-BOM/GBK/GB18030/UTF-16。"""
    raw = Path(path).read_bytes()
    # BOM 快速路径
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace")
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    try:
        import chardet
        guess = chardet.detect(raw[:65536])
        enc = (guess.get("encoding") or "utf-8").lower()
    except Exception:
        enc = "utf-8"
    for e in (enc, "utf-8", "gb18030", "big5"):
        if not e:
            continue
        try:
            return raw.decode(e)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def parse_txt(path: str) -> DocTree:
    text = read_text_smart(path)
    tree = DocTree()
    first = True
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if first:
            tree.title = line
            first = False
            # 首行若是明显标题行，也作为标题块
            if _HEADING_RE.match(line) or len(line) <= 30:
                tree.blocks.append(Block(type=HEADING, level=1, text=line))
            else:
                tree.blocks.append(Block(type=PARAGRAPH, text=line))
            continue
        if _HEADING_RE.match(line) and len(line) <= 40:
            tree.blocks.append(Block(type=HEADING, level=2, text=line))
        else:
            tree.blocks.append(Block(type=PARAGRAPH, text=line))
    return tree
