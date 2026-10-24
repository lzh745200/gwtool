# -*- coding: utf-8 -*-
"""v1.15 一键汇编验收（红→绿基线，按 doublecheck spec）。

覆盖两条尚未实现的行为（先红后绿）：
1. 目录默认只收 1–2 级（`toc_max_level=2`），三级 `1.` 枚举不进目录；
   把 `toc_max_level` 调到 3 时三级才进目录。
2. 跨页重影：任何一页"版心以下窄带"不得出现下一页正文首行（当前正文页仍有重影）。
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gwtool.core.model import HEADING, PARAGRAPH, Block, DocTree
from gwtool.core.template import default_template

pytest.importorskip("pymupdf")

# PDF 渲染依赖 QApplication（Qt 字体子系统）；离屏环境下必须显式创建。
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def _tree(title: str, blocks: list[Block]) -> DocTree:
    return DocTree(title=title, blocks=blocks)


def _sample_trees() -> list[DocTree]:
    long_para = ("为深入贯彻落实上级关于进一步加强和改进新形势下安全生产工作的各项部署要求，"
                 "现就有关事项通知如下。")
    blocks = [
        Block(type=HEADING, level=1, text="一、总体要求"),
        Block(type=PARAGRAPH, text=long_para * 3),
        Block(type=HEADING, level=2, text="（一）具体安排"),
        Block(type=PARAGRAPH, text=long_para * 3),
        Block(type=HEADING, level=3, text="1. 细化措施"),
        Block(type=PARAGRAPH, text=long_para * 4),
    ]
    second = [
        Block(type=PARAGRAPH, text=long_para * 6),
        Block(type=HEADING, level=1, text="二、重点任务"),
        Block(type=PARAGRAPH, text=long_para * 4),
    ]
    return [_tree("材料一", blocks), _tree("材料二", second)]


# ---------------------------------------------------------------- 目录层级
def test_toc_default_max_level_is_two():
    """默认模板必须带 toc_max_level=2，且三级标题不进目录。"""
    tpl = default_template()
    assert getattr(tpl, "toc_max_level", None) == 2, (
        "默认模板缺少 toc_max_level=2（目录会被 1. 2. 3. 枚举淹没）")

    from gwtool.core.pdfrender import collect_headings

    tpl.insert_material_titles = False
    heads = collect_headings(_sample_trees()[:1], tpl)
    levels = [lv for _t, lv in heads]
    assert 3 not in levels, f"默认目录仍收录三级标题：{heads}"
    assert [1, 2] == sorted(set(levels)), f"默认目录应只含 1/2 级：{heads}"


def test_toc_level3_when_configured():
    from gwtool.core.pdfrender import collect_headings

    tpl = default_template()
    tpl.insert_material_titles = False
    tpl.toc_max_level = 3
    heads = collect_headings(_sample_trees()[:1], tpl)
    assert 3 in [lv for _t, lv in heads], f"toc_max_level=3 时三级应进目录：{heads}"


# ---------------------------------------------------------------- 跨页重影
def _pdf_bleed_report(pdf_path: str, tpl) -> list[str]:
    import pymupdf

    mm = 2.834645669
    doc = pymupdf.open(pdf_path)
    problems = []
    try:
        top = tpl.margin_top_mm * mm
        for i in range(doc.page_count - 1):
            page, nxt = doc[i], doc[i + 1]
            bottom = page.rect.height - tpl.margin_bottom_mm * mm
            # 下一页正文首行（版心内最上面的一行）
            next_lines = []
            for blk in nxt.get_text("dict").get("blocks", []):
                for line in blk.get("lines", []):
                    txt = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
                    if not txt:
                        continue
                    y0 = line["bbox"][1]
                    if y0 >= top - 2:
                        next_lines.append((y0, txt))
            next_lines.sort()
            if not next_lines:
                continue
            first = "".join(next_lines[0][1].split())
            # 本页版心以下（页边距带）出现的文字
            for blk in page.get_text("dict").get("blocks", []):
                for line in blk.get("lines", []):
                    txt = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
                    if not txt:
                        continue
                    y0 = line["bbox"][1]
                    if y0 > bottom + 1 and "".join(txt.split()) == first:
                        problems.append(
                            f"p{i + 1} 版心以下出现下一页首行 {first[:24]!r}")
    finally:
        doc.close()
    return problems


def test_no_body_page_bleed(tmp_path):
    from gwtool.core.pdfrender import render_compiled_pdf

    tpl = default_template()
    tpl.cover.enabled = True
    tpl.cover.title = "测试汇编"
    out = tmp_path / "汇编.pdf"
    render_compiled_pdf(_sample_trees(), tpl, str(out))
    problems = _pdf_bleed_report(str(out), tpl)
    assert not problems, "跨页重影未清除：\n" + "\n".join(problems)


# ---------------------------------------------------------------- 封面/目录/页码基线
def test_cover_and_toc_page_numbers(tmp_path):
    import pymupdf

    tpl = default_template()
    tpl.cover.enabled = True
    tpl.cover.title = "测试汇编"
    tpl.cover.org = "××市人民政府办公室"
    tpl.cover.date = "2026年10月"
    out = tmp_path / "汇编.pdf"
    from gwtool.core.pdfrender import render_compiled_pdf

    render_compiled_pdf(_sample_trees(), tpl, str(out))
    doc = pymupdf.open(str(out))
    try:
        assert doc.page_count >= 3
        p1 = doc[0].get_text("text")
        assert "测试汇编" in p1
        assert "目 录" not in p1 and "目  录" not in p1, "封面页混入目录标题"
        p2 = doc[1].get_text("text")
        assert p2.strip().startswith("目"), f"第 2 页应为目录页：{p2[:40]!r}"
        assert "— 1 —" not in p1 + p2, "封面/目录不应有页码"
        assert "— 1 —" in doc[2].get_text("text"), "正文首页页码应为 1"
    finally:
        doc.close()
