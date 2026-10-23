# -*- coding: utf-8 -*-
"""C2 合成回归语料生成器（``scripts/gen_corpus.py``）的护栏测试。

背景
----
E5 吞吐、E2 召回、跨平台 smoke 都需要**大批量、可复现、无真实数据**的公文
语料。真实语料一是不够批量、二是涉密风险；本生成器用仓库既有的 12 文种骨架、
10 份法定文种示意样张与写作句式库拼装语料，并明确标注**合成**。

本文件守六件事
--------------
1. **确定性**：同 seed / 同 count 两次生成，每个文件与 manifest / labels
   逐字节一致（跨平台 smoke 与回归基线的前提）；
2. **篇幅与体例**：每份 800~2000 字；文号按「×政发〔2026〕N号」（命令用
   「第N号」令号、纪要不写编号）；文末带合成标注；
3. **无真实数据**：不出现身份证/手机号/邮箱/真实日期/真实机构名，
   占位一律用「××市/××局」「2026年×月×日」；
4. **注入与召回**：``--inject-errors`` 的 labels 与文档逐条对得上，
   注入错形的召回率不低于 60%（宽线下限；实测记录在断言消息里）；
5. **吞吐**：万字级语料生成耗时 < 2s（宽松上限，慢机器也过）；
6. **规模**：一次生成 60 份不崩、manifest 数目自洽。

⚠ 合成语料**不得**用于 E3 精度声明：manifest 的 ``synthetic`` / ``warning``
字段与每份文末标注都在提醒这一点。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "gen_corpus.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_corpus_v110c", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN = _load_generator()

#: 必含的文种（验收点名）
REQUIRED_KINDS = {"通知", "请示", "报告", "意见", "函", "纪要"}

#: 真实机构/地名（合成语料里一个都不许出现；骨架里的部队条例全称会被
#: 生成器统一替换成 ××× 占位）
REAL_NAMES = re.compile(
    "中国人民解放军|北京|上海|天津|重庆|河北|山西|广东|江苏|浙江"
    "|国务院|中共中央|中华人民共和国|应急管理部|公安部|教育部|财政部")


# ================================================================ 工具
def _docs(path: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8")
            for p in sorted(path.glob("synth_*.txt"))}


def _manifest(path: Path) -> dict:
    return json.loads((path / "manifest.json").read_text(encoding="utf-8"))


def _labels(path: Path) -> dict:
    return json.loads((path / "labels.json").read_text(encoding="utf-8"))


# ================================================================ 确定性
class TestDeterminism:
    def test_same_seed_is_byte_identical(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        GEN.generate(a, count=6, seed=20261101, inject_errors_flag=True)
        GEN.generate(b, count=6, seed=20261101, inject_errors_flag=True)
        names_a = sorted(p.name for p in a.iterdir())
        names_b = sorted(p.name for p in b.iterdir())
        assert names_a == names_b
        for name in names_a:
            assert (a / name).read_bytes() == (b / name).read_bytes(), (
                f"{name} 两次生成不一致（同 seed 必须逐字节相同）")

    def test_different_seed_changes_content(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        GEN.generate(a, count=3, seed=1)
        GEN.generate(b, count=3, seed=2)
        differing = [name for name in _docs(a)
                     if _docs(a)[name] != _docs(b)[name]]
        assert differing, "不同 seed 应产出不同语料"

    def test_regenerate_same_dir_is_byte_identical(self, tmp_path):
        """同一目录重复生成（覆盖旧产物）也必须逐字节一致，不能残留旧文件。"""
        out = tmp_path / "reuse"
        GEN.generate(out, count=4, seed=8, inject_errors_flag=True)
        first = {p.name: p.read_bytes() for p in out.iterdir()}
        GEN.generate(out, count=4, seed=8, inject_errors_flag=True)
        second = {p.name: p.read_bytes() for p in out.iterdir()}
        assert first == second

    def test_shrinking_count_removes_stale_files(self, tmp_path):
        out = tmp_path / "shrink"
        GEN.generate(out, count=6, seed=8)
        assert len(_docs(out)) == 6
        GEN.generate(out, count=3, seed=8)
        assert len(_docs(out)) == 3, "缩小 count 后旧文件必须清理"
        assert len(_manifest(out)["files"]) == 3

    def test_manifest_records_seed_version_and_synthetic_flag(self, tmp_path):
        out = tmp_path / "c"
        GEN.generate(out, count=4, seed=99)
        manifest = _manifest(out)
        assert manifest["seed"] == 99
        assert manifest["synthetic"] is True
        assert manifest["generator"]["version"] == GEN.GENERATOR_VERSION
        assert "合成" in manifest["warning"]
        assert "E3" in manifest["warning"], "必须声明不得用于 E3 精度声明"

    def test_manifest_sha256_matches_files(self, tmp_path):
        out = tmp_path / "c"
        GEN.generate(out, count=4, seed=7)
        manifest = _manifest(out)
        for item in manifest["files"]:
            raw = (out / item["name"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == item["sha256"]
            assert item["chars"] == GEN.char_count(raw.decode("utf-8"))


# ================================================================ 篇幅与体例
class TestContentShape:
    def test_doc_count_and_naming(self, tmp_path):
        out = tmp_path / "c"
        GEN.generate(out, count=5, seed=3)
        docs = _docs(out)
        assert len(docs) == 5
        assert len(_manifest(out)["files"]) == 5
        assert all(name.startswith("synth_") and name.endswith(".txt")
                   for name in docs)

    def test_char_count_within_window(self, tmp_path):
        out = tmp_path / "c"
        manifest = GEN.generate(out, count=8, seed=5)
        for item in manifest["files"]:
            text = (out / item["name"]).read_text(encoding="utf-8")
            chars = GEN.char_count(text)
            assert 800 <= chars <= 2000, f"{item['name']} 字数 {chars} 越界"
            assert item["chars"] == chars

    def test_every_doc_marked_synthetic(self, tmp_path):
        out = tmp_path / "c"
        GEN.generate(out, count=4, seed=5)
        for name, text in _docs(out).items():
            tail = [ln for ln in text.strip().split("\n") if ln.strip()][-1]
            assert tail.startswith("（合成语料"), f"{name} 文末缺少合成标注：{tail!r}"
            assert "非真实公文" in tail

    def test_no_residual_placeholders(self, tmp_path):
        out = tmp_path / "c"
        GEN.generate(out, count=8, seed=5)
        for name, text in _docs(out).items():
            for token in ("【", "】", "……", "{", "}"):
                assert token not in text, f"{name} 残留模板占位符 {token!r}"

    def test_doc_number_convention(self, tmp_path):
        out = tmp_path / "c"
        manifest = GEN.generate(out, count=26, seed=5)   # 两个完整文种轮次
        docs = _docs(out)
        for item in manifest["files"]:
            text = docs[item["name"]]
            kind = item["kind"]
            if kind == "纪要":
                assert "×政发〔2026〕" not in text, "纪要按体例不写发文字号"
                assert not re.search(r"^第\d+号$", text, re.M)
            elif kind == "命令":
                assert re.search(r"^第\d+号$", text, re.M), "命令应用令号"
                assert "×政发〔2026〕" not in text, "命令不写发文字号"
            else:
                assert re.search(r"×政发〔2026〕\d{1,3}号", text), (
                    f"{item['name']} 缺少占位文号")

    def test_kinds_cover_required_and_skeletons(self, tmp_path):
        out = tmp_path / "c"
        manifest = GEN.generate(out, count=26, seed=5)
        kinds = {item["kind"] for item in manifest["files"]}
        assert REQUIRED_KINDS <= kinds
        assert kinds == set(GEN.ALL_KINDS), "一轮应覆盖全部 13 个文种"

    def test_every_doc_uses_placeholder_units(self, tmp_path):
        out = tmp_path / "c"
        GEN.generate(out, count=8, seed=5)
        for name, text in _docs(out).items():
            assert "××" in text, f"{name} 未使用「××」占位单位/人名"


# ================================================================ 隐私
class TestNoRealData:
    def test_no_pii_patterns(self, tmp_path):
        out = tmp_path / "c"
        GEN.generate(out, count=8, seed=13)
        checks = {
            "身份证/长数字串": re.compile(r"(?<!\d)\d{7,}"),
            "手机号": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
            "邮箱": re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}"),
            "真实数字日期": re.compile(r"\d{4}年\d{1,2}月\d{1,2}日"),
        }
        for name, text in _docs(out).items():
            for label, pattern in checks.items():
                hit = pattern.search(text)
                assert hit is None, f"{name} 命中 {label}: {hit.group(0)!r}"

    def test_no_real_org_names(self, tmp_path):
        out = tmp_path / "c"
        manifest = GEN.generate(out, count=26, seed=13)
        for item in manifest["files"]:
            text = (out / item["name"]).read_text(encoding="utf-8")
            assert not REAL_NAMES.search(text), (
                f"{item['name']} 出现真实机构/地名：{REAL_NAMES.search(text).group(0)}")

    def test_only_placeholder_doc_numbers(self, tmp_path):
        out = tmp_path / "c"
        GEN.generate(out, count=8, seed=13)
        bad = []
        for name, text in _docs(out).items():
            for match in re.finditer(r"〔2026〕\d+号", text):
                head = text[max(0, match.start() - 3):match.start()]
                if head != "×政发":
                    bad.append((name, match.group(0)))
        assert not bad, f"出现非占位文号：{bad}"

    def test_injected_corpus_still_has_no_real_data(self, tmp_path):
        out = tmp_path / "c"
        GEN.generate(out, count=8, seed=17, inject_errors_flag=True)
        for name, text in _docs(out).items():
            assert REAL_NAMES.search(text) is None, name
            assert not re.search(r"(?<!\d)\d{7,}", text), name


# ================================================================ 注入与召回
class TestInjection:
    @staticmethod
    def _corpus(tmp_path):
        out = tmp_path / "inj"
        manifest = GEN.generate(out, count=12, seed=7, inject_errors_flag=True)
        return out, manifest

    def test_labels_align_with_documents(self, tmp_path):
        out, manifest = self._corpus(tmp_path)
        items = _labels(out)["items"]
        assert items, "注入模式必须产出 labels.json"
        assert manifest["injected"] is True
        assert manifest["labels"] == "labels.json"
        for item in items:
            text = (out / item["file"]).read_text(encoding="utf-8")
            seg = text[item["offset"]:item["offset"] + item["length"]]
            assert seg == item["wrong"], (item, seg)
            assert item["wrong"] != item["correct"]
            assert item["category"]
            assert item["kind"] in GEN.ALL_KINDS

    def test_manifest_counts_are_consistent(self, tmp_path):
        out, manifest = self._corpus(tmp_path)
        items = _labels(out)["items"]
        assert manifest["totals"]["injections"] == len(items)
        per_file = sum(item["injections"] for item in manifest["files"])
        assert per_file == len(items)
        assert _labels(out)["total"] == len(items)

    def test_injected_docs_keep_shape_and_marking(self, tmp_path):
        out, manifest = self._corpus(tmp_path)
        for item in manifest["files"]:
            text = (out / item["name"]).read_text(encoding="utf-8")
            assert 800 <= GEN.char_count(text) <= 2000, item["name"]
            assert text.rstrip().split("\n")[-1].startswith("（合成语料")

    def test_injection_recall_meets_floor(self, tmp_path, tmp_db):
        """召回宽线下限 60%；实测值写进断言消息，便于报告引用。"""
        from gwtool.core import corrector

        out, _manifest = self._corpus(tmp_path)
        items = _labels(out)["items"]
        docs = _docs(out)
        hit = 0
        misses = []
        for item in items:
            fixes = corrector.check_text(docs[item["file"]])
            if any(c.start == item["offset"] and c.suggestion == item["correct"]
                   for c in fixes):
                hit += 1
            else:
                misses.append((item["file"], item["wrong"], item["correct"]))
        recall = hit / len(items) if items else 0.0
        assert recall >= 0.60, (
            f"注入召回率 {recall:.1%}（{hit}/{len(items)}）低于 60% 宽线下限；"
            f"漏报样例：{misses[:3]}")

    def test_clean_mode_has_no_labels(self, tmp_path):
        out = tmp_path / "clean"
        manifest = GEN.generate(out, count=4, seed=7)
        assert manifest["injected"] is False
        assert not (out / "labels.json").exists()


# ================================================================ 吞吐与规模
class TestThroughputAndScale:
    def test_ten_thousand_chars_under_two_seconds(self, tmp_path):
        out = tmp_path / "perf"
        start = time.perf_counter()
        manifest = GEN.generate(out, count=16, seed=23)
        elapsed = time.perf_counter() - start
        total = manifest["totals"]["chars"]
        assert total >= 10000, f"吞吐样本只有 {total} 字"
        assert elapsed < 2.0, f"生成 {total} 字耗时 {elapsed * 1000:.0f} ms（上限 2s）"

    def test_generate_60_docs_without_crash(self, tmp_path):
        out = tmp_path / "bulk"
        manifest = GEN.generate(out, count=60, seed=42)
        assert manifest["count"] == 60
        assert len(list(out.glob("synth_*.txt"))) == 60
        assert len(manifest["files"]) == 60
        assert manifest["totals"]["docs"] == 60
        chars = [item["chars"] for item in manifest["files"]]
        assert all(800 <= c <= 2000 for c in chars)
        assert manifest["totals"]["chars"] == sum(chars)


# ================================================================ 命令行
class TestCommandLine:
    def test_cli_generates_injected_corpus(self, tmp_path):
        out = tmp_path / "cli"
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--out", str(out),
             "--count", "3", "--seed", "5", "--inject-errors"],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", timeout=120)
        assert proc.returncode == 0, proc.stderr
        summary = json.loads(proc.stdout.strip().splitlines()[-1])
        assert summary["synthetic"] is True
        assert summary["count"] == 3
        assert summary["injections"] > 0
        assert (out / "manifest.json").is_file()
        assert (out / "labels.json").is_file()
