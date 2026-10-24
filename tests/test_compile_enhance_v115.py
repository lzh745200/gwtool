# -*- coding: utf-8 -*-
"""v1.15 一键汇编产物规范回归（T1：封面/目录/页码/字体/A4+A3 小册子）。

验收口径全部用**真实产物 + PyMuPDF 量化**：
  P1  封面页存在且**无页码**；目录页**无页码**；正文首页是"— 1 —"且连续；
  P2  docx 字体分层（run 级 `w:rFonts@eastAsia`）：
        材料/公文标题 小标宋（22pt）、一级 黑体、二级 楷体、三级 仿宋加粗、
        正文 仿宋；封面题字 小标宋；
  P3  docx 分节：封面/目录节不编号，正文节 `w:pgNumType@start=1`；
  P4  A4 产物 595×842pt；A3 骑马钉 1190×842pt、页数偶数、左右两页按
      `booklet_order` 落位；
  P5  目录页码 = 纸面页码（正文从 1 起），与标题实际所在页一致；
  P6  健壮性：块类型/层级未规范化（全 paragraph、level=0/9、空标题）不崩。
"""
from __future__ import annotations

import re
import zipfile

import pymupdf as fitz
from docx import Document

from gwtool.core import booklet, docxgen, pdfrender
from gwtool.core.model import Block, DocTree, HEADING, PARAGRAPH
from gwtool.core.template import DocTemplate, cover_visible, default_template

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# 页面尺寸（pt）：A4 纵向 / A3 横向
A4_W, A4_H = 595.2755905511812, 841.8897637795277
A3_W, A3_H = A4_W * 2, A4_H


# ---------------------------------------------------------------- 公共样张
def _trees(with_headings: bool = True, para: int = 10) -> list[DocTree]:
    """两篇材料：第一篇带 h1/h2/h3，第二篇只有正文（模拟未规范化）。"""
    t1 = DocTree(title="关于加强公文处理工作的通知")
    t1.blocks = []
    if with_headings:
        t1.blocks += [Block(type=HEADING, level=1, text="一、总体要求"),
                      Block(type=HEADING, level=2, text="（一）提高认识"),
                      Block(type=HEADING, level=3, text="1. 落实责任")]
    else:
        t1.blocks += [Block(type=HEADING, level=1, text="一、总体要求")]
    t1.blocks += [Block(type=PARAGRAPH,
                        text="第一段：为深入贯彻落实上级部署，现就有关事项通知如下。" * 2)
                  for _ in range(para)]
    t2 = DocTree(title="关于规范材料汇编工作的意见")
    t2.blocks = [Block(type=PARAGRAPH,
                       text="第二篇正文：各单位要高度重视，认真抓好落实。" * 2)
                 for _ in range(para + 2)]
    return [t1, t2]


def _render(tmp_path, name: str = "out.pdf", trees=None, tpl=None):
    out = tmp_path / name
    pdfrender.render_compiled_pdf(trees or _trees(), tpl or default_template(), str(out))
    return out


def _page_numbers(pdf) -> list[list[int]]:
    """逐页取"— N —"形式的纸面页码（可能同一页多处）。"""
    doc = fitz.open(str(pdf))
    try:
        return [[int(m) for m in re.findall(r"—\s*(\d+)\s*—", doc[p].get_text("text"))]
                for p in range(doc.page_count)]
    finally:
        doc.close()


def _front_pages(tpl, n_headings: int) -> int:
    """前置页数：封面（有内容才出页）+ 目录分片页数。"""
    front = 1 if cover_visible(tpl) else 0
    if tpl.toc_enabled:
        front += max(1, len(pdfrender._toc_chunks(n_headings)))
    return front


def _east_asia(par) -> str:
    """段落首个 run 的 `w:rFonts@eastAsia`（docx 中文字体就靠它）。"""
    if not par.runs:
        return ""
    rpr = par.runs[0]._element.find(_W + "rPr")
    if rpr is None:
        return ""
    rfonts = rpr.find(_W + "rFonts")
    return (rfonts.get(_W + "eastAsia") or "") if rfonts is not None else ""


def _run_pt(par):
    run = par.runs[0] if par.runs else None
    return run.font.size.pt if (run is not None and run.font.size) else None


# ================================================================ 封面 / 目录 / 页码
class TestCoverAndPageNumbers:
    def test_default_template_carries_cover(self):
        """v1.15：内置模板默认带封面，"一键汇编"不配置也出封面页。"""
        tpl = default_template()
        assert tpl.cover.enabled is True
        assert cover_visible(tpl) is True
        assert tpl.cover.title.strip(), "默认封面标题不能为空，否则封面是空白页"
        # 裸模板（供归档清单等复用）仍不出封面：要素全空 → 不占页
        assert DocTemplate().cover.enabled is False
        assert cover_visible(DocTemplate()) is False

    def test_cover_page_has_no_page_number(self, tmp_path, qapp):
        """PDF 第 1 页是封面，且不得出现任何"— N —"页码。"""
        tpl = default_template()
        tpl.cover.title = "2026 年度公文汇编"
        tpl.cover.org = "××单位办公室"
        tpl.cover.date = "二〇二六年十月"
        pdf = _render(tmp_path, "cover.pdf", tpl=tpl)
        doc = fitz.open(str(pdf))
        try:
            cover_text = doc[0].get_text("text")
            assert "2026 年度公文汇编" in cover_text.replace("\n", " "), cover_text[:80]
            assert re.search(r"—\s*\d+\s*—", cover_text) is None, "封面不应有页码"
        finally:
            doc.close()

    def test_toc_page_has_no_page_number(self, tmp_path, qapp):
        """目录页（封面之后）同样不编号。"""
        tpl = default_template()
        pdf = _render(tmp_path, "toc.pdf", tpl=tpl)
        heads = pdfrender.collect_headings(_trees(), tpl)
        front = _front_pages(tpl, len(heads))
        doc = fitz.open(str(pdf))
        try:
            toc_pages = list(range(1, front))       # 第 0 页是封面
            assert toc_pages, "本用例要求模板带封面+目录"
            for pno in toc_pages:
                text = doc[pno].get_text("text")
                assert "目" in text and "录" in text, f"第{pno + 1}页不是目录页"
                assert re.search(r"—\s*\d+\s*—", text) is None, (
                    f"目录页（第 {pno + 1} 页）不应有页码")
        finally:
            doc.close()

    def test_body_numbering_starts_at_one_and_is_continuous(self, tmp_path, qapp):
        """正文首页"— 1 —"，之后 2/3/4… 连续；封面与目录页无页码。"""
        tpl = default_template()
        pdf = _render(tmp_path, "body.pdf", tpl=tpl)
        nums = _page_numbers(pdf)
        heads = pdfrender.collect_headings(_trees(), tpl)
        front = _front_pages(tpl, len(heads))
        assert len(nums) > front, f"产物只有 {len(nums)} 页，没有正文页"
        for pno in range(front):
            assert nums[pno] == [], f"前置页（第{pno + 1}页）不该有页码：{nums[pno]}"
        body = [n[0] for n in nums[front:]]
        assert body[0] == 1, f"正文首页页码应为 1，实际 {body[0]}"
        assert body == list(range(1, len(body) + 1)), f"正文页码不连续：{body}"

    def test_toc_numbers_match_printed_page_numbers(self, tmp_path, qapp):
        """目录里写的必须是**纸面页码**（正文从 1 起），不是 PDF 物理页序。"""
        tpl = default_template()
        trees = _trees()
        pdf = _render(tmp_path, "tocnum.pdf", trees=trees, tpl=tpl)
        heads = pdfrender.collect_headings(trees, tpl)
        front = _front_pages(tpl, len(heads))
        doc = fitz.open(str(pdf))
        try:
            lines = [ln.strip() for ln in doc[front - 1].get_text("text").splitlines()
                     if ln.strip()]
            pairs, i = [], 0
            while i < len(lines) - 1:
                if re.fullmatch(r"\d+", lines[i + 1]):
                    pairs.append((lines[i], int(lines[i + 1])))
                    i += 2
                else:
                    i += 1
            assert len(pairs) == len(heads), f"目录条目 {len(pairs)} != 标题 {len(heads)}"
            for text, printed in pairs:
                for pno in range(front, doc.page_count):
                    if doc[pno].search_for(text[:12]):
                        expect = pno + 1 - front
                        assert printed == expect, (
                            f"目录「{text[:14]}」写 {printed}，实际纸面页码 {expect}")
                        break
        finally:
            doc.close()

    def test_no_page_number_when_disabled(self, tmp_path, qapp):
        """关掉页码时全篇都不盖（回归：别把 skip 逻辑写成"总是盖前 N 页"）。"""
        tpl = default_template()
        tpl.page_number_enabled = False
        pdf = _render(tmp_path, "nonum.pdf", tpl=tpl)
        assert all(n == [] for n in _page_numbers(pdf))

    def test_paragraph_only_trees_do_not_crash(self, tmp_path, qapp):
        """T2 规范化未接入（块全是 paragraph、level 异常）时不得崩。"""
        trees = _trees(with_headings=False)
        trees[0].blocks.insert(0, Block(type=HEADING, level=0, text="零级标题"))
        trees[0].blocks.insert(1, Block(type=HEADING, level=9, text="九级标题"))
        trees[0].blocks.insert(2, Block(type=HEADING, level=2, text="   "))
        tpl = default_template()
        pdf = _render(tmp_path, "robust.pdf", trees=trees, tpl=tpl)
        assert fitz.open(str(pdf)).page_count >= 2
        heads = pdfrender.collect_headings(trees, tpl)
        assert all(text.strip() for text, _lv in heads), "空标题不该进目录"
        assert all(1 <= lv <= 3 for _t, lv in heads), f"目录层级越界：{heads}"
        # docx 侧同样不得因 level=0/9 抛 KeyError
        out = tmp_path / "robust.docx"
        docxgen.generate_docx(trees, tpl, str(out))
        assert out.exists() and out.stat().st_size > 0


# ================================================================ 分页洁净度
def _lines_of(pdf, pno: int) -> list[str]:
    doc = fitz.open(str(pdf))
    try:
        return [ln.strip() for ln in doc[pno].get_text("text").splitlines() if ln.strip()]
    finally:
        doc.close()


def _squeeze(text: str) -> str:
    return "".join(text.split())


class TestPageBreakCleanliness:
    """封面页跨页重影：Qt 手动逐页绘制会把"下一页首行"再画到上一页底部。

    实测（v1.15 收尾）：3 页产物的封面页文本曾是
    ``材料汇编 目 录`` —— 目录标题既在目录页首行（对），又被画到封面页
    版心最下方（错，且像素级可见）。修法见
    `pdfrender._strip_cover_page_bleed`（只清封面页，正文页不做同样处理：
    公文套话在相邻两页重复是常态，按"文本相同"删会误删正文）。
    """

    def test_cover_page_contains_no_toc_title(self, tmp_path, qapp):
        """有封面 + 有目录：封面页只有封面要素，目录标题只在目录页。"""
        tpl = default_template()
        tpl.cover.title = "测试汇编"
        tpl.cover.org = "××市人民政府办公室"
        tpl.cover.date = "2026年10月"
        pdf = _render(tmp_path, "clean_cover.pdf", tpl=tpl)
        cover = _squeeze(" ".join(_lines_of(pdf, 0)))
        assert "目录" not in cover, f"封面页混入了目录标题：{_lines_of(pdf, 0)}"
        assert "测试汇编" in cover, f"封面内容缺失：{cover}"
        toc_lines = _lines_of(pdf, 1)
        assert _squeeze(toc_lines[0]) == "目录", f"目录页首行不是目录标题：{toc_lines[:3]}"

    def test_toc_title_is_first_line_without_cover(self, tmp_path, qapp):
        """无封面 + 有目录：第 1 页首行就是目录标题（无前置页时也不得漂移）。"""
        tpl = DocTemplate()          # 裸模板：不启用封面
        tpl.toc_enabled = True
        pdf = _render(tmp_path, "clean_nocover.pdf", tpl=tpl)
        first_page = _lines_of(pdf, 0)
        assert _squeeze(first_page[0]) == "目录", f"首行不是目录标题：{first_page[:3]}"

    def test_no_page_repeats_next_page_first_line(self, tmp_path, qapp):
        """**每一页**底部窄带都不得出现"下一页首行"的重影。

        样张用 60 个**单行且互不相同**的段落：公文套话的重复行会干扰判据
        （``下。`` 这类折行尾巴在相邻页重复出现是正常的），所以这里让每一行
        文本都唯一 —— 底部窄带里出现"下一页首行"就一定是重影，不是正文。
        这正是旧实现（整篇 translate + ctx.clip）栽的地方：60 段样张实测有
        一页会多出 1 行（23 行 > 版心 22 行容量），封面页还会印出目录表。
        """
        tpl = default_template()
        trees = [DocTree(title="关于做好年度工作的通知")]
        trees[0].blocks = [Block(type=HEADING, level=1, text="一、总体要求")]
        for i in range(60):
            trees[0].blocks.append(Block(
                type=PARAGRAPH, text="第%02d段甲乙丙丁戊己庚辛壬癸子丑寅卯。" % (i + 1)))
        pdf = _render(tmp_path, "clean_any.pdf", trees=trees, tpl=tpl)
        top_band = tpl.margin_top_mm * 2.834645669 + 80
        bottom_band = A4_H - tpl.margin_bottom_mm * 2.834645669 - 80
        doc = fitz.open(str(pdf))
        try:
            def spans(pno):
                out = []
                for blk in doc[pno].get_text("dict")["blocks"]:
                    for ln in blk.get("lines", []):
                        for sp in ln.get("spans", []):
                            txt = (sp.get("text") or "").strip()
                            if txt and not re.fullmatch(r"—\s*\d+\s*—", txt):
                                out.append((sp["bbox"], txt))
                return out

            assert doc.page_count >= 4, f"样张只有 {doc.page_count} 页，不是多页场景"
            for pno in range(doc.page_count - 1):
                next_top = {_squeeze(t) for box, t in spans(pno + 1)
                            if box[1] < top_band}
                for box, txt in spans(pno):
                    assert not (box[3] > bottom_band and _squeeze(txt) in next_top), (
                        f"第{pno + 1}页底部重影了下一页首行「{txt}」（y={box[1]:.0f}）")
        finally:
            doc.close()

    def test_each_page_respects_type_area_capacity(self, tmp_path, qapp):
        """每页正文行数不得超过版心容量（28 磅 → 22 行），重影会顶破这条线。"""
        tpl = default_template()
        trees = [DocTree(title="关于做好年度工作的通知")]
        trees[0].blocks = [Block(type=PARAGRAPH,
                                 text="第%02d段甲乙丙丁戊己庚辛壬癸子丑寅卯。" % i)
                           for i in range(60)]
        pdf = _render(tmp_path, "capacity.pdf", trees=trees, tpl=tpl)
        capacity = int((tpl.page_height_mm - tpl.margin_top_mm
                        - tpl.margin_bottom_mm) / 25.4 * 72 // tpl.line_spacing_pt)
        doc = fitz.open(str(pdf))
        try:
            for pno in range(doc.page_count):
                lines = [ln for blk in doc[pno].get_text("dict")["blocks"]
                         for ln in blk.get("lines", [])
                         if "".join(s["text"] for s in ln["spans"]).strip()
                         and not re.fullmatch(r"—\s*\d+\s*—",
                                              "".join(s["text"] for s in ln["spans"]).strip())]
                assert len(lines) <= capacity, (
                    f"第{pno + 1}页 {len(lines)} 行，超出 28 磅行距下的版心容量 {capacity} 行")
        finally:
            doc.close()

    def test_cover_bleed_strip_runs_after_both_passes(self):
        """回归锚点：两遍渲染后都要清理封面重影（漏了第一遍会带偏标题定位）。"""
        import inspect

        source = inspect.getsource(pdfrender.render_compiled_pdf)
        assert source.count("_strip_cover_page_bleed(") == 2, (
            "两遍渲染后都应做封面页重影清理")


# ================================================================ docx 字体 / 分节
class TestDocxFontsAndSections:
    def _doc(self, tmp_path):
        out = tmp_path / "fonts.docx"
        docxgen.generate_docx(_trees(), default_template(), str(out))
        return out

    def test_font_layering(self, tmp_path):
        """材料标题/一级/二级/三级/正文 的 eastAsia 字体与字号逐项断言。"""
        doc = Document(str(self._doc(tmp_path)))
        by_text = {p.text.strip(): p for p in doc.paragraphs if p.text.strip()}

        title = by_text["关于加强公文处理工作的通知"]
        assert title.style.name.startswith("Heading 1"), "材料标题仍须是 Heading 1（TOC 抓取）"
        assert _east_asia(title) == "方正小标宋简体", f"标题字体={_east_asia(title)}"
        assert _run_pt(title) == 22, f"标题字号={_run_pt(title)}（应二号=22pt）"

        h1 = by_text["一、总体要求"]
        assert h1.style.name.startswith("Heading 1")
        assert _east_asia(h1) == "黑体", f"一级标题字体={_east_asia(h1)}"
        assert _run_pt(h1) == 16

        h2 = by_text["（一）提高认识"]
        assert h2.style.name.startswith("Heading 2")
        assert _east_asia(h2) == "楷体_GB2312", f"二级标题字体={_east_asia(h2)}"
        assert _run_pt(h2) == 16

        h3 = by_text["1. 落实责任"]
        assert h3.style.name.startswith("Heading 3")
        assert _east_asia(h3) == "仿宋_GB2312", f"三级标题字体={_east_asia(h3)}"
        assert h3.runs[0].font.bold is True, "三级标题须加粗"

        body = next(p for p in doc.paragraphs if p.text.startswith("第一段："))
        assert _east_asia(body) == "仿宋_GB2312", f"正文字体={_east_asia(body)}"
        assert _run_pt(body) == 16, "正文三号=16pt"
        assert not body.runs[0].font.bold

    def test_cover_title_font(self, tmp_path):
        """封面题字同为小标宋（与 PDF 侧一致）。"""
        doc = Document(str(self._doc(tmp_path)))
        cover = next(p for p in doc.paragraphs if p.text.strip() == "材料汇编")
        assert _east_asia(cover) == "方正小标宋简体"
        assert _run_pt(cover) == 30

    def test_two_sections_and_page_number_restart(self, tmp_path):
        """封面/目录节不编号，正文节 `pgNumType@start=1`，页码只在正文节。"""
        path = self._doc(tmp_path)
        doc = Document(str(path))
        assert len(doc.sections) == 2, f"应分成前置/正文两节，实际 {len(doc.sections)} 节"
        front, body = doc.sections[0], doc.sections[1]
        assert front._sectPr.find(_W + "pgNumType") is None, "前置节不该设页码起始"
        pg = body._sectPr.find(_W + "pgNumType")
        assert pg is not None and pg.get(_W + "start") == "1", "正文节页码须从 1 起"
        assert "PAGE" in body.footer.paragraphs[0]._p.xml
        assert "PAGE" in body.even_page_footer.paragraphs[0]._p.xml
        # 前置节没有页脚部件（→ Word/WPS 不显示页码）
        with zipfile.ZipFile(path) as zf:
            document_xml = zf.read("word/document.xml").decode("utf-8")
        assert document_xml.count("<w:sectPr") == 2

    def test_cover_before_toc_before_body(self, tmp_path):
        """页序：封面 → 目录 → 正文（红头紧随目录）。"""
        path = self._doc(tmp_path)
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8")
        i_cover = xml.find("材料汇编")
        i_toc = xml.find(" TOC ")
        i_body = xml.find("关于加强公文处理工作的通知")
        assert -1 < i_cover < i_toc < i_body, f"顺序：封面={i_cover} 目录={i_toc} 正文={i_body}"

    def test_blank_cover_elements_do_not_emit_page(self, tmp_path):
        """要素全空时不出封面（归档清单复用同一模板不能凭空多一页）。

        注意：即使没有封面，只要开着目录，前置目录页仍需单独成节（目录不编号），
        所以这里连目录一起关掉，才能断言"单节、无前置页"。
        """
        tpl = default_template()
        tpl.cover.title = ""
        tpl.cover.org = ""
        tpl.cover.date = ""
        tpl.toc_enabled = False
        out = tmp_path / "nocover.docx"
        docxgen.generate_docx(_trees(), tpl, str(out))
        doc = Document(str(out))
        assert len(doc.sections) == 1, "无封面且无目录时不该再分节"
        assert cover_visible(tpl) is False


# ================================================================ A4 / A3 小册子
def _marker_pdf(path, pages: int = 8) -> None:
    """造一份 8 页 A4，每页写「第N页」大字，用于核对拼版落位。"""
    doc = fitz.open()
    try:
        for i in range(pages):
            page = doc.new_page(width=A4_W, height=A4_H)
            page.insert_text((80, 120), f"PAGE{i + 1}", fontname="china-s", fontsize=60)
        doc.save(str(path))
    finally:
        doc.close()


class TestBookletImposition:
    def test_imposition_plan_derivation(self):
        """页数 → 补白 → 印张 的推导（6 页 → 补 8 页 → 2 张 A3 → 4 个 A3 页）。"""
        plan = booklet.imposition_plan(6)
        assert (plan.src_pages, plan.padded_pages, plan.sheets,
                plan.output_pages, plan.blank_slots) == (6, 8, 2, 4, 2)
        assert booklet.imposition_plan(8).sheets == 2
        assert booklet.imposition_plan(9).padded_pages == 12
        assert booklet.imposition_plan(1).padded_pages == 4
        assert booklet.imposition_plan(0).output_pages == 0

    def test_a3_sheet_size_and_even_page_count(self, tmp_path):
        """A3 产物：1190×842pt 横向、页数偶数、张数 = 页数/2。"""
        src = tmp_path / "src8.pdf"
        _marker_pdf(src, 8)
        out = tmp_path / "bk8.pdf"
        n = booklet.make_booklet(str(src), str(out))
        doc = fitz.open(str(out))
        try:
            assert (n, doc.page_count) == (4, 4)
            assert n % 2 == 0
            rect = doc[0].rect
            assert abs(rect.width - A3_W) < 1.5 and abs(rect.height - A3_H) < 1.5, (
                f"A3 尺寸 {rect.width:.1f}x{rect.height:.1f}pt（应 1190.6x841.9）")
            assert rect.width > rect.height, "A3 应为横向"
        finally:
            doc.close()

    def test_slots_follow_booklet_order(self, tmp_path):
        """每张 A3 左/右半幅的页码必须等于 booklet_order 的 [左, 右]。"""
        pages = 8
        src = tmp_path / "src.pdf"
        _marker_pdf(src, pages)
        out = tmp_path / "bk.pdf"
        booklet.make_booklet(str(src), str(out))
        order = booklet.booklet_order(pages)
        assert order == [[8, 1], [2, 7], [6, 3], [4, 5]]
        doc = fitz.open(str(out))
        try:
            half = doc[0].rect.width / 2
            for idx, (left_no, right_no) in enumerate(order):
                page = doc[idx]
                for slot, expected in ((0, left_no), (1, right_no)):
                    hit = page.search_for(f"PAGE{expected}")
                    assert hit, f"A3 第{idx + 1}页 {'左' if slot == 0 else '右'}幅缺少 PAGE{expected}"
                    x = hit[0].x0
                    if slot == 0:
                        assert x < half, f"PAGE{expected} 应在左半幅（x={x:.0f} < {half:.0f}）"
                    else:
                        assert x > half, f"PAGE{expected} 应在右半幅（x={x:.0f} > {half:.0f}）"
        finally:
            doc.close()

    def test_odd_page_count_pads_with_blank(self, tmp_path):
        """5 页源 → 补到 8 页：最后两幅留白，不得报错。"""
        src = tmp_path / "src5.pdf"
        _marker_pdf(src, 5)
        out = tmp_path / "bk5.pdf"
        n = booklet.make_booklet(str(src), str(out))
        doc = fitz.open(str(out))
        try:
            assert n == 4 and doc.page_count == 4
            found = set()
            for pno in range(doc.page_count):
                for m in re.finditer(r"PAGE\d+", doc[pno].get_text("text")):
                    found.add(m.group(0))
            assert found == {f"PAGE{i}" for i in range(1, 6)}, f"内容缺失：{found}"
        finally:
            doc.close()

    def test_gutter_keeps_content_off_the_fold(self, tmp_path):
        """装订边安全：内容不得压到 A3 正中间的折线上。

        按 **span** 判定而不是块：PyMuPDF 的 block 会把左右两幅同一基线上的
        文字并成一个块（跨中线），用它判定会误报。
        """
        src = tmp_path / "src.pdf"
        _marker_pdf(src, 4)
        out = tmp_path / "bk_gutter.pdf"
        gutter, safety = 6.0, 2.0
        booklet.make_booklet(str(src), str(out), gutter_pt=gutter, safety_pt=safety)
        doc = fitz.open(str(out))
        try:
            half = doc[0].rect.width / 2
            checked = 0
            for pno in range(doc.page_count):
                for block in doc[pno].get_text("dict")["blocks"]:
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            if not span["text"].strip():
                                continue
                            x0, x1 = span["bbox"][0], span["bbox"][2]
                            checked += 1
                            assert (x1 <= half - gutter + 1.0
                                    or x0 >= half + gutter - 1.0), (
                                f"第{pno + 1}页文字压到折线附近"
                                f"（x0={x0:.1f}, x1={x1:.1f}, 中线={half:.1f}）")
            assert checked > 0, "没有量到任何文字，判据失效"
        finally:
            doc.close()

    def test_a4_a3_dual_output(self, tmp_path, qapp):
        """一键汇编的 A4 + A3 双产物：尺寸、页数、页序关系一次核对。"""
        a4_src = _render(tmp_path, "a4src.pdf", tpl=default_template())
        out_a4 = tmp_path / "汇编_A4.pdf"
        out_a3 = tmp_path / "汇编_A3.pdf"
        info = booklet.make_booklet_pair(str(a4_src), str(out_a4), str(out_a3))
        doc_a4 = fitz.open(str(out_a4))
        doc_a3 = fitz.open(str(out_a3))
        try:
            assert (info["a4_pages"], info["a3_pages"]) == (doc_a4.page_count, doc_a3.page_count)
            assert abs(doc_a4[0].rect.width - A4_W) < 1.5, "A4 宽应为 595pt"
            assert abs(doc_a4[0].rect.height - A4_H) < 1.5, "A4 高应为 842pt"
            assert abs(doc_a3[0].rect.width - A3_W) < 1.5, "A3 宽应为 1190pt"
            assert info["a3_pages"] % 2 == 0, "A3 页数必须为偶数"
            plan = info["plan"]
            assert plan.output_pages == info["a3_pages"]
            assert plan.padded_pages >= plan.src_pages
            assert doc_a3.page_count == len(booklet.booklet_order(doc_a4.page_count))
        finally:
            doc_a4.close()
            doc_a3.close()
