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

from .. import logs
from ..db import connection as dbconn
from ..db import dao
from . import corrector
from .parsers.txt_parser import read_text_smart
from .wordfmt.csv_tsv import sniff_delimiter

log = logs.get_logger("ruleset")

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
    """从 CSV/TSV 批量导入纠错对。

    兼容三种常见形态：
      1) 两列「错,对」（最简，类别与来源用默认值）
      2) 三列「错,对,类别」（dict_manager 原有导入格式）
      3) 六列完整表头（本模块导出的格式，含来源/置信度/启用）
    自动跳过表头、空行、``#`` 注释行、以及错或对为空的行；同一「错→对」
    重复导入按 INSERT OR REPLACE 覆盖，不会产生重复条目。

    返回 {"imported": n, "skipped": m, "rows": total, "issues": [...]}。
    ``issues`` 是逐行原因清单（此前只有计数、没有原因，用户拿到"跳过了 3 行"
    却不知道是哪 3 行、为什么）。

    **整批单事务**：任一行写入失败 → 全部回滚。此前逐行各自 commit，
    中途失败会留下"导了一半"的库，而用户以为整批都成功了。
    """
    text = read_text_smart(path)
    delimiter = sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    conn = dbconn.get_conn()
    imported = skipped = total = 0
    issues: list[tuple[int, str]] = []
    # 表头判定针对**第一条非注释、非空行**，而不是"文件的第 1 行"。
    # 原先用 first 标志只看第 1 行：若文件以 `# 说明` 开头（很常见），
    # 表头就落到第 2 行、错过判定，被当成数据导入。
    header_checked = False
    try:
        for line_no, row in enumerate(reader, start=1):
            # 用新名字承接规范化结果，不覆盖循环变量本身：
            # 覆盖循环变量会让后续（或嵌套循环里）对它的引用指向被改写过的值，
            # 这类"改了别名而非元素"的写法是静态检查与人工复核都容易漏掉的隐患。
            cells = [(c or "").strip() for c in row]
            if not any(cells):
                skipped += 1
                continue
            if cells[0].startswith("#"):
                skipped += 1              # 注释行：计入 skipped，但不计入 total
                continue
            if not header_checked:
                header_checked = True
                if _is_header(cells):
                    continue
            total += 1
            if len(cells) < 2 or not cells[0] or not cells[1]:
                skipped += 1
                issues.append((line_no, "列数不足或「错/对」为空"))
                continue
            wrong, correct = cells[0], cells[1]
            if wrong == correct:
                skipped += 1
                issues.append((line_no, "前后相同，无意义"))
                continue
            category = cells[2] if len(cells) > 2 and cells[2] else default_category
            source = cells[3] if len(cells) > 3 and cells[3] else default_source
            try:
                confidence = float(cells[4]) if len(cells) > 4 and cells[4] else 0.99
            except ValueError:
                confidence = 0.99
                issues.append((line_no, "置信度不是数字，按 0.99 处理"))
            confidence = min(max(confidence, 0.0), 1.0)
            # 导出文件里标了停用就保持停用，逐行精确处理，不要一导入就全启用
            enabled = True
            if len(cells) > 5:
                enabled = cells[5] not in ("0", "否", "false", "False", "no", "No")
            # ⚠️ 直接写 SQL、不调 dao.add_error_pair：后者自带 conn.commit()，
            # 一旦中途提交前面的行就落盘了，整批回滚随即失效。
            conn.execute(
                "INSERT OR REPLACE INTO error_pairs(wrong,correct,category,"
                "confidence,enabled,source) VALUES(?,?,?,?,?,?)",
                (wrong, correct, category, confidence, 1 if enabled else 0, source))
            imported += 1
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception as rb_exc:
            # 不让"回滚失败"顶掉原始异常（那会掩盖真正的病因），
            # 但它意味着库可能停在**半途状态** —— 必须留痕可查。
            log.warning("导入失败后回滚也失败（%s），库可能处于中间状态", rb_exc)
        raise

    # 纠错流水线缓存了词库，导入后必须失效，否则新规则本次会话内不生效
    corrector.invalidate_cache()
    return {"imported": imported, "skipped": skipped, "rows": total,
            "issues": issues, "delimiter": delimiter}
