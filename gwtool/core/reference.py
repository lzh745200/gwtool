# -*- coding: utf-8 -*-
"""写作参考引擎：跨资料库/词典/句式库的统一相关度检索。"""
from __future__ import annotations

from dataclasses import dataclass

from ..db import dao, tokenize as tok


@dataclass
class ReferenceItem:
    source: str      # documents / dictionary / phrases
    ref_id: int
    title: str
    snippet: str
    rank: float

    @property
    def source_label(self) -> str:
        return {"documents": "资料", "dictionary": "词典", "phrases": "句式"}.get(
            self.source, self.source)


def lookup(query: str, limit_each: int = 12) -> list[ReferenceItem]:
    """三源检索后组内归一化再合并排序。

    BM25 分数跨表量纲不可比（且词典源走 LIKE 人工分级），直接合并有偏；
    这里在每源内部做 min-max 归一到 [0,1]，词典源直接用其 0.9/0.8/0.7 分级，
    再叠加标题命中与标签命中加权。
    """
    if not (query or "").strip():
        return []
    q = query.strip()
    raw: list[tuple[str, list]] = []
    for searcher, src in ((dao.search_documents, "documents"),
                          (dao.search_dictionary, "dictionary"),
                          (dao.search_phrases, "phrases")):
        raw.append((src, searcher(q, limit_each)))

    items: list[ReferenceItem] = []
    tag_map: dict[int, str] = {}
    for src, results in raw:
        if not results:
            continue
        if src == "dictionary":
            for r in results:
                items.append(ReferenceItem(src, r.ref_id, r.title, r.snippet,
                                           max(0.0, min(r.rank, 1.0))))
            continue
        scores = [abs(r.rank) for r in results]
        lo, hi = min(scores), max(scores)
        span = (hi - lo) or 1.0
        for r in results:
            base = 1.0 - (abs(r.rank) - lo) / span     # 组内相对相关度 0..1
            boost = 0.0
            if r.title and q in r.title:
                boost += 0.15                           # 标题命中加权
            if src == "documents":
                if not tag_map:
                    tag_map = _document_tags()
                tags = tag_map.get(r.ref_id) or ""
                if tags and any(t.strip() and (t.strip() in q or q in t.strip())
                                for t in tags.replace("，", ",").split(",")):
                    boost += 0.10                       # 标签命中加权
            items.append(ReferenceItem(src, r.ref_id, r.title, r.snippet,
                                       min(base + boost, 1.0)))
    items.sort(key=lambda x: x.rank, reverse=True)
    # 去重（同源同 id）
    seen = set()
    out = []
    for it in items:
        key = (it.source, it.ref_id)
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def _document_tags() -> dict[int, str]:
    """id -> tags（轻量查询，供标签命中加权）。回收站里的文档不参与。"""
    from ..db.connection import get_conn
    rows = get_conn().execute(
        "SELECT id, tags FROM documents WHERE tags<>'' AND deleted_time=''"
    ).fetchall()
    return {int(r["id"]): r["tags"] for r in rows}


def document_full_text(ref_id: int) -> str:
    d = dao.get_document(ref_id)
    return d.content_text if d else ""


def phrase_full_text(ref_id: int) -> str:
    conn_p = dao.list_phrases()
    for p in conn_p:
        if p.id == ref_id:
            return p.context or p.phrase
    return ""


def dictionary_entry(ref_id: int) -> dict:
    from ..db.connection import get_conn
    row = get_conn().execute(
        "SELECT * FROM dictionary WHERE id=?", (ref_id,)).fetchone()
    return dict(row) if row else {}


def related_words(word: str, limit: int = 8) -> list[str]:
    """同音/近音联想词：来自词典表同拼音词。"""
    entries = dao.lookup_dictionary(word)
    if not entries:
        return []
    pinyin = entries[0].get("pinyin", "")
    if not pinyin:
        return []
    from ..db.connection import get_conn
    rows = get_conn().execute(
        "SELECT DISTINCT word FROM dictionary WHERE pinyin=? AND word<>? LIMIT ?",
        (pinyin, word, limit)).fetchall()
    return [r["word"] for r in rows]
