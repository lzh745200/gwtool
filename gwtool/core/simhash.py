# -*- coding: utf-8 -*-
"""相似文档查重：jieba 特征 + SimHash（纯 Python，无新增依赖）。"""
from __future__ import annotations

from ..db import tokenize as tok

_BITS = 64


def _features(text: str) -> list[str]:
    words = tok.tokenize(text).split()
    feats = list(words)
    feats.extend(words[i] + words[i + 1] for i in range(len(words) - 1))
    return [f for f in feats if len(f) >= 2]


def _hash64(s: str) -> int:
    # FNV-1a 64 位
    h = 0xCBF29CE484222325
    for ch in s.encode("utf-8"):
        h ^= ch
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def simhash(text: str) -> int:
    feats = _features(text)
    if not feats:
        return 0
    weights: dict[str, int] = {}
    for f in feats:
        weights[f] = weights.get(f, 0) + 1
    v = [0] * _BITS
    for f, w in weights.items():
        h = _hash64(f)
        for i in range(_BITS):
            v[i] += w if (h >> i) & 1 else -w
    out = 0
    for i in range(_BITS):
        if v[i] > 0:
            out |= 1 << i
    return out


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def to_db(h: int) -> int:
    """64 位无符号 -> SQLite 有符号 64 位（超范围整数无法直接入库）。"""
    return h - (1 << 64) if h >= (1 << 63) else h


def from_db(v: int) -> int:
    return v + (1 << 64) if v < 0 else v


def _trigrams(text: str) -> set[str]:
    s = "".join(text.split())
    if len(s) < 3:
        return {s} if s else set()
    return {s[i:i + 3] for i in range(len(s) - 2)}


def jaccard(a: str, b: str) -> float:
    """字符三元组 Jaccard 相似度（短文本精确值）。"""
    sa, sb = _trigrams(a), _trigrams(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def similarity(a: str, b: str) -> float:
    """精确相似度：字符三元组 Jaccard（长短文本均稳定）。"""
    return jaccard(a, b)


def find_similar(docs: dict[int, str], threshold: float = 0.7,
                 hashes: dict[int, int] | None = None) -> list[tuple[int, int, float]]:
    """docs: {id: 正文}；返回 [(id1, id2, 相似度)] 按相似度降序。

    hashes: 已持久化的 SimHash 表（缺省则现算）——资料库较大时避免全库重算。
    两级策略：SimHash（快）做粗筛（阈值放宽 0.25），
    粗筛命中的对再算字符三元组 Jaccard（准）作为最终相似度。
    """
    ids = list(docs.keys())
    known = hashes or {}
    hashes = {i: known.get(i) or simhash(docs[i]) for i in ids}
    out: list[tuple[int, int, float]] = []
    for x in range(len(ids)):
        for y in range(x + 1, len(ids)):
            a, b = ids[x], ids[y]
            if hashes[a] == 0 and hashes[b] == 0:
                coarse = 1.0
            else:
                coarse = 1.0 - hamming(hashes[a], hashes[b]) / _BITS
            if coarse < threshold - 0.25:
                continue
            exact = jaccard(docs[a], docs[b])
            if exact >= threshold:
                out.append((a, b, exact))
    out.sort(key=lambda t: -t[2])
    return out
