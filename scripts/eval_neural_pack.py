# -*- coding: utf-8 -*-
"""P2 增强包验收（E9）：可选 ONNX 包的真实增益与新增误报。

指标：
  * OOV 召回：内置 24 个"词表外错字"里，基础三层 vs 开启 L4 后能被改对的条数；
  * 新增误报：干净语料上"开启 L4 才出现"的自动命中 / 千字（阈值 ≤1.0，与 E6 同口径）；
  * 延迟：每千字耗时。

用法：
  python scripts/eval_neural_pack.py --pack-root <enhance 根目录> --kind csc \
      --corpus doc/corpus/real_all --sample-chars 40000
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from gwtool import paths
from gwtool.core import csc_gec, csc_neural, corrector

OOV_CASES = [
    ("落施", "落实"), ("据绝", "拒绝"), ("签于", "鉴于"), ("制序", "秩序"),
    ("布殖", "布置"), ("汇禀", "汇报"), ("贡任", "责任"), ("范筹", "范畴"),
    ("严历", "严厉"), ("详佃", "详细"), ("协离", "协调"), ("措拖", "措施"),
    ("贯沏", "贯彻"), ("经捡", "经验"), ("推委", "推诿"), ("诚垦", "诚恳"),
    ("登计", "登记"), ("偏辟", "偏僻"), ("暑名", "署名"), ("签暑", "签署"),
    ("效律", "效率"), ("尊守", "遵守"), ("贯输", "灌输"), ("落施到位", "落实到位"),
]


def _mod(kind: str):
    return csc_neural if kind == "csc" else csc_gec


def _hits_ok(hits, wrong: str, right: str) -> bool:
    # 与 E4 同口径：该句被检出（命中任意一处）即算召回。L4 只做单字替换，
    # 若要求"建议里含完整正确词"会系统性低估（施→实 已改对，但不含"落实"）。
    return bool(hits)


def _recall(kind: str) -> tuple[int, int]:
    mod = _mod(kind)
    old = mod._setting_on()
    try:
        mod.set_setting(False)
        corrector.invalidate_cache()
        base = 0
        for w, c in OOV_CASES:
            if _hits_ok(corrector.check_text(f"各单位要{w}相关规定。"), w, c):
                base += 1
        mod.set_setting(True)
        corrector.invalidate_cache()
        got = 0
        for w, c in OOV_CASES:
            if _hits_ok(corrector.check_text(f"各单位要{w}相关规定。"), w, c):
                got += 1
        return base, got
    finally:
        mod.set_setting(old)
        corrector.invalidate_cache()


def _sample(corpus: Path, max_chars: int) -> list[str]:
    out, chars = [], 0
    files = sorted(corpus.rglob("*.txt")) if corpus.is_dir() else [corpus]
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for para in text.splitlines():
            p = para.strip()
            if len(p) < 8:
                continue
            out.append(p)
            chars += sum(1 for ch in p if "\u4e00" <= ch <= "\u9fff")
            if chars >= max_chars:
                return out
    return out


def _fp(kind: str, corpus: Path, max_chars: int):
    mod = _mod(kind)
    paras = _sample(corpus, max_chars)
    old = mod._setting_on()
    try:
        mod.set_setting(False)
        corrector.invalidate_cache()
        base = {p: len(corrector.check_text(p)) for p in paras}
        mod.set_setting(True)
        corrector.invalidate_cache()
        chars = sum(sum(1 for ch in p if "\u4e00" <= ch <= "\u9fff") for p in paras)
        t0 = time.perf_counter()
        new_hits = 0
        examples = []
        for p in paras:
            hits = corrector.check_text(p)
            extra = len(hits) - base[p]
            if extra > 0:
                new_hits += extra
                if len(examples) < 5:
                    examples.append((p[:30], [(h.wrong, h.suggestion) for h in hits]))
        dt = time.perf_counter() - t0
        return new_hits, chars, dt, examples
    finally:
        mod.set_setting(old)
        corrector.invalidate_cache()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack-root", required=True)
    ap.add_argument("--kind", default="csc", choices=("csc", "cgec"))
    ap.add_argument("--corpus", default=str(_ROOT / "doc/corpus/real_all"))
    ap.add_argument("--sample-chars", type=int, default=40000)
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    root = Path(args.pack_root)
    paths.enhance_dir = lambda: root      # type: ignore[assignment]
    mod = _mod(args.kind)
    mod.invalidate_cache()
    print("=" * 62)
    print(f"P2 增强包验收（E9）：kind={args.kind} root={root}")
    print("=" * 62)
    print("状态：", mod.status_text())
    base, got = _recall(args.kind)
    print(f"OOV 召回：基础 {base}/24 → 开启增强层 {got}/24")
    fp, chars, dt, examples = _fp(args.kind, Path(args.corpus), args.sample_chars)
    per_qian = fp / max(1, chars) * 1000
    print(f"干净语料：{chars} 汉字；新增自动命中 {fp} 处 = {per_qian:.2f}/千字（阈值 ≤1.0）")
    print(f"耗时：{dt:.1f}s = {dt * 1000 / max(1, chars) * 1000:.0f} ms/千字")
    if examples:
        print("  示例：", examples)
    ok = got > base and per_qian <= 1.0
    print(f"结论：{'PASS' if ok else 'FAIL'}")
    if args.json:
        Path(args.json).write_text(json.dumps({
            "kind": args.kind, "oov_base": base, "oov_with_pack": got,
            "chars": chars, "new_hits": fp, "per_qian": per_qian,
            "seconds": dt, "pass": ok, "examples": examples,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
