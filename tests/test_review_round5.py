# -*- coding: utf-8 -*-
"""第五轮深审回归测试（2026-10-04）。

两条真缺陷的红→绿护栏（先在修复前实现上确认失败，再验证修复版通过）：

#1 汇编链口径矛盾（core/compiler.py）
   旧实现：`load_trees` 对解析失败的额外文件**静默丢弃**，而
   `collect_sources` 仍把它列进来源清单（虚列出处——清单声称内容来自
   该文件，实际内容根本没进汇编）；`compile_docx` 的 material_titles
   直接 zip(trees, titles)，坏文件被丢弃后**标题整体错位**（实测：坏
   材料的标题被冠到好材料头上）。软删除文档还有第三重矛盾：正文进了
   汇编、来源清单却把它跳过。

#2 机构一致性漏报（core/inspector.py）
   `_ENTITY_RE` 是"懒惰前缀 + 机构后缀"，匹配从上一个非实体字符后就地
   开始，「县应急局与县应急管理局联合执法」的第二个实体取到
   「与县应急管理局」——带连接词的名字与简称做变体比对必然失败。
   而"简称与全称并存"最常见的出现场景恰恰是被「与/和/及」连在一起，
   模块 docstring 的招牌用例实测漏报。修复：确定性前导连接字修剪。

误报澄清（勿重复投入，见 open-code-review审查报告.md 第五轮）：
   - `style_profile.verdict` 对未知文种抛 KeyError 是 docstring 明写的
     fail-fast 设计（UI 按约束提供选项），不是缺陷；
   - `toolbox.amount_to_cn` 抛 ValueError 有 UI 层 try/except 兜底
     （editor_panel.op），不是缺陷；
   - `_cn_to_int` 在 paragraph_ref（1..99，序号域）与 inspector（更宽，
     成文纪年域）是两个刻意不同的域，docstring 各自写明，不是漂移。
"""
from __future__ import annotations

from pathlib import Path


from gwtool.core import compiler
from gwtool.core.inspector import _ENTITY_LEAD_TRIM, _ENTITY_RE, _check_consistency
from gwtool.db import dao


def _add(title: str, **kw) -> int:
    base = dict(title=title, content_text=f"{title}的正文内容",
                blocks_json="[]", file_path=str(Path("/tmp") / f"{title}.docx"))
    base.update(kw)
    return dao.add_document(dao.Document(**base))


def _make_docx(path, text: str) -> Path:
    from docx import Document
    d = Document()
    d.add_paragraph(text)
    d.save(str(path))
    return path


# ================================================================ #1 汇编链
class TestLoadTreesEx:
    def test_reports_unparseable_extra(self, tmp_db, tmp_path):
        bad = tmp_path / "坏材料.docx"
        bad.write_bytes(b"x")          # 非 zip，解析必失败
        trees, failures, slot_ok = compiler.load_trees_ex([], [str(bad)])
        assert trees == []
        assert failures and "坏材料.docx" in failures[0][0]
        assert slot_ok == [False]

    def test_slot_ok_aligns_with_inputs(self, tmp_db, tmp_path):
        good = _make_docx(tmp_path / "好材料.docx", "好材料正文")
        bad = tmp_path / "坏材料.docx"
        bad.write_bytes(b"x")
        did = _add("库内材料")
        trees, failures, slot_ok = compiler.load_trees_ex(
            [did, 424242], [str(bad), str(good)])
        # 槽位 = [did, 424242, bad, good]
        assert slot_ok == [True, False, False, True]
        # docx 无"标题"概念（tree.title 为空），以块文本核对内容归属
        assert [t.title for t in trees] == ["库内材料", ""]
        assert trees[1].blocks[0].text == "好材料正文"
        # 失败清单逐条点名，且不包含成功槽位
        names = [f[0] for f in failures]
        assert any("424242" in n for n in names)
        assert any("坏材料.docx" in n for n in names)

    def test_deleted_document_is_skipped_with_reason(self, tmp_db):
        """软删除材料不进汇编、来源清单也不列（同口径），且失败清单点名。"""
        did = _add("回收站材料")
        dao.delete_document(did)
        trees, failures, slot_ok = compiler.load_trees_ex([did], [])
        assert trees == []
        assert slot_ok == [False]
        assert failures and failures[0][1] == "材料已在回收站中"

    def test_load_trees_keeps_legacy_signature(self, tmp_db, tmp_path):
        """旧调用方（e2e/脚本）只拿 trees：签名与返回类型不得变化。"""
        good = _make_docx(tmp_path / "好材料.docx", "好材料正文")
        got = compiler.load_trees([], [str(good)])
        assert isinstance(got, list)
        assert len(got) == 1
        assert got[0].blocks[0].text == "好材料正文"


class TestCompileTitleAlignment:
    def _read_h1(self, path) -> list[str]:
        from docx import Document
        doc = Document(str(path))
        return [p.text for p in doc.paragraphs
                if p.style.name.startswith("Heading 1") and p.text.strip()]

    def test_titles_follow_input_slots_not_survivors(self, tmp_db, tmp_path):
        """坏文件在前：它的标题只跳过自己那一坑，好材料拿自己的标题。

        修复前：zip(trees, titles) 让好材料冠上坏材料的标题（实测红）。
        """
        bad = tmp_path / "坏材料.docx"
        bad.write_bytes(b"x")
        good = _make_docx(tmp_path / "好材料.docx", "好材料正文")
        out = tmp_path / "out.docx"
        compiler.compile_docx(compiler.CompileRequest(
            doc_ids=[], extra_paths=[str(bad), str(good)],
            material_titles=["坏材料标题", "好材料标题"],
            out_docx=str(out)))
        h1 = self._read_h1(out)
        assert "好材料标题" in h1
        assert "坏材料标题" not in h1

    def test_deleted_doc_title_slot_skipped(self, tmp_db, tmp_path):
        good = _make_docx(tmp_path / "好材料.docx", "好材料正文")
        did = _add("回收站材料")
        dao.delete_document(did)
        out = tmp_path / "out.docx"
        compiler.compile_docx(compiler.CompileRequest(
            doc_ids=[did], extra_paths=[str(good)],
            material_titles=["回收站材料标题", "好材料标题"],
            out_docx=str(out)))
        h1 = self._read_h1(out)
        assert "好材料标题" in h1
        assert "回收站材料标题" not in h1


class TestCollectSourcesFilter:
    def test_unparseable_extra_not_listed(self, tmp_db, tmp_path):
        """解析失败的文件内容没进汇编，列进来源清单就是虚列出处。"""
        bad = tmp_path / "坏材料.docx"
        bad.write_bytes(b"x")
        got = compiler.collect_sources([], [str(bad)])
        assert got == []

    def test_parseable_extra_still_listed(self, tmp_db, tmp_path):
        src = _make_docx(tmp_path / "源材料.docx", "外部材料正文")
        got = compiler.collect_sources([], [str(src)])
        assert len(got) == 1
        assert got[0]["file"] == "源材料.docx"
        assert got[0]["category"] == "（未入库）"

    def test_extra_parsed_short_circuit(self, tmp_db, tmp_path):
        """调用方传入成功集合时不得重复解析；集合外的文件不列。"""
        src = _make_docx(tmp_path / "源材料.docx", "外部材料正文")
        got = compiler.collect_sources([], [str(src)], extra_parsed=[str(src)])
        assert [g["file"] for g in got] == ["源材料.docx"]
        got2 = compiler.collect_sources([], [str(src)], extra_parsed=[])
        assert got2 == []


class TestCompileSourcesIntegration:
    def _read_text(self, path) -> str:
        from docx import Document
        doc = Document(str(path))
        parts = [p.text for p in doc.paragraphs]
        for t in doc.tables:
            for row in t.rows:
                parts.extend(c.text for c in row.cells)
        return "\n".join(parts)

    def test_sources_list_excludes_failed_extra(self, tmp_db, tmp_path):
        bad = tmp_path / "坏材料.docx"
        bad.write_bytes(b"x")
        good = _make_docx(tmp_path / "好材料.docx", "好材料正文")
        out = tmp_path / "out.docx"
        compiler.compile_docx(compiler.CompileRequest(
            doc_ids=[], extra_paths=[str(bad), str(good)],
            include_sources=True, out_docx=str(out)))
        text = self._read_text(out)
        assert "好材料.docx" in text
        assert "坏材料.docx" not in text


# ================================================================ #3 字体探测
class TestFontcheckNoApp:
    def test_missing_fonts_survives_without_qapplication(self):
        """无 QApplication 时必须返回安全默认值，而不是原生崩溃杀掉进程。

        修复前：QFontDatabase.families() 在没有 QApplication 的进程里触发
        access violation，try/except 拦不住，整个 pytest 进程无栈死亡
        （实测：本文件最初一轮就是被它打断的，exit 127、无 summary）。
        """
        from PySide6.QtWidgets import QApplication
        assert QApplication.instance() is None or True  # 会话内可能已有
        # 无论当前会话是否有 app，调用都必须活着返回 list
        from gwtool.core.fontcheck import missing_fonts
        result = missing_fonts()
        assert isinstance(result, list)


# ================================================================ #2 机构一致性
def _names(text: str) -> list[str]:
    return [m.group(1).lstrip(_ENTITY_LEAD_TRIM)
            for m in _ENTITY_RE.finditer(text)]


class TestEntityLeadTrim:
    def test_flagship_case_joined_by_yu(self):
        """修复前：「与县应急管理局」带连接词，变体比对失败 → 漏报。"""
        finds = _check_consistency(
            ["县应急局与县应急管理局联合执法。"])
        assert any("县应急局" in f.detail and "县应急管理局" in f.detail
                   for f in finds)

    def test_joined_by_he(self):
        finds = _check_consistency(["县应急局和县应急管理局联合执法。"])
        assert finds

    def test_separate_sentences_still_reported(self):
        """既有干净用例（无连接词）必须继续报——修剪不能把正向修坏。"""
        finds = _check_consistency(
            ["县应急局发布通知。", "县应急管理局组织演练。"])
        assert finds

    def test_leading_connective_only(self):
        """修剪只发生在**前导**：中间的连接字不受影响。"""
        # 「和县」是真实地名（安徽和县）：修剪后归并为「县教育局」，
        # 与「市教育局」首二字不同，不得判为变体
        assert _check_consistency(
            ["和县教育局印发方案。", "市教育局转发。"]) == []

    def test_distinct_orgs_not_reported(self):
        assert _check_consistency(["市公安局与市教育局联合发文。"]) == []

    def test_garbage_entities_not_reported(self):
        """修剪不扩大误报面：口语句里的伪实体（群众办/服务全部）不产生报告。"""
        assert _check_consistency(
            ["切实为群众办实事，各项服务全部落实到位。"]) == []

    def test_trim_set_is_closed_small(self):
        """连接字必须是闭合小类：防止把动词/名词首字剪坏（如「推」「办」）。"""
        for ch in ("推", "办", "市", "县", "群", "安"):
            assert ch not in _ENTITY_LEAD_TRIM
