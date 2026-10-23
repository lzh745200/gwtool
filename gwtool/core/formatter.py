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

# C0 控制字符（保留 \t \n \r）+ DEL。\x0b/\x0c 先归一成换行，再清其余。
_CTRL_BREAK = str.maketrans({"\x0b": "\n", "\x0c": "\n"})
_CTRL_RX = re.compile(r"[\x00-\x08\x0e-\x1f\x7f]")


def strip_control_chars(text: str) -> tuple[str, int]:
    """清除正文里的不可见控制字符（保留 \\t \\n \\r），返回 (新文本, 处数)。

    **为什么要清**（v1.9b，审计 P1-05~07）：从 PDF 复制、旧 .doc 转换、
    OCR 识别的文本里常带 ``\\x00`` 这类空字节。它会一路进库，直到汇编/导出
    DOCX 时由 python-docx 抛

        ValueError: All strings must be XML compatible: Unicode or ASCII,
                    no NULL bytes or control characters

    —— 一句英文异常、且整批汇编直接失败（一篇坏材料拖垮整批）。
    ``\\x0b``/``\\x0c`` 在 Word 语义里是软换行/分页符，先转成换行再清别的，
    避免把"两段粘成一段"。
    """
    if not text:
        return text, 0
    before = len(text)
    n_break = sum(1 for ch in text if ch in "\x0b\x0c")
    new = text.translate(_CTRL_BREAK)
    new = _CTRL_RX.sub("", new)
    # 「处理处数」= 被删掉的字符数 + 被转成换行的字符数（转换不改变长度）
    return new, (before - len(new)) + n_break


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


def ensure_indent_placeholder(text: str,
                              indent_chars: float = 2.0) -> tuple[str, int]:
    """正文行首缩进**归一**：缩进只允许有一个事实来源——模板。

    老实现给每个正文段补「　　」（两个全角空格）"供纯文本视图"，而
    ``docxgen._add_body`` 又会按模板写 ``w:firstLineChars=200``（2 字首行缩进）
    —— 两者相加就是 **4 字**缩进。实测（审计 P1-09）：同一篇材料 PDF 里首行的
    横坐标 x0 = **143.4pt**，而正确值应为 **111.4pt**，差值 32pt 恰好是
    2 个三号字（16pt×2），字号越大越明显，且用户完全看不出是哪来的。

    所以这里改为**移除**行首占位（全角空格/半角空格/制表符），让模板的
    ``first_line_indent_chars`` 成为唯一来源：
      · ``indent_chars > 0``：模板承担缩进 → 正文里不该再带占位；
      · ``indent_chars <= 0``：用户明确选了"不缩进" → 同样不该带占位。
    两种情况都是"移除"，参数只用于日志措辞与调用方语义对齐。
    """
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
        stripped = s.lstrip("\u3000 \t")
        if stripped != s:
            n += 1
        out.append(stripped)
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


def run_full_cleanup(text: str,
                     template_indent_chars: float = 2.0) -> tuple[str, list[str]]:
    """依次执行全部微调，返回 (新文本, 日志列表)。

    ``template_indent_chars``：所用模板的 ``first_line_indent_chars``
    （默认 2，与 `DocTemplate` 默认值一致）。正文行首缩进按它**归一**，
    不再叠加（详见 `ensure_indent_placeholder`）。
    """
    log = []
    text, n = strip_control_chars(text)
    if n:
        log.append(f"清理不可见控制字符 {n} 处")
    text, n = cleanup_spaces(text)
    if n:
        log.append(f"清理多余空格/空行 {n} 处")
    text, n = full_to_half_digits(text)
    if n:
        log.append(f"全角数字转半角 {n} 处")
    text, n = normalize_paragraphs(text)
    if n:
        log.append(f"去除段首多余空白 {n} 行")
    # 缩进归一放在「标题编号规范化」之前：后者会 strip() 整行，把行首占位
    # 顺手吃掉却不记账 —— 先归一才能向用户交代"缩进由模板承担"。
    text, n = ensure_indent_placeholder(text, template_indent_chars)
    if n:
        if template_indent_chars > 0:
            log.append(f"归一首行缩进 {n} 行（改由模板 "
                       f"{template_indent_chars:g} 字缩进承担，不再叠加）")
        else:
            log.append(f"移除行首缩进占位 {n} 行（模板设为不缩进）")
    text, n = normalize_heading_numbers(text)
    if n:
        log.append(f"规范化标题编号 {n} 处")
    return text, log
