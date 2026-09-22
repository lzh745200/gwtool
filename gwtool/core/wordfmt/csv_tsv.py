# -*- coding: utf-8 -*-
"""CSV / TSV / TXT 词表解析。

**修掉的四个历史问题**（此前 `ui/dict_manager._import_dict` 的实现）：
  1. 硬编码 ``utf-8-sig`` 打开 —— 中文 Windows 上 Excel 另存的 GBK 表**必然乱码**；
  2. 不识别表头，把表头行当数据导入；
  3. 只按"首行有无制表符"猜分隔符，分号分隔的表会整行变成一个字段；
  4. 无注释行支持。

分隔符判定改为**在前若干有效行上数候选字符**，取出现次数最多且各行列数一致的那个；
编码统一走 `parsers.txt_parser.read_text_smart`。
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from ..parsers.txt_parser import read_text_smart
from . import ParseResult
from .tabular import rows_to_entries

_CANDIDATES = (",", "\t", ";", "|")


def _effective_lines(text: str, limit: int = 20):
    """取前若干"有效行"（去空行与 # 注释），用于分隔符嗅探。"""
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        out.append(ln)
        if len(out) >= limit:
            break
    return out


def sniff_delimiter(text: str) -> str:
    """在候选分隔符里选一个：各行列数一致且列数 > 1 者优先，其次选总量最大者。

    ⚠️ 不做"取最大计数"这么简单：中文词表里的逗号常出现在字段**内部**
    （如「错误写法」列写"甲，乙"），只看总量会误选逗号。要求"各行列数一致"
    能有效排除这种噪声。
    """
    lines = _effective_lines(text)
    best, best_score = ",", (0, 0)
    for d in _CANDIDATES:
        counts = [ln.count(d) for ln in lines]
        if not counts or max(counts) == 0:
            continue
        cols = [c + 1 for c in counts]
        consistent = 1 if len(set(cols)) == 1 and cols[0] > 1 else 0
        score = (consistent, sum(counts))
        if score > best_score:
            best, best_score = d, score
    return best


def parse(path, role: str = "pairs", options=None) -> ParseResult:
    options = options or {}
    res = ParseResult(fmt=Path(path).suffix.lower().lstrip(".") or "txt")
    try:
        text = read_text_smart(str(path))
    except OSError as exc:
        raise ValueError("词表文件读不出来：%s" % exc) from exc

    if not text.strip():
        res.warn("文件是空的，没有任何条目")
        return res

    delimiter = options.get("delimiter") or sniff_delimiter(text)
    res.warn("分隔符：%s；编码已自动探测" % ("制表符" if delimiter == "\t"
                                          else "「%s」" % delimiter))
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = []
    for i, row in enumerate(reader, start=1):
        s = "".join(str(c or "") for c in row).strip()
        if not s or s.startswith("#"):
            continue                       # 空行 / 注释行：不算错
        rows.append((i, row))
    rows_to_entries(rows, role, res)
    return res
