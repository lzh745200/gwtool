# -*- coding: utf-8 -*-
"""JSON 词表解析。

接受的形态（**都支持**，因为用户拿到的 JSON 什么样都有）：
  1. 裸数组：``[{"wrong": "...", "correct": "..."}, ...]``
  2. 带分组的对象：``{"error_pairs": [...], "terms": [...], "words": [...]}``
     分组键本身就是角色声明（``words`` → protect，``terms`` → terms）；
  3. 单条对象：``{"wrong": "...", "correct": "..."}``（一次只导一条也允许）

字段名兼容中英文（复用 `tabular.COL_ALIASES` 的别名表，避免两处维护）。
**单条非法只记 issue，不让整包失败** —— 用户手写的 JSON 里出现一两条残缺
条目太常见了。
"""
from __future__ import annotations

import json

from ..parsers.txt_parser import read_text_smart
from . import Entry, ParseResult
from .tabular import COL_ALIASES

# 分组键 → 角色
_GROUP_ROLE = {
    "error_pairs": "pairs", "pairs": "pairs", "纠错对": "pairs",
    "terms": "terms", "term": "terms", "术语": "terms", "glossary": "terms",
    "words": "protect", "word": "protect", "protect": "protect",
    "保护词": "protect", "词表": "protect",
}


# 术语条目里"一个规范名对应多个异名"的字段名（值应为数组）。
# 出现它时展开成多条「异名 → 规范名」——用户写 `aliases` 就是不想把
# 同一个规范名抄 N 遍。
_ALIASES_KEYS = ("aliases", "alias", "异名", "变体", "variants")


def _aliases_of(obj: dict) -> list:
    for k in _ALIASES_KEYS:
        v = obj.get(k)
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x or "").strip()]
        if isinstance(v, str) and v.strip():
            return [x.strip() for x in v.replace("，", ",").split(",") if x.strip()]
    return []


def _pick(obj: dict, field: str) -> str:
    """按字段的中英文别名从对象里取值。"""
    for alias in COL_ALIASES.get(field, ()):
        if alias in obj:
            return str(obj.get(alias) or "").strip()
    return ""


def _entry_from(obj, role: str, line: int, res: ParseResult):
    if not isinstance(obj, dict):
        res.add_issue(line, "条目不是对象：%r" % (obj,))
        return None
    # 条目自带 role 时以它为准（分组键的角色可被条目覆盖）
    declared = _pick(obj, "role").lower()
    eff = declared if declared in ("pairs", "terms", "protect") else role
    if eff not in ("pairs", "terms", "protect"):
        eff = role

    e = Entry(role=eff, src_line=line)
    e.category = _pick(obj, "category")
    e.pinyin = _pick(obj, "pinyin")
    e.definition = _pick(obj, "definition")
    conf = _pick(obj, "confidence")
    if conf:
        try:
            e.confidence = min(max(float(conf), 0.0), 1.0)
        except ValueError:
            res.add_issue(line, "置信度不是数字：%r" % conf)

    if eff == "protect":
        e.word = _pick(obj, "word")
        if not e.word:
            res.add_issue(line, "缺少字段 word")
            return None
        return e

    # 术语：`aliases` 是数组时，每个异名各成一条
    alts = _aliases_of(obj)
    e.correct = _pick(obj, "correct")
    if alts:
        if not e.correct:
            res.add_issue(line, "有 aliases 但缺少规范名（correct/standard）")
            return None
        out = []
        for a in alts:
            if a == e.correct:
                continue
            seg = Entry(role=eff, src_line=line, wrong=a, correct=e.correct,
                        category=e.category, confidence=e.confidence,
                        pinyin=e.pinyin, definition=e.definition)
            out.append(seg)
        if not out:
            res.add_issue(line, "aliases 与规范名相同，无意义")
            return None
        return out          # 列表：调用方按需展开

    e.wrong = _pick(obj, "wrong")
    if not e.wrong or not e.correct:
        res.add_issue(line, "缺少字段 wrong / correct")
        return None
    if e.wrong == e.correct:
        res.add_issue(line, "前后相同（%r），无意义" % e.wrong)
        return None
    return e


def parse(path, role: str = "pairs", options=None) -> ParseResult:
    res = ParseResult(fmt="json")
    try:
        text = read_text_smart(str(path))
    except OSError as exc:
        raise ValueError("词表文件读不出来：%s" % exc) from exc
    if not text.strip():
        res.warn("文件是空的，没有任何条目")
        return res
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError("JSON 语法有误：%s" % exc) from exc

    seq = 0

    def emit(obj, eff_role: str):
        nonlocal seq
        seq += 1
        got = _entry_from(obj, eff_role, seq, res)
        if got is None:
            return
        # 术语的 aliases 会展开成列表（一个规范名 → 多个异名）
        if isinstance(got, list):
            res.entries.extend(got)
        else:
            res.entries.append(got)

    if isinstance(data, list):
        for obj in data:
            emit(obj, role)
    elif isinstance(data, dict):
        groups = [(k, v) for k, v in data.items() if k in _GROUP_ROLE
                  and isinstance(v, list)]
        if groups:
            for key, items in groups:
                g_role = _GROUP_ROLE[key]
                for obj in items:
                    emit(obj, g_role)
            res.warn("按分组键识别角色：%s"
                     % "、".join("%s→%s" % (k, _GROUP_ROLE[k]) for k, _ in groups))
        elif any(a in data for a in COL_ALIASES["wrong"]) or \
                any(a in data for a in COL_ALIASES["word"]):
            emit(data, role)          # 单条对象
        else:
            raise ValueError(
                "JSON 里找不到可识别的条目：既没有 error_pairs/terms/words 这类"
                "分组键，也没有 wrong/correct 或 word 字段")
    else:
        raise ValueError("JSON 顶层必须是数组或对象，实际是 %s"
                         % type(data).__name__)
    return res
