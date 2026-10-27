# -*- coding: utf-8 -*-
"""P3 独立病句基准：FCGEC（EMNLP 2022）valid 集上的语法层评测。

数据不随仓库分发（许可/体积），由 `scripts/fetch_fcgec.py` 按固定 sha256 拉取到
`doc/bench/`（.gitignore 已排除）。本脚本只读缓存，不上网。

指标（语法层，不含拼写/神经层）：
  * 句级召回：病句至少被一条语法规则命中（auto 或 advisory）；
  * 定位召回：命中区间与 FCGEC 标注 operation.pos 重叠（±2 字容差）；
  * 干净句误报：FCGEC 原句为非病句（error_flag=0）时，语法层自动命中 / 万字。

用法：python scripts/eval_grammar_independent.py [--json out.json]
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

from gwtool.core import corrector, grammar_rules, grammar_v2

DATA = _ROOT / "doc/bench/fcgec_valid.json"
GRAMMAR_CATS = set(grammar_v2.CATEGORIES) | {
    grammar_rules.CAT_CONJUNCTION, grammar_rules.CAT_COLLOCATION,
    grammar_rules.CAT_REDUNDANCY,
}
TH_RECALL = 0.15          # 语法层只覆盖部分类型，先给保守门禁
TH_FP_PER_WAN = 0.50


def _ops_positions(raw: str) -> list[int]:
    """从 operation JSON 里取所有 pos（FCGEC 一个样本可能有多条参考答案）。"""
    try:
        data = json.loads(raw)
    except Exception:
        return []
    out = []

    def walk(x):
        if isinstance(x, dict):
            if isinstance(x.get("pos"), int):
                out.append(x["pos"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(data)
    return out


def run(sample: int = 0) -> dict:
    if not DATA.is_file():
        raise SystemExit(f"缺少基准数据：{DATA}（先运行 scripts/fetch_fcgec.py）")
    rows = json.loads(DATA.read_text(encoding="utf-8"))
    if sample:
        rows = dict(list(rows.items())[:sample])

    per_type: dict[str, dict] = {}
    hit_sentence = hit_pos = errors = 0
    fp_auto = fp_adv = chars_clean = clean = 0
    for item in rows.values():
        text = item.get("sentence") or ""
        if not text:
            continue
        hits = [h for h in corrector.check_text(text) if h.category in GRAMMAR_CATS]
        if item.get("error_flag") == 0:
            clean += 1
            chars_clean += sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
            fp_auto += sum(1 for h in hits if h.severity == "error")
            fp_adv += sum(1 for h in hits if h.severity != "error")
            continue
        errors += 1
        t = str(item.get("error_type") or "*").split(";")[0]
        st = per_type.setdefault(t, {"n": 0, "sentence": 0, "position": 0})
        st["n"] += 1
        if hits:
            hit_sentence += 1
            st["sentence"] += 1
        raw = item.get("operation") or ""
        ops = []
        if isinstance(raw, str):
            try:
                ops = json.loads(raw)
            except Exception:
                ops = []
        elif isinstance(raw, list):
            ops = raw
        positions = []
        for op in ops:
            if isinstance(op, dict):
                for v in op.values():
                    if isinstance(v, list):
                        for d in v:
                            if isinstance(d, dict) and isinstance(d.get("pos"), int):
                                positions.append(d["pos"])
        if positions and any(
                h.start <= p + 2 and h.end >= p - 1
                for h in hits for p in positions):
            hit_pos += 1
            st["position"] += 1

    chars_valid = sum(sum(1 for ch in (x.get("sentence") or "")
                          if "\u4e00" <= ch <= "\u9fff") for x in rows.values())
    fp_per_wan = fp_auto / max(1, chars_clean) * 10000
    return {
        "samples": len(rows), "errors": errors, "clean": clean,
        "hit_sentence": hit_sentence,
        "hit_position": hit_pos,
        "recall_sentence": hit_sentence / max(1, errors),
        "recall_position": hit_pos / max(1, errors),
        "clean_chars": chars_clean, "valid_chars": chars_valid,
        "fp_auto": fp_auto, "fp_advisory": fp_adv,
        "fp_per_wan": fp_per_wan,
        "per_type": per_type,
        "pass": (hit_sentence / max(1, errors) >= TH_RECALL
                 and fp_per_wan <= TH_FP_PER_WAN),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    r = run(args.sample)
    print("=" * 64)
    print("P3 独立病句基准：FCGEC valid（语法层，不含拼写/神经层）")
    print("=" * 64)
    print(f"样本 {r['samples']}（病句 {r['errors']} / 干净 {r['clean']}）")
    print(f"句级召回：{r['hit_sentence']}/{r['errors']} = {r['recall_sentence']:.1%}"
          f"（门禁 ≥{TH_RECALL:.0%}）")
    print(f"定位召回：{r['hit_position']}/{r['errors']} = {r['recall_position']:.1%}"
          "（命中与标注 pos 重叠 ±2 字）")
    print(f"干净句自动误报：{r['fp_auto']} 处 / {r['clean_chars']} 字 = "
          f"{r['fp_per_wan']:.3f}/万字（门禁 ≤{TH_FP_PER_WAN}）")
    print(f"仅提示命中：{r['fp_advisory']} 处")
    for t, st in sorted(r["per_type"].items()):
        print(f"  · {t}: 句级 {st['sentence']}/{st['n']}，"
              f"定位 {st['position']}/{st['n']}")
    print(f"\n结论：{'PASS' if r['pass'] else 'FAIL'}")
    if args.json:
        Path(args.json).write_text(json.dumps(r, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    return 0 if r["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
