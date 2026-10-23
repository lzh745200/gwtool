# -*- coding: utf-8 -*-
"""C1 真实语料契约回归（v1.11.0）：语料体检 + 评测联动 + 增强包验收。

覆盖三份交付物：

1. ``scripts/corpus_check.py``：编码/规模/脱敏体检。
   - 干净语料 → exit 0；含身份证/手机/邮箱/银行卡/座机 → exit 1 且给出
     ``文件:行``（报告里只出现掩码值）；空目录 → exit 1；路径不存在 → exit 2；
     非 UTF-8 → exit 1；单篇过短 → exit 1；
   - **不猜人名**（正则无可靠判据，猜了会把"没报错"当"已脱敏"）；
     敏感标记（内部资料/机密）只告警、不影响退出码。
2. ``scripts/eval_corrector.py``：
   - ``--corpus`` 支持 .txt 且真正参与 E3/E5 门禁（干净语料 PASS）；
   - ``--corpus-manifest`` 写逐文件 sha256 + 聚合 sha256 + 样本量；
   - ``--require-packs``：未装增强包时 E4/E6 由 SKIP 变 **FAIL**（有包机器验收用），
     且不改变 ``--profile base`` 的既有语义（仍 exit 0）。
3. ``doc/corpus/README.md``：语料放置/脱敏规范/E3·E6 口径/验收命令，必须可交付。

测试统一用 ``--pack-root <空目录>`` 模拟"本机未装增强包"，避免结果随机器漂移。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK_SCRIPT = ROOT / "scripts" / "corpus_check.py"
EVAL_SCRIPT = ROOT / "scripts" / "eval_corrector.py"
CORPUS_README = ROOT / "doc" / "corpus" / "README.md"
GOV_CN = ROOT / "doc" / "corpus" / "gov_cn"
#: 语料目录里"不是语料"的文件：两个脚本必须用同一份跳过清单。
SKIP_NAMES = {"README.md", "采集合规检查表.md",
              "AUTHORIZATION.example.json", "AUTHORIZATION.json"}

# 规范公文句（人工确认无错字、无数字规范问题）。
CLEAN_SENTENCES = [
    "各部门要高度重视此项工作，严格落实责任制，确保各项任务按期完成。",
    "为进一步规范公文处理流程，现将有关事项通知如下，请遵照执行。",
    "各地区、各部门要结合实际，认真抓好贯彻落实，并及时报告有关情况。",
    "会议听取了关于上半年工作情况的汇报，研究部署了下半年重点任务。",
    "要坚持问题导向，深入基层开展调查研究，切实解决群众反映的突出问题。",
    "各单位要加强协调配合，形成工作合力，共同推动各项措施落地见效。",
    "经研究决定，同意你单位请示事项，请按有关规定办理相关手续。",
    "此项工作纳入年度考核，请各单位务必于本月底前完成材料报送。",
]


def _run(script: Path, *args: str, timeout: int = 240) -> tuple[int, str]:
    """独立进程跑脚本，返回 (退出码, 合并输出)。

    独立进程是必须的：要断言的是**退出码**，而两个脚本都会落临时文件/重定向数据
    目录；同进程反复调用互相污染缓存。
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
           "QT_QPA_PLATFORM": "offscreen"}
    proc = subprocess.run(
        [sys.executable, str(script), *args], cwd=str(ROOT), env=env,
        capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _run_check(*args: str) -> tuple[int, str]:
    return _run(CHECK_SCRIPT, *args)


def _run_eval(*args: str) -> tuple[int, str]:
    return _run(EVAL_SCRIPT, *args)


def _line_of(stdout: str, key: str) -> str:
    hits = [ln for ln in stdout.splitlines() if f"] {key} " in ln]
    assert hits, f"输出里找不到 {key} 结果行：\n{stdout}"
    return hits[0]


def _load_script(path: Path, name: str):
    """按文件路径加载脚本模块（scripts/ 不是包，无法直接 import）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str, encoding: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 不用 Path.write_text(..., newline=)：`newline` 是 Python 3.10 才加的参数，
    # 而本项目的发布基线是 Python 3.9（麒麟 V10 自带）——CI 实测在 3.9 上
    # 直接 TypeError。open() 的 newline 从 3.3 起就有，语义相同。
    with open(path, "w", encoding=encoding, newline="\n") as fh:
        fh.write(text)
    return path


def _big_paragraph() -> str:
    return "".join(CLEAN_SENTENCES[:3])


# ---------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def empty_pack_root(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("c1_no_enhance_pack")


@pytest.fixture(scope="module")
def clean_check_corpus(tmp_path_factory) -> Path:
    """体检用干净语料：顶层 .md + 子目录 .txt（验证递归扫描）。"""
    d = tmp_path_factory.mktemp("c1_check_clean")
    _write(d / "gongwen.md", "\n".join(CLEAN_SENTENCES * 4))
    _write(d / "sub" / "notice.txt", "\n".join(CLEAN_SENTENCES * 2))
    return d


@pytest.fixture(scope="module")
def eval_corpus_dir(tmp_path_factory) -> Path:
    """评测用语料：顶层 .txt、约 3,600 汉字（E3 分母够、E5 探针稳定）。"""
    d = tmp_path_factory.mktemp("c1_eval_corpus")
    _write(d / "gongwen.txt", "\n".join(_big_paragraph() for _ in range(40)))
    return d


@pytest.fixture(scope="module")
def check_runs(tmp_path_factory) -> dict:
    """一次性跑完 corpus_check 的正反例，供多个用例共享。"""
    base = tmp_path_factory.mktemp("c1_check_cases")

    clean = base / "clean"
    _write(clean / "gongwen.md", "\n".join(CLEAN_SENTENCES * 4))
    _write(clean / "sub" / "notice.txt", "\n".join(CLEAN_SENTENCES * 2))

    pii = base / "pii"
    _write(pii / "bad.md", "\n".join([
        "各单位要高度重视此项工作，严格落实责任制，确保各项任务按期完成。",
        "联系人身份证号 110101199003072316，手机 13812345678。",
        "邮箱 zhangsan@example.com，银行卡 6222020200112233445，"
        "座机 010-12345678。",
    ]))

    empty = base / "empty"
    empty.mkdir()

    short = base / "short"
    _write(short / "tiny.md", "这是一篇很短的语料。")

    gbk = base / "gbk"
    gbk.mkdir()
    (gbk / "gbk.md").write_bytes("各单位要高度重视此项工作。".encode("gbk"))

    warn = base / "warn"
    _write(warn / "internal.md",
           "内部资料，请勿外传。\n" + "\n".join(CLEAN_SENTENCES))

    no_pii_names = base / "names"
    _write(no_pii_names / "name.md",
           "张三、李四、王五参加会议。\n" + "\n".join(CLEAN_SENTENCES))

    csv_only = base / "csv"
    _write(csv_only / "data.csv", "a,b\n1,2\n")

    single = base / "single.txt"
    _write(single, "\n".join(CLEAN_SENTENCES * 6))

    return {
        "base": base, "clean": clean, "pii": pii, "empty": empty,
        "short": short, "gbk": gbk, "warn": warn, "names": no_pii_names,
        "csv": csv_only, "single": single,
        "clean_out": base / "reports" / "clean",
        "pii_out": base / "reports" / "pii.json",
        "warn_out": base / "reports" / "warn.json",
    }


@pytest.fixture(scope="module")
def clean_corpus_check_run(check_runs):
    rc, out = _run_check(str(check_runs["clean"]), "--out",
                         str(check_runs["clean_out"]))
    return rc, out


@pytest.fixture(scope="module")
def pii_corpus_check_run(check_runs):
    # 缩小规模下限，让"脱敏"成为唯一 FAIL 原因（否则会混进"单篇过短/总量不足"）
    rc, out = _run_check(str(check_runs["pii"]), "--out",
                         str(check_runs["pii_out"]),
                         "--min-doc-cjk", "0", "--min-total-cjk", "0")
    return rc, out


@pytest.fixture(scope="module")
def eval_clean_run(empty_pack_root, eval_corpus_dir, tmp_path_factory):
    """--corpus 指向 .txt 语料：E3/E5 参与门禁并 PASS；同时写清单。"""
    out = tmp_path_factory.mktemp("c1_eval_out")
    manifest = out / "corpus_manifest.json"
    json_path = out / "eval.json"
    rc, text = _run_eval(
        "--profile", "base", "--pack-root", str(empty_pack_root),
        "--corpus", str(eval_corpus_dir),
        "--corpus-manifest", str(manifest),
        "--json", str(json_path), "--gen-sample", "300")
    return rc, text, manifest, json_path


@pytest.fixture(scope="module")
def require_packs_run(empty_pack_root):
    """未装增强包 + --require-packs：E4/E6 必须 FAIL（不能 SKIP 混过去）。"""
    rc, text = _run_eval("--profile", "all", "--pack-root", str(empty_pack_root),
                         "--require-packs", "--gen-sample", "100")
    return rc, text


# ---------------------------------------------------------------- 语料体检
def test_clean_corpus_passes(clean_corpus_check_run, check_runs):
    """干净语料（递归含子目录）→ exit 0，JSON+MD 报告都写出来。"""
    rc, out = clean_corpus_check_run
    assert rc == 0, f"干净语料应 exit 0，实际 {rc}\n{out}"
    json_path = Path(str(check_runs["clean_out"]) + ".json")
    md_path = Path(str(check_runs["clean_out"]) + ".md")
    assert json_path.exists() and md_path.exists(), out
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["ok"] is True and report["exit_code"] == 0
    assert report["corpus"]["files"] == 2, "递归扫描应覆盖子目录语料"
    assert report["corpus"]["cjk"] >= 1000
    assert len(report["files"][0]["sha256"]) == 64
    assert "PASS" in md_path.read_text(encoding="utf-8")


def test_pii_hits_fail_with_file_and_line(pii_corpus_check_run, check_runs):
    """五类 PII 任一命中 → exit 1；报告给出 文件:行，且只出现掩码值。"""
    rc, out = pii_corpus_check_run
    assert rc == 1, f"含 PII 的语料应 exit 1，实际 {rc}\n{out}"
    report = json.loads(Path(check_runs["pii_out"]).read_text(encoding="utf-8"))
    kinds = {e["kind"] for e in report["errors"]}
    for kind in ("身份证", "手机号", "邮箱", "银行卡", "座机号"):
        assert kind in kinds, f"未检出 {kind}：{report['errors']}"
    by_kind = {e["kind"]: e for e in report["errors"]}
    assert by_kind["身份证"]["file"] == "bad.md" and by_kind["身份证"]["line"] == 2
    assert by_kind["手机号"]["line"] == 2
    assert by_kind["邮箱"]["line"] == 3 and by_kind["银行卡"]["line"] == 3
    assert by_kind["座机号"]["line"] == 3
    # 报告是会被归档/发出去的文件，绝不能二次泄露完整 PII
    raw = Path(check_runs["pii_out"]).read_text(encoding="utf-8")
    for secret in ("110101199003072316", "13812345678",
                   "zhangsan@example.com", "6222020200112233445"):
        assert secret not in raw, f"报告泄露了完整 PII：{secret}"
    assert "*" in raw


def test_empty_directory_fails(check_runs):
    """空目录不是"干净"，而是"没有语料" → exit 1。"""
    rc, out = _run_check(str(check_runs["empty"]))
    assert rc == 1, f"空目录应 exit 1，实际 {rc}\n{out}"
    assert "无语料" in out or "没有" in out


def test_missing_path_is_usage_error(check_runs):
    rc, out = _run_check(str(check_runs["base"] / "does_not_exist"))
    assert rc == 2, f"路径不存在应 exit 2（用法错误），实际 {rc}\n{out}"


def test_non_utf8_file_fails(check_runs):
    """GBK 老文件必须 FAIL 并指出编码问题（评测端会自动探测，但契约要求 UTF-8）。"""
    rc, out = _run_check(str(check_runs["gbk"]))
    assert rc == 1, f"非 UTF-8 应 exit 1，实际 {rc}\n{out}"
    assert "编码" in out and "UTF-8" in out


def test_too_short_document_fails_default_floor(check_runs):
    """默认单篇下限 100 汉字：一段话的"语料"不能当负样本。"""
    rc, out = _run_check(str(check_runs["short"]))
    assert rc == 1, f"单篇过短应 exit 1，实际 {rc}\n{out}"
    assert "单篇过短" in out


def test_warning_marker_only_warns(check_runs):
    """敏感标记（内部资料）只告警：exit 0，但报告里要有 warning。"""
    rc, out = _run_check(str(check_runs["warn"]), "--out",
                         str(check_runs["warn_out"]),
                         "--min-doc-cjk", "0", "--min-total-cjk", "0")
    assert rc == 0, f"仅敏感标记不应判 FAIL，实际 {rc}\n{out}"
    report = json.loads(Path(check_runs["warn_out"]).read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert any(w["kind"] == "敏感标记" for w in report["warnings"]), report


def test_names_are_not_guessed(check_runs):
    """**不猜人名**：正文出现「张三/李四」不报 PII（正则判不了，猜了更危险）。"""
    rc, out = _run_check(str(check_runs["names"]),
                         "--min-doc-cjk", "0", "--min-total-cjk", "0")
    assert rc == 0, f"人名不应被当成可判定的 PII，实际 {rc}\n{out}"
    assert "身份证" not in out and "手机号" not in out


def test_single_file_corpus_accepted(check_runs):
    rc, out = _run_check(str(check_runs["single"]))
    assert rc == 0, f"单个 .txt 语料应可用于体检，实际 {rc}\n{out}"


def test_usage_rejects_non_corpus_suffix(check_runs):
    """单文件只接受 .md/.txt；.csv 属于用法错误（表格不能当公文句语料）。"""
    target = check_runs["csv"] / "data.csv"
    rc, out = _run_check(str(target))
    assert rc == 2, f"非语料后缀应 exit 2，实际 {rc}\n{out}"


def test_readme_is_not_treated_as_corpus(tmp_path):
    """契约 README 与语料同目录时，两个工具都必须跳过它。

    README 里写着身份证/银行卡的**示例**数字；一旦被当成负样本读进 E3，
    既会污染误报率，也会让 corpus_check 把自己的说明文档判成"泄露 PII"。
    """
    d = tmp_path / "corpus_with_readme"
    d.mkdir()
    content = "\n".join(CLEAN_SENTENCES * 6)
    (d / "README.md").write_text(
        "# 说明\n\n示例：身份证 110101199003072316，手机 13812345678。\n",
        encoding="utf-8")
    _write(d / "gongwen.md", content)

    rc, out = _run_check(str(d))
    assert rc == 0, f"README 不应被当成语料/不应触发 PII，实际 {rc}\n{out}"

    check_mod = _load_script(CHECK_SCRIPT, "corpus_check_mod")
    assert [p.name for p in check_mod._iter_files(d)] == ["gongwen.md"]
    eval_mod = _load_script(EVAL_SCRIPT, "eval_mod")
    assert [p.name for p in eval_mod._corpus_files(str(d))] == ["gongwen.md"]


def test_skip_corpus_names_are_shared_by_both_tools():
    """D 批交付物（检查表/AUTHORIZATION）不是语料：两个脚本的跳过清单必须一致。

    两份常量刻意各存一份（脚本要能单独拷走运行），因此用测试锁住"内容相同"，
    防止只改一边导致 corpus_check 跳过、eval 却把检查表算进 E3。
    """
    check_mod = _load_script(CHECK_SCRIPT, "corpus_check_skip_names")
    eval_mod = _load_script(EVAL_SCRIPT, "eval_skip_names")
    assert set(check_mod.SKIP_CORPUS_NAMES) == SKIP_NAMES
    assert check_mod.SKIP_CORPUS_NAMES == eval_mod.SKIP_CORPUS_NAMES


def test_skip_files_are_not_counted_as_corpus(tmp_path):
    """目录里混入 README / 采集合规检查表 / AUTHORIZATION 时只统计真实语料。"""
    d = tmp_path / "mixed_corpus"
    _write(d / "gongwen.md", "\n".join(CLEAN_SENTENCES * 6))
    _write(d / "采集合规检查表.md", "# 合规检查表\n\n" + "检查项说明。" * 300)
    _write(d / "README.md", "# 说明\n\n" + CLEAN_SENTENCES[0] * 20)
    _write(d / "AUTHORIZATION.example.json", '{"authorized": false}\n')
    _write(d / "AUTHORIZATION.json", '{"authorized": true}\n')

    rc, out = _run_check(str(d), "--min-doc-cjk", "0", "--min-total-cjk", "0")
    assert rc == 0, out
    check_mod = _load_script(CHECK_SCRIPT, "corpus_check_skip_count")
    report = check_mod.scan_corpus(d, min_doc_cjk=0, min_total_cjk=0)
    assert report["corpus"]["files"] == 1, report["files"]
    assert [f["path"] for f in report["files"]] == ["gongwen.md"]

    eval_mod = _load_script(EVAL_SCRIPT, "eval_skip_count")
    assert [p.name for p in eval_mod._corpus_files(str(d))] == ["gongwen.md"]


# ---------------------------------------------------------------- 语料质量提示
def _scan(check_mod, d: Path) -> dict:
    return check_mod.scan_corpus(d, min_doc_cjk=0, min_total_cjk=0)


def test_quality_title_split_positive(tmp_path):
    """标题被拆成两段（拼接后才是完整标题）→ 质量提示，但不判 FAIL。"""
    check_mod = _load_script(CHECK_SCRIPT, "corpus_check_title")
    d = tmp_path / "split_title"
    body = "\n".join(CLEAN_SENTENCES * 6)
    _write(d / "split.md",
           "关于进一步加强安全生产工作\n的通知\n关于进一步加强安全生产工作的通知\n"
           + body)
    report = _scan(check_mod, d)
    assert report["ok"] is True and report["exit_code"] == 0
    kinds = [q["kind"] for q in report["quality"]]
    assert "标题跨段" in kinds, report["quality"]
    assert report["quality"][0]["line"] == 2

    # 已清洗（单段就是完整标题）→ 不得告警
    clean = tmp_path / "clean_title"
    _write(clean / "clean.md", "关于进一步加强安全生产工作的通知\n" + body)
    report2 = _scan(check_mod, clean)
    assert report2["ok"] is True
    assert not [q for q in report2["quality"] if q["kind"] == "标题跨段"], \
        report2["quality"]


def test_quality_unpaired_and_long_paragraph(tmp_path):
    """书名号/引号不配对、单段过长都只提示、不 FAIL。"""
    check_mod = _load_script(CHECK_SCRIPT, "corpus_check_quality")
    body = "\n".join(CLEAN_SENTENCES * 6)

    unpaired = tmp_path / "unpaired"
    _write(unpaired / "q.md", "会议指出“要加强管理。\n" + body)
    report = _scan(check_mod, unpaired)
    assert report["ok"] is True and report["exit_code"] == 0
    assert any(q["kind"] == "成对符号不配对" for q in report["quality"])

    paired = tmp_path / "paired"
    _write(paired / "q.md", "会议指出“要加强管理”。\n" + body)
    assert not _scan(check_mod, paired)["quality"]

    longpara = tmp_path / "longpara"
    _write(longpara / "q.md", "关于工作的通知\n" + "各单位要抓好落实。" * 200)
    report = _scan(check_mod, longpara)
    assert report["ok"] is True
    assert any(q["kind"] == "单段过长" for q in report["quality"])

    normal = tmp_path / "normal"
    _write(normal / "q.md", "关于工作的通知\n" + body)
    assert not _scan(check_mod, normal)["quality"]


def test_quality_hints_do_not_affect_exit_code(tmp_path):
    """质量提示再密集也不影响退出码（只有编码/规模/脱敏才 FAIL）。"""
    d = tmp_path / "many_quality"
    lines = ["关于进一步加强安全生产工作", "的通知",
             "关于进一步加强安全生产工作的通知"]
    lines += ["会议指出“要加强管理。", "各单位要抓好落实。" * 200]
    lines += CLEAN_SENTENCES * 6
    _write(d / "q.md", "\n".join(lines))
    rc, out = _run_check(str(d), "--min-doc-cjk", "0", "--min-total-cjk", "0")
    assert rc == 0, f"质量提示不应影响退出码，实际 {rc}\n{out}"
    assert "质量提示" in out


@pytest.mark.skipif(not GOV_CN.is_dir(),
                    reason="本地手采语料 doc/corpus/gov_cn 不存在（gitignored）")
def test_local_gov_cn_corpus_passes_and_has_no_title_split():
    """只读回归：已清洗的真实语料必须 PASS，且不再有任何「标题跨段」提示。

    该目录不入 git（含真实公文），CI 上自动跳过；开发机可用它做人工回归。
    """
    check_mod = _load_script(CHECK_SCRIPT, "corpus_check_govcn")
    report = check_mod.scan_corpus(GOV_CN)
    assert report["ok"] is True and report["exit_code"] == 0, report["errors"]
    assert report["corpus"]["files"] >= 10
    title_split = [q for q in report["quality"] if q["kind"] == "标题跨段"]
    assert not title_split, f"清洗后的语料不应再有标题跨段：{title_split[:3]}"


# ---------------------------------------------------------------- 评测联动
def test_eval_txt_corpus_gates_e3_and_writes_manifest(eval_clean_run):
    """.txt 语料真正参与门禁；--corpus-manifest 写出可对比的 sha256 清单。"""
    rc, out, manifest, json_path = eval_clean_run
    assert rc == 0, f"干净 .txt 语料应 exit 0，实际 {rc}\n{out}"
    e3 = _line_of(out, "E3a")
    assert e3.startswith("[PASS] E3a ") and "外部语料" in e3 and "示意" not in e3
    assert _line_of(out, "E3b").startswith("[SKIP] E3b ")
    assert _line_of(out, "E5").startswith("[PASS] E5 ")

    payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
    assert payload["corpus"]["external"] is True
    assert payload["corpus"]["chars"] >= 1000
    assert payload["require_packs"] is False
    assert payload["corpus"]["manifest_path"] == str(manifest)

    data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    meta = data["corpus"]
    assert meta["segments"] >= 40
    assert len(meta["aggregate_sha256"]) == 64
    assert meta["files"], "清单里必须有逐文件指纹"
    entry = meta["files"][0]
    assert entry["path"] == "gongwen.txt"
    raw = (Path(payload["corpus"]["path"]) / "gongwen.txt").read_bytes()
    assert entry["sha256"] == hashlib.sha256(raw).hexdigest()
    assert entry["bytes"] == len(raw) and entry["cjk"] > 0


def test_base_profile_still_exits_zero(eval_clean_run):
    """不变式：``--profile base`` 的既有语义不受 C1 新参数影响。"""
    rc, out, _manifest, _json = eval_clean_run
    assert rc == 0
    assert "退出码：0" in out
    assert "门禁项：通过" in out


# ---------------------------------------------------------------- 增强包验收
def test_require_packs_fails_when_packs_missing(require_packs_run):
    """未装增强包 + --require-packs：E4/E6 **FAIL**（不允许 SKIP 混过去）。"""
    rc, out = require_packs_run
    assert rc == 1, f"--require-packs 未装包应 exit 1，实际 {rc}\n{out}"
    e4 = _line_of(out, "E4")
    e6 = _line_of(out, "E6")
    assert e4.startswith("[FAIL] E4 ") and "--require-packs" in e4
    assert e6.startswith("[FAIL] E6 ") and "--require-packs" in e6
    assert "增强包" in e4 and "增强包" in e6
    summary = [ln for ln in out.splitlines() if ln.startswith("门禁项：")]
    assert summary and "E4" in summary[0] and "E6" in summary[0]


def test_require_packs_manifest_needs_corpus(empty_pack_root, tmp_path):
    """--corpus-manifest 必须配 --corpus：否则是用法错误（exit 2）。"""
    rc, out = _run_eval("--profile", "base", "--pack-root", str(empty_pack_root),
                        "--corpus-manifest", str(tmp_path / "m.json"),
                        "--gen-sample", "50")
    assert rc == 2, f"缺 --corpus 应 exit 2，实际 {rc}\n{out}"
    assert "corpus-manifest" in out


# ---------------------------------------------------------------- 交付文档
def test_corpus_readme_documents_contract():
    """doc/corpus/README.md 是交付物：放置/脱敏/口径/命令/阈值都要写清楚。"""
    assert CORPUS_README.exists(), "缺少 doc/corpus/README.md"
    text = CORPUS_README.read_text(encoding="utf-8")
    for needle in ("doc/corpus", "不入 git", "脱敏", "corpus_check.py",
                   "身份证", "手机号", "邮箱", "银行卡", "座机",
                   "E3", "E3a", "E3b", "e3_all", "E6", "--corpus-manifest",
                   "--require-packs", "为什么不能拿技术文档当负样本",
                   "100 ms/千字", "0.5 处/万字", "语料质量提示", "标题跨段",
                   "SKIP_CORPUS_NAMES", "语料清洗要求"):
        assert needle in text, f"语料契约 README 缺少关键内容：{needle}"
