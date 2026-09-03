# -*- coding: utf-8 -*-
"""中文字体兜底回归测试（v1.3.0 修复 A5）。

缺陷：系统没有任何中文字体时（麒麟最小安装、精简字体镜像、CI 离屏环境），
Qt 渲染出的 PDF 正文整篇空白——文本层只剩 PyMuPDF 盖的页码，目录页码全部
退化成“—”，且全程不报任何错，用户拿到的是一份看起来正常的空白文件。

修复：pdfrender.ensure_cjk_font() 注入 PyMuPDF 自带的 Droid Sans Fallback
（Apache-2.0，随既有依赖离线分发），并加入 _font_family 回退链末尾。
"""
import pytest

from gwtool.core.model import Block, DocTree, HEADING, PARAGRAPH
from gwtool.core.template import default_template

_HEADING_1 = "关于第一季度工作的报告"
_HEADING_2 = "（一）主要成效"
_BODY = "一季度以来，各项工作平稳有序推进，重点任务完成情况良好。"


def _sample_tree() -> DocTree:
    return DocTree(title=_HEADING_1, blocks=[
        Block(type=HEADING, level=1, text=_HEADING_1),
        Block(type=PARAGRAPH, text=_BODY),
        Block(type=HEADING, level=2, text=_HEADING_2),
        Block(type=PARAGRAPH, text="项目建设部署已经完成，资金拨付截至本月底。"),
    ])


def test_ensure_cjk_font_injects_when_no_system_cjk_font(qapp, monkeypatch):
    """系统零中文字体时，必须注入 PyMuPDF 自带字体并返回可用族名。"""
    from PySide6.QtGui import QFontDatabase

    from gwtool.core import pdfrender

    monkeypatch.setattr(pdfrender, "_cjk_injected", None)
    monkeypatch.setattr(QFontDatabase, "families", staticmethod(lambda *a, **k: []))

    family = pdfrender.ensure_cjk_font()
    assert family, "系统无中文字体时应注入兜底字体，实际返回空"
    registered = set(QFontDatabase.applicationFontFamilies(pdfrender._cjk_font_id))
    assert family in registered, f"{family} 未真正注册进 Qt 字体库"


def test_font_family_falls_back_to_injected_cjk_font(qapp, monkeypatch):
    """模板指定的公文字体不存在时，回退链必须落到已注入的中文字体，
    而不是掉到只有拉丁字形的 systemFont（那正是空白 PDF 的成因）。"""
    from PySide6.QtGui import QFontDatabase

    from gwtool.core import pdfrender

    injected = "Droid Sans Fallback"
    monkeypatch.setattr(pdfrender, "_cjk_injected", injected)
    monkeypatch.setattr(QFontDatabase, "families",
                        staticmethod(lambda *a, **k: [injected]))
    assert pdfrender._font_family("仿宋_GB2312") == injected


def test_ensure_cjk_font_noop_when_system_has_cjk_font(qapp, monkeypatch):
    """系统已有中文字体时不应多此一举地注入（避免无谓的 3.5MB 字体常驻）。"""
    from PySide6.QtGui import QFontDatabase

    from gwtool.core import pdfrender

    monkeypatch.setattr(pdfrender, "_cjk_injected", None)
    monkeypatch.setattr(QFontDatabase, "families",
                        staticmethod(lambda *a, **k: ["宋体", "SimSun"]))
    assert pdfrender.ensure_cjk_font() == "", "系统已有中文字体，无需注入"
    # 回退链顺序：模板字体 -> fallback(默认 SimSun) -> 宋体 -> ...
    assert pdfrender._font_family("仿宋_GB2312") == "SimSun"


def test_rendered_pdf_has_chinese_text_layer(tmp_path, qapp):
    """端到端：渲染出的 PDF 文本层必须含中文正文与标题，目录页码不得全是“—”。

    在离屏/无字体环境下这条用例直接检验注入是否生效；在 Windows 原生环境下
    检验系统字体路径。两种环境都必须产出可读的中文 PDF。
    """
    import pymupdf as fitz

    from gwtool.core.pdfrender import _locate_headings, render_compiled_pdf

    tpl = default_template()
    tpl.red_header.enabled = True
    tpl.toc_enabled = True
    out = tmp_path / "cjk.pdf"
    render_compiled_pdf([_sample_tree()], tpl, str(out))

    doc = fitz.open(str(out))
    text_all = "\n".join(page.get_text() for page in doc)
    doc.close()

    for needle in (_HEADING_1, _HEADING_2, _BODY[:12]):
        assert needle in text_all, f"PDF 文本层缺少「{needle}」，中文正文可能整篇空白"

    pages = _locate_headings(str(out), [_HEADING_1, _HEADING_2], 0)
    assert all(p > 0 for p in pages), f"标题定位失败 {pages}，目录页码会全部显示“—”"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
