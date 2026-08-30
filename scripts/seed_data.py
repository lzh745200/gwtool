# -*- coding: utf-8 -*-
"""种子数据生成脚本（构建期运行一次，产物随程序打包，运行期零网络）。

生成内容（resources/data/seed.db）：
  1. error_pairs：
     - 精标错别字对（gwtool.core.corrector_data.CURATED_PAIRS）；
     - 程序化生成的同音混淆对 ≥30000 条：取 jieba 词频表高频双字词，
       用 pypinyin 同音（不限声调）常见字替换一位生成错写形式，
       仅保留“错写本身不是常用词”的条目（避免误报真实词）。
  2. dictionary：优先使用 CC-CEDICT（词、拼音、英文释义，公知开源数据）；
     下载失败时退化为 jieba 高频词 + pypinyin 拼音。

用法：python scripts/seed_data.py [--target 40000]
"""
from __future__ import annotations

import argparse
import gzip
import io
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CC_CEDICT_URLS = [
    "https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz",
    "https://raw.githubusercontent.com/rubber-duck/CC-CEDICT-MCs/master/cedict.txt",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=40000,
                    help="生成混淆对目标数量（默认40000，≥30000）")
    args = ap.parse_args()

    import jieba
    jieba.setLogLevel(60)
    jieba.initialize()
    freq = jieba.dt.FREQ

    from pypinyin import lazy_pinyin, Style

    out_path = ROOT / "gwtool" / "resources" / "data" / "seed.db"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    conn = sqlite3.connect(str(out_path))
    from gwtool.db.schema import TABLES, FTS_TABLES
    for ddl in TABLES + FTS_TABLES:
        conn.execute(ddl)

    # ------------------------------------------------ 1. 错别字对
    cur = conn.cursor()
    from gwtool.core.corrector_data import CURATED_PAIRS
    seen = set()
    n_curated = 0
    for wrong, correct, cat, conf in CURATED_PAIRS:
        if conf <= 0 or wrong == correct:
            continue
        cur.execute("INSERT OR IGNORE INTO error_pairs(wrong,correct,category,confidence,enabled,source)"
                    " VALUES(?,?,?,?,1,'curated')", (wrong, correct, cat, conf))
        seen.add((wrong, correct))
        n_curated += 1

    # 常用单字池（按词频取前 3500）
    single_chars = [(w, f) for w, f in freq.items() if len(w) == 1 and f >= 100
                    and re.match(r"^[\u4e00-\u9fff]$", w)]
    single_chars.sort(key=lambda x: -x[1])
    char_pool = [w for w, _ in single_chars[:3500]]

    # 同音组（不含声调）
    homophones: dict[str, list[str]] = {}
    for ch in char_pool:
        py = lazy_pinyin(ch, style=Style.NORMAL)[0]
        homophones.setdefault(py, []).append(ch)

    # 高频双字词
    two_char = [(w, f) for w, f in freq.items()
                if len(w) == 2 and f >= 50 and re.match(r"^[\u4e00-\u9fff]{2}$", w)]
    two_char.sort(key=lambda x: -x[1])

    n_generated = 0
    for word, f in two_char:
        if n_generated >= args.target:
            break
        py0 = lazy_pinyin(word[0], style=Style.NORMAL)[0]
        py1 = lazy_pinyin(word[1], style=Style.NORMAL)[0]
        subs0 = [c for c in homophones.get(py0, []) if c != word[0]]
        subs1 = [c for c in homophones.get(py1, []) if c != word[1]]
        candidates = [s + word[1] for s in subs0] + [word[0] + s for s in subs1]
        for wrong in candidates:
            if (wrong, word) in seen:
                continue
            # 错写本身不能是常用词（词频>30 视为常用词，避免误报真实词）
            if freq.get(wrong, 0) > 30:
                continue
            cur.execute(
                "INSERT OR IGNORE INTO error_pairs(wrong,correct,category,confidence,enabled,source)"
                " VALUES(?,?,?,0.55,1,'generated')",
                (wrong, word, "错别字(生成)"))
            seen.add((wrong, word))
            n_generated += 1
            if n_generated >= args.target:
                break
    conn.commit()
    total_pairs = cur.execute("SELECT count(*) FROM error_pairs").fetchone()[0]
    print(f"[seed] error_pairs: 精标 {n_curated} + 生成 {n_generated} = {total_pairs}")

    # ------------------------------------------------ 2. 词典
    n_dict = load_cedict(cur)
    if n_dict == 0:
        n_dict = load_fallback_dict(cur, freq)
    conn.commit()
    total_dict = cur.execute("SELECT count(*) FROM dictionary").fetchone()[0]
    print(f"[seed] dictionary: {total_dict} 条（本批写入 {n_dict}）")
    conn.close()
    print(f"[seed] OK -> {out_path} ({out_path.stat().st_size/1024/1024:.1f} MB)")


def load_cedict(cur: sqlite3.Cursor) -> int:
    """尝试下载 CC-CEDICT；失败返回 0。"""
    for url in CC_CEDICT_URLS:
        try:
            print(f"[seed] 下载 CC-CEDICT: {url}")
            req = urllib.request.Request(url, headers={"User-Agent": "gwtool-seed/1.0"})
            data = urllib.request.urlopen(req, timeout=60).read()
            if url.endswith(".gz"):
                data = gzip.decompress(data)
            text = data.decode("utf-8", errors="replace")
            n = 0
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = re.match(r"^(\S+)\s+(\S+)\s+\[(.+?)\]\s+/(.+)/$", line)
                if not m:
                    continue
                trad, simp, pinyin, eng = m.groups()
                if not re.match(r"^[\u4e00-\u9fff]{1,8}$", simp):
                    continue
                definition = eng.replace("/", "；")
                if len(definition) > 800:
                    definition = definition[:800]
                cur.execute(
                    "INSERT OR IGNORE INTO dictionary(word,pinyin,definition,example,source)"
                    " VALUES(?,?,?,?,'cc-cedict')", (simp, pinyin, definition, ""))
                n += 1
                if n % 20000 == 0:
                    print(f"[seed] cedict {n} ...")
            if n > 0:
                return n
        except Exception as e:  # noqa: BLE001
            print(f"[seed] 下载失败({e})，尝试下一个源")
    return 0


def load_fallback_dict(cur: sqlite3.Cursor, freq: dict[str, int]) -> int:
    """离线兜底词典：jieba 高频词 + pypinyin 拼音（无释义）。"""
    from pypinyin import lazy_pinyin, Style
    words = [(w, f) for w, f in freq.items()
             if 1 <= len(w) <= 4 and re.match(r"^[\u4e00-\u9fff]+$", w) and f >= 2]
    words.sort(key=lambda x: -x[1])
    n = 0
    for w, _ in words[:80000]:
        py = " ".join(lazy_pinyin(w, style=Style.NORMAL))
        cur.execute(
            "INSERT OR IGNORE INTO dictionary(word,pinyin,definition,example,source)"
            " VALUES(?,?,?,?,'jieba')", (w, py, "", ""))
        n += 1
    return n


if __name__ == "__main__":
    main()
