# -*- coding: utf-8 -*-
"""v1.14.0 C4：E3 归因工具（scripts/e3_triage.py）+ 引号内数字用法守卫。

两类改动：
1. **工具**：`e3_triage.py` 把真实语料上的每条命中做成结构化条目（类别/置信度/
   上下文/文件/建议），带稳定 id 与人工判定栏，供"真实句子 → 归因 → 守卫 →
   正反例 → 复跑"这条流水线用。这里测它的**契约**：口径与评测一致、条目字段
   齐全、判定可回写、缺陷信号可识别。
2. **守卫**：35 篇语料归因发现 `“1936.10.22”`（引号内历史刻字）被「日期格式」
   提示 —— 引号/书名号内是**原文引用**，数字用法整类不再提示。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from gwtool.core import corrector

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "e3_triage.py"


def _hits(text: str):
    return corrector.check_text(text)


def _load_triage():
    spec = importlib.util.spec_from_file_location("e3_triage", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def triage_mod():
    assert SCRIPT.exists(), f"缺少归因脚本 {SCRIPT}"
    return _load_triage()


# ============================================================ ① 引号内数字用法
class TestQuotedNumberGuard:
    """引号/书名号内是原文引用，数字用法（提示类）不再"规范化"。"""

    def test_quoted_date_not_hinted(self, tmp_db):
        text = "会师纪念园内矗立着一座石雕日晷，四周地砖上镌刻着“1936.10.22”，记录着胜利会师的历史时刻。"
        hits = [c for c in corrector.check_text(text) if c.category == "数字用法"]
        assert not hits, [(c.wrong, c.suggestion) for c in hits]

    def test_unquoted_date_still_hinted(self, tmp_db):
        """反例：正文里（非引用）的同一种写法仍要提示，能力不能被一起收掉。"""
        hits = [c for c in corrector.check_text("会议定于1936.10.22召开。")
                if c.category == "数字用法"]
        assert any(c.wrong == "1936.10.22" for c in hits), hits

    @pytest.mark.parametrize("left,right", [("“", "”"), ("《", "》"), ("「", "」")])
    def test_all_quote_kinds_protect(self, tmp_db, left, right):
        text = f"文件写到{left}2026.10.3{right}。"
        assert not [c for c in corrector.check_text(text)
                    if c.category == "数字用法"], text

    def test_unclosed_quote_protects_to_end(self, tmp_db):
        """采集残缺（引号未闭合）时也宁可少报：从开符号保护到串尾。"""
        text = "原文为“1936.10.22"
        assert not [c for c in corrector.check_text(text)
                    if c.category == "数字用法"], text

    def test_book_title_paren_still_guarded(self, tmp_db):
        """既有能力不回归：书名号内的半角括号仍不替换（v1.10 D1）。"""
        text = "是贯彻落实《法治中国建设规划(2020—2025年)》等文件的重点工作之一"
        assert not [c for c in corrector.check_text(text) if c.category == "标点"]

    def test_halfwidth_comma_real_hit_kept(self, tmp_db):
        """反例：正文（非引用）里的半角逗号是真命中，必须保留。"""
        text = "各省、自治区、直辖市人民政府办公厅,国务院各部委："
        hits = [c for c in corrector.check_text(text) if c.category == "标点"]
        assert any(c.suggestion.endswith("，") for c in hits), hits


# ============================================================ ② 归因工具契约
class TestTriageTool:
    """e3_triage 的口径、字段、回写、缺陷识别。"""

    def _corpus(self, tmp_path: Path) -> Path:
        d = tmp_path / "corpus"
        d.mkdir()
        (d / "A局_2026_通知.md").write_text(
            "各省、自治区、直辖市人民政府办公厅,国务院各部委：\n"
            "会师纪念园内镌刻着“1936.10.22”，记录着胜利会师的历史时刻。\n"
            "扫描二维码分享到手机\n",
            encoding="utf-8")
        (d / "B局_2026_报告.md").write_text(
            "各部门要高度重视此项工作，严格落实责任制，确保各项任务按期完成。\n",
            encoding="utf-8")
        (d / "README.md").write_text("不是语料，应被跳过。\n", encoding="utf-8")
        return d

    def test_summary_and_items(self, triage_mod, tmp_path, tmp_db):
        data = triage_mod.triage(str(self._corpus(tmp_path)))
        s = data["summary"]
        # 口径：E3a=可自动替换类（半角逗号 1 处）；引号内日期已被守卫，E3b 为 0
        assert s["e3a_hits"] == 1, data["summary"]
        assert s["e3b_hits"] == 0, data["summary"]
        item = data["items"][0]
        for key in ("id", "file", "line", "category", "confidence", "wrong",
                    "suggestion", "left", "right", "context", "verdict",
                    "verdict_note", "auto_hint", "auto_reason", "tier"):
            assert key in item, f"条目缺少字段 {key}"
        assert item["wrong"] == "厅," and item["tier"] == "E3a"
        assert item["verdict"] == "" and item["verdict_note"] == ""
        assert item["auto_hint"].startswith("疑似")
        assert "办公" in item["left"] and "国务院" in item["right"]

    def test_skips_non_corpus_files(self, triage_mod, tmp_path, tmp_db):
        data = triage_mod.triage(str(self._corpus(tmp_path)))
        names = {Path(f["file"]).name for f in data["files"]}
        assert "README.md" not in names
        assert data["meta"]["skipped_files"], "跳过的文件要透明化"
        assert any(Path(p).name == "README.md"
                   for p in data["meta"]["skipped_files"])

    def test_chars_and_per_wan_consistent(self, triage_mod, tmp_path, tmp_db):
        data = triage_mod.triage(str(self._corpus(tmp_path)))
        s = data["summary"]
        chars = data["meta"]["chars"]
        assert chars > 0
        assert s["e3a_per_wan"] == round(s["e3a_hits"] / chars * 10000, 3)

    def test_ids_are_stable(self, triage_mod, tmp_path, tmp_db):
        corpus = str(self._corpus(tmp_path))
        a = triage_mod.triage(corpus)
        b = triage_mod.triage(corpus)
        assert [i["id"] for i in a["items"]] == [i["id"] for i in b["items"]]

    def test_verdicts_roundtrip(self, triage_mod, tmp_path, tmp_db):
        corpus = str(self._corpus(tmp_path))
        data = triage_mod.triage(corpus)
        vid = data["items"][0]["id"]
        vfile = tmp_path / "verdicts.json"
        vfile.write_text(json.dumps(
            {"verdicts": {vid: {"verdict": "真命中", "note": "源文本用了半角逗号"}}},
            ensure_ascii=False), encoding="utf-8")
        merged = triage_mod.apply_verdicts(data, str(vfile))
        assert merged["items"][0]["verdict"] == "真命中"
        assert "半角逗号" in merged["items"][0]["verdict_note"]
        assert merged["summary"]["verdict"] == {"真命中": 1}
        assert merged["meta"]["verdicts_applied"] == 1

    def test_segment_defects_are_detected(self, triage_mod, tmp_path, tmp_db):
        d = tmp_path / "bad"
        d.mkdir()
        (d / "C局_残.md").write_text(
            "扫描二维码分享到手机\n"
            "标题只有半边《关于加强\n"
            + "很长的段落。" * 400 + "\n", encoding="utf-8")
        data = triage_mod.triage(str(d))
        signals = " ".join("；".join(x["signals"]) for x in data["defects"])
        assert "页面元素残留" in signals
        assert "成对符号不配对" in signals
        assert "单段过长" in signals

    def test_skip_names_match_evaluator(self, triage_mod):
        """两份"不是语料"清单必须一致（各存一份是为了脚本能单独拷走）。"""
        spec = importlib.util.spec_from_file_location(
            "eval_corrector_mod", ROOT / "scripts" / "eval_corrector.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert triage_mod.SKIP_CORPUS_NAMES == mod.SKIP_CORPUS_NAMES
        assert triage_mod.ADVISORY_EXTRA == mod.E3_ADVISORY_EXTRA

    def test_markdown_has_manual_columns(self, triage_mod, tmp_path, tmp_db):
        data = triage_mod.triage(str(self._corpus(tmp_path)))
        md = triage_mod._md(data)
        assert "人工判定" in md and "备注" in md
        assert "E3a 可自动替换类（门禁）" in md
        assert "复跑" in md

    def test_missing_corpus_returns_no_files(self, triage_mod, tmp_path, tmp_db):
        """路径不存在时不静默给出"0 误报"的假结论（triage 层返回 0 文件）。"""
        data = triage_mod.triage(str(tmp_path / "nope"))
        assert data["meta"]["files"] == 0
        assert data["summary"]["hits"] == 0

    def test_whitelist_marks_source_deviation(self, triage_mod, tmp_path, tmp_db):
        """源文本偏差白名单：命中项标「语料缺陷」，并从 E3a 门禁口径扣除。"""
        data = triage_mod.triage(str(self._corpus(tmp_path)))
        wl = [{"wrong": "厅,", "suggestion": "厅，",
               "contains": "人民政府办公厅,国务院各部委", "note": "源文本半角逗号"}]
        data = triage_mod.apply_whitelist(data, wl)
        s = data["summary"]
        assert s["source_deviation"] == 1
        assert s["e3a_gated_hits"] == s["e3a_hits"] - 1
        assert any(i["verdict"] == "语料缺陷" for i in data["items"])
        assert "源文本偏差" in triage_mod._md(data)


# ============================================================ ③ 生成层降级语义
class TestGeneratedLayerAdvisory:
    """[v1.14.0 C4] 生成层（39 504 条，seed 库 confidence 全 0.55）整层降为提示级。

    这是**能力边界**的显式变更，必须钉死四个语义：
      ① 整篇应用不改生成层；② 精标/上下文规则照旧改；③ 命中仍然可见（提示）；
      ④ 有显式恢复开关 `correct_block(apply_low_confidence=True)`。
    能力矩阵见 `doc/纠错能力校准_v1.14.md`。
    """

    @pytest.fixture(autouse=True)
    def _gen_pairs(self, tmp_db):
        from gwtool.db import dao

        dao.add_error_pair("尤人", "有人", "错别字(生成)", 0.55, source="generated")
        dao.add_error_pair("一震", "一阵", "错别字(生成)", 0.55, source="generated")
        corrector.invalidate_cache()
        yield
        corrector.invalidate_cache()

    def test_category_is_advisory(self):
        assert "错别字(生成)" in corrector.ADVISORY_CATEGORIES

    def test_correct_block_skips_generated(self):
        """整篇应用（correct_block）不得自动替换生成层命中。"""
        text = "不怨天、不尤人；让人心头一震。"
        fixed, cs = corrector.correct_block(text)
        assert fixed == text, f"生成层被自动替换了：{fixed!r}"
        assert not [c for c in cs if c.category == "错别字(生成)"]

    def test_correct_block_applies_curated_and_rules(self):
        """反例：精标对与上下文/语法规则必须照旧写回（能力不能被一起收掉）。"""
        fixed, cs = corrector.correct_block("各单位要加强力度，这项工作必须抓紧布署。")
        assert "加大力度" in fixed and "加强力度" not in fixed
        assert "部署" in fixed and "布署" not in fixed

    def test_check_text_still_reports_generated(self):
        """提示可见性：生成层命中仍要出现在 check_text 结果里（只是不自动改）。"""
        hits = [c for c in corrector.check_text("不怨天、不尤人。")
                if c.category == "错别字(生成)"]
        assert hits and hits[0].suggestion == "有人"
        assert hits[0].wrong == "尤人"

    def test_explicit_restore_switch(self):
        """恢复自动替换的显式入口：apply_low_confidence=True 只放开生成层。"""
        text = "不怨天、不尤人。"
        fixed, cs = corrector.correct_block(text, apply_low_confidence=True)
        assert "有人" in fixed and "尤人" not in fixed
        # 提示标签类永远不放开（写回会把正文改成标签）
        label_text = "该项目大约需要五天左右完成。"
        fixed2, _ = corrector.correct_block(label_text, apply_low_confidence=True)
        assert fixed2 == label_text, "提示标签类被写回正文了"

    def test_report_marks_generated_as_advisory(self):
        """报告/导出把生成层算作提示类（不要求在报告里单列新文案，但分类口径一致）。"""
        from gwtool.core import report

        assert report.ADVISORY_CATEGORIES == corrector.ADVISORY_CATEGORIES
        assert "错别字(生成)" in report.ADVISORY_CATEGORIES

    def test_restore_path_documented(self):
        """生成层恢复入口的活契约：默认提示级，apply_low_confidence=True 可显式恢复。

        旧版本这里是一个无条件 skip 且断言**与当前契约相反**（长期 skip 等于
        没测）。v1.18 收口改为真跑：默认不得自动改，显式开关必须放开，
        这样一旦有人删掉 ADVISORY 那行，本用例会立刻红。
        """
        assert "错别字(生成)" in corrector.ADVISORY_CATEGORIES
        default_fixed, _ = corrector.correct_block("不怨天、不尤人。")
        assert "尤人" in default_fixed, "默认不得自动改生成层"
        low_fixed, _ = corrector.correct_block("不怨天、不尤人。",
                                               apply_low_confidence=True)
        assert "有人" in low_fixed and "尤人" not in low_fixed


# ============================================================ ④ 220 篇新类守卫
_SEED = ROOT / "gwtool" / "resources" / "data" / "seed.db"


@pytest.fixture(scope="class")
def dict_db(tmp_path_factory):
    """类级"全量词典"库（重复字层依赖词典证据集）。

    与 `test_real_corpus_fixes_v110d.py::dict_db` / `tests/test_repeat_rules.py`
    同构：空库下 `check_repeat` 直接返回 []，负例会"假通过"，所以必须导入
    seed.db 的 CC-CEDICT 全量词条。
    """
    from gwtool.db import connection as dbconn

    prev = dbconn.current_db_file()
    d = tmp_path_factory.mktemp("v113_repeat")
    dbconn.configure(d / "v113.db")
    conn = dbconn.get_conn()
    assert _SEED.exists(), f"缺少随包词典 {_SEED}"
    conn.execute("ATTACH DATABASE ? AS seeddb", (str(_SEED),))
    conn.execute(
        "INSERT OR IGNORE INTO dictionary(word,pinyin,definition,example,source)"
        " SELECT word,pinyin,definition,example,source FROM seeddb.dictionary")
    conn.commit()
    conn.execute("DETACH DATABASE seeddb")
    corrector.invalidate_cache()
    yield
    corrector.invalidate_cache()
    dbconn.close_current_thread()
    dbconn.configure(prev)
    corrector.invalidate_cache()


@pytest.mark.usefixtures("dict_db")
class TestV114RealCorpusGuards:
    """220 篇语料（45.7 万字）新发现的确定性误报，逐类钉正反例。"""

    @pytest.mark.parametrize("text,wrong", [
        ("一辆辆大巴穿山而来，一床床棉被销往各地。", "床床"),
        ("申亮亮烈士的名字刻在碑上。", "亮亮"),
        ("甚至想嗅嗅当年的油墨味道。", "嗅嗅"),
        ("像一把把出鞘的刀。", "把把"),
        ("接管富庶川南的十八军军长张国华。", "军军"),
        ("龙舌岩岩壁上的标语令群众动容。", "岩岩"),
    ])
    def test_repeat_classes_suppressed(self, text, wrong):
        hits = [c for c in _hits(text) if c.category == "重复字"]
        assert not [c for c in hits if wrong in c.wrong], (text, hits)

    def test_conjunction_enumeration(self):
        """`只有一个/一种…，就是`：列举句式，"就"不是"只有…才"的条件关联。"""
        for text in ("如此“刁难”的目的只有一个，就是检验部队的极限。",
                     "我们那时只有一个思想，就是无论如何也要克服困难。"):
            assert not [c for c in _hits(text) if c.category == "关联词"], text
        # 反例：真条件关联照报
        hits = [c for c in _hits("只有努力工作，就能成功。") if c.category == "关联词"]
        assert hits, "真误配被一并吞掉了"

    def test_bylines_and_quoted_punctuation(self):
        """体裁问题：通讯社署名括号、引文里的 `？？` 不替换。"""
        assert not [c for c in _hits("中新社巴黎10月22日电 (记者 李洋)法国总统…")
                    if c.category == "标点"]
        assert not [c for c in _hits("（采写：谌霖秋）") if c.category == "标点"]
        quoted = "“出路在哪里？？出路在哪里？？？”"
        assert not [c for c in _hits(quoted) if c.category == "标点"], _hits(quoted)
        # 反例：正文里的重复问号仍要报
        assert [c for c in _hits("这是真的吗？？") if c.category == "标点"]

    def test_qidong_word_boundary(self):
        """`建立起动态` 里的 `起动` 是"建立+起+动态"，不是"启动"的错形。"""
        assert not [c for c in _hits("我们建立起动态的“兵情档案”。")
                    if c.wrong == "起动"], _hits("我们建立起动态的“兵情档案”。")
        # 反例：真的"起动"仍要建议"启动"
        assert [c for c in _hits("应按规程起动设备。") if c.wrong == "起动"]
