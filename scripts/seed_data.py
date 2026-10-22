# -*- coding: utf-8 -*-
"""种子数据生成脚本（构建期运行一次，产物随程序打包，运行期零网络）。

生成内容（resources/data/seed.db）：
  1. error_pairs：**只放程序化生成的同音混淆对**（≥30000 条）：取 jieba 词频表
     高频双字词，用 pypinyin 同音（不限声调）常见字替换一位生成错写形式，
     仅保留"错写本身不是常用词"的条目（避免误报真实词）；
     **不再写入人工精标对**（v1.8）：精标表的唯一事实来源是
     `gwtool.core.corrector_data.CURATED_PAIRS`，写进种子库只会形成
     "内置改了、线上不变"的快照陷阱（v1.8 真实修复过：删除的精标对被快照复活）。
  2. dictionary：优先使用 CC-CEDICT（词、拼音、英文释义，公知开源数据）；
     下载失败时退化为 jieba 高频词 + pypinyin 拼音。

产物约定（tests/test_seed_artifact.py 会守住，改了这里也要改测试）：
  · 表结构与 `gwtool.db.schema` 当前版本一致，`PRAGMA user_version = SCHEMA_VERSION`；
  · `error_pairs` 按错形去重（同一错形只留一个正形，字典序最小 ⇒ 结果可复现，
    不依赖库内行序）。

用法：python scripts/seed_data.py [--target 40000]
"""
from __future__ import annotations

import argparse
import gzip
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

    # ------------------------------------------------ 1. 错别字对（只放生成层）
    #
    # v1.8：**不再写人工精标对**。精标表的唯一事实来源是代码里的
    # `corrector_data.CURATED_PAIRS`（`corrector._load_patterns` 会忽略库里
    # `source='curated'` 的快照行）。历史教训：种子库曾带 220 条精标快照，
    # 于是"内置表里删掉的错对被快照复活"、"降级的类别被旧值盖回"，
    # 且单测（空库）完全看不到 —— 只有真实安装才复现。
    cur = conn.cursor()
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

    # 先收集，再按**错形**去重后统一写入：
    # 同一个错形只能指向一个正形（纠错面板只显示一条建议），若保留多行，
    # "最终显示哪条"就取决于库内行序 —— 不可复现。取字典序最小的正形，
    # 与 .tmp 重建脚本/历史产物的口径一致。
    generated: dict[str, str] = {}
    for word, _freq in two_char:
        if len(generated) >= args.target:
            break
        py0 = lazy_pinyin(word[0], style=Style.NORMAL)[0]
        py1 = lazy_pinyin(word[1], style=Style.NORMAL)[0]
        subs0 = [c for c in homophones.get(py0, []) if c != word[0]]
        subs1 = [c for c in homophones.get(py1, []) if c != word[1]]
        candidates = [s + word[1] for s in subs0] + [word[0] + s for s in subs1]
        for wrong in candidates:
            if len(generated) >= args.target:
                break
            # 错写本身不能是常用词（词频>30 视为常用词，避免误报真实词）
            if freq.get(wrong, 0) > 30:
                continue
            best = generated.get(wrong)
            if best is None or word < best:
                generated[wrong] = word
    for wrong in sorted(generated):
        cur.execute(
            "INSERT OR IGNORE INTO error_pairs(wrong,correct,category,confidence,"
            "enabled,source) VALUES(?,?,'错别字(生成)',0.55,1,'generated')",
            (wrong, generated[wrong]))
    n_generated = len(generated)
    conn.commit()
    total_pairs = cur.execute("SELECT count(*) FROM error_pairs").fetchone()[0]
    print(f"[seed] error_pairs: 生成 {n_generated} = {total_pairs}"
          "（人工精标对不入库，由 corrector_data.CURATED_PAIRS 提供）")

    # ------------------------------------------------ 2. 词典
    n_dict = load_cedict(cur)
    if n_dict == 0:
        n_dict = load_fallback_dict(cur, freq)
    conn.commit()
    total_dict = cur.execute("SELECT count(*) FROM dictionary").fetchone()[0]
    print(f"[seed] dictionary: {total_dict} 条（本批写入 {n_dict}）")
    # 版本号：产物与当前 schema 对齐（旧产物停在 user_version=0，缺后加的表，
    # 打开时会被当成"需要迁移的老库"——种子库不该是那个状态）。
    from gwtool.db.schema import SCHEMA_VERSION
    conn.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")
    conn.commit()
    conn.execute("PRAGMA integrity_check")
    conn.execute("VACUUM")
    conn.commit()
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
                raw_line = line.strip()
                if not raw_line or raw_line.startswith("#"):
                    continue
                m = re.match(r"^(\S+)\s+(\S+)\s+\[(.+?)\]\s+/(.+)/$", raw_line)
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
        except Exception as e:
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
