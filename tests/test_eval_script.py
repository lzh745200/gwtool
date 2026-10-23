# -*- coding: utf-8 -*-
"""评测脚本（scripts/eval_corrector.py）的门禁契约回归：profile / SKIP / 语料。

背景（P0-2）：评测脚本原先默认跑 6 项，E4 恒 FAIL（词表法天花板）→ 整条命令
exit 1，于是它**根本不能当 CI 门禁**；而 E3/E5/E6 在 224 字内置小样上"空过"，
E6 在未装增强包的机器上也 PASS —— 数值看似齐全，实际没有一条是真门禁。

本文件锁死修复后的语义：
  1. `--profile base` 是 CI 用的档位：E1/E2 硬门禁，E4/E6 属增强层 → SKIP；
     无 `--corpus` 时 E3/E5 标"示意·不参与门禁" → SKIP。**exit 0**。
  2. `--profile all` 在未装增强包时必须把 E4/E6 显示为 SKIP（不再假 PASS/假 FAIL）。
  3. 给了 `--corpus` 后 E3/E5 才真正参与门禁：规范语料 → exit 0；
     有误报 → exit 1；**空语料是配置错误，判 E3 FAIL 而不是静默通过**。
  4. JSON 必须带 profile / 语料指纹 / 样本量，结果项带 status。
  5. CI（.github/workflows/build.yml）两个 job 都在 e2e 之前跑
     `--profile base`，且该步骤不需要增强包。

测试用 `--pack-root <空目录>` 强制"未安装增强包"，避免开发机真实装有增强包
时结果随机器漂移（这正是本项要消灭的"环境依赖型假绿"）。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "eval_corrector.py"
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"

# 语料门禁用统一的小样本量：E2 在 300 条上约 82%（阈值 75%），既省时又留余量。
GEN_SAMPLE = 300

# 规范公文体句子（人工确认无错字、无数字规范问题），用作"干净语料"。
CLEAN_LINES = [
    "各部门要高度重视此项工作，严格落实责任制，确保各项任务按期完成。",
    "为进一步规范公文处理流程，现将有关事项通知如下，请遵照执行。",
    "各地区、各部门要结合实际，认真抓好贯彻落实，并及时报告有关情况。",
    "会议听取了关于上半年工作情况的汇报，研究部署了下半年重点任务。",
    "要坚持问题导向，深入基层开展调查研究，切实解决群众反映的突出问题。",
    "各单位要加强协调配合，形成工作合力，共同推动各项措施落地见效。",
    "经研究决定，同意你单位请示事项，请按有关规定办理相关手续。",
    "此项工作纳入年度考核，请各单位务必于本月底前完成材料报送。",
]
# 合成长段落（3 句/段，约 90 汉字/段）：E5 的探针只取前 40 段，段落太短会让
# "每段固定开销"把 ms/千字放大成假 FAIL；长段落才能稳定度量真实吞吐。
CLEAN_PARAGRAPHS = ["".join(CLEAN_LINES[0:3]), "".join(CLEAN_LINES[3:6]),
                    "".join(CLEAN_LINES[6:8])]


def _load_module():
    """按文件路径加载脚本模块（scripts/ 不是包，无法 import）。"""
    spec = importlib.util.spec_from_file_location("eval_corrector", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(*args: str, timeout: int = 240) -> tuple[int, str]:
    """在独立进程里跑评测脚本，返回 (退出码, 合并输出)。

    独立进程是必须的：本文件要断言的正是**退出码**，而 main() 内部会重定向
    数据目录、落临时库；同进程反复调用容易互相污染缓存。
    """
    full_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                "QT_QPA_PLATFORM": "offscreen"}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(ROOT), env=full_env, capture_output=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _line_of(stdout: str, key: str) -> str:
    hits = [ln for ln in stdout.splitlines() if f"] {key} " in ln]
    assert hits, f"输出里找不到 {key} 结果行：\n{stdout}"
    return hits[0]


def _json_payload(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def empty_pack_root(tmp_path_factory) -> Path:
    """空增强包目录：让 installed_pack() 恒为 None（稳定模拟"本机未装增强包"）。"""
    d = tmp_path_factory.mktemp("no_enhance_pack")
    return d


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("eval_outs")


@pytest.fixture(scope="module")
def clean_corpus_dir(tmp_path_factory) -> Path:
    """干净语料：规范公文长段落 × 40（约 1 万汉字）。

    刻意放大到万字符级：E5（耗时门禁）在几十字的样本上会被单段固定开销放大到
    几百 ms/千字，产生假 FAIL —— 那正是"小样本空过/假红"要避免的同类问题。
    """
    d = tmp_path_factory.mktemp("corpus_clean")
    lines = CLEAN_PARAGRAPHS * 40
    (d / "gongwen.md").write_text("\n".join(lines), encoding="utf-8")
    return d


@pytest.fixture(scope="module")
def dirty_corpus_dir(tmp_path_factory) -> Path:
    """有真实误报的语料：干净段落 + 两行重复标点（必然命中"标点"规则）。"""
    d = tmp_path_factory.mktemp("corpus_dirty")
    lines = CLEAN_PARAGRAPHS * 40 + [
        "这是测试。。重复标点的问题。",
        "材料中共有三处。。需要复核。",
    ]
    (d / "gongwen.md").write_text("\n".join(lines), encoding="utf-8")
    return d


#: 只命中"提示类"的句子：数字用法 / 标点规范（引号未配对）/ 机构沿革。
#: 它们都不该进 E3a（可自动替换类）门禁。
ADVISORY_LINES = [
    "二〇二六年十月三日印发的文件，请遵照执行。",
    "会议指出“要加强管理，确保落实。",
    "由市环保局负责，市生态环境局配合。",
]


@pytest.fixture(scope="module")
def advisory_corpus_dir(tmp_path_factory) -> Path:
    """干净段落 + 三类提示类命中：E3a 应为 0，E3b 应 ≥3。"""
    d = tmp_path_factory.mktemp("corpus_advisory")
    lines = CLEAN_PARAGRAPHS * 40 + ADVISORY_LINES
    (d / "gongwen.md").write_text("\n".join(lines), encoding="utf-8")
    return d


@pytest.fixture(scope="module")
def base_run(empty_pack_root, out_dir):
    """--profile base：CI 门禁档位，无 --corpus。"""
    json_path = out_dir / "base.json"
    rc, out = _run("--profile", "base", "--pack-root", str(empty_pack_root),
                   "--gen-sample", str(GEN_SAMPLE), "--json", str(json_path))
    return rc, out, json_path


@pytest.fixture(scope="module")
def all_run(empty_pack_root):
    """--profile all：默认档位，但本机（空 pack-root）未装增强包。"""
    rc, out = _run("--profile", "all", "--pack-root", str(empty_pack_root),
                   "--gen-sample", str(GEN_SAMPLE))
    return rc, out


@pytest.fixture(scope="module")
def clean_corpus_run(empty_pack_root, clean_corpus_dir):
    rc, out = _run("--profile", "base", "--pack-root", str(empty_pack_root),
                   "--corpus", str(clean_corpus_dir),
                   "--gen-sample", str(GEN_SAMPLE))
    return rc, out


@pytest.fixture(scope="module")
def dirty_corpus_run(empty_pack_root, dirty_corpus_dir):
    rc, out = _run("--profile", "base", "--pack-root", str(empty_pack_root),
                   "--corpus", str(dirty_corpus_dir),
                   "--gen-sample", "100")
    return rc, out


@pytest.fixture(scope="module")
def advisory_corpus_run(empty_pack_root, advisory_corpus_dir, out_dir):
    """提示类语料：E3a 应 PASS、E3b 只报告（SKIP）。"""
    json_path = out_dir / "advisory.json"
    rc, out = _run("--profile", "base", "--pack-root", str(empty_pack_root),
                   "--corpus", str(advisory_corpus_dir),
                   "--gen-sample", str(GEN_SAMPLE), "--json", str(json_path))
    return rc, out, json_path


# ---------------------------------------------------------------- base 档位
def test_base_profile_exits_zero(base_run):
    """验收：--profile base exit 0（不需要增强包，CI 可直接当门禁）。"""
    rc, out, _json = base_run
    assert rc == 0, f"--profile base 应 exit 0，实际 {rc}\n{out}"
    assert "[FAIL]" not in out, f"base 档位不应出现 FAIL：\n{out}"
    assert "[PASS] E1 " in _line_of(out, "E1") and "100.0%" in _line_of(out, "E1")
    assert "[PASS] E2 " in _line_of(out, "E2")
    assert "通过 2/2 项" in out
    assert "退出码：0" in out


def test_base_profile_skips_enhancement_and_ungated_items(base_run):
    """base 档位：E4/E6 属增强层、E3a/E3b/E5 缺语料 → 全部 SKIP 且写明原因。"""
    _rc, out, _json = base_run
    e3 = _line_of(out, "E3a")
    assert e3.startswith("[SKIP] E3a ")
    assert "示意·不参与门禁" in e3 and "缺 --corpus" in e3
    e3b = _line_of(out, "E3b")
    assert e3b.startswith("[SKIP] E3b ")
    assert "提示类" in e3b and "不参与门禁" in e3b
    e4 = _line_of(out, "E4")
    assert e4.startswith("[SKIP] E4 ") and "profile=base" in e4
    e5 = _line_of(out, "E5")
    assert e5.startswith("[SKIP] E5 ") and "示意·不参与门禁" in e5
    e6 = _line_of(out, "E6")
    assert e6.startswith("[SKIP] E6 ") and "不含增强层" in e6
    # 无 --corpus 时 E3a/E3b/E5/E6 都要带统一标记（P0-2 验收口径）
    assert "示意·不参与门禁" in e6
    # SKIP 不计 FAIL，也不影响退出码——汇总必须说清楚
    assert "未参与门禁" in out and "不影响退出码" in out


def test_json_contract(base_run):
    """JSON 必须带 profile / 语料指纹 / 样本量，结果项带 status。

    同时锁 E3 口径拆分：E3a/E3b/e3_all 三个键都在，且旧键 E3 仍等于总口径。
    """
    _rc, _out, json_path = base_run
    payload = _json_payload(json_path)
    assert payload["profile"] == "base"
    assert payload["layers"] == ["base"]

    corpus = payload["corpus"]
    assert corpus["external"] is False
    assert corpus["segments"] >= 8 and corpus["chars"] > 0
    assert isinstance(corpus["fingerprint"], str) and len(corpus["fingerprint"]) == 16
    assert len(corpus["fingerprint"].strip("0")) > 0

    sample = payload["sample_chars"]
    assert set(sample) == {"E3", "E3a", "E3b", "E5", "E6"}
    assert sample["E3"] > 0 and sample["E5"] > 0
    assert sample["E3a"] == sample["E3"] == sample["E3b"]

    statuses = {r["key"]: r["status"] for r in payload["results"]}
    assert statuses == {"E1": "PASS", "E2": "PASS", "E3a": "SKIP", "E3b": "SKIP",
                        "E4": "SKIP", "E5": "SKIP", "E6": "SKIP"}
    for item in payload["results"]:
        assert item["passed"] == (item["status"] == "PASS")
        assert item["status"] in {"PASS", "FAIL", "SKIP"}
    data = payload["data"]
    assert data["profile"] == "base"
    assert data["corpus"]["fingerprint"] == corpus["fingerprint"]

    # E3 口径三件套：E3a 可替换类 / E3b 提示类 / e3_all 旧总口径
    for key in ("E3a", "E3b", "e3_all"):
        assert key in data, f"JSON data 缺少 {key}"
    assert data["E3"] == data["e3_all"], "旧键 E3 应等于 e3_all 总口径"
    assert data["E3b"]["gated"] is False, "提示类永远不参与门禁"
    assert "机构沿革" in data["e3_all"]["advisory_categories"]
    assert {"数字用法", "表达提示", "标点规范"} <= set(
        data["e3_all"]["advisory_categories"])


# ---------------------------------------------------------------- all 档位
def test_all_profile_without_packs_skips_e4_e6(all_run):
    """验收：--profile all 在未装增强包时 E4/E6 必须 SKIP，而不是假 PASS/假 FAIL。"""
    rc, out = all_run
    assert rc == 0, f"未装增强包时 all 档不含 FAIL，应 exit 0，实际 {rc}\n{out}"
    e4 = _line_of(out, "E4")
    assert e4.startswith("[SKIP] E4 ") and "增强包" in e4
    e6 = _line_of(out, "E6")
    assert e6.startswith("[SKIP] E6 ") and "增强包" in e6
    assert "示意·不参与门禁" in e6
    assert "[PASS] E1 " in _line_of(out, "E1")
    assert "[PASS] E2 " in _line_of(out, "E2")
    assert "[FAIL]" not in out


# ---------------------------------------------------------- 语料参与门禁
def test_clean_corpus_gates_e3_and_e5_to_pass(clean_corpus_run):
    """给了规范语料后 E3a/E5 从 SKIP 变成真门禁，并应 PASS。"""
    rc, out = clean_corpus_run
    assert rc == 0, f"规范语料应 exit 0，实际 {rc}\n{out}"
    e3 = _line_of(out, "E3a")
    assert e3.startswith("[PASS] E3a ")
    assert "外部语料" in e3 and "示意" not in e3
    e3b = _line_of(out, "E3b")
    assert e3b.startswith("[SKIP] E3b ")
    assert "只报告" in e3b and "不参与门禁" in e3b
    e5 = _line_of(out, "E5")
    assert e5.startswith("[PASS] E5 ") and "示意" not in e5
    # 增强层项仍按 profile 跳过
    assert _line_of(out, "E4").startswith("[SKIP] E4 ")
    assert _line_of(out, "E6").startswith("[SKIP] E6 ")


def test_real_failure_sets_exit_code_one(dirty_corpus_run):
    """有真实 FAIL 才 exit 1：语料里有重复标点（"标点"属可替换类）→ E3a 必然 FAIL。"""
    rc, out = dirty_corpus_run
    assert rc == 1, f"存在真实误报时应 exit 1，实际 {rc}\n{out}"
    assert _line_of(out, "E3a").startswith("[FAIL] E3a ")
    # 可替换类误报不该被算成提示类
    assert "可替换类" in _line_of(out, "E3a") and "标点" in _line_of(out, "E3a")
    summary = [ln for ln in out.splitlines() if ln.startswith("门禁项：")]
    assert summary, f"缺少汇总行：\n{out}"
    assert "E3a" in summary[0].split("未达标：", 1)[1]
    assert "退出码：1" in out


def test_empty_corpus_is_a_real_failure(empty_pack_root, tmp_path):
    """空语料是配置错误：判 E3a FAIL 并 exit 1，绝不能当门禁通过。"""
    empty = tmp_path / "no_corpus"
    empty.mkdir()
    rc, out = _run("--profile", "base", "--pack-root", str(empty_pack_root),
                   "--corpus", str(empty), "--gen-sample", "100")
    assert rc == 1, f"空语料应 exit 1，实际 {rc}\n{out}"
    assert _line_of(out, "E3a").startswith("[FAIL] E3a ")
    assert "为空或不可读" in out


# ---------------------------------------------------------------- E3a/E3b 拆分
def test_advisory_only_corpus_passes_e3a_but_reports_e3b(advisory_corpus_run):
    """提示类命中只进 E3b：E3a 必须 PASS，E3b 只报告（SKIP）。"""
    rc, out, json_path = advisory_corpus_run
    assert rc == 0, f"只有提示类命中时 E3a 应 PASS，实际 exit {rc}\n{out}"
    e3a = _line_of(out, "E3a")
    assert e3a.startswith("[PASS] E3a ")
    assert "可替换类 0 处" in e3a, e3a
    assert "标点规范" not in e3a.split("分类：", 1)[1], "提示类不该算进 E3a"
    e3b = _line_of(out, "E3b")
    assert e3b.startswith("[SKIP] E3b ") and "提示类" in e3b
    assert "数字用法" in e3b and "标点规范" in e3b and "机构沿革" in e3b, e3b
    # 文本输出里两个口径都要能看到
    assert "E3 口径" in out and "e3_all" in out

    data = _json_payload(json_path)["data"]
    assert data["E3a"]["hits"] == 0 and data["E3a"]["status"] == "PASS"
    assert data["E3a"]["gated"] is True
    assert data["E3b"]["hits"] >= 3 and data["E3b"]["status"] == "SKIP"
    assert data["E3b"]["gated"] is False
    assert data["e3_all"]["hits"] == data["E3b"]["hits"]
    assert data["E3a"]["hits"] + data["E3b"]["hits"] == data["e3_all"]["hits"]
    assert set(data["E3b"]["by_category"]) >= {"数字用法", "标点规范", "机构沿革"}


def test_org_rename_counts_as_advisory():
    """机构沿革按数据表自注"提示级"归 E3b（改这一行即可翻转口径）。"""
    ev = _load_module()
    assert ev.E3_ADVISORY_EXTRA == ("机构沿革",)
    assert ev.TH_FP_PER_WAN == 0.50, "E3a 阈值是有意固定的红线"


# ---------------------------------------------------------------- 参数兼容
def test_profile_resolution_and_legacy_layers():
    """--profile 与旧 --layers 的映射/冲突语义（纯函数，不起子进程）。"""
    ev = _load_module()
    ap = ev._build_parser()

    def ns(profile=None, layers=None):
        return argparse.Namespace(profile=profile, layers=layers)

    assert ev._resolve_profile(ns(), ap) == "all"          # 旧默认行为
    assert ev._resolve_profile(ns(layers="base"), ap) == "base"
    assert ev._resolve_profile(ns(layers="base,l4"), ap) == "l4"
    assert ev._resolve_profile(ns(layers="base,l5"), ap) == "l5"
    assert ev._resolve_profile(ns(layers="base,l4,l5"), ap) == "all"
    assert ev._resolve_profile(ns(profile="l5"), ap) == "l5"
    assert ev._resolve_profile(ns(profile="base", layers="base"), ap) == "base"
    with pytest.raises(SystemExit):
        ev._resolve_profile(ns(profile="all", layers="base"), ap)
    with pytest.raises(SystemExit):
        ev._resolve_profile(ns(layers="l4,l5"), ap)       # 缺 base，非法组合


def test_result_status_semantics():
    """Result 的 SKIP 不算 passed，且行格式保持 [TAG] E<n> 名称：文本。"""
    ev = _load_module()
    assert ev.PROFILES["base"] == ("base",)
    assert set(ev.PROFILES) == {"base", "l4", "l5", "all"}

    skip = ev._skipped_result("E4", "示例", 0.1, "文本", "未安装增强包")
    assert skip.status == "SKIP" and skip.skipped and not skip.passed
    assert skip.line() == "[SKIP] E4 示例：文本"

    ok = ev._gated_result("E1", "示例", 1.0, "文本", True)
    assert ok.status == "PASS" and ok.passed

    bad = ev._gated_result("E1", "示例", 0.5, "文本", False, "（阈值 1.0）")
    assert bad.status == "FAIL" and not bad.passed
    assert bad.line() == "[FAIL] E1 示例：文本（阈值 1.0）"
    assert bad.to_json()["passed"] is False


def test_corpus_fingerprint_is_stable_and_sensitive():
    ev = _load_module()
    a = ev._corpus_fingerprint(["第一段", "第二段"])
    assert a == ev._corpus_fingerprint(["第一段", "第二段"])
    assert a != ev._corpus_fingerprint(["第一段", "第三段"])
    assert len(a) == 16


# ---------------------------------------------------------------- CI 接线
def test_ci_runs_base_profile_before_e2e_in_both_jobs():
    """CI 两个 job 都必须在 e2e 之前跑 `--profile base`，且不依赖增强包。"""
    wf = WORKFLOW.read_text(encoding="utf-8")
    win = wf.split("build-windows:", 1)[1].split("build-linux:", 1)[0]
    linux = wf.split("build-linux:", 1)[1].split("package-linux:", 1)[0]

    win_cmd = "run: python scripts/eval_corrector.py --profile base"
    linux_cmd = "run: $PYBIN scripts/eval_corrector.py --profile base"
    for job, cmd in ((win, win_cmd), (linux, linux_cmd)):
        assert cmd in job, f"缺少评测门禁步骤：{cmd}"
        gate = job.index(cmd)
        e2e = job.index("scripts/e2e_check.py")
        assert gate < e2e, "评测门禁必须排在 e2e 自检之前"
    # 门禁步骤本身不带增强包参数（--profile base 不依赖增强包）
    assert "--pack-root" not in wf
