# -*- coding: utf-8 -*-
"""P1 语法层验收（E8）：病句集召回 + 真实语料新增误报。

口径：
  * 召回：病句集里只要检出任一 grammar_v2 类别命中（auto 或 advisory）即算检出；
  * 误报：真实语料上 grammar_v2 类别 **auto severity** 命中数 / 万字（硬门禁 ≤0.2）；
    advisory 命中单列报告、不参与门禁（改法不唯一，按 v1.17 分级只提示）。

用法：
  python scripts/eval_grammar.py [--bad-set tests/data/grammar_bad_sentences_v119.json]
      [--corpus doc/corpus/real_all] [--json out.json] [--fast]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from gwtool.core import corrector, grammar_v2

TH_RECALL = 0.60
TH_FP_AUTO_PER_WAN = 0.20
_CATEGORIES = set(grammar_v2.CATEGORIES)


def _cjk(text: str) -> int:
    return sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")


def recall(bad_set: Path) -> tuple[float, dict, list]:
    rows = json.loads(bad_set.read_text(encoding="utf-8"))
    per: dict[str, list[int]] = {}
    miss = []
    for row in rows:
        cat = row["category"]
        hits = [h for h in corrector.check_text(row["text"])
                if h.category in _CATEGORIES]
        ok = bool(hits)
        per.setdefault(cat, [0, 0])
        per[cat][0] += int(ok)
        per[cat][1] += 1
        if not ok:
            miss.append(row["text"])
    total_ok = sum(v[0] for v in per.values())
    total = sum(v[1] for v in per.values())
    return (total_ok / max(1, total), per, miss)


def false_positives(corpus: Path) -> tuple[int, int, int, int]:
    """返回 (auto_fp, advisory, chars, paragraphs)。"""
    auto = advisory = chars = paras = 0
    files = sorted(corpus.rglob("*.txt")) if corpus.is_dir() else [corpus]
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for para in text.splitlines():
            if len(para.strip()) < 8:
                continue
            paras += 1
            chars += _cjk(para)
            for h in corrector.check_text(para):
                if h.category not in _CATEGORIES:
                    continue
                if h.severity == "error":
                    auto += 1
                else:
                    advisory += 1
    return auto, advisory, chars, paras


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bad-set", default=str(
        _ROOT / "tests/data/grammar_bad_sentences_v119.json"))
    ap.add_argument("--corpus", default=str(_ROOT / "doc/corpus/real_all"))
    ap.add_argument("--json", default="")
    ap.add_argument("--fast", action="store_true",
                    help="只取语料前 20 个文件（供测试/CI 快速跑）")
    args = ap.parse_args()

    bad = Path(args.bad_set)
    rec, per, miss = recall(bad)
    corpus = Path(args.corpus)
    if args.fast and corpus.is_dir():
        files = sorted(corpus.rglob("*.txt"))[:20]
        tmp = Path(os.environ.get("TEMP", str(_ROOT))) / "eval_grammar_fast"
        tmp.mkdir(parents=True, exist_ok=True)
        for f in files:
            (tmp / f.name).write_text(f.read_text(encoding="utf-8", errors="replace"),
                                      encoding="utf-8")
        corpus = tmp
    auto, adv, chars, paras = false_positives(corpus)
    fp_per_wan = auto / max(1, chars) * 10000

    print("=" * 64)
    print("语法层验收（E8）：病句集召回 + 真实语料新增误报")
    print("=" * 64)
    print(f"规则数：{grammar_v2.rule_count()}（目标 ≥120）")
    print(f"病句集：{sum(v[1] for v in per.values())} 句 / "
          f"检出 {sum(v[0] for v in per.values())} 句 = {rec:.1%}"
          f"（阈值 ≥{TH_RECALL:.0%}）")
    for cat, (ok, n) in per.items():
        print(f"  · {cat}: {ok}/{n} = {ok / max(1, n):.0%}")
    print(f"真实语料：{paras} 段 / {chars} 汉字")
    print(f"  · 新增自动误报（grammar_v2 auto）：{auto} 处 = "
          f"{fp_per_wan:.3f} 处/万字（阈值 ≤{TH_FP_AUTO_PER_WAN}）")
    print(f"  · 仅提示命中（advisory）：{adv} 处（只报告，不参与门禁）")
    if miss:
        print(f"  · 漏检示例：{miss[:5]}")
    ok = rec >= TH_RECALL and fp_per_wan <= TH_FP_AUTO_PER_WAN
    print(f"\n结论：{'PASS' if ok else 'FAIL'}（召回{'达标' if rec >= TH_RECALL else '未达标'}，"
          f"误报{'达标' if fp_per_wan <= TH_FP_AUTO_PER_WAN else '未达标'}）")
    if args.json:
        Path(args.json).write_text(json.dumps({
            "rules": grammar_v2.rule_count(), "recall": rec,
            "per_category": {k: {"ok": v[0], "n": v[1]} for k, v in per.items()},
            "corpus": {"chars": chars, "paragraphs": paras},
            "auto_fp": auto, "auto_fp_per_wan": fp_per_wan,
            "advisory": adv, "pass": ok, "miss": miss,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
