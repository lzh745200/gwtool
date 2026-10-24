# -*- coding: utf-8 -*-
"""公文 DOCX 生成引擎（python-docx）。

生成要素：封面 -> 红头 -> 目录(TOC域, 打开自动更新页码) -> 正文 -> 版记；
格式要点（GB/T 9704 常用实践）：
  - 正文仿宋_GB2312 三号 16pt、固定行距28磅、首行缩进2字符(w:firstLineChars=200)；
  - 页码宋体四号、奇数页右对齐/偶数页左对齐（外侧），格式 “— 1 —”；
  - 标题用 Heading 1-3 样式（供 TOC 抓取），字体覆盖为黑体/楷体/仿宋；
  - 全部使用标准 OOXML 元素，WPS/永中 Office 可正常打开不跑版。
"""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Mm, Pt, RGBColor

from . import compile_norm
from .model import DocTree, HEADING, LIST_ITEM, TABLE
from .template import DocTemplate, HeadingStyle, cover_visible, material_label

_ALIGN = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}


# ------------------------------------------------------------------ 底层工具
def _set_run_font(run, cn_font: str, size_pt: float, bold: bool = False,
                  color: tuple[int, int, int] | None = None) -> None:
    """同时设置西文与中文（eastAsia）字体，缺一不可。"""
    run.font.name = cn_font
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), cn_font)
    rfonts.set(qn("w:hAnsi"), cn_font)
    rfonts.set(qn("w:eastAsia"), cn_font)
    if color:
        run.font.color.rgb = RGBColor(*color)


def _set_line_exact(par, pt: float) -> None:
    """固定行距，并把**段前/段后显式清零**（P1-01）。

    为什么必须写 before/after=0：python-docx 自带模板的 `styles.xml`
    `docDefaults` 里带着 `<w:spacing w:after="200" w:line="276"
    w:lineRule="auto"/>`。段落上只写 `w:line`/`w:lineRule` 时，`w:after=200`
    （10 磅段后距）**仍会被继承**并叠加到行距上 —— 实测（WPS 12.0 渲染）
    模板要求 28 磅固定行距 / 每面 22 行，实际渲成 32.4 磅 / 19~20 行，
    整篇版心不合规、页数虚增。补上 before/after=0 后实测回到 28.0 磅。
    """
    pf = par.paragraph_format
    pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    pf.line_spacing = Pt(pt)
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)


def _zero_doc_defaults_spacing(doc) -> None:
    """把文档级默认段前/段后也清零（P1-01 的第二道保险）。

    `_set_line_exact` 只覆盖"我们自己创建的正文/标题/页脚/间距段"；表格单元格
    段落、`add_page_break()` 造出的空段落等不经过它，仍会继承 docDefaults 的
    `w:after=200`。同一份材料里一半段落 28 磅、另一半 38 磅，比整体偏移更难查，
    因此这里把文档默认值也清零，让整篇口径一致。

    只清 before/after（含 beforeLines/afterLines 这类按行计数、优先级更高的
    属性），**不动 docDefaults 的 line/lineRule**：表格等未显式设行距的段落
    继续沿用模板原有的 1.15 倍行距，不会被本次修复压成固定值。
    """
    try:
        defaults = doc.styles.element.find(qn("w:docDefaults"))
        if defaults is None:
            return
        ppr_default = defaults.find(qn("w:pPrDefault"))
        if ppr_default is None:
            return
        ppr = ppr_default.find(qn("w:pPr"))
        if ppr is None:
            return
        spacing = ppr.find(qn("w:spacing"))
        if spacing is None:
            return
        for attr in ("w:beforeLines", "w:afterLines"):
            if spacing.get(qn(attr)) is not None:
                del spacing.attrib[qn(attr)]
        spacing.set(qn("w:before"), "0")
        spacing.set(qn("w:after"), "0")
    except Exception as exc:  # 模板结构异常不能拦住生成（默认行距仍可用）
        from ..logs import get_logger
        get_logger("docx").warning("清零文档默认段距失败，沿用模板默认值：%s", exc)


def _set_first_line_chars(par, chars: float) -> None:
    """以“字符”为单位的首行缩进（w:firstLineChars=200 即 2 字符）。"""
    ppr = par._p.get_or_add_pPr()
    ind = ppr.find(qn("w:ind"))
    if ind is None:
        ind = OxmlElement("w:ind")
        ppr.append(ind)
    ind.set(qn("w:firstLineChars"), str(int(chars * 100)))
    ind.set(qn("w:firstLine"), str(int(chars * 240)))  # 兜底twips(12pt字面值)


def _bottom_border(par, color: str = "FF0000", sz: int = 15) -> None:
    ppr = par._p.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(sz))       # 1/8 pt
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), color)
    pbdr.append(bottom)
    ppr.append(pbdr)


def _add_field(par, instr: str, hint: str = "") -> None:
    """插入 Word 域（PAGE / TOC 等）。"""
    r1 = par.add_run()
    fld = OxmlElement("w:fldChar")
    fld.set(qn("w:fldCharType"), "begin")
    fld.set(qn("w:dirty"), "true")   # 打开文档时重算
    r1._element.append(fld)
    r2 = par.add_run()
    it = OxmlElement("w:instrText")
    it.set(qn("xml:space"), "preserve")
    it.text = instr
    r2._element.append(it)
    r3 = par.add_run()
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    r3._element.append(sep)
    if hint:
        par.add_run(hint)
    r4 = par.add_run()
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    r4._element.append(end)


def _enable_update_fields(doc) -> None:
    """settings.xml 加 w:updateFields，Word/WPS 打开时自动更新域（目录页码）。"""
    settings = doc.settings.element
    upd = settings.find(qn("w:updateFields"))
    if upd is None:
        upd = OxmlElement("w:updateFields")
        upd.set(qn("w:val"), "true")
        settings.append(upd)


def _enable_even_odd_footers(doc) -> None:
    settings = doc.settings.element
    eo = settings.find(qn("w:evenAndOddHeaders"))
    if eo is None:
        eo = OxmlElement("w:evenAndOddHeaders")
        settings.append(eo)


# ------------------------------------------------------------------ 主流程
def _apply_page_setup(sec, tpl: DocTemplate) -> None:
    """把模板的纸张与页边距写进某一节。

    v1.15 起"封面+目录"与"正文"分成两节（正文页码要从 1 起算）。
    `add_section()` 虽会复制上一节的页面设置，但模板参数可被用户改、
    不同 Word/WPS 版本对 sectPr 复制的处理也有差异，显式再设一遍更稳。
    """
    sec.page_width = Mm(tpl.page_width_mm)
    sec.page_height = Mm(tpl.page_height_mm)
    sec.top_margin = Mm(tpl.margin_top_mm)
    sec.bottom_margin = Mm(tpl.margin_bottom_mm)
    sec.left_margin = Mm(tpl.margin_left_mm)
    sec.right_margin = Mm(tpl.margin_right_mm)
    sec.footer_distance = Mm(20)


def _restart_page_numbering(sec) -> None:
    """让本节页码从 1 重新起算（`<w:pgNumType w:start="1"/>`）。

    规范要求：封面、目录不编号，正文首页是"— 1 —"。只"不给封面盖页码"
    不够 —— 正文若沿用全文连续编号，首页会显示"— 3 —"，与目录里写的"1"
    对不上。分节 + `pgNumType@start=1` 才能让 PAGE 域在正文节重新计数
    （Word 与 WPS 都支持的标准 OOXML 元素）。
    """
    sect_pr = sec._sectPr
    pg_num = sect_pr.find(qn("w:pgNumType"))
    if pg_num is None:
        pg_num = OxmlElement("w:pgNumType")
        sect_pr.append(pg_num)
    pg_num.set(qn("w:start"), "1")
    pg_num.set(qn("w:fmt"), "decimal")


def generate_docx(trees: list[DocTree], tpl: DocTemplate, out_path: str) -> str:
    doc = Document()
    # P1-01：先把模板自带的段后距清零，后续每段再显式写 0，两处一起才保证
    # "正文 28 磅固定行距、每面 22 行" 的版心口径在 Word/WPS 里都成立。
    _zero_doc_defaults_spacing(doc)

    front_sec = doc.sections[0]
    _apply_page_setup(front_sec, tpl)

    # 页码：奇偶页脚（外侧）+ 打开自动更新域
    _enable_even_odd_footers(doc)
    _enable_update_fields(doc)

    # 1) 封面（v1.15：内置模板默认带封面；要素全空时不出页，见 cover_visible）
    has_cover = cover_visible(tpl)
    if has_cover:
        _build_cover(doc, tpl)
        doc.add_page_break()

    # 2) 目录（TOC 域）
    #     P1-04：目录必须排在红头**之前** —— 与内置 PDF 渲染器
    #     （pdfrender.trees_to_html：封面 → 目录 → 红头+正文）同序。旧实现把
    #     红头夹在封面与目录之间，且红头后又跟一个分页符，结果红头独占一整页
    #     （实测第 2 页只有"××单位文件""×政发〔2026〕1号"两行），同一模板导出
    #     docx 与 PDF 的页序还不一致，用户无法二选一交付。
    if tpl.toc_enabled:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(tpl.toc_title)
        _set_run_font(run, "宋体", 22, bold=True)
        toc_p = doc.add_paragraph()
        # 目录层级：toc_max_level 按**公文层级**计（1=一、，2=（一），3=1.）。
        # 材料标题额外占一级 Heading，故 Word 域的层级上限要加 shift（封顶 4）。
        toc_levels = max(1, min(4, int(getattr(tpl, "toc_max_level", 2))
                                + (1 if tpl.insert_material_titles else 0)))
        _add_field(toc_p, f' TOC \\o "1-{toc_levels}" \\h \\z \\u ',
                   "（打开文档后按提示更新目录页码）")
        doc.add_page_break()

    # 3) 分节：封面/目录属于"前置部分"，不编号；正文另起一节、页码从 1 起算
    if has_cover or tpl.toc_enabled:
        body_sec = doc.add_section(WD_SECTION.NEW_PAGE)
        _apply_page_setup(body_sec, tpl)
        _restart_page_numbering(body_sec)
        if tpl.page_number_enabled:
            _build_footers(body_sec, tpl)
    elif tpl.page_number_enabled:
        _build_footers(front_sec, tpl)   # 无前置部分：单节，页码自然从 1 起

    # 4) 红头：紧随目录、与正文首页同页（P1-04，不再单独占一页）
    if tpl.red_header.enabled:
        _build_red_header(doc, tpl)

    # 5) 正文
    for mat_no, tree in enumerate(trees, 1):
        has_mat_title = bool(tpl.insert_material_titles and (tree.title or "").strip())
        # v1.15：材料标题占用 Heading 1（小标宋），材料内部层级整体下移一级
        # （`rebase_material_levels`）。字体必须跟着**公文层级**走，否则「一、」
        # 会落到 Heading 2 的楷体（实测缺陷）：style_lv = 层级 − shift，
        # 一级黑体 / 二级楷体 / 三级仿宋加粗保持不变。
        shift = 1 if has_mat_title else 0
        if has_mat_title:
            label = material_label(tpl.material_title_prefix, mat_no)
            # v1.15：材料/公文标题用小标宋二号（GB/T 9704），与一级标题「一、」
            # 的黑体三号区分开；仍挂在 Heading 1 样式上，TOC 域才能抓到它。
            _add_heading(doc, f"{label}{tree.title}", 1, tpl.doc_title,
                         tpl.line_spacing_pt)
        for blk in tree.effective_blocks(tpl.insert_material_titles):
            if blk.type == HEADING:
                raw = max(int(blk.level or 1), 1)
                # 字体按**公文序号**判定（一、黑体 / （一）楷体 / 1. 仿宋加粗）；
                # 序号判定不出来时才退回"层级 − shift"，两者对已重定基/未重定基
                # 的树都成立（直接调用 generate_docx 的入口也不会错位）。
                off = compile_norm.official_level(blk.text)
                style_lv = off if off else min(max(raw - shift, 1), 3)
                style_lv = min(max(style_lv, 1), 3)
                hs = {1: tpl.h1, 2: tpl.h2, 3: tpl.h3}[style_lv]
                _add_heading(doc, blk.text, min(raw, 4), hs, tpl.line_spacing_pt)
            elif blk.type == TABLE and blk.rows:
                _add_table(doc, blk.rows, tpl)
            elif blk.type == LIST_ITEM:
                _add_body(doc, blk.text, tpl, indent=tpl.first_line_indent_chars)
            else:
                _add_body(doc, blk.text, tpl)

    # 5) 版记
    if tpl.colophon.enabled and tpl.colophon.lines:
        doc.add_page_break()
        _build_colophon(doc, tpl)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))

    # 水印（页眉 VML 艺术字），透明度/角度取模板参数
    if tpl.watermark_text.strip():
        from .watermark import add_watermark_docx
        add_watermark_docx(str(out), tpl.watermark_text.strip(),
                           opacity=tpl.watermark_opacity,
                           angle=(360 - tpl.watermark_angle) % 360)
        # P1-11：add_watermark_docx 只写默认(奇数)页眉，而本模板开了
        # evenAndOddHeaders —— 偶数页读的是 even_page_header，不继承默认页眉，
        # 于是偶数页完全没有水印（实测：WPS 渲染后奇数页 5 条矢量路径、偶数页
        # 0 条）。这里把同一段 VML 复制到偶数页页眉，奇偶页都盖到。
        _mirror_watermark_to_even_header(str(out))
    return str(out)


def _mirror_watermark_to_even_header(docx_path: str) -> bool:
    """把默认(奇数)页眉里的水印 VML 复制到偶数页页眉（P1-11），返回是否改动。

    只复制**含 `w:pict` 的整段**（保留 pStyle/run 结构），不重新拼 XML ——
    VML 的唯一来源仍是 `watermark.add_watermark_docx`，两处不会漂移。
    失败只记日志：水印是附加要素，不能因为页眉结构异常把已生成好的公文搞丢。
    """
    from copy import deepcopy

    from docx import Document

    try:
        doc = Document(docx_path)
        changed = 0
        for sec in doc.sections:
            source = sec.header._element
            pict_paras = [p for p in source.findall(qn("w:p"))
                          if p.find(".//" + qn("w:pict")) is not None]
            if not pict_paras:
                continue
            targets = [sec.even_page_header]
            if sec.different_first_page_header_footer:
                targets.append(sec.first_page_header)   # 首页单独页眉时才需要
            for target in targets:
                target.is_linked_to_previous = False
                element = target._element
                for old in [p for p in element.findall(qn("w:p"))
                            if p.find(".//" + qn("w:pict")) is not None]:
                    element.remove(old)                 # 重复调用不叠加
                for para in pict_paras:
                    element.append(deepcopy(para))
                changed += 1
        if changed:
            doc.save(docx_path)
        return bool(changed)
    except Exception as exc:                            # 水印是附加要素，不阻断
        from ..logs import get_logger
        get_logger("docx").warning("偶数页水印复制失败，仅奇偶不一致：%s", exc)
        return False


# ------------------------------------------------------------------ 部件
def _build_footers(sec, tpl: DocTemplate) -> None:
    """奇数页页码右对齐、偶数页左对齐（外侧），宋体四号。"""
    def make(footer, align):
        footer.is_linked_to_previous = False
        par = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
        par.text = ""
        par.alignment = align
        _set_line_exact(par, 20)
        pre, post = "", ""
        fmt = tpl.page_number_format or "— {page} —"
        if "{page}" in fmt:
            pre, post = fmt.split("{page}", 1)
        if pre:
            r = par.add_run(pre)
            _set_run_font(r, tpl.page_number_font, tpl.page_number_size_pt)
        _add_field(par, r" PAGE \* MERGEFORMAT ", "1")
        if post:
            r = par.add_run(post)
            _set_run_font(r, tpl.page_number_font, tpl.page_number_size_pt)
        # 域内提示字号
        for rr in par.runs:
            _set_run_font(rr, tpl.page_number_font, tpl.page_number_size_pt)

    make(sec.footer, WD_ALIGN_PARAGRAPH.RIGHT)     # 奇数页（默认页脚）
    make(sec.even_page_footer, WD_ALIGN_PARAGRAPH.LEFT)


def _build_cover(doc, tpl: DocTemplate) -> None:
    cover = tpl.cover
    for _ in range(4):
        _spacer(doc, tpl)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_line_exact(p, 40)
    run = p.add_run(cover.title or tpl.name)
    _set_run_font(run, "方正小标宋简体", 30, color=(0, 0, 0))
    if cover.subtitle:
        p2 = doc.add_paragraph()
        p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _set_line_exact(p2, 32)
        r2 = p2.add_run(cover.subtitle)
        _set_run_font(r2, "楷体_GB2312", 18)
    for _ in range(4):
        _spacer(doc, tpl)
    for line in [cover.org, cover.date, *cover.extra_lines]:
        # P1-6 的硬契约：封面要素**留空时产物与改动前逐字节一致**。
        # 这里用 `strip()` 判空而非直接判真：只含空格的行会生成一个空段落，
        # 把落款整体往下顶一行的位置。docx 与 pdf 两侧必须用同一判据，
        # 否则同一份内容导出两个格式的封面行数不一致。
        if not (line or "").strip():
            continue
        p3 = doc.add_paragraph()
        p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _set_line_exact(p3, 28)
        r3 = p3.add_run(line)
        _set_run_font(r3, "仿宋_GB2312", 16)


def _build_red_header(doc, tpl: DocTemplate) -> None:
    rh = tpl.red_header
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_line_exact(p, rh.org_size_pt * 1.35)
    run = p.add_run(rh.org)
    # 旧版这里写成 `rh.org_font if rh.org_font != FONT_XBS else FONT_XBS`——
    # 三元两侧同值，等价于直接用 rh.org_font。清理掉，避免后来者误以为
    # 这里有"非小标宋时回退"的逻辑而据此改动行为。
    _set_run_font(run, rh.org_font, rh.org_size_pt, color=(255, 0, 0))
    if rh.doc_number:
        p2 = doc.add_paragraph()
        p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _set_line_exact(p2, 28)
        r2 = p2.add_run(rh.doc_number)
        _set_run_font(r2, rh.doc_number_font, rh.doc_number_size_pt)
    if rh.red_line:
        pl = doc.add_paragraph()
        _set_line_exact(pl, 12)
        _bottom_border(pl, color="FF0000", sz=15)


def _add_heading(doc, text: str, level: int, hs: HeadingStyle, line_pt: float) -> None:
    """用 Heading 样式（供目录抓取）并覆盖字体。

    level 到 4：材料标题占 Heading 1 后，公文三级「1.」可能落在 Heading 4；
    TOC 域按层级上限（toc_max_level + shift）决定是否收录它。
    """
    par = doc.add_paragraph(style=f"Heading {min(level, 4)}")
    par.alignment = _ALIGN.get(hs.align, WD_ALIGN_PARAGRAPH.LEFT)
    _set_line_exact(par, line_pt)
    if hs.indent_chars:
        _set_first_line_chars(par, hs.indent_chars)
    run = par.add_run(text)
    _set_run_font(run, hs.font, hs.size_pt, bold=hs.bold, color=(0, 0, 0))


def _add_body(doc, text: str, tpl: DocTemplate, indent: float | None = None) -> None:
    par = doc.add_paragraph()
    par.alignment = _ALIGN.get(tpl.align, WD_ALIGN_PARAGRAPH.JUSTIFY)
    _set_line_exact(par, tpl.line_spacing_pt)
    ind = tpl.first_line_indent_chars if indent is None else indent
    if ind:
        _set_first_line_chars(par, ind)
    run = par.add_run(text)
    _set_run_font(run, tpl.body_font, tpl.body_size_pt)


def _add_table(doc, rows: list[list[str]], tpl: DocTemplate) -> None:
    """表格：全框线（公文常用），字号比正文小两号，单元格文字左对齐。"""
    if not rows:
        return
    ncols = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=ncols)
    try:
        table.style = "Table Grid"
    except Exception:
        pass  # 样式缺失时保留默认表格
    for i, row in enumerate(rows):
        for j in range(ncols):
            cell = table.cell(i, j)
            par = cell.paragraphs[0]
            par.text = ""
            run = par.add_run(row[j] if j < len(row) else "")
            _set_run_font(run, tpl.body_font, max(tpl.body_size_pt - 2, 9))
    # 表格与后续正文留一行间隙
    p = doc.add_paragraph()
    _set_line_exact(p, tpl.line_spacing_pt * 0.6)


def _build_colophon(doc, tpl: DocTemplate) -> None:
    """版记：横线分隔的条目区，置于末页。"""
    lines = tpl.colophon.lines
    for i, line in enumerate(lines):
        par = doc.add_paragraph()
        _set_line_exact(par, 24)
        if i == 0:
            _top_and_bottom(par)
        elif i == len(lines) - 1:
            ppr = par._p.get_or_add_pPr()
            pbdr = OxmlElement("w:pBdr")
            b = OxmlElement("w:bottom")
            b.set(qn("w:val"), "single")
            b.set(qn("w:sz"), "12")
            b.set(qn("w:space"), "1")
            b.set(qn("w:color"), "000000")
            pbdr.append(b)
            ppr.append(pbdr)
        run = par.add_run(line)
        _set_run_font(run, "仿宋_GB2312", 14)


def _top_and_bottom(par) -> None:
    ppr = par._p.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr")
    for tag in ("w:top", "w:bottom"):
        el = OxmlElement(tag)
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "12")
        el.set(qn("w:space"), "1")
        el.set(qn("w:color"), "000000")
        pbdr.append(el)
    ppr.append(pbdr)


def _spacer(doc, tpl: DocTemplate) -> None:
    p = doc.add_paragraph()
    _set_line_exact(p, tpl.line_spacing_pt)
