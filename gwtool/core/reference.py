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
    """按 BM25 相关度归一后合并排序。"""
    if not (query or "").strip():
        return []
    items: list[ReferenceItem] = []
    for searcher, src in ((dao.search_documents, "documents"),
                          (dao.search_dictionary, "dictionary"),
                          (dao.search_phrases, "phrases")):
        for r in searcher(query, limit_each):
            items.append(ReferenceItem(src, r.ref_id, r.title, r.snippet, r.rank))
    # bm25 越小越相关 -> 相关度 = 1/(1+|rank|)
    for it in items:
        it.rank = 1.0 / (1.0 + abs(it.rank))
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
