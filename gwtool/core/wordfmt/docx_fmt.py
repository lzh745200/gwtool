# -*- coding: utf-8 -*-
"""DOCX 表格词表解析。

**为什么值得支持**：行业术语表在实务中大量以 Word 表格下发（科室在通知里附一张
表格让人填/用），比 CSV 更常见。python-docx 已在依赖清单里，零新增依赖。

只取**表格**，不解析正文段落：正文是散文，把每段当成词条会导入一屏噪声。
多张表时默认用**第一张**（与 xlsx 同一策略：可预测优于"默默合并"），
并在 warning 里说明 —— 有的文件首页是封面表、真正的词表在后面。
"""
from __future__ import annotations

from . import ParseResult
from .tabular import rows_to_entries


def _cell_text(cell) -> str:
    """单元格文本：合并单元格会重复取值，这里去重相邻重复。"""
    return (cell.text or "").replace("\r", "\n").strip()


def _table_rows(table) -> list:
    out = []
    for row in table.rows:
        cells = [_cell_text(c) for c in row.cells]
        # 整行重复（合并单元格在 python-docx 里会展开成同样内容）→ 只留一份
        if out and cells and cells == out[-1][1]:
            continue
        out.append((0, cells))
    return out


def parse(path, role: str = "pairs", options=None) -> ParseResult:
    options = options or {}
    res = ParseResult(fmt="docx")
    try:
        import docx                                   # python-docx，已在依赖里
    except ImportError as exc:
        raise ValueError("缺少 python-docx，无法读取 Word 词表：%s" % exc) from exc
    try:
        doc = docx.Document(str(path))
    except Exception as exc:
        raise ValueError("读不了这个 Word 文件：%s" % exc) from exc

    tables = [t for t in doc.tables if len(t.rows) >= 1]
    if not tables:
        res.warn("这个 Word 文件里没有表格；词表必须以**表格**形式给出"
                 "（正文段落不会被当作词条）")
        return res

    want = options.get("table")
    if want is not None:
        try:
            table = tables[int(want)]
        except (ValueError, IndexError):
            res.warn("指定的第 %s 张表格不存在，改用第一张" % want)
            table = tables[0]
    else:
        table = tables[0]
    if len(tables) > 1:
        res.warn("文件里有 %d 张表格，本次只取第一张"
                 "（其余未导入；如需其它表格请单独另存后再导入）" % len(tables))

    # 行号以"表格内的第几行"计，与用户在 Word 里看到的行数对应
    rows = [(i, cells) for i, (_, cells) in enumerate(_table_rows(table), start=1)]
    rows_to_entries(rows, role, res)
    return res
