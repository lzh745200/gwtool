# -*- coding: utf-8 -*-
"""一键排版微调：规范化文字层面问题，返回 (新文本, 变更日志)。"""
from __future__ import annotations

import re

# 公文标题编号链：一、 -> (一) -> 1. -> (1)
_H1 = re.compile(r"^[一二三四五六七八九十]+[、.]")
_H2 = re.compile(r"^[（(][一二三四五六七八九十]+[)）]")
_H3 = re.compile(r"^\d{1,2}[、．.](?!\d)")
_H4 = re.compile(r"^[（(]\d{1,2}[)）]")

_FULL2HALF_DIGIT = str.maketrans("０１２３４５６７８９", "0123456789")


def full_to_half_digits(text: str) -> tuple[str, int]:
    """全角数字 -> 半角（年份/日期规范）。"""
    def repl(m):
        return m.group(0).translate(_FULL2HALF_DIGIT)
    new, n = re.subn(r"[０-９]{2,}", repl, text)
    return new, n


def cleanup_spaces(text: str) -> tuple[str, int]:
    """去行尾空格/制表符；中文间多余空格；连续空行压为一行。"""
    n = 0
    lines = text.split("\n")
    out = []
    for ln in lines:
        stripped = ln.rstrip(" \t\u3000")
        if stripped != ln:
            n += 1
        # 中文之间的空格（非缩进语义）压缩为一个
        s2, k = re.subn(r"(?<=[\u4e00-\u9fff，。；：、）】])[ \t\u3000]+(?=[\u4e00-\u9fff，。；：、（【])",
                        " ", stripped)
        n += k
        out.append(s2)
    # 连续空行 >1 压缩为 1 个空行
    txt = "\n".join(out)
    txt, k = re.subn(r"\n{3,}", "\n\n", txt)
    return txt, n + k


def normalize_paragraphs(text: str) -> tuple[str, int]:
    """每个非空行作为自然段；行首多余空白去除（缩进由排版引擎保证）。"""
    n = 0
    lines = [ln for ln in text.split("\n")]
    out = []
    for ln in lines:
        s = ln.lstrip(" \t")
        if s != ln:
            n += 1
        out.append(s)
    return "\n".join(out), n


def ensure_indent_placeholder(text: str) -> tuple[str, int]:
    """正文段（非标题）补全角两空格首行缩进标记（供纯文本视图）。"""
    lines = text.split("\n")
    out = []
    n = 0
    for ln in lines:
        s = ln.rstrip()
        if not s:
            out.append(s)
            continue
        if _is_heading_line(s):
            out.append(s)
            continue
        if not s.startswith("\u3000"):
            out.append("\u3000\u3000" + s.lstrip("\u3000"))
            n += 1
        else:
            out.append(s)
    return "\n".join(out), n


def _is_heading_line(s: str) -> bool:
    if len(s) > 45:
        return False
    return bool(_H1.match(s) or _H2.match(s) or _H3.match(s) or _H4.match(s)
                or re.match(r"^第[一二三四五六七八九十百\d]+[章条]", s))


def normalize_heading_numbers(text: str) -> tuple[str, int]:
    """统一标题编号为规范层级：一、 / （一） / 1. / （1）。"""
    n = 0
    lines = text.split("\n")
    out = []
    for ln in lines:
        s = ln.strip()
        repl = s
        m = re.match(r"^([一二三四五六七八九十]+)[、..]\s*(.*)$", s)
        if m and len(s) <= 45:
            repl = f"{m.group(1)}、{m.group(2)}"
        m2 = re.match(r"^[（(]([一二三四五六七八九十]+)[)）]\s*(.*)$", s)
        if m2 and len(s) <= 45:
            repl = f"（{m2.group(1)}）{m2.group(2)}"
        m3 = re.match(r"^(\d{1,2})[、．.](?!\d)\s*(.*)$", s)
        if m3 and len(s) <= 45:
            repl = f"{m3.group(1)}.{m3.group(2)}"
        if repl != s:
            n += 1
        out.append(repl)
    return "\n".join(out), n


def run_full_cleanup(text: str) -> tuple[str, list[str]]:
    """依次执行全部微调，返回 (新文本, 日志列表)。"""
    log = []
    text, n = cleanup_spaces(text)
    if n:
        log.append(f"清理多余空格/空行 {n} 处")
    text, n = full_to_half_digits(text)
    if n:
        log.append(f"全角数字转半角 {n} 处")
    text, n = normalize_paragraphs(text)
    if n:
        log.append(f"去除段首多余空白 {n} 行")
    text, n = normalize_heading_numbers(text)
    if n:
        log.append(f"规范化标题编号 {n} 处")
    text, n = ensure_indent_placeholder(text)
    if n:
        log.append(f"补全首行缩进 {n} 段")
    return text, log
