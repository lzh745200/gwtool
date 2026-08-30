# -*- coding: utf-8 -*-
"""jieba 分词工具：FTS5 中文全文检索的预分词。

策略：
  - 建索引：文本 -> jieba 精确模式分词 -> 空格连接
  - 查询：查询串同样分词后逐词加双引号，FTS5 MATCH 语法 AND 组合，
    引号包裹避免 FTS5 把纯数字/特殊字符当作语法。
"""
from __future__ import annotations

import re

import jieba

_jieba_ready = False


def _ensure_jieba() -> None:
    global _jieba_ready
    if not _jieba_ready:
        jieba.setLogLevel(60)  # 静默
        jieba.initialize()
        _jieba_ready = True


def tokenize(text: str) -> str:
    """分词并以空格连接（用于写入 FTS）。"""
    _ensure_jieba()
    return " ".join(w for w in jieba.lcut(text or "") if w.strip())


_TOKEN = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+")


def build_match_query(query: str, max_terms: int = 24) -> str:
    """把用户输入转换为 FTS5 MATCH 表达式，如：搜索"乡村振兴 政策" ->
    '"乡村" "振兴" "政策"'。各词之间是隐式 AND。"""
    _ensure_jieba()
    terms: list[str] = []
    # 先按 jieba 分，再对每个分词结果里的连续片段取词，保证中英混合也能查
    for w in jieba.lcut_for_search(query or ""):
        w = w.strip()
        if w:
            terms.append('"%s"' % w.replace('"', ""))
    # 去重保序
    seen = set()
    uniq = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return " ".join(uniq[:max_terms])
