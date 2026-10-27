# -*- coding: utf-8 -*-
"""P3：FCGEC 独立基准回归（数据缺失时跳过，CI 不下载）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "doc/bench/fcgec_valid.json"
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.mark.skipif(not DATA.is_file(), reason="本地未下载 FCGEC（scripts/fetch_fcgec.py）")
def test_fcgec_harness_runs_and_fp_gate():
    import eval_grammar_independent as ev

    r = ev.run(sample=200)
    assert r["samples"] == 200
    assert r["errors"] > 0 and r["clean"] > 0
    assert 0.0 <= r["recall_sentence"] <= 1.0
    assert r["fp_per_wan"] <= ev.TH_FP_PER_WAN, (
        f"独立基准干净句误报 {r['fp_per_wan']:.3f}/万字，超过 {ev.TH_FP_PER_WAN}")
