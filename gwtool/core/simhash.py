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


def _ngrams(s: str, n: int) -> set[str]:
    """s 的 n-gram 集合（s 已去空白；n 自动收敛到 len(s)）。"""
    if not s:
        return set()
    n = max(1, min(n, len(s)))
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def jaccard(a: str, b: str) -> float:
    """字符 n-gram Jaccard 相似度（短文本与长短混合均稳定）。

    ⚠ 两边必须用**同一个阶**，否则集合永远无法相交。旧实现让每一边按自身
    长度定阶（<3 字时整个串当唯一元素），于是 "甲乙" 与 "甲乙丙" 这样
    「一个明显是另一个前缀」的对被判成 0.0 —— 短句查重直接失效。
    现在由**较短的一边**统一定阶（上限 3）：短串退化为 2-gram/1-gram，
    包含关系能体现为高重合，长文本行为与原先完全一致。
    """
    sa0 = "".join((a or "").split())
    sb0 = "".join((b or "").split())
    if not sa0 and not sb0:
        return 1.0
    if not sa0 or not sb0:
        return 0.0
    n = min(3, len(sa0), len(sb0))
    sa, sb = _ngrams(sa0, n), _ngrams(sb0, n)
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
