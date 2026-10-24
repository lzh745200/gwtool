# -*- coding: utf-8 -*-
"""v1.15：汇编验收矩阵脚本的接口回归（不跑全矩阵）。

只断言**脚本契约**：退出码含义、JSON 结构、报告文件落盘、SKIP 必须带原因、
用法错误返回 2。具体 PASS/FAIL 由矩阵报告承载——产品能力仍在并行开发中时，
本测试也应当稳定（退出码允许 0 或 1）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "compile_matrix.py"


def _run(args: list[str], out_dir: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--out", str(out_dir)],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300)


def test_fast_matrix_report_contract(tmp_path):
    out = tmp_path / "matrix"
    proc = _run(["--fast"], out)
    # 0 = 全 PASS/SKIP；1 = 有 FAIL（产品能力未就绪时就是 1）；2 只用于用法/依赖错误
    assert proc.returncode in (0, 1), (
        f"退出码 {proc.returncode}；stderr={proc.stderr[-800:]}")
    json_path = out / "compile_matrix.json"
    md_path = out / "compile_matrix.md"
    assert json_path.is_file(), "未生成 JSON 报告"
    assert md_path.is_file(), "未生成 Markdown 报告"
    assert md_path.read_text(encoding="utf-8").strip()

    data = json.loads(json_path.read_text(encoding="utf-8"))
    for key in ("meta", "scenarios", "checks", "summary", "defects", "skips"):
        assert key in data, f"报告缺字段 {key}"
    assert data["meta"]["tool"] == "compile_matrix"
    assert data["meta"]["fast"] is True
    assert isinstance(data["meta"]["deps"], dict)

    checks = data["checks"]
    assert checks, "checks 为空（矩阵必须至少产出一条检查）"
    for c in checks:
        assert set(("id", "scenario", "group", "item", "status", "detail")
                   ) <= set(c), c
        assert c["status"] in ("PASS", "FAIL", "SKIP"), c
        assert c["id"] and c["item"], c
    summary = data["summary"]
    assert summary["total"] == len(checks)
    for key in ("pass", "fail", "skip"):
        assert isinstance(summary[key], int)
    assert summary["pass"] + summary["fail"] + summary["skip"] == len(checks)
    # 与退出码自洽：有 FAIL 必须是 1，无 FAIL 才是 0
    assert proc.returncode == (1 if summary["fail"] else 0)

    # FAIL 与 defects 对齐；SKIP 必须写清原因（不假装 PASS 的另一半：也不许静默 SKIP）
    assert len(data["defects"]) == summary["fail"]
    assert len(data["skips"]) == summary["skip"]
    for c in data["skips"]:
        assert c["detail"].strip(), f"SKIP 没写原因：{c['id']}"
    for c in data["defects"]:
        assert c["detail"].strip(), f"FAIL 没写现象：{c['id']}"

    scenarios = data["scenarios"]
    assert scenarios, "scenarios 为空"
    for s in scenarios:
        assert s["id"] and s["label"] and s["inputs"]
        assert Path(s["artifact_dir"]).exists(), f"产物目录不存在：{s['artifact_dir']}"
    ok = [s for s in scenarios if not s.get("error")]
    if not ok:
        # 产品侧编译/渲染仍在并行开发时允许整场景失败，但必须是**如实 FAIL**，
        # 不能把失败写成 PASS，也不能吞掉错误。
        assert summary["fail"] >= 1, "场景全部失败却没有 FAIL 记录"
        for s in scenarios:
            assert s.get("error"), f"{s['id']} 失败但没有 error 证据"
    for s in ok:
        for key in ("docx", "a4_pdf", "a3_pdf"):
            assert Path(s["artifacts"][key]).is_file(), f"{s['id']} 缺 {key}"


def test_usage_error_returns_2(tmp_path):
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--not-a-real-option"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 2, f"用法错误应返回 2，实得 {proc.returncode}"
