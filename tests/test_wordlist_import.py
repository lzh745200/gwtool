# -*- coding: utf-8 -*-
"""用户导入词表的护栏测试。

**为什么必须单独一个文件**：既有的两条"零误报"护栏（词典全量、真实语料）
是**自洽**的 —— 词表既是检测器的来源、又是断言的语料，新词必被自身词表放行，
**导入再多也打不破**。代价是它们**只防误报、对"导入导致的退化"零覆盖**。
本文件补的就是这个缺口。

守护三件事：
  1. **证据集 / 豁免集分离**：用户导入的词不得进入 `repeat_rules` 的判词依据
     （否则常见二字词会把正常语句翻成误报 —— 这是本设计要防的核心回归）；
  2. **豁免的确有效**：用户自己的含叠字专名不得被误报；
  3. **不过度豁免**：一般重复字仍须照常报出。

与 `test_repeat_rules.py` 相同：独立临时库 + 从随包 seed.db 导入全量词典，
模块结束恢复会话库，不污染同会话其它测试模块。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from gwtool.core import corrector, repeat_rules
from gwtool.db import connection as dbconn
from gwtool.db import dao

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "gwtool" / "resources" / "data" / "seed.db"

# 污染源样本：都是**极常见的二字词**，一旦进入证据集，⑤/cXc 立刻会误报
POLLUTING_WORDS = ("会在", "是在", "了在", "在会")
# 被污染判据命中的正常语句（曾真实误报过一次，见 test_repeat_rules 的对应用例）
NATURAL_SENTENCE = "现在会在结果对话框中逐条点名。"

SELF_SOURCE = "用户导入:回归测试"


@pytest.fixture(scope="module", autouse=True)
def _dict_env(tmp_path_factory):
    """独立临时库 + 全量词典；模块结束恢复会话库与缓存。"""
    prev = dbconn.current_db_file()
    d = tmp_path_factory.mktemp("wordlist_import")
    dbconn.configure(d / "wordlist.db")
    conn = dbconn.get_conn()
    assert SEED.exists(), f"缺少随包词典 {SEED}"
    conn.execute("ATTACH DATABASE ? AS seeddb", (str(SEED),))
    conn.execute(
        "INSERT OR IGNORE INTO dictionary(word,pinyin,definition,example,source)"
        " SELECT word,pinyin,definition,example,source FROM seeddb.dictionary")
    conn.commit()
    conn.execute("DETACH DATABASE seeddb")
    repeat_rules.invalidate_cache()
    corrector.invalidate_cache()
    yield
    repeat_rules.invalidate_cache()
    corrector.invalidate_cache()
    dbconn.configure(prev)


def _import_words(words, source=SELF_SOURCE, role="protect"):
    """模拟一次"用户导入词表"：写入 dictionary 并登记来源。"""
    for w in words:
        dao.add_dictionary_entry(w, source=source)
    dao.upsert_wordlist_source(source, role=role, label="回归测试",
                               fmt="csv", entry_count=len(words))
    repeat_rules.invalidate_cache()
    corrector.invalidate_cache()


def _hits(text):
    return [(c.wrong, c.confidence) for c in repeat_rules.check_repeat(text)]


class TestImportDoesNotCreateFalsePositives:
    """★ 核心：导入词表**不得**让本来正常的语句被报出来。"""

    def test_baseline_sentence_is_clean_before_import(self):
        """前置事实：没有用户词时，这句话本来就不报。"""
        assert _hits(NATURAL_SENTENCE) == []

    def test_importing_common_bigrams_keeps_sentence_clean(self):
        """导入常见二字词后，同一句话**必须仍然不报**。

        这是本设计存在的理由：⑤b/⑤c 的左右证据与 cXc 的 deletable 原先都读
        同一个词集合，用户导入"会在"之后它们会立刻成立，把这句话翻成
        0.85 / 0.5 的误报。
        """
        _import_words(POLLUTING_WORDS)
        assert _hits(NATURAL_SENTENCE) == [], (
            "导入常见二字词后正常语句被误报 —— 证据集与用户词集串了")

    def test_isolation_is_load_bearing(self):
        """反证：若这些词**真的**进了证据集，这句话就会被 cXc 通道报出来。

        注意误报渠道：这句话里**没有相邻同字**，所以走不到 `_judge_run`
        （①②③④⑤⑥⑦⑧ 都是对"同字连"的判定）。真正会中招的是**独立的
        cXc 间隔重复通道**（`_check_interval_repeat`）——它的 `deletable`
        同样读词集合，「现在会在…」里的 "在会在" 正是它的目标形态。
        （本用例最初写成注入后调 `_judge_run`，直接失败 —— 那次失败恰好
        纠正了"误报走哪条路"的错误认知。）
        """
        ctx = repeat_rules._context()
        polluted = dict(ctx)
        polluted["words"] = set(ctx["words"]) | set(POLLUTING_WORDS)
        got = repeat_rules._check_interval_repeat(NATURAL_SENTENCE, polluted)
        assert got, ("注入证据集后 cXc 通道仍未报出：说明本用例没有真正验证到"
                     "隔离的必要性，污染样本或判据已变，请重新挑选")


class TestEvidenceAndExemptionSetsAreSeparate:
    """两组集合的边界：谁在证据集、谁在豁免集，必须泾渭分明。"""

    def test_evidence_set_excludes_user_sources(self):
        ctx = repeat_rules._context()
        for w in POLLUTING_WORDS:
            assert w not in ctx["words"], f"{w!r} 混进了证据集（判词依据）"

    def test_exemption_set_includes_user_sources(self):
        ctx = repeat_rules._context()
        for w in POLLUTING_WORDS:
            assert w in ctx["user_terms"], f"{w!r} 应进豁免集"

    def test_dao_split_matches(self):
        """DAO 层的两个取词函数必须与 context 的划分一致。"""
        ev = set(dao.builtin_dictionary_words())
        us = set(dao.user_dictionary_words())
        assert ev and us
        assert not (ev & us), "证据集与用户词集不得有交集"
        assert set(POLLUTING_WORDS) <= us
        # all_dictionary_words 保留原语义（外部断言依赖它）
        assert set(dao.all_dictionary_words()) == ev | us

    def test_evidence_set_not_emptied_by_user_import(self):
        """证据集不能被用户导入"挤空"——它仍须是那份全量汉语词表。"""
        assert len(dao.builtin_dictionary_words()) >= 100000


class TestUserTermExemption:
    """豁免必须"说到哪免到哪"，且不能过度。"""

    def test_user_term_with_doubled_chars_is_exempt(self):
        """用户自己的含叠字专名（如"鑫鑫实业"）不得被误报。"""
        _import_words(("鑫鑫实业有限公司",))
        assert _hits("由鑫鑫实业有限公司承接。") == []

    def test_exemption_does_not_leak_to_other_text(self):
        """豁免只覆盖用户词自己那一段，不能变成全局放行。"""
        _import_words(("鑫鑫实业有限公司",))
        # 同一批字若出现在别处、不构成该词 → 仍须报
        assert _hits("重复字测试：进行行。"), "一般重复字被误豁免"

    def test_ordinary_repeat_still_reported(self):
        """控制组：一般重复字仍照常报 0.85。"""
        assert ("作作", 0.85) in _hits("工作作。")

    def test_long_user_term_does_not_break_scan(self):
        """超长用户词不得拖垮扫描：窗口已由 user_maxlen 封顶 _MAX_WIN。"""
        _import_words(("超长" * 20 + "词",))
        ctx = repeat_rules._context()
        assert ctx["user_maxlen"] <= repeat_rules._MAX_WIN
        assert _hits("工作作。")  # 仍能正常判定

    def test_source_removal_restores_baseline(self):
        """移走该来源后，行为须回到基线（可回退）。"""
        dao.delete_wordlist_source(SELF_SOURCE)
        repeat_rules.invalidate_cache()
        ctx = repeat_rules._context()
        assert not ctx["user_terms"]
        assert _hits(NATURAL_SENTENCE) == []


# ====================================================================== 批 2
from gwtool.core import ruleset, wordlist
from gwtool.core.wordfmt import parse as wf_parse


class TestFormatParsing:
    """各格式的解析正确性（修掉的历史问题优先）。"""

    def test_csv_gbk_is_decoded(self, tmp_path):
        """GBK 词表必须能读对 —— 旧实现硬编码 utf-8-sig，中文必乱码。"""
        p = tmp_path / "gbk.csv"
        p.write_bytes("错误写法,正确写法\n布署,部署\n".encode("gbk"))
        res = wf_parse(p, "pairs")
        assert [(e.wrong, e.correct) for e in res.entries] == [("布署", "部署")]

    def test_same_content_two_encodings_parse_identically(self, tmp_path):
        """同内容的 GBK 与 UTF-8-BOM 文件，解析结果必须一致。"""
        body = "错误写法,正确写法\n布署,部署\n截止,截至\n"
        p1 = tmp_path / "a.csv"
        p1.write_bytes(body.encode("gbk"))
        p2 = tmp_path / "b.csv"
        p2.write_bytes(body.encode("utf-8-sig"))
        r1 = [(e.wrong, e.correct) for e in wf_parse(p1, "pairs").entries]
        r2 = [(e.wrong, e.correct) for e in wf_parse(p2, "pairs").entries]
        assert r1 == r2 == [("布署", "部署"), ("截止", "截至")]

    def test_tsv_delimiter_detected(self, tmp_path):
        p = tmp_path / "t.tsv"
        p.write_text("错误写法\t正确写法\n布署\t部署\n", encoding="utf-8")
        res = wf_parse(p, "pairs")
        assert [(e.wrong, e.correct) for e in res.entries] == [("布署", "部署")]

    def test_semicolon_delimiter_detected(self, tmp_path):
        """分号分隔的表：旧实现会整行变成一个字段。"""
        p = tmp_path / "s.csv"
        p.write_text("错误写法;正确写法\n布署;部署\n", encoding="utf-8")
        res = wf_parse(p, "pairs")
        assert [(e.wrong, e.correct) for e in res.entries] == [("布署", "部署")]

    def test_comment_lines_skipped(self, tmp_path):
        p = tmp_path / "c.csv"
        p.write_text("# 这是说明\n错误写法,正确写法\n# 中间注释\n布署,部署\n",
                     encoding="utf-8")
        res = wf_parse(p, "pairs")
        assert [e.wrong for e in res.entries] == ["布署"]

    def test_header_detected_and_not_imported(self, tmp_path):
        p = tmp_path / "h.csv"
        p.write_text("错误写法,正确写法\n布署,部署\n", encoding="utf-8")
        res = wf_parse(p, "pairs")
        assert all(e.wrong != "错误写法" for e in res.entries), "表头被当成数据"

    def test_header_column_order_is_free(self, tmp_path):
        """有表头时列顺序随意。"""
        p = tmp_path / "o.csv"
        p.write_text("正确写法,错误写法\n部署,布署\n", encoding="utf-8")
        res = wf_parse(p, "pairs")
        assert [(e.wrong, e.correct) for e in res.entries] == [("布署", "部署")]

    def test_invalid_rows_reported_with_line_number(self, tmp_path):
        p = tmp_path / "bad.csv"
        p.write_text("错误写法,正确写法\n布署,部署\n只有一列\n,半行\n",
                     encoding="utf-8")
        res = wf_parse(p, "pairs")
        assert len(res.entries) == 1
        assert res.issues and all(isinstance(n, int) for n, _ in res.issues)

    def test_json_bare_array(self, tmp_path):
        import json as _json
        p = tmp_path / "a.json"
        p.write_text(_json.dumps([{"wrong": "布署", "correct": "部署"}],
                                 ensure_ascii=False), encoding="utf-8")
        res = wf_parse(p, "pairs")
        assert [e.wrong for e in res.entries] == ["布署"]

    def test_json_grouped_keys_declare_role(self, tmp_path):
        import json as _json
        p = tmp_path / "g.json"
        p.write_text(_json.dumps({
            "error_pairs": [{"wrong": "布署", "correct": "部署"}],
            "words": [{"word": "鑫鑫实业"}],
        }, ensure_ascii=False), encoding="utf-8")
        res = wf_parse(p, "pairs")
        roles = {e.role for e in res.entries}
        assert roles == {"pairs", "protect"}

    def test_json_aliases_expand_to_multiple_entries(self, tmp_path):
        """术语 `aliases` 是数组 → 每个异名各成一条。"""
        import json as _json
        p = tmp_path / "t.json"
        p.write_text(_json.dumps(
            {"terms": [{"standard": "碳达峰",
                        "aliases": ["碳排放达峰", "碳达峰峰值"]}]},
            ensure_ascii=False), encoding="utf-8")
        res = wf_parse(p, "terms")
        assert [e.wrong for e in res.entries] == ["碳排放达峰", "碳达峰峰值"]
        assert all(e.correct == "碳达峰" for e in res.entries)

    def test_unsupported_ext_raises_readable_error(self, tmp_path):
        p = tmp_path / "x.abc"
        p.write_text("x", encoding="utf-8")
        try:
            wf_parse(p, "pairs")
        except ValueError as exc:
            assert "不支持" in str(exc)
        else:
            raise AssertionError("不支持的扩展名应报可读错误")


class TestConflictMatrix:
    """冲突矩阵逐格：新增 / 覆盖 / 冲突 / 跳过。"""

    def _prep(self, tmp_path, text, name="r.csv"):
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        return wf_parse(p, "pairs")

    def test_same_pair_is_overwrite_not_add(self, tmp_path):
        dao.add_error_pair("截止", "截至", category="测试", source="已有来源")
        rep = wordlist.precheck(self._prep(tmp_path, "截止,截至\n"), "新来源")
        assert len(rep.overwrites) == 1 and not rep.adds

    def test_different_correct_same_wrong_is_conflict_not_overwrite(
            self, tmp_path):
        """同错词、不同对 → 冲突，**默认并存不覆盖**。"""
        dao.add_error_pair("截止", "截至", category="测试", source="已有来源")
        rep = wordlist.precheck(self._prep(tmp_path, "截止,到\n"), "新来源")
        assert len(rep.conflicts) == 1
        assert not rep.overwrites

    def test_conflict_kept_by_default(self, tmp_path):
        dao.add_error_pair("截止", "截至", category="测试", source="已有来源")
        parsed = self._prep(tmp_path, "截止,到\n")
        rep = wordlist.precheck(parsed, "新来源")
        out = wordlist.apply(parsed, rep, "新来源")
        assert out.ok and out.conflicts_kept == 1 and out.pairs_added == 0
        pairs = {p.correct for p in dao.all_error_pairs(only_enabled=False)
                 if p.wrong == "截止"}
        assert "截至" in pairs and "到" not in pairs, "默认不得覆盖既有写法"

    def test_conflict_accepted_when_asked(self, tmp_path):
        dao.add_error_pair("截止", "截至", category="测试", source="已有来源")
        parsed = self._prep(tmp_path, "截止,到\n")
        rep = wordlist.precheck(parsed, "新来源")
        out = wordlist.apply(parsed, rep, "新来源", accept_conflicts=True)
        assert out.ok and out.pairs_added == 1

    def test_duplicate_within_batch_skipped(self, tmp_path):
        rep = wordlist.precheck(
            self._prep(tmp_path, "布署,部署\n布署,部署\n"), "新来源")
        assert len(rep.adds) == 1 and len(rep.skips) == 1

    def test_protect_same_source_is_skip(self, tmp_path):
        p = tmp_path / "w.csv"
        p.write_text("词\n鑫鑫实业\n", encoding="utf-8")
        parsed = wf_parse(p, "protect")
        rep = wordlist.precheck(parsed, "词表来源")
        wordlist.apply(parsed, rep, "词表来源", "protect")
        rep2 = wordlist.precheck(parsed, "词表来源", "protect")
        assert len(rep2.skips) == 1 and not rep2.adds


class TestTransactionality:
    def test_rollback_leaves_nothing_behind(self, tmp_path,
                                            monkeypatch):
        """中途失败必须全回滚 —— 否则留下"导了一半"的库。"""
        p = tmp_path / "r.csv"
        p.write_text("错误写法,正确写法\n甲甲,甲\n乙乙,乙\n丙丙,丙\n",
                     encoding="utf-8")
        parsed = wf_parse(p, "pairs")
        rep = wordlist.precheck(parsed, "回滚来源")
        before = dao.count_error_pairs_by("回滚来源")

        orig = wordlist._write_one
        seq = {"n": 0}

        def boom(conn, e, *a, **k):
            seq["n"] += 1
            if seq["n"] == 2:
                raise RuntimeError("注入的失败")
            return orig(conn, e, *a, **k)

        monkeypatch.setattr(wordlist, "_write_one", boom)
        out = wordlist.apply(parsed, rep, "回滚来源")
        assert not out.ok and "注入的失败" in out.error
        assert dao.count_error_pairs_by("回滚来源") == before, "回滚没干净"

    def test_success_commits_all(self, tmp_path):
        p = tmp_path / "r.csv"
        p.write_text("错误写法,正确写法\n甲甲,甲\n乙乙,乙\n", encoding="utf-8")
        parsed = wf_parse(p, "pairs")
        rep = wordlist.precheck(parsed, "提交来源")
        out = wordlist.apply(parsed, rep, "提交来源")
        assert out.ok and out.pairs_added == 2
        assert dao.count_error_pairs_by("提交来源") == 2


class TestRulesetUpgrade:
    """ruleset 的升级点（TSV / 注释 / 逐行原因 / 事务），契约须保持。"""

    def test_tsv_now_supported(self, tmp_path):
        p = tmp_path / "x.tsv"
        p.write_text("错误写法\t正确写法\n布署\t部署\n", encoding="utf-8")
        res = ruleset.import_error_pairs(str(p), default_source="TSV来源")
        assert res["imported"] == 1

    def test_comment_lines_skipped(self, tmp_path):
        p = tmp_path / "x.csv"
        p.write_text("# 注释\n错误写法,正确写法\n布署,部署\n", encoding="utf-8")
        res = ruleset.import_error_pairs(str(p), default_source="注释来源")
        assert res["imported"] == 1

    def test_issues_carry_reasons(self, tmp_path):
        p = tmp_path / "x.csv"
        p.write_text("错误写法,正确写法\n布署,部署\n只有一列\n", encoding="utf-8")
        res = ruleset.import_error_pairs(str(p), default_source="原因来源")
        assert res["imported"] == 1
        assert res["issues"] and res["issues"][0][1], "无效行必须带原因"

    def test_return_keys_kept(self, tmp_path):
        """既有调用方按这三个键取值，不能少。"""
        p = tmp_path / "x.csv"
        p.write_text("布署,部署\n", encoding="utf-8")
        res = ruleset.import_error_pairs(str(p), default_source="契约来源")
        for k in ("imported", "skipped", "rows"):
            assert k in res


# ====================================================================== 批 3
class TestExcelDocxXmlFormats:
    """批 3：xlsx / docx 表格 / TBX / XLIFF 的端到端解析。"""

    def test_xlsx_roundtrip_via_project_writer(self, tmp_path):
        """用项目自己的写入器造样本当 oracle（零依赖、无需外部样张）。"""
        from gwtool.core import xlsx
        p = tmp_path / "a.xlsx"
        xlsx.write_table(str(p), ["错误写法", "正确写法", "类别"],
                         [["布署", "部署", "错别字"],
                          ["截止", "截至", "错别字"]],
                         sheet_name="纠错对")
        res = wf_parse(p, "pairs")
        assert [(e.wrong, e.correct, e.category) for e in res.entries] == [
            ("布署", "部署", "错别字"), ("截止", "截至", "错别字")]

    def test_xlsx_multi_sheet_warns_and_takes_first(self, tmp_path):
        from gwtool.core import xlsx
        p = tmp_path / "b.xlsx"
        xlsx.write_xlsx(str(p), [
            xlsx.Sheet(name="词表", headers=["错误写法", "正确写法"],
                       rows=[["布署", "部署"]]),
            xlsx.Sheet(name="说明", headers=["备注"], rows=[["不该被导入"]]),
        ])
        res = wf_parse(p, "pairs")
        assert [e.wrong for e in res.entries] == ["布署"]
        assert any("只取第一张" in w for w in res.warnings)

    def test_docx_table_parsed_and_body_ignored(self, tmp_path):
        """只读**表格**；正文段落不得被当成词条。"""
        import docx
        p = tmp_path / "a.docx"
        doc = docx.Document()
        doc.add_paragraph("这段正文不应成为词条")
        t = doc.add_table(rows=3, cols=2)
        for i, (a, b) in enumerate([("词", "拼音"),
                                    ("鑫鑫实业", "xinxin"),
                                    ("达丰公司", "dafeng")]):
            t.cell(i, 0).text = a
            t.cell(i, 1).text = b
        doc.save(str(p))
        res = wf_parse(p, "protect")
        assert [e.word for e in res.entries] == ["鑫鑫实业", "达丰公司"]
        assert all("正文" not in e.word for e in res.entries)

    def test_docx_without_table_warns(self, tmp_path):
        import docx
        p = tmp_path / "b.docx"
        d = docx.Document()
        d.add_paragraph("只有正文")
        d.save(str(p))
        res = wf_parse(p, "protect")
        assert not res.entries
        assert any("没有表格" in w for w in res.warnings)

    def test_tbx_standard_and_alias(self, tmp_path):
        p = tmp_path / "t.tbx"
        p.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<tbx xmlns="urn:iso:std:iso:30042:ed-2"><text><body>'
            '<termEntry id="c1">'
            '<langSet xml:lang="zh-CN"><tig><term>碳达峰</term></tig></langSet>'
            '<langSet xml:lang="zh"><tig><term>碳排放达峰</term></tig></langSet>'
            '</termEntry></body></text></tbx>', encoding="utf-8")
        res = wf_parse(p, "pairs")
        assert [(e.role, e.wrong, e.correct) for e in res.entries] == [
            ("terms", "碳排放达峰", "碳达峰")]

    def test_tbx_drops_non_cjk_terms(self, tmp_path):
        """多语言条目里的外文不得变成中文规范名的"异名"。"""
        p = tmp_path / "m.tbx"
        p.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<tbx xmlns="urn:iso:std:iso:30042:ed-2"><text><body>'
            '<termEntry id="c1">'
            '<langSet xml:lang="zh"><tig><term>碳达峰</term></tig></langSet>'
            '<langSet xml:lang="en"><tig><term>carbon peak</term></tig></langSet>'
            '</termEntry></body></text></tbx>', encoding="utf-8")
        res = wf_parse(p, "terms")
        assert all("carbon" not in e.wrong for e in res.entries)
        assert any("跳过" in w for w in res.warnings)

    def test_tbx_single_form_reported_not_imported(self, tmp_path):
        p = tmp_path / "s.tbx"
        p.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<tbx xmlns="urn:iso:std:iso:30042:ed-2"><text><body>'
            '<termEntry id="c1">'
            '<langSet xml:lang="zh"><tig><term>碳中和</term></tig></langSet>'
            '</termEntry></body></text></tbx>', encoding="utf-8")
        res = wf_parse(p, "terms")
        assert not res.entries and res.issues

    def test_xliff_source_to_target(self, tmp_path):
        p = tmp_path / "t.xlf"
        p.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">'
            '<file source-language="zh" target-language="zh-CN" original="t">'
            '<body><trans-unit id="1">'
            '<source>碳排放达峰</source><target>碳达峰</target>'
            '</trans-unit></body></file></xliff>', encoding="utf-8")
        res = wf_parse(p, "terms")
        assert [(e.wrong, e.correct) for e in res.entries] == [
            ("碳排放达峰", "碳达峰")]

    def test_xliff_missing_target_reported(self, tmp_path):
        p = tmp_path / "u.xlf"
        p.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">'
            '<file source-language="zh" target-language="zh-CN" original="t">'
            '<body><trans-unit id="1"><source>双碳目标</source></trans-unit>'
            '</body></file></xliff>', encoding="utf-8")
        res = wf_parse(p, "terms")
        assert not res.entries
        assert any("target" in why for _, why in res.issues)

    def test_unrecognised_xml_raises_readable(self, tmp_path):
        p = tmp_path / "x.xml"
        p.write_text('<?xml version="1.0"?><root><a/></root>', encoding="utf-8")
        try:
            wf_parse(p, "terms")
        except ValueError as exc:
            assert "认不出" in str(exc)
        else:
            raise AssertionError("无法识别的 XML 词表应报可读错误")

    def test_malformed_xml_raises_readable(self, tmp_path):
        p = tmp_path / "bad.tbx"
        p.write_text("<tbx><unclosed>", encoding="utf-8")
        try:
            wf_parse(p, "terms")
        except ValueError as exc:
            assert "XML" in str(exc)
        else:
            raise AssertionError("畸形 XML 应报可读错误")
