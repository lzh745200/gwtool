# -*- coding: utf-8 -*-
"""纠错规则集的 CSV 导入导出。

「单位内部规范词库」的实际用法是：某个科室整理一份 CSV，导入后作为独立来源
（source）存在，之后可以整体启用/停用、整体导出给别的电脑或同事。此前只有
导入没有导出，词库一旦入库就取不出来，也没法按来源整体开关。

编码约定：
  - 导出用 utf-8-sig（带 BOM），Excel 双击即可正确显示中文；
  - 导入复用 parsers.txt_parser.read_text_smart 做多编码探测
    （UTF-8/BOM/GBK/GB18030/UTF-16），因为用户拿来的 CSV 多半是 Excel
    在中文 Windows 上另存的 GBK。
"""
from __future__ import annotations

import csv
import io

from ..db import dao
from . import corrector
from .parsers.txt_parser import read_text_smart

# 导出列顺序，同时也是导入时识别表头的依据
HEADER = ("错误写法", "正确写法", "类别", "来源", "置信度", "启用")
_HEADER_ALIASES = {
    "错误写法": 0, "错": 0, "wrong": 0, "错误": 0,
    "正确写法": 1, "对": 1, "correct": 1, "正确": 1,
    "类别": 2, "分类": 2, "category": 2,
    "来源": 3, "source": 3,
    "置信度": 4, "confidence": 4,
    "启用": 5, "enabled": 5,
}


def export_error_pairs(path: str, source: str = "", category: str = "") -> int:
    """导出纠错对为 CSV（UTF-8-BOM），返回写出的行数。

    source/category 为空表示不限；否则只导出该规则集，便于单独分发。
    """
    pairs = dao.all_error_pairs(only_enabled=False)
    rows = [p for p in pairs
            if (not source or (p.source or "未标注") == source)
            and (not category or (p.category or "未分类") == category)]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADER)
        for p in rows:
            writer.writerow([p.wrong, p.correct, p.category or "", p.source or "",
                             f"{p.confidence:.2f}", "1" if p.enabled else "0"])
    return len(rows)


def _is_header(cells: list[str]) -> bool:
    """首行是表头而非数据：至少能对上两个已知列名。"""
    hits = sum(1 for c in cells if (c or "").strip().lower() in _HEADER_ALIASES
               or (c or "").strip() in _HEADER_ALIASES)
    return hits >= 2


def import_error_pairs(path: str, default_source: str = "用户导入",
                       default_category: str = "用户导入") -> dict[str, int]:
    """从 CSV 批量导入纠错对。

    兼容三种常见形态：
      1) 两列「错,对」（最简，类别与来源用默认值）
      2) 三列「错,对,类别」（dict_manager 原有导入格式）
      3) 六列完整表头（本模块导出的格式，含来源/置信度/启用）
    自动跳过表头、空行、以及错或对为空的行；同一「错→对」重复导入按
    INSERT OR REPLACE 覆盖，不会产生重复条目。

    返回 {"imported": n, "skipped": m, "rows": total}。
    """
    text = read_text_smart(path)
    reader = csv.reader(io.StringIO(text))
    imported = skipped = total = 0
    first = True
    for cells in reader:
        if first:
            first = False
            if _is_header(cells):
                continue
        cells = [(c or "").strip() for c in cells]
        if not any(cells):
            skipped += 1
            continue
        total += 1
        if len(cells) < 2 or not cells[0] or not cells[1]:
            skipped += 1
            continue
        wrong, correct = cells[0], cells[1]
        category = cells[2] if len(cells) > 2 and cells[2] else default_category
        source = cells[3] if len(cells) > 3 and cells[3] else default_source
        try:
            confidence = float(cells[4]) if len(cells) > 4 and cells[4] else 0.99
        except ValueError:
            confidence = 0.99
        confidence = min(max(confidence, 0.0), 1.0)
        # 导出文件里标了停用就保持停用，逐行精确处理，不要一导入就全启用
        enabled = True
        if len(cells) > 5:
            enabled = cells[5] not in ("0", "否", "false", "False", "no", "No")
        dao.add_error_pair(wrong, correct, category=category,
                           confidence=confidence, source=source, enabled=enabled)
        imported += 1

    # 纠错流水线缓存了词库，导入后必须失效，否则新规则本次会话内不生效
    corrector.invalidate_cache()
    return {"imported": imported, "skipped": skipped, "rows": total}
