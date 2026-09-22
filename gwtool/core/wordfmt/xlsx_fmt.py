# -*- coding: utf-8 -*-
"""XLSX 词表解析。

用自带的零依赖读取器 `core.xlsx_read`（**不是** openpyxl/pandas —— 项目红线）。
读回来的二维字符串直接交给 `tabular.rows_to_entries`，表头映射与 CSV 完全一致。

**只取第一张工作表**：多工作表文件里，别的表往往是"说明""修订记录"之类，
合并进来会导入一堆噪声。发现有多张表时给一条 warning 告知用户，
让他自己决定要不要拆开单独导 —— 比"默默合并"可预测得多。
"""
from __future__ import annotations

from ..xlsx_read import XlsxError, read_sheet, sheet_names
from . import ParseResult
from .tabular import rows_to_entries


def parse(path, role: str = "pairs", options=None) -> ParseResult:
    options = options or {}
    res = ParseResult(fmt="xlsx")
    try:
        # 只要表名就走 sheet_names —— 不要用 read_all(max_rows=1) 去"顺便"拿：
        # 那会对任何超过 1 行的表直接抛"行数超限"，纯属自伤。
        names = sheet_names(str(path))
    except XlsxError:
        raise
    except Exception as exc:
        raise ValueError("读不了这个 xlsx：%s" % exc) from exc

    want = options.get("sheet") or ""
    if want:
        rows = read_sheet(str(path), want)
        if want not in names:
            res.warn("指定的工作表「%s」不存在，已按读取结果处理" % want)
    else:
        rows = read_sheet(str(path), "")
        if len(names) > 1:
            res.warn("工作簿有 %d 张工作表，本次只取第一张「%s」"
                     "（其余：%s）；如需其它表请单独另存后再导入"
                     % (len(names), names[0], "、".join(names[1:5])))
    if not rows:
        res.warn("工作表里没有内容")
        return res
    # xlsx 的行号即二维下标 +1，与 Excel 界面的行号一致，便于用户按提示定位
    rows_to_entries(list(enumerate(rows, start=1)), role, res)
    return res
