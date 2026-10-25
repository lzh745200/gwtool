# -*- coding: utf-8 -*-
"""C3 跨平台验收脚本的契约测试。

只跑 `--fast`（跳过 1 万字纠错/写作提示计时），离屏 + 临时输出目录：
  · 环境采集 + 功能 smoke 的 JSON 必填字段齐全；
  · 退出码 0（WARN/SKIP 允许，FAIL 不允许）；
  · 报告文件确实生成、Markdown 含人读摘要与判据；
  · 脚本不依赖 Office/COM（不 import win32com/pythoncom，不调用 Word/WPS）；
  · `--json` 模式 stdout 是纯 JSON（不写文件）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "accept_env.py"

REQUIRED_TOP = ("schema", "tool", "app_version", "generated_at", "mode",
                "platform", "environment", "checks", "summary", "artifacts")
REQUIRED_CHECKS = {
    "env.python", "env.qt", "env.pymupdf", "env.fonts", "env.seed_db",
    "smoke.db", "smoke.import", "smoke.docx", "smoke.pdf",
    "smoke.backup_restore", "perf.corrector", "perf.writing_hints",
}
ALLOWED_STATUS = {"PASS", "WARN", "FAIL", "SKIP"}


def _run(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout)


def _check_shape(data: dict) -> None:
    for key in REQUIRED_TOP:
        assert key in data, f"报告缺少顶层字段：{key}"
    assert data["schema"] == 1
    assert data["tool"] == "gwtool-accept-env"
    assert data["mode"] in ("fast", "full")
    # 环境
    pf = data["platform"]
    for key in ("os", "machine", "python"):
        assert pf.get(key), f"platform 缺少 {key}"
    env = data["environment"]
    assert env["versions"].get("gwtool")
    assert "fonts" in env and isinstance(env["fonts"].get("cjk_fonts"), list)
    assert env["paths"].get("seed_db")
    # 检查项
    checks = data["checks"]
    assert isinstance(checks, list) and checks
    ids = set()
    for c in checks:
        for key in ("id", "title", "status", "detail", "metrics"):
            assert key in c, f"检查项缺少 {key}: {c}"
        assert c["status"] in ALLOWED_STATUS, c
        ids.add(c["id"])
    missing = REQUIRED_CHECKS - ids
    assert not missing, f"缺少检查项：{sorted(missing)}"
    # 汇总
    summary = data["summary"]
    for key in ("pass", "warn", "fail", "skip", "verdict"):
        assert key in summary, f"summary 缺少 {key}"
    assert summary["fail"] == 0, f"验收出现 FAIL：{summary}"
    assert summary["verdict"] in ("PASS", "WARN")
    assert summary["pass"] + summary["warn"] + summary["fail"] + \
        summary["skip"] == len(checks)


def test_fast_json_contract(tmp_path):
    """--fast --json：纯 JSON、必填字段、退出码 0。"""
    r = _run(["--fast", "--json"])
    assert r.returncode == 0, f"exit={r.returncode}\nstderr:\n{r.stderr[-3000:]}"
    data = json.loads(r.stdout)
    assert data["mode"] == "fast"
    _check_shape(data)
    # --fast 必须跳过两个耗时项
    perf = {c["id"]: c for c in data["checks"]
            if c["id"].startswith("perf.")}
    assert perf and all(c["status"] == "SKIP" for c in perf.values())
    # --json 只打印，不落文件
    assert list(tmp_path.glob("accept_report_*")) == []


def test_fast_writes_json_and_markdown(tmp_path):
    """--fast --out：生成同名 json + md，Markdown 含结论与判据。"""
    r = _run(["--fast", "--out", str(tmp_path)])
    assert r.returncode == 0, f"exit={r.returncode}\nstderr:\n{r.stderr[-3000:]}"
    jsons = sorted(tmp_path.glob("accept_report_*.json"))
    mds = sorted(tmp_path.glob("accept_report_*.md"))
    assert len(jsons) == 1 and len(mds) == 1, (jsons, mds)
    data = json.loads(jsons[0].read_text(encoding="utf-8"))
    _check_shape(data)
    assert data["artifacts"]["json"] == str(jsons[0])
    assert data["artifacts"]["markdown"] == str(mds[0])
    md = mds[0].read_text(encoding="utf-8")
    for key in ("# 公文汇编助手 · 环境验收报告", "结论：", "判据",
                "± 1.5 pt", "回传"):
        assert key in md, f"Markdown 缺少：{key}"


def test_script_has_no_office_com_dependency():
    """真机验收不得要求装 Office/WPS（CI/最小安装麒麟上要能跑）。"""
    src = SCRIPT.read_text(encoding="utf-8").lower()
    for token in ("win32com", "pythoncom", "docx2pdf", "comtypes",
                  "win32.client", "word.application"):
        assert token not in src, f"验收脚本引入了 Office/COM 依赖：{token}"


def test_unwritable_out_dir_exits_2(tmp_path):
    """输出路径是文件而非目录：脚本应安全失败（退出码 2）而不是崩溃。"""
    bad = tmp_path / "not_a_dir"
    bad.write_text("x", encoding="utf-8")
    r = _run(["--fast", "--out", str(bad)])
    assert r.returncode == 2, f"exit={r.returncode}\nstdout:{r.stdout[:500]}"


def test_report_does_not_embed_sample_body(tmp_path):
    """报告只写环境与 smoke 指标，不夹带样例正文（避免误传业务内容）。"""
    r = _run(["--fast", "--json"])
    assert r.returncode == 0
    assert "验收正文样本段落" not in r.stdout
    data = json.loads(r.stdout)
    assert "checks" in data and "environment" in data

def test_quiet_native_stdout_blocks_fd_noise():
    """fd 级噪声不得污染 `--json` 的 stdout（Linux CI 上 MuPDF 会直接写 fd 1）。

    v1.11.0 首次 CI 的真实故障：`--fast --json` 在 Linux 上 stdout 混入
    `MuPDF error: ...`，`json.loads` 从 char 0 就失败。修法是 fd 层重定向，
    这里用一个"直接 os.write(1, ...)"的 C 级噪声来钉住它。
    """
    snippet = (
        "import importlib.util, json, os\n"
        "spec = importlib.util.spec_from_file_location("
        f"'accept_env', r'{SCRIPT}')\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "with m._quiet_native_stdout():\n"
        "    os.write(1, b'NOISE-FROM-C\\n')\n"
        "print(json.dumps({'ok': True}))\n"
    )
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
               QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([sys.executable, "-c", snippet], cwd=str(ROOT),
                       env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "NOISE-FROM-C" not in r.stdout, r.stdout[:600]
    assert json.loads(r.stdout.strip()) == {"ok": True}
