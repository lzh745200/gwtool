# -*- coding: utf-8 -*-
"""汇编规范化（``gwtool/core/compile_norm.py``，v1.15 T2）的回归测试。

背景（实测）：资料库里**纯文本 / markdown** 导入的文档，``blocks_json`` 常常
为空或全是 ``paragraph``；markdown 的 ``#``/``##`` 字面留在正文里；PDF 提取
文本是逐行硬换行。结果 TOC 抓不到标题、"一、提高认识""（一）统一思想"全被
当正文，字体/缩进规则失效。

本文件锁四类输入的**规范化前后块类型对比**（纯文本 / markdown / PDF 提取
文本 / mixed），外加标题与层级判定阈值、主送/落款、硬换行续接、幂等、纯函数
以及 ``compiler.load_trees`` 的接入行为。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gwtool.core import compile_norm as N
from gwtool.core import compiler
from gwtool.core.model import (Block, DocTree, HEADING, LIST_ITEM, PARAGRAPH,
                               TABLE)
from gwtool.db import dao


def _tree(blocks: list[Block], title: str = "") -> DocTree:
    return DocTree(title=title, blocks=list(blocks))


def _lines(texts: list[str]) -> list[Block]:
    return [Block(type=PARAGRAPH, text=t) for t in texts]


def _shape(tree: DocTree) -> list[tuple[str, int, str]]:
    return [(b.type, b.level, b.text) for b in tree.blocks]


def _norm(blocks: list[Block], title: str = "") -> DocTree:
    return N.normalize_tree(_tree(blocks, title))


# ---------------------------------------------------------------- 纯文本
PLAIN_LINES = [
    "关于进一步加强安全生产工作的通知",
    "各市人民政府：",
    "为进一步加强安全生产工作，现将有关事项通知如下，请结合实际抓好贯彻",
    "落实。",
    "一、提高认识",
    "各单位要充分认识做好安全生产工作的重要性。",
    "（一）统一思想",
    "切实把思想和行动统一到部署上来。",
    "1. 加强领导",
    "成立工作领导小组。",
    "××市人民政府",
    "2026年10月3日",
]


class TestPlainText:
    def test_before_all_blocks_are_paragraphs(self):
        tree = _tree(_lines(PLAIN_LINES))
        assert [b.type for b in tree.blocks] == [PARAGRAPH] * len(PLAIN_LINES)

    def test_after_block_types_and_levels(self):
        tree = _norm(_lines(PLAIN_LINES))
        assert _shape(tree) == [
            (PARAGRAPH, 0, "各市人民政府："),
            (PARAGRAPH, 0,
             "为进一步加强安全生产工作，现将有关事项通知如下，请结合实际抓好贯彻落实。"),
            (HEADING, 1, "一、提高认识"),
            (PARAGRAPH, 0, "各单位要充分认识做好安全生产工作的重要性。"),
            (HEADING, 2, "（一）统一思想"),
            (PARAGRAPH, 0, "切实把思想和行动统一到部署上来。"),
            (HEADING, 3, "1. 加强领导"),
            (PARAGRAPH, 0, "成立工作领导小组。"),
            (PARAGRAPH, 0, "××市人民政府"),
            (PARAGRAPH, 0, "2026年10月3日"),
        ]

    def test_title_and_roles(self):
        tree, stats = N.normalize_tree_ex(_tree(_lines(PLAIN_LINES)))
        assert tree.title == "关于进一步加强安全生产工作的通知"
        assert stats.title_removed is True
        assert stats.recipient == "各市人民政府："
        assert stats.signature == ["××市人民政府", "2026年10月3日"]
        assert stats.headings == {1: 1, 2: 1, 3: 1}
        assert stats.merged_lines == 1

    def test_hard_wrapped_sentence_merged(self):
        tree = _norm(_lines(PLAIN_LINES))
        assert any("抓好贯彻落实。" in b.text for b in tree.blocks)
        assert not any(b.text == "落实。" for b in tree.blocks)


# ---------------------------------------------------------------- markdown
MD_LINES = [
    "# 关于进一步加强安全生产工作的通知",
    "",
    "## 一、总体要求",
    "正文第一段。",
    "",
    "- 列表项一",
    "- 列表项二",
    "",
    "```python",
    "# 这是代码注释",
    "x = 1",
    "```",
    "",
    "### （一）统一思想",
    "具体内容。",
]


class TestMarkdown:
    def test_before_markers_are_literal_text(self):
        tree = _tree(_lines(MD_LINES))
        texts = [b.text for b in tree.blocks]
        assert "# 关于进一步加强安全生产工作的通知" in texts
        assert "## 一、总体要求" in texts

    def test_after_markers_stripped_and_levels_mapped(self):
        tree = _norm(_lines(MD_LINES))
        assert tree.title == "关于进一步加强安全生产工作的通知"
        # 缺陷 b：markdown 的 ## 一、 与 txt/docx 的"一、"统一按公文序号定级
        assert _shape(tree) == [
            (HEADING, 1, "一、总体要求"),
            (PARAGRAPH, 0, "正文第一段。"),
            (PARAGRAPH, 0, "列表项一列表项二"),
            (PARAGRAPH, 0, "# 这是代码注释"),
            (PARAGRAPH, 0, "x = 1"),
            (HEADING, 2, "（一）统一思想"),
            (PARAGRAPH, 0, "具体内容。"),
        ]

    def test_code_fence_content_is_not_classified(self):
        tree = _norm(_lines(MD_LINES))
        code = [b for b in tree.blocks if b.text in ("# 这是代码注释", "x = 1")]
        assert len(code) == 2
        assert all(b.type == PARAGRAPH for b in code), "围栏内不参与标题识别"

    def test_blockquote_marker_stripped(self):
        tree = _norm([Block(text="> 引用内容。")])
        assert _shape(tree) == [(PARAGRAPH, 0, "引用内容。")]


# ---------------------------------------------------------------- PDF 提取文本
PDF_LINES = [
    "关于进一步加强安全生产工作的通知",
    "各市人民政府：",
    "为进一步加强安全生产工作，现将有关事项通知",
    "如下，请结合实际抓好贯彻落实。",
    "一、提高认识",
    "各单位要充分认识做好安全生产工作的重要性，",
    "切实把思想和行动统一到部署上来。",
    "（一）统一思想",
    "成立工作领导小组，明确责任分工。",
    "××市人民政府",
    "2026年10月3日",
]


class TestPdfExtracted:
    def test_hard_wraps_merged_into_paragraphs(self):
        tree = _norm(_lines(PDF_LINES))
        texts = [b.text for b in tree.blocks]
        assert ("为进一步加强安全生产工作，现将有关事项通知"
                "如下，请结合实际抓好贯彻落实。") in texts
        assert ("各单位要充分认识做好安全生产工作的重要性，"
                "切实把思想和行动统一到部署上来。") in texts

    def test_levels_recipient_signature(self):
        tree, stats = N.normalize_tree_ex(_tree(_lines(PDF_LINES)))
        assert tree.title == "关于进一步加强安全生产工作的通知"
        assert stats.recipient == "各市人民政府："
        assert stats.signature == ["××市人民政府", "2026年10月3日"]
        assert stats.headings == {1: 1, 2: 1}
        assert stats.merged_lines == 2


# ---------------------------------------------------------------- mixed
class TestMixed:
    def test_existing_heading_table_and_markdown_together(self):
        tree = _tree([
            Block(type=HEADING, level=1, text="# 一、总体要求"),
            Block(text="正文内容。"),
            Block(type=TABLE, rows=[["序号", "事项"], ["1", "内容"]]),
            Block(text="## （一）统一思想"),
            Block(text="尾段内容。"),
        ], title="原标题")
        got = _norm(tree.blocks, tree.title)
        assert got.title == "原标题", "首块已是 heading，不再从正文里抢标题"
        assert _shape(got)[0] == (HEADING, 1, "一、总体要求")
        assert _shape(got)[2][0] == TABLE
        assert got.blocks[2].rows == [["序号", "事项"], ["1", "内容"]]
        assert _shape(got)[3] == (HEADING, 2, "（一）统一思想")

    def test_existing_list_item_kept(self):
        got = _norm([Block(type=LIST_ITEM, text="一、清单项"),
                     Block(text="正文内容。")])
        assert got.blocks[0].type == LIST_ITEM
        assert got.blocks[0].text == "一、清单项"


# ---------------------------------------------------------------- 标题
class TestTitleRules:
    def test_positive_titles(self):
        for text in ("关于进一步加强安全生产工作的通知",
                     "××市安全生产专项整治实施方案",
                     "在全市安全生产工作会议上的讲话",
                     "关于2026年工作安排的报告"):
            tree, stats = N.normalize_tree_ex(
                _tree([Block(text=text), Block(text="正文内容。")]))
            assert stats.title == text, text
            assert tree.title == text
            assert [b.text for b in tree.blocks] == ["正文内容。"]

    def test_negative_titles(self):
        for text in ("现将有关事项通知如下。",          # 句末标点
                     "一、总体要求",                    # 序号标题
                     "这是一段没有文种词的正文内容",      # 无文种尾词
                     "关于" + "很长的标题" * 10 + "的通知"):  # 超过 40 字
            tree, stats = N.normalize_tree_ex(
                _tree([Block(text=text), Block(text="正文内容。")]))
            assert stats.title == "", text
            assert len(tree.blocks) >= 1 and tree.blocks[0].text.startswith(text)

    def test_title_overrides_db_title(self):
        tree, stats = N.normalize_tree_ex(_tree(
            [Block(text="关于进一步加强安全生产工作的通知"),
             Block(text="正文内容。")], title="库内标题"))
        assert stats.title == "关于进一步加强安全生产工作的通知"
        assert tree.title == "关于进一步加强安全生产工作的通知"

    def test_existing_heading_first_is_not_stolen(self):
        tree, stats = N.normalize_tree_ex(_tree(
            [Block(type=HEADING, level=1, text="关于进一步加强安全生产工作的通知")],
            title="库内标题"))
        assert stats.title == ""
        assert tree.title == "库内标题"
        assert tree.blocks[0].type == HEADING


# ---------------------------------------------------------------- 层级
class TestHeadingRules:
    def test_numbering_levels(self):
        cases = {
            "一、提高认识": 1,
            "十二、其他事项": 1,
            "（一）统一思想": 2,
            "（十二）附加要求": 2,
            "1. 加强领导": 3,
            "2、具体要求": 3,
            "（1）细化措施": 3,
        }
        for text, level in cases.items():
            got = _norm([Block(text=text), Block(text="正文内容。")])
            assert (got.blocks[0].type, got.blocks[0].level) == (HEADING, level), text

    def test_leading_spaces_tolerated(self):
        got = _norm([Block(text="   一、提高认识"), Block(text="正文内容。")])
        assert got.blocks[0].type == HEADING
        assert got.blocks[0].text == "一、提高认识"

    def test_long_line_with_numbering_stays_paragraph(self):
        text = "一、" + "这是正文里引用序号的长句子" * 5
        assert len(text) > 60
        got = _norm([Block(text=text), Block(text="正文内容。")])
        assert got.blocks[0].type == PARAGRAPH
        assert got.blocks[0].text.startswith(text)

    def test_numbering_in_middle_not_heading(self):
        got = _norm([Block(text="正文里提到一、二点意见。")])
        assert got.blocks[0].type == PARAGRAPH

    def test_markdown_heading_ignores_length_limit(self):
        long_head = "# " + "很长的标题" * 20
        got = _norm([Block(text=long_head)])
        assert got.blocks[0].type == HEADING
        assert got.blocks[0].level == 1
        assert got.blocks[0].text == "很长的标题" * 20


# ---------------------------------------------------------------- 缺陷 b：三来源一致
def _headings(tree: DocTree) -> list[tuple[int, str]]:
    return [(b.level, b.text) for b in tree.effective_blocks(True)
            if b.type == HEADING]


class TestSourceConsistency:
    """同一材料从 txt / markdown / docx 进入汇编，TOC 层级必须一致（缺陷 b）。"""

    @staticmethod
    def _txt_style() -> DocTree:
        # parsers/txt_parser.py 的真实产物：首行 level1，其后"一、"一律 level2
        return DocTree(title="关于进一步加强安全生产工作的通知", blocks=[
            Block(type=HEADING, level=1, text="关于进一步加强安全生产工作的通知"),
            Block(type=HEADING, level=2, text="一、总体要求"),
            Block(type=HEADING, level=2, text="（一）主要成效"),
            Block(type=HEADING, level=2, text="1. 具体措施"),
            Block(text="正文内容。"),
        ])

    @staticmethod
    def _markdown_style() -> DocTree:
        return DocTree(blocks=_lines([
            "# 关于进一步加强安全生产工作的通知",
            "## 一、总体要求",
            "### （一）主要成效",
            "#### 1. 具体措施",
            "正文内容。",
        ]))

    @staticmethod
    def _docx_style() -> DocTree:
        return DocTree(title="关于进一步加强安全生产工作的通知", blocks=[
            Block(type=HEADING, level=1, text="一、总体要求"),
            Block(type=HEADING, level=2, text="（一）主要成效"),
            Block(type=HEADING, level=3, text="1. 具体措施"),
            Block(text="正文内容。"),
        ])

    def test_three_sources_same_levels(self):
        txt = _headings(N.normalize_tree(self._txt_style()))
        markdown = _headings(N.normalize_tree(self._markdown_style()))
        docx = _headings(N.normalize_tree(self._docx_style()))
        assert txt == markdown == docx == [
            (1, "一、总体要求"),
            (2, "（一）主要成效"),
            (3, "1. 具体措施"),
        ]

    def test_old_parser_level_is_overridden_by_numbering(self):
        got = _norm([Block(type=HEADING, level=2, text="一、总体要求"),
                     Block(text="正文。")])
        assert got.blocks[0].level == 1
        got = _norm([Block(type=HEADING, level=1, text="（一）主要成效"),
                     Block(text="正文。")])
        assert got.blocks[0].level == 2

    def test_heading_without_numbering_keeps_parser_level(self):
        got = _norm([Block(type=HEADING, level=2, text="总体要求"),
                     Block(text="正文。")])
        assert (got.blocks[0].type, got.blocks[0].level) == (HEADING, 2)

    def test_markdown_heading_without_numbering_keeps_hash_level(self):
        got = _norm([Block(text="## 总体要求"), Block(text="正文。")])
        assert got.blocks[0].type == HEADING and got.blocks[0].level == 2


# ---------------------------------------------------------------- 缺陷 a：标题重定基
class TestMaterialTitleRebase:
    """材料标题以 Heading 1 插入时，内部 heading 整体降一级（缺陷 a）。"""

    @staticmethod
    def _tree(title: str = "材料一") -> DocTree:
        return DocTree(title=title, blocks=[
            Block(type=HEADING, level=1, text="一、总体要求"),
            Block(type=PARAGRAPH, text="正文内容。"),
            Block(type=HEADING, level=2, text="（一）主要成效"),
            Block(type=HEADING, level=3, text="1. 具体措施"),
            Block(type=LIST_ITEM, text="清单项"),
            Block(type=TABLE, rows=[["序号", "事项"]]),
        ])

    def test_levels_shift_by_one_and_cap_at_four(self):
        got = N.rebase_material_levels(self._tree())
        levels = [(b.type, b.level) for b in got.blocks]
        assert levels == [
            (HEADING, 2), (PARAGRAPH, 0), (HEADING, 3), (HEADING, 4),
            (LIST_ITEM, 0), (TABLE, 0),
        ]
        assert got.title == "材料一"

    def test_level_four_caps_at_four(self):
        tree = DocTree(title="材料", blocks=[Block(type=HEADING, level=4,
                                                  text="无序号标题")])
        assert N.rebase_material_levels(tree).blocks[0].level == 4

    def test_idempotent(self):
        once = N.rebase_material_levels(self._tree())
        twice = N.rebase_material_levels(once)
        thrice = N.rebase_material_levels(twice)
        assert [b.level for b in twice.blocks] == [b.level for b in once.blocks]
        assert [b.level for b in thrice.blocks] == [b.level for b in once.blocks]

    def test_does_not_mutate_input(self):
        tree = self._tree()
        before = [b.level for b in tree.blocks]
        N.rebase_material_levels(tree)
        assert [b.level for b in tree.blocks] == before

    def test_table_rows_and_text_kept(self):
        got = N.rebase_material_levels(self._tree())
        table = [b for b in got.blocks if b.type == TABLE][0]
        assert table.rows == [["序号", "事项"]]
        assert [b.text for b in got.blocks if b.type == PARAGRAPH] == ["正文内容。"]

    @staticmethod
    def _capture_compile(monkeypatch, trees: list[DocTree], tpl) -> list[DocTree]:
        # 落到临时目录：早期版本写 Path("stub.docx")，会在仓库根留下 4 字节垃圾
        # （v1.15 收尾时实测到 stub.docx 污染工作区）。
        import tempfile

        captured: dict = {}
        out_docx = Path(tempfile.mkdtemp(prefix="compile_stub_")) / "stub.docx"

        def fake_generate_docx(gen_trees, _gen_tpl, out_path):
            captured["trees"] = gen_trees
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_text("stub", encoding="utf-8")
            return str(out_path)

        monkeypatch.setattr(
            compiler, "load_trees_ex",
            lambda _ids, _paths, **_kw: (trees, [], [True] * len(trees)))
        monkeypatch.setattr(compiler.docxgen, "generate_docx",
                            fake_generate_docx)
        compiler.compile_docx(compiler.CompileRequest(
            doc_ids=[1] * len(trees), out_docx=str(out_docx),
            template=tpl))
        return captured["trees"]

    def test_compile_shifts_when_material_title_inserted(self, monkeypatch):
        from gwtool.core.template import DocTemplate
        got = self._capture_compile(monkeypatch, [self._tree()], DocTemplate())
        heads = [(b.level, b.text) for b in got[0].blocks if b.type == HEADING]
        # 封顶 4：公文三级「1.」= 材料标题(1) + 3，不能再压成 3（否则字体落到楷体）
        assert heads == [(2, "一、总体要求"), (3, "（一）主要成效"),
                         (4, "1. 具体措施")]
        assert got[0].title == "材料一"

    def test_compile_two_materials_both_shift(self, monkeypatch):
        from gwtool.core.template import DocTemplate
        trees = [self._tree("材料一"), self._tree("材料二")]
        got = self._capture_compile(monkeypatch, trees, DocTemplate())
        for tree in got:
            assert [b.level for b in tree.blocks if b.type == HEADING] == [2, 3, 4]

    def test_compile_no_shift_when_material_titles_disabled(self, monkeypatch):
        from gwtool.core.template import DocTemplate
        tpl = DocTemplate()
        tpl.insert_material_titles = False
        got = self._capture_compile(monkeypatch, [self._tree()], tpl)
        assert [b.level for b in got[0].blocks if b.type == HEADING] == [1, 2, 3]

    def test_compile_no_shift_for_untitled_material(self, monkeypatch):
        from gwtool.core.template import DocTemplate
        untitled = DocTree(title="", blocks=[
            Block(type=HEADING, level=1, text="一、总体要求"),
            Block(text="正文内容。"),
        ])
        got = self._capture_compile(monkeypatch, [untitled], DocTemplate())
        assert got[0].title == ""
        assert [b.level for b in got[0].blocks if b.type == HEADING] == [1], \
            "材料无标题时不插入标题，也不应凭空加层"

    def test_compile_no_heading_fabricated(self, monkeypatch):
        from gwtool.core.template import DocTemplate
        plain = DocTree(title="材料三", blocks=[Block(text="正文内容。")])
        got = self._capture_compile(monkeypatch, [plain], DocTemplate())
        assert not [b for b in got[0].blocks if b.type == HEADING]

    def test_library_blocks_not_modified_by_compile(self, tmp_db, tmp_path,
                                                   monkeypatch):
        blocks = [Block(type=HEADING, level=1, text="一、总体要求"),
                  Block(text="正文内容。")]
        did = dao.add_document(dao.Document(
            title="材料甲", content_text="一、总体要求\n正文内容。",
            blocks_json=json.dumps([b.to_dict() for b in blocks],
                                   ensure_ascii=False), file_type="docx"))
        before = dao.get_document(did).blocks_json

        def fake_generate_docx(_trees, _tpl, out_path):
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_text("stub", encoding="utf-8")
            return str(out_path)

        monkeypatch.setattr(compiler.docxgen, "generate_docx",
                            fake_generate_docx)
        compiler.compile_docx(compiler.CompileRequest(
            doc_ids=[did], out_docx=str(tmp_path / "out.docx")))
        assert dao.get_document(did).blocks_json == before, \
            "汇编层级重定基只作用于产物，不得写回资料库块数据"


# ---------------------------------------------------------------- 主送 / 落款
class TestRecipientAndSignature:
    def test_recipient_recognized_and_not_merged(self):
        tree, stats = N.normalize_tree_ex(_tree(_lines([
            "关于进一步加强安全生产工作的通知",
            "各单位：",
            "现将有关事项通知如下。",
        ])))
        assert stats.recipient == "各单位："
        assert [b.text for b in tree.blocks] == ["各单位：", "现将有关事项通知如下。"]

    def test_guide_colon_is_not_recipient(self):
        tree, stats = N.normalize_tree_ex(_tree(_lines([
            "关于进一步加强安全生产工作的通知",
            "现通知如下：",
            "第一条内容。",
        ])))
        assert stats.recipient == ""
        assert stats.title == "关于进一步加强安全生产工作的通知"

    def test_signature_needs_both_org_and_date(self):
        only_org = _norm(_lines(["正文内容。", "××市人民政府"]))
        only_date = _norm(_lines(["正文内容。", "2026年10月3日"]))
        both = _norm(_lines(["正文内容。", "××市人民政府", "2026年10月3日"]))
        assert [b.text for b in only_org.blocks][-1] == "××市人民政府"
        assert [b.text for b in only_date.blocks][-1] == "2026年10月3日"
        assert [b.text for b in both.blocks][-2:] == ["××市人民政府", "2026年10月3日"]
        assert all(b.type == PARAGRAPH for b in both.blocks)

    def test_signature_does_not_swallow_body_after_it(self):
        tree, stats = N.normalize_tree_ex(_tree(_lines([
            "正文内容。", "××市人民政府", "2026年10月3日", "另附说明。"])))
        assert stats.signature == []


# ---------------------------------------------------------------- 续接
class TestMergeRules:
    def test_unfinished_line_is_continued(self):
        got = _norm(_lines(["这是被硬换行拆断的第一部分",
                            "第二部分在这里。"]))
        assert [b.text for b in got.blocks] == ["这是被硬换行拆断的第一部分第二部分在这里。"]

    def test_finished_line_starts_new_paragraph(self):
        got = _norm(_lines(["第一段结束。", "第二段开始。"]))
        assert [b.text for b in got.blocks] == ["第一段结束。", "第二段开始。"]

    def test_blank_lines_do_not_block_continuation(self):
        got = _norm(_lines(["上半句", "", "下半句。"]))
        assert [b.text for b in got.blocks] == ["上半句下半句。"]

    def test_heading_and_table_interrupt_continuation(self):
        got = _norm([
            Block(text="上半句"),
            Block(text="一、标题"),
            Block(text="标题下正文。"),
            Block(type=TABLE, rows=[["甲", "乙"]]),
            Block(text="下半句。"),
        ])
        assert [b.type for b in got.blocks] == [PARAGRAPH, HEADING, PARAGRAPH, TABLE, PARAGRAPH]

    def test_recipient_interrupts_continuation(self):
        got = _norm(_lines(["关于进一步加强安全生产工作的通知",
                            "各单位：", "现将有关事项通知如下。"]))
        assert [b.type for b in got.blocks] == [PARAGRAPH, PARAGRAPH]


# ---------------------------------------------------------------- 幂等 / 纯函数
def _four_inputs() -> list[tuple[str, DocTree]]:
    return [
        ("plain", _tree(_lines(PLAIN_LINES))),
        ("markdown", _tree(_lines(MD_LINES))),
        ("pdf", _tree(_lines(PDF_LINES))),
        ("mixed", _tree([
            Block(type=HEADING, level=1, text="# 一、总体要求"),
            Block(text="正文内容。"),
            Block(type=TABLE, rows=[["序号", "事项"]]),
            Block(text="## （一）统一思想"),
        ], title="原标题")),
    ]


class TestIdempotency:
    def test_normalize_twice_is_identical(self):
        for name, tree in _four_inputs():
            once = N.normalize_tree(tree)
            twice = N.normalize_tree(once)
            assert once.to_json() == twice.to_json(), name
            assert _shape(once) == _shape(twice), name

    def test_normalize_three_times_stable(self):
        for name, tree in _four_inputs():
            once = N.normalize_tree(tree)
            twice = N.normalize_tree(once)
            thrice = N.normalize_tree(twice)
            assert _shape(twice) == _shape(thrice), name


class TestPurity:
    def test_input_tree_not_mutated(self):
        tree = _tree(_lines(PLAIN_LINES))
        before = json.loads(tree.to_json())
        N.normalize_tree(tree)
        assert json.loads(tree.to_json()) == before
        assert tree.title == ""

    def test_returns_new_tree(self):
        tree = _tree(_lines(PLAIN_LINES))
        got = N.normalize_tree(tree)
        assert got is not tree and got.blocks is not tree.blocks

    def test_empty_tree(self):
        got = N.normalize_tree(DocTree())
        assert got.blocks == [] and got.title == ""

    def test_none_tolerated(self):
        got = N.normalize_tree(None)  # type: ignore[arg-type]
        assert got.blocks == []

    def test_whitespace_blocks_dropped_and_table_kept(self):
        got = _norm([Block(text="   "), Block(type=TABLE, rows=[["甲"]])])
        assert [b.type for b in got.blocks] == [TABLE]


# ---------------------------------------------------------------- 接入 compiler
class TestCompilerIntegration:
    def test_plain_text_db_document_is_normalized(self, tmp_db):
        did = dao.add_document(dao.Document(
            title="库内标题", content_text="\n".join(PLAIN_LINES),
            blocks_json="[]", file_type="txt"))
        tree = compiler.load_trees([did], [])[0]
        assert tree.title == "关于进一步加强安全生产工作的通知"
        types = [(b.type, b.level) for b in tree.blocks]
        assert (HEADING, 1) in types and (HEADING, 2) in types and (HEADING, 3) in types
        assert not any(b.text.startswith("#") for b in tree.blocks)

    def test_markdown_blocks_are_normalized(self, tmp_db):
        blocks = [b.to_dict() for b in _lines(MD_LINES)]
        did = dao.add_document(dao.Document(
            title="markdown", content_text="\n".join(MD_LINES),
            blocks_json=json.dumps(blocks, ensure_ascii=False), file_type="md"))
        tree = compiler.load_trees([did], [])[0]
        assert tree.title == "关于进一步加强安全生产工作的通知"
        assert any(b.type == HEADING and b.text == "一、总体要求" for b in tree.blocks)
        # markdown 标题标记不得残留在 heading 文本里；围栏内的代码行原样保留
        assert not any(b.type == HEADING and b.text.startswith("#")
                       for b in tree.blocks)
        assert any(b.text == "# 这是代码注释" for b in tree.blocks)

    def test_extra_text_file_is_normalized(self, tmp_db, tmp_path):
        p = tmp_path / "外部通知.txt"
        p.write_text("\n".join(PLAIN_LINES), encoding="utf-8")
        tree = compiler.load_trees([], [str(p)])[0]
        assert tree.title == "关于进一步加强安全生产工作的通知"
        assert any(b.type == HEADING and b.level == 1 for b in tree.blocks)

    def test_existing_wellformed_tree_keeps_heading_and_table(self, tmp_db):
        blocks = [
            Block(type=HEADING, level=1, text="一、总体要求"),
            Block(text="正文内容。"),
            Block(type=TABLE, rows=[["序号", "事项"], ["1", "内容"]]),
        ]
        did = dao.add_document(dao.Document(
            title="结构化文档", content_text="正文内容。",
            blocks_json=json.dumps([b.to_dict() for b in blocks],
                                   ensure_ascii=False), file_type="docx"))
        tree = compiler.load_trees([did], [])[0]
        assert tree.blocks[0].type == HEADING and tree.blocks[0].text == "一、总体要求"
        table = [b for b in tree.blocks if b.type == TABLE]
        assert table and table[0].rows == [["序号", "事项"], ["1", "内容"]]

    def test_slot_semantics_unchanged(self, tmp_db, tmp_path):
        did = dao.add_document(dao.Document(
            title="材料甲", content_text="正文内容。", blocks_json="[]",
            file_type="txt"))
        trees, failures, slot_ok = compiler.load_trees_ex(
            [did, 999999], [str(tmp_path / "不存在.txt")])
        assert len(trees) == 1 and len(failures) == 2
        assert slot_ok == [True, False, False]


# ---------------------------------------------------------------- DOCX/PDF 一致性
class TestOutputPathConsistency:
    """同一批材料导出 DOCX 与内置 PDF，目录层级必须逐项一致。

    材料标题在两条渲染路径上都按 Heading 1 插入；如果只有 DOCX 路径做了
    层级重定基，PDF 目录就会与 DOCX 不同（T4 验收矩阵实测）。现在重定基
    收口在 ``compiler.load_trees_ex(insert_material_titles=...)``，DOCX
    （compile_docx）与 PDF（PdfRenderWorker → load_trees）共用。
    """

    @staticmethod
    def _add_doc(title: str = "材料甲") -> int:
        return dao.add_document(dao.Document(
            title=title,
            content_text="关于进一步加强安全生产工作的通知\n"
                         "一、总体要求\n（一）主要成效\n正文内容。",
            blocks_json="[]", file_type="txt"))

    @staticmethod
    def _levels(tree: DocTree) -> list[int]:
        return [b.level for b in tree.blocks if b.type == HEADING]

    def test_load_trees_flag_shifts_titled_material(self, tmp_db):
        did = self._add_doc()
        plain = compiler.load_trees([did], [])[0]
        shifted = compiler.load_trees([did], [], insert_material_titles=True)[0]
        assert self._levels(plain) == [1, 2]
        assert self._levels(shifted) == [2, 3]
        assert plain.title == shifted.title == "关于进一步加强安全生产工作的通知"

    def test_load_trees_ex_accepts_keyword(self, tmp_db):
        did = self._add_doc()
        trees, failures, slot_ok = compiler.load_trees_ex(
            [did], [], insert_material_titles=True)
        assert slot_ok == [True] and not failures
        assert self._levels(trees[0]) == [2, 3]

    def test_compile_trees_match_load_trees_flag(self, tmp_db, tmp_path,
                                                 monkeypatch):
        did = self._add_doc()
        captured: dict = {}

        def fake_generate_docx(trees, _tpl, out_path):
            captured["trees"] = trees
            Path(out_path).write_text("stub", encoding="utf-8")
            return str(out_path)

        monkeypatch.setattr(compiler.docxgen, "generate_docx",
                            fake_generate_docx)
        compiler.compile_docx(compiler.CompileRequest(
            doc_ids=[did], out_docx=str(tmp_path / "out.docx")))
        expected = compiler.load_trees([did], [], insert_material_titles=True)

        def heads(tree: DocTree) -> list[tuple[int, str]]:
            return [(b.level, b.text) for b in tree.blocks if b.type == HEADING]

        assert heads(captured["trees"][0]) == heads(expected[0]) == [
            (2, "一、总体要求"), (3, "（一）主要成效")]

    def test_repeated_load_trees_no_double_shift(self, tmp_db):
        did = self._add_doc()
        first = compiler.load_trees([did], [], insert_material_titles=True)[0]
        second = compiler.load_trees([did], [], insert_material_titles=True)[0]
        assert self._levels(first) == self._levels(second) == [2, 3]

    def test_compile_twice_no_double_shift(self, tmp_db, tmp_path, monkeypatch):
        did = self._add_doc()
        captured: list = []

        def fake_generate_docx(trees, _tpl, out_path):
            captured.append(trees)
            Path(out_path).write_text("stub", encoding="utf-8")
            return str(out_path)

        monkeypatch.setattr(compiler.docxgen, "generate_docx",
                            fake_generate_docx)
        req = compiler.CompileRequest(doc_ids=[did],
                                      out_docx=str(tmp_path / "out.docx"))
        compiler.compile_docx(req)
        compiler.compile_docx(req)
        assert self._levels(captured[0][0]) == [2, 3]
        assert self._levels(captured[1][0]) == [2, 3], "重复汇编不得继续降级"

    def test_pdf_and_docx_heading_levels_match(self, tmp_db, qapp, tmp_path):
        from gwtool.core import pdfrender
        from gwtool.core.template import DocTemplate

        did = self._add_doc()
        tpl = DocTemplate()
        # 本用例验 DOCX/PDF 层级**逐项一致**，需要把三级也纳入目录
        # （默认 toc_max_level=2 只收 1/2 级，是 v1.15 的目录口径）。
        tpl.toc_max_level = 3
        trees = compiler.load_trees([did], [], insert_material_titles=True)
        expected = [(trees[0].title, 1)] + [
            (b.text, b.level)
            for b in trees[0].effective_blocks(True) if b.type == HEADING]
        assert expected == [("关于进一步加强安全生产工作的通知", 1),
                            ("一、总体要求", 2), ("（一）主要成效", 3)]
        # 内置 PDF 的目录层级来源与 DOCX 同一批树 → 逐项一致
        assert pdfrender.collect_headings(trees, tpl) == expected

        out = tmp_path / "汇编产物.pdf"
        pdfrender.render_compiled_pdf(trees, tpl, str(out))
        assert out.exists() and out.stat().st_size > 0

    def test_pdf_render_worker_uses_rebased_trees(self, tmp_db, qapp, tmp_path):
        from gwtool.core.template import DocTemplate
        from gwtool.ui import workers

        did = self._add_doc()
        out = tmp_path / "worker.pdf"
        w = workers.PdfRenderWorker([did], [], DocTemplate(), str(out))
        done: list[str] = []
        errors: list[str] = []
        w.done.connect(done.append)
        w.error.connect(errors.append)
        w.run()
        assert not errors, errors
        assert done and Path(done[0]).exists()

        # 静态护栏：worker 必须把模板开关传给 load_trees（否则 PDF 又回退成
        # "材料标题与一级标题同级"的扁平层级）。
        src = (Path(__file__).resolve().parents[1]
               / "gwtool" / "ui" / "workers.py")
        text = src.read_text(encoding="utf-8")
        assert "insert_material_titles=" in text
        assert "load_trees(" in text

    def test_compile_docx_legacy_string_template_keeps_error_contract(
            self, tmp_db):
        """历史调用方用 JSON 字符串当模板时，"空材料"仍须报 ValueError。"""
        with pytest.raises(ValueError, match="没有可汇编"):
            compiler.compile_docx(compiler.CompileRequest(template="{}"))
