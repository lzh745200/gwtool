# -*- coding: utf-8 -*-
"""v1.10 B 批 PDF/产物保真回归（P1-02 / P1-04 / P1-08 / P1-11）。

全部用**真实产物**验收，不 mock 渲染：
  P1-02  行距：模板 line_spacing_pt 20/28/40 三档以前渲染出的 PDF 逐页完全相同
         （恒 ~20.6pt、30~31 行/页）——Qt 不吃 CSS `line-height`。现在按
         `QTextBlockFormat.setLineHeight(FixedHeight)` 逐块固定，三档必须可区分、
         且 28 磅时每页 22 行（公文版心口径）；目录页码（两遍渲染）与外侧页码
         盖章不得被改坏。
  P1-08  PDF 解析：旧实现一行一块（12 自然段→26 段），现在要按版式聚类还原
         自然段，并把与正文同字号的「一、总体要求」识别为标题。
  P1-04  docx 红头：必须紧跟目录、与正文首页同页（旧实现独占一页，且与内置
         PDF 渲染器的页序相反）。
  P1-11  docx 水印：开了 evenAndOddHeaders 就必须同时写入偶数页页眉（旧实现
         只在奇数页，实测偶数页 0 条矢量路径）。
"""
from __future__ import annotations

import re
import zipfile

import pymupdf as fitz
from docx.oxml.ns import qn

from gwtool.core import docxgen, pdfrender
from gwtool.core.model import Block, DocTree, HEADING, PARAGRAPH
from gwtool.core.parsers.pdf_parser import parse_pdf
from gwtool.core.template import DocTemplate, default_template

_CN = "一二三四五六七八九十"


# ---------------------------------------------------------------- 公共工具
def _tree(tag: str = "材料", n_para: int = 40) -> DocTree:
    t = DocTree(title=f"{tag}文件的通知")
    t.blocks.append(Block(type=HEADING, level=1, text=f"{tag}文件的通知"))
    t.blocks.append(Block(type=HEADING, level=2, text="一、总体要求"))
    for i in range(n_para):
        t.blocks.append(Block(type=PARAGRAPH,
                              text=f"第{i + 1}段：为深入贯彻落实上级部署，"
                                   f"现就有关事项通知如下，请认真抓好落实。"))
    return t


def _page_grid(pdf, pages=(1, 2)) -> list[tuple[int, int, float]]:
    """(页码, 行数, 行距pt)。页码行「— N —」不计入。"""
    out = []
    doc = fitz.open(str(pdf))
    try:
        for pno in pages:
            if pno >= doc.page_count:
                continue
            ys: list[float] = []
            for blk in doc[pno].get_text("dict")["blocks"]:
                for ln in blk.get("lines", []):
                    text = "".join(s["text"] for s in ln["spans"]).strip()
                    if not text or re.fullmatch(r"—\s*\d+\s*—", text):
                        continue
                    ys.append(ln["bbox"][1])
            ys.sort()
            pitch = (ys[-1] - ys[0]) / (len(ys) - 1) if len(ys) > 1 else 0.0
            out.append((pno + 1, len(ys), round(pitch, 1)))
    finally:
        doc.close()
    return out


def _render(tmp_path, name: str, tpl, trees=None):
    out = tmp_path / name
    pdfrender.render_compiled_pdf(trees or [_tree()], tpl, str(out))
    return out


# ================================================================ P1-02 行距
class TestPdfLineSpacing:
    def test_three_tiers_are_distinguishable(self, tmp_path, qapp):
        """20/28/40 三档必须渲染出不同行距与行数（旧实现三档完全一致）。"""
        rows = {}
        for spacing in (20, 28, 40):
            tpl = DocTemplate()
            tpl.line_spacing_pt = spacing
            tpl.toc_enabled = False
            pdf = _render(tmp_path, f"ls{spacing}.pdf", tpl)
            grid = _page_grid(pdf)
            assert grid, f"line_spacing={spacing} 没有量到正文行"
            rows[spacing] = grid
        # 行距随模板单调增大
        p20 = rows[20][0][2]
        p28 = rows[28][0][2]
        p40 = rows[40][0][2]
        assert p20 < p28 < p40, f"三档行距未区分：{p20}/{p28}/{p40}"
        # 每页行数随行距单调减少
        n20, n28, n40 = rows[20][0][1], rows[28][0][1], rows[40][0][1]
        assert n40 < n28 < n20, f"每页行数未随行距变化：{n20}/{n28}/{n40}"

    def test_28pt_matches_template_and_gives_22_lines(self, tmp_path, qapp):
        """模板 28 磅 → 实测行距 28±1.5pt、每页 22±1 行（旧实现 20.6pt / 30 行）。"""
        tpl = DocTemplate()          # 默认 line_spacing_pt=28、body 16pt
        tpl.toc_enabled = False
        pdf = _render(tmp_path, "line28.pdf", tpl)
        grid = _page_grid(pdf, pages=(0, 1, 2))
        body = [g for g in grid if g[1] >= 10]
        assert body, f"没有找到正文页：{grid}"
        for _pno, lines, pitch in body:
            assert abs(pitch - 28.0) <= 1.5, (
                f"行距 {pitch}pt 与模板 28pt 不符（旧实现恒为 20.6pt）")
            assert 21 <= lines <= 23, f"每页 {lines} 行，应为 22 行左右"

    def test_40pt_reduces_lines_per_page(self, tmp_path, qapp):
        """40 磅时每页行数应明显少于 28 磅（旧实现两者相同）。"""
        tpl28 = DocTemplate()
        tpl28.toc_enabled = False
        tpl40 = DocTemplate()
        tpl40.toc_enabled = False
        tpl40.line_spacing_pt = 40
        g28 = _page_grid(_render(tmp_path, "t28.pdf", tpl28))
        g40 = _page_grid(_render(tmp_path, "t40.pdf", tpl40))
        assert g40[0][1] < g28[0][1], f"40 磅每页 {g40[0][1]} 行，未少于 28 磅的 {g28[0][1]} 行"
        assert abs(g40[0][2] - 40.0) <= 2.5, f"40 磅实测 {g40[0][2]}pt"

    def test_type_area_is_156mm(self, tmp_path, qapp):
        """版心宽度必须落在 156mm（442.2pt）附近（documentMargin 已清零）。"""
        tpl = DocTemplate()
        tpl.toc_enabled = False
        tpl.red_header.enabled = True          # 红头带一条 width=100% 的红色分隔线
        tpl.red_header.org = "××单位文件"
        pdf = _render(tmp_path, "typearea.pdf", tpl, trees=[_tree(n_para=2)])
        doc = fitz.open(str(pdf))
        try:
            widths = []
            for dr in doc[0].get_drawings():
                rect = dr["rect"]
                if dr.get("fill") and rect.width > 100:
                    widths.append(rect.width)
            assert widths, "没有量到全宽元素（红色分隔线）"
            # 156mm = 442.2pt；留 2pt 容差（Qt 的设备像素取整）
            assert abs(max(widths) - 442.2) <= 3.0, f"版心宽 {max(widths):.1f}pt 偏离 156mm"
        finally:
            doc.close()

    def test_toc_page_numbers_survive_new_line_spacing(self, tmp_path, qapp):
        """两遍渲染算出的目录页码必须仍与正文实际页一致（换行距后也成立）。"""
        trees = []
        for i in range(5):
            t = DocTree(title=f"材料{i + 1}的通知")
            t.blocks.append(Block(type=HEADING, level=1, text=f"材料{i + 1}的通知"))
            for k in range(2):
                # 标题文本必须互不相同：目录页码是按"同名标题按顺序取第一次出现"
                # 消歧的，测试里用重名会把消歧逻辑和断言混在一起。
                t.blocks.append(Block(type=HEADING, level=2,
                                      text=f"（{_CN[k]}）材料{i + 1}分项要求{k + 1}"))
                for j in range(4):
                    t.blocks.append(Block(
                        type=PARAGRAPH,
                        text=f"第{j + 1}段：为深入贯彻落实上级部署，现就有关事项通知如下。"))
            trees.append(t)
        tpl = DocTemplate()            # 默认 28 磅 + 目录 + 页码
        pdf = _render(tmp_path, "toc.pdf", tpl, trees=trees)
        heads = pdfrender.collect_headings(trees, tpl)
        toc_pages = max(1, len(pdfrender._toc_chunks(len(heads))))
        front = 1 if tpl.cover.enabled else 0
        front += toc_pages

        doc = fitz.open(str(pdf))
        try:
            pairs = []
            for pno in range(front - toc_pages, front):
                lines = [ln.strip() for ln in doc[pno].get_text("text").splitlines()
                         if ln.strip()]
                i = 0
                while i < len(lines) - 1:
                    if re.fullmatch(r"\d+", lines[i + 1]):
                        pairs.append((lines[i], int(lines[i + 1])))
                        i += 2
                    else:
                        i += 1
            assert len(pairs) == len(heads), f"目录条目 {len(pairs)} != 标题 {len(heads)}"
            actual = {}
            for text, _lv in heads:
                for pno in range(front, doc.page_count):
                    if doc[pno].search_for(text[:12]):
                        # v1.15：封面/目录不编号，正文从 1 起算 —— 目录里写的是
                        # **纸面页码**，比物理页号少 front
                        actual[text] = pno + 1 - front
                        break
            wrong = [(t, pg, actual.get(t)) for t, pg in pairs if actual.get(t) != pg]
            assert not wrong, f"目录页码与正文不一致：{wrong[:4]}"
        finally:
            doc.close()

    def test_table_blocks_keep_natural_line_height(self, tmp_path, qapp):
        """表格块不套固定行高（否则目录表会被撑高、分页假设失效）。"""
        from PySide6.QtGui import QTextDocument

        t = DocTree(title="表格材料")
        t.blocks.append(Block(type=PARAGRAPH, text="表格之前的正文段落。"))
        t.blocks.append(Block(type="table", rows=[["序号", "内容"], ["1", "甲"], ["2", "乙"]]))
        tpl = DocTemplate()
        html = pdfrender.trees_to_html([t], tpl, toc_pages=[])
        doc = QTextDocument()
        doc.setHtml(html)
        ranges = pdfrender._table_block_ranges(doc)
        assert ranges, "应能识别出表格占用的块区间"
        changed = pdfrender._apply_fixed_line_height(doc, tpl)
        assert changed > 0, "正文段落应被设置固定行高"
        fixed_type = pdfrender._fixed_height_enum()
        blk = doc.begin()
        while blk.isValid():
            inside = any(lo <= blk.blockNumber() <= hi for lo, hi in ranges)
            if inside:
                assert blk.blockFormat().lineHeightType() != fixed_type, (
                    f"表格块 {blk.blockNumber()} 被套了固定行高")
            blk = blk.next()


# ================================================================ P1-08 PDF 解析
_P1 = ("为深入贯彻落实上级部署要求，进一步加强和改进新形势下机关公文处理工作，"
       "切实提高公文质量和工作效率，现就有关事项通知如下，请各单位认真抓好落实。")
_P2 = ("各单位要高度重视公文处理工作，主要负责同志要亲自抓、负总责，明确专人负责，"
       "确保各项要求落到实处、取得实效。")


def _wrap(text: str, n: int) -> list[str]:
    return [text[i:i + n] for i in range(0, len(text), n)]


def _make_flat_pdf(path, paras: int = 12, chars: int = 27) -> int:
    """无首行缩进、按 N 字硬折行（旧实现在这类样张上 12 段→26 块）。"""
    doc = fitz.open()
    try:
        page = doc.new_page()
        page.insert_text((170, 80), "关于加强公文处理工作的通知",
                         fontname="china-s", fontsize=22)
        y = 120
        for i in range(paras):
            body = _P1 if i % 2 == 0 else _P2
            for seg in _wrap(body, chars):
                page.insert_text((90, y), seg, fontname="china-s", fontsize=16)
                y += 28
        doc.save(str(path))
    finally:
        doc.close()
    return paras


def _make_indented_pdf(path, paras: int = 12, chars: int = 27) -> int:
    """真实公文版式：首行缩进 2 字、续行顶格、同字号编号标题、页脚页码。"""
    doc = fitz.open()
    try:
        page = doc.new_page()
        page.insert_text((170, 80), "关于加强公文处理工作的通知",
                         fontname="china-s", fontsize=22)
        y, left = 130, 90
        indent = left + 32                      # 2 字 = 32pt
        for i in range(paras):
            if i % 4 == 0:
                page.insert_text((indent, y), f"一、总体要求{i // 4 + 1}",
                                 fontname="china-s", fontsize=16)
                y += 28
            body = _P1 if i % 2 == 0 else _P2
            for k, seg in enumerate(_wrap(body, chars)):
                page.insert_text((indent if k == 0 else left, y), seg,
                                 fontname="china-s", fontsize=16)
                y += 28
        page.insert_text((285, 800), "— 1 —", fontname="china-s", fontsize=14)
        doc.save(str(path))
    finally:
        doc.close()
    return paras


class TestPdfParserSegmentation:
    def test_flat_pdf_merges_lines_into_paragraphs(self, tmp_path):
        """无缩进样张：12 自然段 → 段落块数应接近 12（旧实现 26）。"""
        pdf = tmp_path / "flat.pdf"
        _make_flat_pdf(pdf)
        tree = parse_pdf(str(pdf))
        paras = [b for b in tree.blocks if b.type == PARAGRAPH]
        assert 8 <= len(paras) <= 14, f"段落块 {len(paras)}（自然段 12，旧实现 26）"
        # 段落长度不再是"一行的 27 字"
        lengths = [len(p.text) for p in paras]
        assert max(lengths) > 60, f"段落没有被合并，长度分布={lengths}"

    def test_indented_pdf_paragraphs_and_headings(self, tmp_path):
        """带缩进样张：段落数接近自然段；同字号「一、总体要求」须识别为标题。"""
        pdf = tmp_path / "indented.pdf"
        _make_indented_pdf(pdf)
        tree = parse_pdf(str(pdf))
        paras = [b for b in tree.blocks if b.type == PARAGRAPH]
        assert 10 <= len(paras) <= 16, f"段落块 {len(paras)}（自然段 12）"
        heads = [b for b in tree.blocks if b.type == HEADING]
        assert heads and heads[0].level == 1
        assert heads[0].text == "关于加强公文处理工作的通知"
        numbered = [b for b in heads if b.level == 2]
        assert len(numbered) == 3, f"同字号编号标题未识别：{[b.text for b in heads]}"
        assert all(b.text.startswith("一、总体要求") for b in numbered)
        assert tree.title == "关于加强公文处理工作的通知"

    def test_page_number_lines_are_dropped(self, tmp_path):
        """页脚「— 1 —」不能作为段落混进汇编材料。"""
        pdf = tmp_path / "indented2.pdf"
        _make_indented_pdf(pdf, paras=4)
        tree = parse_pdf(str(pdf))
        texts = [b.text for b in tree.blocks]
        assert not any(re.fullmatch(r"—\s*\d+\s*—", t) for t in texts), texts
        assert not any(t.strip() == "— 1 —" for t in texts)

    def test_short_single_line_paragraphs_are_not_merged(self, tmp_path):
        """每段只有一行的短段落不得被并成一段（旧实现/过度合并都会并）。"""
        doc = fitz.open()
        try:
            page = doc.new_page()
            y = 120
            for i in range(12):
                page.insert_text((122, y), f"第{i + 1}项要求。",
                                 fontname="china-s", fontsize=16)
                y += 28
            doc.save(str(tmp_path / "short.pdf"))
        finally:
            doc.close()
        tree = parse_pdf(str(tmp_path / "short.pdf"))
        paras = [b for b in tree.blocks if b.type == PARAGRAPH]
        assert len(paras) == 12, f"短段落被合并：{len(paras)} 段"

    def test_paragraph_spanning_pages_is_merged(self, tmp_path):
        """跨页续排的同一段应合并为 1 段（上一行写满则视作续行）。"""
        segs = _wrap(_P1 * 3, 27)
        half = len(segs) // 2
        doc = fitz.open()
        try:
            page = doc.new_page()
            y = 120
            for seg in segs[:half]:
                page.insert_text((90, y), seg, fontname="china-s", fontsize=16)
                y += 28
            page2 = doc.new_page()
            y = 120
            for seg in segs[half:]:
                page2.insert_text((90, y), seg, fontname="china-s", fontsize=16)
                y += 28
            doc.save(str(tmp_path / "cross.pdf"))
        finally:
            doc.close()
        tree = parse_pdf(str(tmp_path / "cross.pdf"))
        paras = [b for b in tree.blocks if b.type == PARAGRAPH]
        assert len(paras) == 1, f"跨页段落被拆成 {len(paras)} 段"


# ================================================================ P1-04 红头页序
def _full_template() -> DocTemplate:
    tpl = default_template()
    tpl.cover.enabled = True
    tpl.cover.title = "公文汇编"
    tpl.cover.org = "××单位办公室"
    tpl.cover.date = "二〇二六年八月"
    tpl.red_header.enabled = True
    tpl.red_header.org = "××单位文件"
    tpl.red_header.doc_number = "×政发〔2026〕1号"
    tpl.toc_enabled = True
    return tpl


def _document_xml(path) -> str:
    with zipfile.ZipFile(path) as zf:
        return zf.read("word/document.xml").decode("utf-8")


class TestDocxRedHeaderLayout:
    def test_toc_comes_before_red_header(self, tmp_path):
        """页序必须是 封面 → 目录 → 红头+正文（旧实现把红头夹在中间）。"""
        out = tmp_path / "order.docx"
        docxgen.generate_docx([_tree(n_para=6)], _full_template(), str(out))
        xml = _document_xml(out)
        i_toc = xml.find(" TOC ")
        i_org = xml.find("××单位文件")
        i_body = xml.find("一、总体要求")
        assert -1 < i_toc < i_org < i_body, (
            f"顺序错误：TOC={i_toc} 红头={i_org} 正文={i_body}")

    def test_red_header_shares_page_with_body(self, tmp_path):
        """红头与正文首页之间不得有分页符（旧实现红头独占一页）。"""
        out = tmp_path / "samepage.docx"
        docxgen.generate_docx([_tree(n_para=6)], _full_template(), str(out))
        xml = _document_xml(out)
        i_org = xml.find("××单位文件")
        i_body = xml.find("一、总体要求")
        between = xml[i_org:i_body]
        assert 'w:type="page"' not in between, "红头与正文之间仍有分页符"
        # 发文字号也在同一页（红头三要素不拆开）
        assert "×政发〔2026〕1号" in between

    def test_cover_still_separate_page(self, tmp_path):
        """封面与目录之间仍必须有分页符（回归：别把封面也并进去）。"""
        out = tmp_path / "cover.docx"
        docxgen.generate_docx([_tree(n_para=4)], _full_template(), str(out))
        xml = _document_xml(out)
        cover = xml.find("公文汇编")
        toc = xml.find(" TOC ")
        assert cover < toc
        assert 'w:type="page"' in xml[cover:toc], "封面后缺少分页符"

    def test_without_toc_red_header_follows_cover(self, tmp_path):
        """关掉目录时：封面 → 分页 → 红头+正文（不能把红头单独甩到一页）。"""
        tpl = _full_template()
        tpl.toc_enabled = False
        out = tmp_path / "notoc.docx"
        docxgen.generate_docx([_tree(n_para=4)], tpl, str(out))
        xml = _document_xml(out)
        i_org = xml.find("××单位文件")
        i_body = xml.find("一、总体要求")
        assert -1 < i_org < i_body
        assert 'w:type="page"' not in xml[i_org:i_body]


# ================================================================ P1-11 水印奇偶页
class TestDocxWatermarkOddEven:
    def _docx(self, tmp_path, text="征求意见稿"):
        tpl = _full_template()
        tpl.watermark_text = text
        out = tmp_path / "wm.docx"
        docxgen.generate_docx([_tree(n_para=8)], tpl, str(out))
        return out

    def test_even_page_header_receives_watermark(self, tmp_path):
        """开 evenAndOddHeaders 时，偶数页页眉必须也有水印 VML（旧实现没有）。"""
        out = self._docx(tmp_path)
        with zipfile.ZipFile(out) as zf:
            headers = {n: zf.read(n).decode("utf-8")
                       for n in zf.namelist() if re.match(r"word/header\d*\.xml$", n)}
        assert len(headers) >= 2, f"只有 {len(headers)} 个页眉部件：{sorted(headers)}"
        for name, xml in headers.items():
            assert "v:shape" in xml, f"{name} 里没有水印图形"
            assert "征求意见稿" in xml, f"{name} 里没有水印文字"

    def test_section_references_default_and_even_header(self, tmp_path):
        """sectPr 必须同时引用 default（奇数）与 even（偶数）页眉。"""
        out = self._docx(tmp_path)
        xml = _document_xml(out)
        sect = re.search(r"<w:sectPr.*?</w:sectPr>", xml, re.S)
        assert sect, "找不到 sectPr"
        types = set(re.findall(r'<w:headerReference w:type="(\w+)"', sect.group(0)))
        assert {"default", "even"} <= types, f"页眉引用类型={types}"

    def test_no_watermark_keeps_single_header(self, tmp_path):
        """不开水印时不凭空造出偶数页页眉（产物与改动前一致）。"""
        tpl = _full_template()
        tpl.watermark_text = ""
        out = tmp_path / "nowm.docx"
        docxgen.generate_docx([_tree(n_para=4)], tpl, str(out))
        with zipfile.ZipFile(out) as zf:
            headers = [n for n in zf.namelist()
                       if re.match(r"word/header\d*\.xml$", n)]
        assert headers == [], f"未开��印却生成了页眉：{headers}"

    def test_mirror_is_idempotent(self, tmp_path):
        """重复调用镜像函数不得叠加出两份水印（页眉里只留一段 pict）。"""
        out = self._docx(tmp_path)
        docxgen._mirror_watermark_to_even_header(str(out))
        docxgen._mirror_watermark_to_even_header(str(out))
        from docx import Document
        doc = Document(str(out))
        sec = doc.sections[0]
        picts = [p for p in sec.even_page_header._element.findall(qn("w:p"))
                 if p.find(".//" + qn("w:pict")) is not None]
        assert len(picts) == 1, f"偶数页页眉里有 {len(picts)} 段水印"
