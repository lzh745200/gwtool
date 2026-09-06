# -*- coding: utf-8 -*-
"""编辑器结构保全与大纲跳转回归（第 16 轮逐模块探测产出）。

缺陷一：save_to_db 调不带 blocks_json 的 update_document_content，结构被
清成 "[]"——导入的表格/标题层级随一次编辑保存全部丢失，汇编输出降级。
缺陷二：大纲跳转调用 QTextEdit.centerCursor()，PySide6 6.11 已移除该方法，
点击大纲条目 AttributeError，跳转失效。
"""
from __future__ import annotations

import json

import pytest

from gwtool.core.importer import parse_any
from gwtool.core.model import PARAGRAPH, TABLE
from gwtool.db import dao


@pytest.fixture()
def table_doc(tmp_db):
    """带标题与表格结构的 docx 入库，返回文档 id。"""
    from docx import Document as DX
    p = tmp_db.parent / "带表格.docx"
    d = DX()
    d.add_heading("一、总体要求", level=1)
    d.add_paragraph("工作布署如下。")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "项目"
    t.cell(0, 1).text = "布署情况"
    t.cell(1, 0).text = "甲"
    t.cell(1, 1).text = "截止8月底"
    d.save(str(p))
    r = parse_any(str(p))
    return dao.add_document(dao.Document(title=r.tree.title,
                                         content_text=r.tree.plain_text(),
                                         blocks_json=r.tree.to_json(),
                                         file_type="docx"))


def test_save_without_change_preserves_blocks(table_doc, qapp):
    """未改动文本保存：blocks_json 原样保留（表格/标题层级不丢）。"""
    from gwtool.ui.editor_panel import EditorPanel
    panel = EditorPanel()
    panel.load_document(table_doc)
    panel.save_to_db()
    kept = json.loads(dao.get_document(table_doc).blocks_json)
    tbl = next(b for b in kept if b["type"] == TABLE)
    assert tbl["rows"][0] == ["项目", "部署情况"] or tbl["rows"][0] == ["项目", "布署情况"]
    assert any(b["type"] == "heading" for b in kept)


def test_save_after_change_rebuilds_headings(table_doc, qapp):
    """改动文本保存：按标题正则重建块（不再是空 []），层级映射与大纲一致。"""
    from gwtool.ui.editor_panel import EditorPanel
    panel = EditorPanel()
    panel.load_document(table_doc)
    panel.editor.setPlainText("一、总体要求\n工作部署已经完成。\n（一）基本原则\n持续深化。")
    panel.save_to_db()
    rebuilt = json.loads(dao.get_document(table_doc).blocks_json)
    heads = [b for b in rebuilt if b["type"] == "heading"]
    assert [b["text"] for b in heads] == ["一、总体要求", "（一）基本原则"]
    assert [b["level"] for b in heads] == [1, 2]
    assert not any(b["type"] == TABLE for b in rebuilt)  # 纯文本编辑的固有取舍


def test_outline_jump_no_attribute_error(table_doc, qapp):
    """大纲跳转：ensureCursorVisible 取代已移除的 centerCursor。"""
    from gwtool.ui.editor_panel import EditorPanel
    panel = EditorPanel()
    panel.load_document(table_doc)
    panel.rebuild_outline()
    item = panel.outline.topLevelItem(0)
    panel._jump_to_heading(item, 0)          # 修复前：AttributeError
    assert panel.editor.textCursor().blockNumber() + 1 == 1


def test_compilation_uses_rebuilt_headings(table_doc, qapp):
    """编辑器改字保存后重建的标题，仍以 Heading 块进汇编树（目录可抓取）。"""
    from gwtool.core.compiler import load_trees
    from gwtool.ui.editor_panel import EditorPanel
    panel = EditorPanel()
    panel.load_document(table_doc)
    panel.editor.setPlainText("一、总体要求\n正文。")
    panel.save_to_db()
    tree = load_trees([table_doc], [])[0]
    assert any(b.type == "heading" and "总体要求" in b.text for b in tree.blocks)
