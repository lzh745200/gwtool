# -*- coding: utf-8 -*-
"""汇编生成 + 小册子 + PDF 渲染测试。"""


from gwtool.core.model import Block, DocTree
from gwtool.core.template import default_template
from gwtool.core import docxgen, booklet


def _sample_trees():
    t1 = DocTree(title="关于第一季度工作的报告")
    t1.blocks = [
        Block(type="heading", level=1, text="一、总体情况"),
        Block(type="paragraph", text="一季度各项工作平稳推进，现将有关情况报告如下。"),
        Block(type="heading", level=2, text="（一）主要成效"),
        Block(type="paragraph", text="重点任务按时完成，制度不断完善。"),
    ]
    t2 = DocTree(title="关于安全生产的通知")
    t2.blocks = [
        Block(type="heading", level=1, text="一、高度重视"),
        Block(type="paragraph", text="各单位要高度重视安全生产工作，压实责任。"),
    ]
    return [t1, t2]


def test_generate_docx_structure(tmp_path):
    tpl = default_template()
    tpl.toc_enabled = True
    out = tmp_path / "out.docx"
    docxgen.generate_docx(_sample_trees(), tpl, str(out))
    assert out.exists() and out.stat().st_size > 0

    from docx import Document
    doc = Document(str(out))
    texts = [p.text for p in doc.paragraphs]
    # 目录域存在
    assert any("TOC" in (p._p.xml or "") for p in doc.paragraphs)
    # 正文标题存在
    assert "一、总体情况" in texts
    # settings 包含 updateFields 与 evenAndOddHeaders
    xml = doc.settings.element.xml
    assert "updateFields" in xml
    assert "evenAndOddHeaders" in xml
    # 页脚包含 PAGE 域
    sec = doc.sections[0]
    assert "PAGE" in sec.footer.paragraphs[0]._p.xml
    assert "PAGE" in sec.even_page_footer.paragraphs[0]._p.xml
    # 正文行距 28 磅
    body_par = next(p for p in doc.paragraphs if p.text.startswith("一季度"))
    assert body_par.paragraph_format.line_spacing.pt == 28


def test_docx_reopenable_and_headings(tmp_path):
    """输出 docx 能被 python-docx 重新打开（OOXML 合法性），标题样式正确。"""
    out = tmp_path / "check.docx"
    docxgen.generate_docx(_sample_trees(), default_template(), str(out))
    from docx import Document
    doc = Document(str(out))
    styles = [p.style.name for p in doc.paragraphs if p.text == "一、总体情况"]
    assert styles and styles[0].startswith("Heading")


# ------------------------------------------------ P1-6：封面要素的可空契约
def _cover_docx(tmp_path, name: str, extra_lines=None):
    """生成一份带封面的 docx，返回 (路径, 段落文本列表)。"""
    tpl = default_template()
    tpl.cover.enabled = True
    tpl.cover.title = "关于汇编年度要点的通知"
    tpl.cover.org = "××市人民政府办公室"
    tpl.cover.date = "2026年8月"
    if extra_lines:
        tpl.cover.extra_lines = list(extra_lines)
    out = tmp_path / f"{name}.docx"
    docxgen.generate_docx(_sample_trees(), tpl, str(out))

    from docx import Document
    return out, [p.text for p in Document(str(out)).paragraphs]


def test_cover_extra_lines_are_rendered(tmp_path):
    """P1-6：文种/密级/紧急程度/发文字号进入封面。"""
    _out, texts = _cover_docx(
        tmp_path, "filled",
        extra_lines=["内部　急件", "通知", "×政办发〔2026〕12号"])
    assert "内部　急件" in texts
    assert "通知" in texts
    assert "×政办发〔2026〕12号" in texts
    # 单位与日期不能因为新增行而消失
    assert "××市人民政府办公室" in texts
    assert "2026年8月" in texts


def test_cover_blank_extra_lines_change_nothing(tmp_path):
    """P1-6 的硬验收判据：封面要素**全部留空**时，产物与改动前逐字节一致。

    `docxgen` 与 `pdfrender` 都是 `for line in [...]: if not line: continue`，
    所以"留空"的唯一要求就是**别往 extra_lines 里塞空串**。本用例直接钉住
    这条契约：空列表 与 空串列表 必须产出完全相同的封面段落序列。
    """
    _p1, with_empty_list = _cover_docx(tmp_path, "blank_a", extra_lines=[])
    _p2, with_empty_strs = _cover_docx(tmp_path, "blank_b",
                                       extra_lines=["", "", "", ""])
    assert with_empty_list == with_empty_strs, (
        "空串被渲染成了空段落，留空行为与改动前不一致")

    # 段落数必须与"根本没有 extra_lines 字段"时相同
    tpl = default_template()
    tpl.cover.enabled = True
    tpl.cover.title = "关于汇编年度要点的通知"
    tpl.cover.org = "××市人民政府办公室"
    tpl.cover.date = "2026年8月"
    out = tmp_path / "blank_c.docx"
    docxgen.generate_docx(_sample_trees(), tpl, str(out))
    from docx import Document
    baseline = [p.text for p in Document(str(out)).paragraphs]
    assert baseline == with_empty_list, "留空时多出了段落"


def test_pdf_cover_skips_blank_lines(qapp):
    """PDF 侧同一条契约：空串不进 `setHtml`（否则 Qt 会渲染出空行撑高封面）。

    必须带 `qapp` 夹具：`trees_to_html` 内部要 `ensure_cjk_font()`，会碰 Qt
    的字体 API。没有 QApplication 时**进程直接崩**（exit=127，无 traceback），
    只在全文件连跑时复现 —— 单跑本用例时前面的用例已经建好了 QApplication，
    所以看不出问题。这类"只在组合下崩"的坑最值得写进注释。
    """
    from gwtool.core import pdfrender

    tpl = default_template()
    tpl.cover.enabled = True
    tpl.cover.title = "关于汇编年度要点的通知"
    tpl.cover.org = "××市人民政府办公室"
    tpl.cover.date = "2026年8月"

    def _html(extra):
        tpl.cover.extra_lines = list(extra)
        return pdfrender.trees_to_html(_sample_trees(), tpl)

    assert _html([]) == _html(["", "", "", " "]), (
        "PDF 封面对空/空白行的处理与留空不一致")


def test_pdf_cover_escapes_extra_lines(qapp):
    """新增的封面行来自用户输入，必须过 `_esc` —— 未转义会让 Qt 把它当 HTML。"""
    from gwtool.core import pdfrender

    tpl = default_template()
    tpl.cover.enabled = True
    tpl.cover.title = "标题"
    tpl.cover.extra_lines = ["<b>密级</b>"]
    html = pdfrender.trees_to_html(_sample_trees(), tpl)
    assert "&lt;b&gt;密级&lt;/b&gt;" in html, "封面附加行未转义"
    assert "<b>密级</b>" not in html


def test_booklet_order_math():
    # 8 页：8,1 | 2,7 | 6,3 | 4,5
    order = booklet.booklet_order(8)
    assert order == [[8, 1], [2, 7], [6, 3], [4, 5]]
    # 4 页
    assert booklet.booklet_order(4) == [[4, 1], [2, 3]]
    # 5 页 -> 补齐 8 页
    order5 = booklet.booklet_order(5)
    assert order5[0] == [8, 1] and len(order5) == 4
    # 12 页
    o12 = booklet.booklet_order(12)
    assert o12[0] == [12, 1] and o12[1] == [2, 11] and o12[3] == [4, 9]


def test_booklet_imposition(tmp_path, qapp):
    """先生成一份多页 A4 PDF，再重排为小册子，输出页数 = 补齐到4倍数后的输出页。"""
    from gwtool.core.pdfrender import trees_to_html, _render_html_to_pdf
    tpl = default_template()
    tpl.toc_enabled = False
    tpl.page_number_enabled = False
    trees = _sample_trees() + [DocTree(title="三", blocks=[Block(text="x" * 50)] * 60)]
    html = trees_to_html(trees, tpl, toc_pages=[])
    src = tmp_path / "src.pdf"
    _render_html_to_pdf(html, tpl, str(src))
    n_src = booklet.export_a4_pdf(str(src), str(src))
    assert n_src >= 4, f"源 PDF 仅 {n_src} 页"

    out = tmp_path / "booklet.pdf"
    n_out = booklet.make_booklet(str(src), str(out))
    assert n_out % 2 == 0 and n_out >= n_src // 2
    import pymupdf as fitz
    d = fitz.open(str(out))
    assert d.page_count == n_out
    # 两页并排：输出宽约为源页宽两倍
    w, h = d[0].rect.width, d[0].rect.height
    assert w > h
    d.close()


def test_pdf_render_full(tmp_path, qapp):
    """两遍渲染：目录页码真实填充 + 外侧页码盖章。"""
    from gwtool.core.pdfrender import render_compiled_pdf
    tpl = default_template()
    tpl.red_header.enabled = True
    tpl.toc_enabled = True
    out = tmp_path / "compiled.pdf"
    render_compiled_pdf(_sample_trees(), tpl, str(out))
    import pymupdf as fitz
    d = fitz.open(str(out))
    text_all = "\n".join(d[i].get_text() for i in range(d.page_count))
    d.close()
    # 页码格式存在
    assert "— 1 —" in text_all.replace(" ", " ") or "—1—" in text_all.replace(" ", "")
    # 目录标题出现
    assert "目" in text_all and "录" in text_all


def test_formatter_and_differ():
    from gwtool.core.formatter import run_full_cleanup
    from gwtool.core.differ import diff_to_html
    text = "一、标题1\n\n\n正文　　带多余空格 。\n2、3个数字混用\n（一）子项"
    new_text, log = run_full_cleanup(text)
    assert "多余空格" in "".join(log) or new_text != text
    html = diff_to_html("第一段内容。\n第二段。", "第一段修改后内容。\n第二段。\n新增段。")
    assert "ins" in html and "del" in html
