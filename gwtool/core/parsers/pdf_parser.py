# -*- coding: utf-8 -*-
"""PDF 解析（文字版）：PyMuPDF 逐行提取 → 按版式几何还原自然段与标题。

为什么不能"一行一个 Block"（v1.10 修复的缺陷）
------------------------------------------------
旧实现把 `page.get_text("dict")` 里的**每一行**直接塞成一段。一个 90 字的
自然段在 A4 上排 3 行，于是变成 3 个段落：实测 12 个自然段的样张解析出
**26 个段落块**。汇编进 docx 后每行都独立成段、每段都首行缩进 2 字，版式
彻底散架 —— 而 PDF 恰恰是"只能拿到 PDF 的来文"的常见形态。

现在的聚类规则（全部基于版式几何，不猜内容）：
  1. **行首缩进** → 新段（公文每段首行缩进 2 字，续行顶格）；
  2. **上一行未写满右侧版心**（`right_edge - x1 > 1.6 字`）→ 该段已结束；
  3. **行距明显放大**（> 1.6 倍常规行距）→ 段落间距，新段；
  4. **字号变化** → 换段（同时用于标题判定）；
  5. 跨页：上一页末行写满了说明还在段中（正文跨页续排）→ 接着合并。
另外剔除页眉页脚里的孤立页码行（`— 3 —`、`- 3 -`、`第 3 页`），否则它们
会作为段落混进汇编成品。

标题识别（旧实现只看字号，同字号的「一、总体要求」永远认不出来）：
  · 字号 ≥ 正文 × 1.25 且不太长 → 一级标题；
  · 与正文同字号但匹配公文编号模式（`一、`/`（一）`/`1.`/`第X章`）且以
    非句末标点收尾 → 二级标题（复用 txt 解析器的同一套正则，口径不漂移）。

已知边界：双栏/多栏版式、表格结构（单元格会被并成段落）不做还原 —— 公文
以单栏为主，强行猜版式比不猜更容易出错；确需保真时请在原程序里另存 PDF。
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from ..model import Block, DocTree, HEADING, PARAGRAPH
from .txt_parser import _HEADING_RE

# 允许 import pymupdf（新）或 fitz（旧别名）
try:
    import pymupdf as _fitz
except ImportError:  # pragma: no cover
    import fitz as _fitz  # type: ignore

_FONT_SIZE_HEADING_RATIO = 1.25

# 几何阈值都以"字号"为单位换算，避免不同字号的文档口径不一
_INDENT_CHARS = 0.6        # 行首缩进超过 0.6 字 → 认为首行缩进
_UNFILLED_CHARS = 1.6      # 行尾空出 1.6 字以上 → 该行没写满（段末）
_GAP_RATIO = 1.6           # 行距超过常规的 1.6 倍 → 段落间距
_SIZE_TOL = 0.6            # 字号差超过 0.6pt → 换段
_MAX_HEADING_LEN = 50      # 公文中超过这个长度的行基本不可能是标题
_MAX_NUMBERED_LEN = 40     # 编号标题更短

# 句末标点：折行极少把"。"留在行中间，所以"行尾是句末标点"是强段落结束信号。
# 纯几何判据（行没写满 / 行首缩进）在两类真实 PDF 上会失效：
#   · 无首行缩进的 PDF（Word 默认两端对齐排版）—— 段落末行恰好写满时无从区分；
#   · 每段只有一两行的短段落 —— 所有行都"短"，右边界估计失真。
_SENTENCE_END = ("。", "！", "？", "；", "：", "”", "」", "』", "）", ")")
# 段落不可能以这些字符开头（标点禁则）：出现即说明是上一段的续行
_NO_PARA_START = "，。；：、）》」』”’,.!?;:)]}"

# 页眉页脚里的孤立页码：— 3 — / - 3 - / 第 3 页 / 共 20 页 / 纯数字 3
_PAGE_NO_RES = tuple(re.compile(p) for p in (
    r"^[—\-–]+\s*\d+\s*[—\-–]+$",
    r"^第\s*\d+\s*页(\s*[，,]?\s*共\s*\d+\s*页)?$",
    r"^\d{1,4}$",
))

# 页边区域（页面高度的上/下 12%）：只在这个带里才按"页码样式"剔除
_HEADER_BAND = 0.12
_FOOTER_BAND = 0.88


@dataclass
class _Line:
    """一行文本及其版式几何（坐标单位：PDF point，原点左上）。"""

    text: str
    size: float
    x0: float
    x1: float
    y0: float
    page: int


@dataclass
class _Layout:
    """整篇的版式基线：正文聚类判断都用它。"""

    body_size: float
    left: float          # 正文左边界（顶格线的 x0）
    right: float         # 正文右边界（写满行的 x1 上界）
    pitch: float         # 常规行距


def _is_page_number(text: str) -> bool:
    return any(r.match(text) for r in _PAGE_NO_RES)


def _collect_lines(doc) -> list[_Line]:
    """逐页提取所有正文行；剔除页眉/页脚区域的孤立页码行。"""
    lines: list[_Line] = []
    for pno, page in enumerate(doc):
        height = float(page.rect.height)
        for blk in page.get_text("dict").get("blocks", []):
            if blk.get("type") != 0:          # 只取文本块，跳过图片
                continue
            for ln in blk.get("lines", []):
                buf: list[str] = []
                size = 0.0
                for span in ln.get("spans", []):
                    t = span.get("text", "")
                    if t.strip():
                        buf.append(t)
                        size = max(size, float(span.get("size", 10)))
                text = "".join(buf).strip()
                if not text:
                    continue
                bbox = ln.get("bbox") or (0.0, 0.0, 0.0, 0.0)
                y0 = float(bbox[1])
                near_edge = y0 < height * _HEADER_BAND or y0 > height * _FOOTER_BAND
                if near_edge and _is_page_number(text):
                    continue                   # 页眉页脚页码，不是正文
                lines.append(_Line(text=text, size=size, x0=float(bbox[0]),
                                   x1=float(bbox[2]), y0=y0, page=pno))
    return lines


def _body_size(lines: list[_Line]) -> float:
    """正文（出现最多的）字号。

    并列时取**较小**的那个：公文标题总比正文大，而一页只有几行的稀疏 PDF
    （如单页通知）常出现"标题 1 行、正文 1 行"的等票局面 —— 取大者会把标题
    当正文、把正文当成异常，实测那种样张会丢掉标题。
    """
    if not lines:
        return 12.0
    counter = Counter(round(ln.size, 1) for ln in lines)
    return max(counter.items(), key=lambda kv: (kv[1], -kv[0]))[0]


def _layout_of(lines: list[_Line], body_size: float) -> _Layout:
    """估版式基线：左右边界 + 常规行距。"""
    body = [ln for ln in lines if abs(ln.size - body_size) <= _SIZE_TOL] or lines
    left = min(ln.x0 for ln in body)
    right = max(ln.x1 for ln in body)
    gaps: list[float] = []
    for prev, cur in zip(body, body[1:]):
        if cur.page != prev.page:
            continue
        gap = cur.y0 - prev.y0
        if 0 < gap < body_size * 4:
            gaps.append(gap)
    gaps.sort()
    pitch = gaps[len(gaps) // 2] if gaps else body_size * 1.3
    return _Layout(body_size=body_size, left=left, right=right, pitch=pitch)


def _heading_level(line: _Line, lay: _Layout) -> int:
    """0=正文；1/2=标题级别。字号优先，其次同字号编号模式。"""
    text = line.text
    if not text or len(text) > _MAX_HEADING_LEN:
        return 0
    if line.size >= lay.body_size * _FONT_SIZE_HEADING_RATIO:
        return 1
    # 同字号编号标题（「一、总体要求」「（一）加强领导」）：公文层级标题常与
    # 正文同字号，只靠字号永远识别不出来。
    if (len(text) <= _MAX_NUMBERED_LEN
            and _HEADING_RE.match(text)
            and not text.endswith(("。", "；", ";", "，", ","))):
        return 2
    return 0


def _is_cjk(ch: str) -> bool:
    return ("\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f"
            or "\uff00" <= ch <= "\uffef")


def _join(prev: str, nxt: str) -> str:
    """拼接折行文本：中文断行处不加空格，西文之间补一个空格。"""
    if not prev:
        return nxt
    if not nxt:
        return prev
    if _is_cjk(prev[-1]) or _is_cjk(nxt[0]):
        return prev + nxt
    return prev + " " + nxt


def _starts_new_paragraph(prev: _Line | None, cur: _Line, lay: _Layout) -> bool:
    """按版式几何判断 cur 是否开启新段。"""
    if prev is None:
        return True
    if abs(cur.size - prev.size) > _SIZE_TOL:
        return True                                  # 字号变了
    if cur.x0 - lay.left > _INDENT_CHARS * lay.body_size:
        return True                                  # 行首缩进 = 新段
    if prev.text.endswith(_SENTENCE_END) and cur.text[:1] not in _NO_PARA_START:
        return True                                  # 上一行以句末标点收尾 = 段末
    if lay.right - prev.x1 > _UNFILLED_CHARS * lay.body_size:
        return True                                  # 上一行没写满 = 段末
    if cur.page == prev.page and (cur.y0 - prev.y0) > _GAP_RATIO * lay.pitch:
        return True                                  # 段间距
    return False         # 上一行写满且无缩进 → 同一个自然段的续行（含跨页续排）


def _flush(blocks: list[Block], buf: list[str]) -> None:
    """把累计的行合成一个段落块。"""
    if not buf:
        return
    text = ""
    for piece in buf:
        text = _join(text, piece)
    text = re.sub(r"[ \t\u3000]{2,}", " ", text).strip()
    if text:
        blocks.append(Block(type=PARAGRAPH, text=text))
    buf.clear()


def parse_pdf(path: str) -> DocTree:
    tree = DocTree()
    doc = _fitz.open(path)
    try:
        lines = _collect_lines(doc)
    finally:
        doc.close()
    if not lines:
        return tree

    lay = _layout_of(lines, _body_size(lines))
    blocks: list[Block] = []
    buf: list[str] = []
    prev: _Line | None = None
    for line in lines:
        level = _heading_level(line, lay)
        if level:
            _flush(blocks, buf)
            blocks.append(Block(type=HEADING, level=level, text=line.text))
            prev = None            # 标题之后必然另起一段
            continue
        if _starts_new_paragraph(prev, line, lay):
            _flush(blocks, buf)
        buf.append(line.text)
        prev = line
    _flush(blocks, buf)

    tree.blocks = blocks
    if blocks and blocks[0].type == HEADING:
        tree.title = blocks[0].text
    return tree
