# -*- coding: utf-8 -*-
"""表格式词表（CSV / TSV / TXT / XLSX / DOCX 表格）的公共逻辑。

三种表格式的差异只在"怎么把单元格读出来"，**表头映射与角色解释完全一致**，
故抽到这里统一实现 —— 否则每加一种格式就要重写一遍列名别名表，
迟早出现"CSV 认得的表头 xlsx 不认得"这类漂移。
"""
from __future__ import annotations

from . import Entry

# 列名别名 → 字段。中英文都收，因为用户拿到的表可能是任何一种。
# ⚠️ 匹配一律先 strip + lower（见 `map_header`），所以这里只需给出规范形。
COL_ALIASES = {
    "wrong": ("错误写法", "错", "wrong", "错误", "异名", "非规范写法",
              "别称", "简称", "旧称", "错误用法", "不规范写法"),
    "correct": ("正确写法", "对", "correct", "正确", "规范名", "规范写法",
                "标准术语", "标准写法", "推荐写法", "standard", "规范"),
    "word": ("词", "词语", "词条", "word", "词表", "术语词", "名称"),
    "category": ("类别", "分类", "category", "类", "专业", "领域"),
    "confidence": ("置信度", "confidence", "可信度"),
    "source": ("来源", "source"),
    "pinyin": ("拼音", "pinyin"),
    "definition": ("释义", "定义", "definition", "说明", "含义"),
    "role": ("类型", "角色", "role", "词表类型", "条目类型"),
}

# 角色 → "两列时第 1、2 列分别是什么字段"
_DEFAULT_COLS = {
    "pairs": ("wrong", "correct"),
    "terms": ("wrong", "correct"),
    "protect": ("word", None),
}


def _norm(cell) -> str:
    return str(cell or "").strip().lower()


def map_header(cells) -> dict:
    """把一行单元格映射成 ``{字段: 列下标}``；对不上足够多字段则视为非表头。

    **命中门槛随列数自适应**：单列表（保护词表常见形态，表头就是「词」）只需
    命中 1 个列名即判为表头；多列表要求 ≥2，避免把普通数据行误判成表头。
    原先固定要求 ≥2，导致单列表的「词」被当成数据导入（实测）。
    """
    cells = list(cells or [])
    out: dict = {}
    for i, c in enumerate(cells):
        key = _norm(c)
        if not key:
            continue
        for field_name, aliases in COL_ALIASES.items():
            if field_name in out:
                continue
            if key in aliases:
                out[field_name] = i
                break
    need = 1 if len(cells) < 2 else 2
    return out if len(out) >= need else {}


def _cell(cells, idx) -> str:
    """取第 idx 列（越界返回空串）。**模块级函数而非内层闭包**。

    原先写成循环体内的 `def pick(idx)`，闭包按引用捕获循环变量 `cells` ——
    同步调用下"看起来能用"，但一旦调用被延迟（重构、惰性求值）就会读到
    最后一行的值。ruff 的 B023 正是为这类隐患设的。
    """
    if idx is None or idx >= len(cells):
        return ""
    return cells[idx]


def rows_to_entries(rows, role: str, res) -> None:
    """把 ``(行号, 单元格列表)`` 序列按角色解释成条目，写进 ``res``。

    有表头 → 按列名映射（列顺序随意）；
    无表头 → 按角色的默认列序（pairs/terms: 错,对[,类别]；protect: 词）。
    两种情形都可能出现"某个字段缺失"，一律记 issue 而不是抛异常 ——
    用户的表往往只填了一半，整批失败是最糟的体验。
    """
    rows = list(rows)
    if not rows:
        res.warn("表内没有任何数据行")
        return
    header = map_header(rows[0][1])
    body = rows[1:] if header else rows
    if header:
        res.warn("已识别表头并按其列名映射字段")
    else:
        res.warn("未识别到表头，按列顺序解释（%s）"
                 % _describe_default(role))

    c_wrong = header.get("wrong")
    c_correct = header.get("correct")
    c_word = header.get("word")
    if not header:                       # 无表头：按角色默认列序
        d1, d2 = _DEFAULT_COLS[role]
        if d1 == "word":
            c_word = 0
        else:
            c_wrong, c_correct = 0, 1

    for line, raw in body:
        # 用新名字承接规范化结果，不覆盖循环变量本身（PLW2901）
        cells = [str(c or "").strip() for c in (raw or [])]
        if not any(cells):
            continue                      # 空行：静默跳过（不是错误）
        if cells[0].startswith("#"):
            continue                      # 注释行

        e = Entry(role=role, src_line=line)
        e.category = _cell(cells, header.get("category"))
        e.pinyin = _cell(cells, header.get("pinyin"))
        e.definition = _cell(cells, header.get("definition"))
        conf = _cell(cells, header.get("confidence"))
        if conf:
            try:
                e.confidence = min(max(float(conf), 0.0), 1.0)
            except ValueError:
                res.add_issue(line, "置信度不是数字：%r" % conf)

        if role == "protect":
            e.word = _cell(cells, c_word) or cells[0]
            if not e.word:
                res.add_issue(line, "缺少词条内容")
                continue
        else:
            e.wrong = _cell(cells, c_wrong) or ("" if header else cells[0])
            e.correct = _cell(cells, c_correct) or (
                cells[1] if (not header and len(cells) > 1) else "")
            if not e.wrong or not e.correct:
                res.add_issue(line, "缺少「%s」或「%s」"
                              % ("异名" if role == "terms" else "错误写法",
                                 "规范名" if role == "terms" else "正确写法"))
                continue
            if e.wrong == e.correct:
                res.add_issue(line, "前后相同（%r），无意义" % e.wrong)
                continue
        res.entries.append(e)


def _describe_default(role: str) -> str:
    if role == "protect":
        return "第 1 列 = 词"
    return "第 1 列 = %s，第 2 列 = %s" % (
        "异名" if role == "terms" else "错误写法",
        "规范名" if role == "terms" else "正确写法")
