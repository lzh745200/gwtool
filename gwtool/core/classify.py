# -*- coding: utf-8 -*-
"""智能分类建议：给一段文本推荐它最可能属于哪个已有资料分类。

导入材料时选分类全靠人工判断，库里分类多了以后很容易随手丢进「未分类」。
本模块用既有分类下文档的词频做画像，给新材料打分排序，作为**建议**供人确认，
不自动落库——分类是归档口径问题，机器猜错比不猜更麻烦。

完全离线：分词复用 db.tokenize（jieba），不引入任何新依赖，也不联网。
"""
from __future__ import annotations

from collections import Counter

from ..db import dao
from ..db.tokenize import tokenize

# 单字与纯数字对区分分类几乎没用（"的""了""2026"），过滤掉以免淹没实词
_MIN_TOKEN_LEN = 2


def _tokens(text: str) -> list[str]:
    return [t for t in tokenize(text or "").split()
            if len(t) >= _MIN_TOKEN_LEN and not t.isdigit()]


def category_profiles(max_docs_per_category: int = 200) -> dict[int, Counter]:
    """每个分类的词频画像：{category_id: Counter(token -> 次数)}。"""
    profiles: dict[int, Counter] = {}
    for cat in dao.list_categories():
        counter: Counter = Counter()
        for doc in dao.list_documents(category_id=cat.id)[:max_docs_per_category]:
            counter.update(_tokens(doc.title))
            counter.update(_tokens(doc.content_text))
        if counter:
            profiles[cat.id] = counter
    return profiles


def suggest(text: str, top_n: int = 3,
            profiles: dict[int, Counter] | None = None) -> list[tuple[int, str, float]]:
    """返回 [(category_id, 分类名, 得分)]，得分降序，已归一化到 0~1。

    得分口径：查询词的加权命中率——把该分类的词频归一化成"这个词在此分类
    出现的相对概率"，再对查询文本的每个词求和并按查询词数平均。这样长文本
    不会因为词多而天然占优，不同分类之间可横向比较。

    profiles 可由调用方预先算好传入（批量建议时避免每篇都重建画像）。
    """
    query = _tokens(text)
    if not query:
        return []
    if profiles is None:
        profiles = category_profiles()
    if not profiles:
        return []

    names = {c.id: c.name for c in dao.list_categories()}
    query_counts = Counter(query)
    scored: list[tuple[int, str, float]] = []
    for cat_id, counter in profiles.items():
        total = sum(counter.values())
        if not total:
            continue
        hit = sum(query_counts[t] * (counter.get(t, 0) / total) for t in query_counts)
        scored.append((cat_id, names.get(cat_id, f"分类{cat_id}"), hit / len(query_counts)))

    scored.sort(key=lambda x: (-x[2], x[1]))
    # 得分为 0 说明一个词都没命中，给这种建议只会误导
    return [(c, n, round(s, 6)) for c, n, s in scored[:top_n] if s > 0]


def suggest_for_document(doc_id: int, top_n: int = 3,
                         profiles: dict[int, Counter] | None = None
                         ) -> list[tuple[int, str, float]]:
    """对库内某篇文档给出分类建议（标题权重高于正文，标题通常更能代表归类口径）。"""
    doc = dao.get_document(doc_id)
    if doc is None:
        return []
    combined = f"{doc.title} {doc.title} {doc.content_text[:2000]}"
    return suggest(combined, top_n=top_n, profiles=profiles)
