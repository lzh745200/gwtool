# -*- coding: utf-8 -*-
"""jieba 分词工具：FTS5 中文全文检索的预分词。

策略：
  - 建索引：文本 -> jieba 精确模式分词 -> 空格连接
  - 查询：查询串同样分词后逐词加双引号，FTS5 MATCH 语法 AND 组合，
    引号包裹避免 FTS5 把纯数字/特殊字符当作语法。
"""
from __future__ import annotations

import re
import threading

import jieba

_jieba_ready = False
# `_jieba_ready` 只是快路径标志，**不能当锁用**：两个线程同时首次分词时
# 会双双看到 False 并同时执行 jieba.initialize()，在词典尚未装载完的窗口里
# lcut 可能抛异常或切出错词。分词入口有主线程（纠错/检索）与后台导入线程
# 两条并发路径（v7 起段落索引也在导入线程里分词），这个窗口真实存在。
_jieba_lock = threading.Lock()


def _ensure_jieba() -> None:
    global _jieba_ready
    if _jieba_ready:
        return
    with _jieba_lock:
        if _jieba_ready:          # 双检：持锁后再确认一次，避免重复初始化
            return
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


def build_match_query_any(query: str, max_terms: int = 24) -> str:
    """与 `build_match_query` 同构，但各词之间用 **OR**。

    用途（v7 段落检索）：段落很短，AND 语义下"多词查询"极易全部落空。
    实测：查「安全生产 责任」被切成 安全/生产/责任 三个词，而索引侧
    「安全生产责任制」被切成 `安全 生产 责任制`、`责任人` 也是独立词 ——
    于是同一段里根本不存在独立的「责任」这个词，AND 必然不命中。
    段落越短这个问题越突出（整篇检索因文本长、四处散落这些词而不易暴露）。

    放宽为 OR 是**召回**手段，不是排序手段：调用方必须先跑 AND、仅在命中
    过少时用它兜底，并把"这是放宽结果"如实告诉用户 —— 否则"搜什么都有"
    会让用户失去对检索结果的信任。
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
    return " OR ".join(groups[:max_terms])


def query_terms(query: str, max_terms: int = 24) -> list[str]:
    """查询串按**建索引同一粒度**切出的主词列表（去掉符号与重复）。

    供调用方判断"这是单词查询还是多词查询"——多词查询在 AND 语义下才有
    召回塌陷的风险，单词查询不需要放宽（放宽了也没有更多可命中）。
    与 build_match_query 用的是同一套精确模式分词，保证判断口径一致。
    """
    _ensure_jieba()
    return list(dict.fromkeys(
        w.strip() for w in jieba.lcut(query or "")
        if _TOKEN.fullmatch(w.strip())))[:max_terms]


def token_spans(text: str) -> list[tuple[str, int, int]]:
    """分词并**带字符区间**（word, start, end）。

    用于需要"词界"而不只是"词"的场景：v7 从参考段落抽象单位名槽位时，
    必须切在真正的词界上，否则会把前面的动词一起吞进槽位
    （`_find_org_spans` 依赖它）。jieba.tokenize 原生给区间，无需自己算。
    """
    _ensure_jieba()
    return [(w, s, e) for (w, s, e) in jieba.tokenize(text or "") if w.strip()]
