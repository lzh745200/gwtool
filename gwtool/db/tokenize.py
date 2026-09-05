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
    '"乡村" "振兴" "政策"'。各词之间是隐式 AND。

    关键设计（第 6 轮深探修复）：主词取 jieba **精确模式**分词——与建索引侧
    同一分词粒度，保证 AND 能命中；lcut_for_search 的拆分变体（如
    "钉钉子精神"拆出"钉子"）放进同一词的 OR 分组——变体是召回手段，
    若与主词并列 AND，任何一个拆分片不在索引里就会拖垮整条查询
    （此前"钉钉子精神""甲乙丙丁"这类词组检索全部漏检即此根因）。
    纯符号/标点 token 一律过滤：索引里不会以符号成词，留着只会产生噪声子句。
    """
    _ensure_jieba()
    precise = [w.strip() for w in jieba.lcut(query or "")
               if _TOKEN.fullmatch(w.strip())]
    if not precise:
        return ""
    search_terms = [w.strip() for w in jieba.lcut_for_search(query or "")
                    if _TOKEN.fullmatch(w.strip())]
    groups: list[str] = []
    for w in precise:
        variants = [v for v in search_terms if v != w and (v in w or w in v)]
        uniq = list(dict.fromkeys([w] + variants))
        groups.append("(" + " OR ".join('"%s"' % t.replace('"', "") for t in uniq) + ")")
    # 分组之间必须用显式 AND：FTS5 的隐式 AND 只作用于相邻短语，
    # 括号分组间写 ("a") ("b") 会报 syntax error（且被 _fts_search 静默吞掉，
    # 表现为两词以上查询全部返回空——第 6 轮深探实测确认）。
    return " AND ".join(groups[:max_terms])
