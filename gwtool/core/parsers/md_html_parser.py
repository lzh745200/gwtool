# -*- coding: utf-8 -*-
"""Markdown / HTML 解析：转纯文本并保留标题层级。"""
from __future__ import annotations

import re

from ..model import Block, DocTree, HEADING, LIST_ITEM, PARAGRAPH


def parse_md(path: str) -> DocTree:
    text = open(path, "r", encoding="utf-8", errors="replace").read()
    return _md_text_to_tree(text)


def _md_text_to_tree(text: str) -> DocTree:
    tree = DocTree()
    in_code = False
    first_h1_done = False
    for line in text.splitlines():
        s = line.rstrip()
        if s.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            if s.strip():
                tree.blocks.append(Block(type=PARAGRAPH, text=s.strip()))
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s.strip())
        if m:
            level = min(len(m.group(1)), 4)
            t = _clean_md(m.group(2))
            if not first_h1_done and level == 1:
                tree.title = t
                first_h1_done = True
            tree.blocks.append(Block(type=HEADING, level=level, text=t))
            continue
        t = _clean_md(s.strip())
        if not t:
            continue
        lm = re.match(r"^[-*+]\s+(.*)$|^\d+[.、]\s+(.*)$", t)
        if lm:
            tree.blocks.append(Block(type=LIST_ITEM, text=(lm.group(1) or lm.group(2)).strip()))
        else:
            tree.blocks.append(Block(type=PARAGRAPH, text=t))
    return tree


def _clean_md(s: str) -> str:
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"\*(.+?)\*", r"\1", s)
    s = re.sub(r"`(.+?)`", r"\1", s)
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)   # 图片
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)  # 链接取文字
    return s.strip()


def parse_html(path: str) -> DocTree:
    html = open(path, "r", encoding="utf-8", errors="replace").read()
    return html_text_to_tree(html)


def html_text_to_tree(html: str) -> DocTree:
    """从 HTML 片段（含网页复制粘贴的片段）提取结构化段落。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    title = (soup.title.get_text().strip() if soup.title else "")
    tree = DocTree(title=title)
    for el in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "br", "blockquote"]):
        if el.name == "br":
            continue
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        if el.name.startswith("h"):
            tree.blocks.append(Block(type=HEADING, level=min(int(el.name[1]), 4), text=text))
        elif el.name == "li":
            tree.blocks.append(Block(type=LIST_ITEM, text=text))
        elif el.name == "blockquote":
            tree.blocks.append(Block(type=PARAGRAPH, text=text, align="left"))
        else:
            tree.blocks.append(Block(type=PARAGRAPH, text=text))
    if not tree.blocks:
        text = soup.get_text("\n", strip=True)
        for line in text.splitlines():
            if line.strip():
                tree.blocks.append(Block(type=PARAGRAPH, text=line.strip()))
    if not tree.title and tree.blocks:
        tree.title = tree.blocks[0].text[:50]
    return tree
