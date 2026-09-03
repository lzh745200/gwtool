# -*- coding: utf-8 -*-
"""内置 PDF 渲染器：QTextDocument + QPdfWriter（完全离线，不依赖 Word/WPS）。

职责：
  1. 把汇编内容（DocTree 列表 + 模板）渲染为带页码的 A4 纵向 PDF（正文可带红头/封面）；
  2. 生成真实页码的目录页（两遍渲染法：第一遍预留目录页并用占位页码渲染，
     用 PyMuPDF 定位每个标题的真实页码，替换后第二遍渲染）；
  3. 为 PDF 盖页码（奇数页右下、偶数页左下，即“外侧”）。

注意：必须在 QApplication 实例化之后调用（字体子系统需要）。
"""
from __future__ import annotations

import re

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore

from PySide6.QtCore import QMarginsF, QByteArray, QSizeF
from PySide6.QtGui import QColor, QFont, QPainter, QPageLayout, QPageSize, QPdfWriter, QTextDocument
from PySide6.QtCore import Qt

from .model import DocTree, HEADING, LIST_ITEM, TABLE
from .template import DocTemplate, material_label

_MM = 2.834645669  # mm -> pt

# 目录每页行数（28磅行距 A4 经验值；首页扣除目录标题约占 2 行）
_TOC_LINES_PER_PAGE = 22


def _toc_chunks(n_rows: int) -> list[int]:
    """目录行分页切片：首页约 20 行，其后每页 22 行。
    返回每页行数切片列表（两遍渲染据此保证目录页数一致）。"""
    if n_rows <= 0:
        return []
    first = max(1, _TOC_LINES_PER_PAGE - 2)
    if n_rows <= first:
        return [n_rows]
    rest = n_rows - first
    full = rest // _TOC_LINES_PER_PAGE
    tail = rest % _TOC_LINES_PER_PAGE
    pages = [first] + [_TOC_LINES_PER_PAGE] * full
    if tail:
        pages.append(tail)
    return pages


# 系统无任何中文字体时注入的兜底族名（PyMuPDF 自带，Apache-2.0）
_CJK_FALLBACK_FAMILY = "Droid Sans Fallback"
_cjk_font_id: int = -1
# None=尚未探测；""=系统已有中文字体，无需注入；其余=已注入的族名
_cjk_injected: str | None = None

_KNOWN_CJK_FONTS = ("SimSun", "宋体", "微软雅黑", "Microsoft YaHei",
                    "Noto Sans CJK SC", "Noto Sans CJK", "WenQuanYi Micro Hei",
                    "WenQuanYi Zen Hei", "仿宋_GB2312", "方正小标宋简体")


def ensure_cjk_font() -> str:
    """保证存在可渲染中文的字体，返回可用于兜底的族名（无则空串）。

    麒麟最小安装、精简字体镜像等机器上一个中文字体都没有，此时 Qt 渲染出的
    PDF 正文会整篇空白（只剩 PyMuPDF 盖的页码），目录页码全部退化成“—”，
    且全程不报任何错——用户拿到的是一份看起来正常的空白文件。
    PyMuPDF 自带 Droid Sans Fallback（覆盖简繁日韩），随既有依赖离线分发，
    注入后即可兜底，无需系统装字体、不涉及版权问题。
    """
    global _cjk_font_id, _cjk_injected
    if _cjk_injected is not None:
        return _cjk_injected

    from PySide6.QtGui import QFontDatabase
    fams = set(QFontDatabase.families())
    for known in _KNOWN_CJK_FONTS:
        if known in fams:
            _cjk_injected = ""
            return ""
    if _CJK_FALLBACK_FAMILY in fams:
        _cjk_injected = _CJK_FALLBACK_FAMILY
        return _CJK_FALLBACK_FAMILY

    try:
        buf = fitz.Font("china-s").buffer
    except Exception:
        buf = None
    if buf:
        _cjk_font_id = QFontDatabase.addApplicationFontFromData(QByteArray(bytes(buf)))
    if _cjk_font_id != -1:
        fams = set(QFontDatabase.applicationFontFamilies(_cjk_font_id))
        _cjk_injected = next(iter(fams), _CJK_FALLBACK_FAMILY)
    else:
        _cjk_injected = ""
    return _cjk_injected


def _font_family(candidate: str, fallback: str = "SimSun") -> str:
    from PySide6.QtGui import QFontDatabase
    injected = ensure_cjk_font()
    fams = set(QFontDatabase.families())
    chain = [candidate, fallback, "宋体", "SimSun", "Noto Sans CJK SC", "微软雅黑"]
    if injected:
        chain.append(injected)
    for c in chain:
        if c in fams:
            return c
    return QFontDatabase.systemFont(QFontDatabase.GeneralFont).family()


def trees_to_html(trees: list[DocTree], tpl: DocTemplate,
                  toc_pages: list[list[tuple[str, int, int]]] | None = None,
                  toc_placeholder: bool = False) -> str:
    """DocTree 列表 -> HTML（供 QTextDocument 渲染）。

    分页注意：Qt 富文本只支持 page-break-before（不支持 after），
    因此每个分节的第一元素携带 page-break-before 样式。
    toc_pages 为每份材料的目录项 [(text, level, page)]；
    toc_placeholder=True 时页码用“00”占位（保证两遍渲染分页一致）。
    """
    body_font = _font_family(tpl.body_font)
    indent_px = int(tpl.first_line_indent_chars * tpl.body_size_pt * 96 / 72)
    css = f"""
    <style>
      body {{ font-family:'{body_font}'; font-size:{tpl.body_size_pt}pt;
             line-height:{tpl.line_spacing_pt}pt; }}
      p {{ text-indent:{indent_px}px; margin:0 0 2pt 0; text-align:justify; }}
      h1 {{ font-family:'{_font_family(tpl.h1.font)}'; font-size:{tpl.h1.size_pt}pt;
           font-weight:{'bold' if tpl.h1.bold else 'normal'}; margin:8pt 0 4pt 0; }}
      h2 {{ font-family:'{_font_family(tpl.h2.font)}'; font-size:{tpl.h2.size_pt}pt;
           font-weight:{'bold' if tpl.h2.bold else 'normal'}; margin:6pt 0 3pt 0; }}
      h3 {{ font-family:'{_font_family(tpl.h3.font)}'; font-size:{tpl.h3.size_pt}pt;
           font-weight:{'bold' if tpl.h3.bold else 'normal'}; margin:6pt 0 3pt 0; }}
      .red-org {{ font-family:'{_font_family("方正小标宋简体", "宋体")}';
                 font-size:{tpl.red_header.org_size_pt}pt; color:#ff0000;
                 text-align:center; margin:0; }}
      .doc-no {{ text-align:center; margin:2pt 0; }}
      .cover-title {{ font-size:28pt; text-align:center; margin-top:110pt; }}
      table.toc {{ width:100%; }}
      table.toc td {{ font-size:{tpl.body_size_pt}pt; padding:1pt 0; }}
    </style>"""
    parts = [css]
    has_front = False

    # ---- 封面 ----
    if tpl.cover.enabled:
        parts.append(f"<p class='cover-title'>{tpl.cover.title or tpl.name}</p>")
        if tpl.cover.subtitle:
            parts.append(f"<p style='text-align:center;text-indent:0'>{tpl.cover.subtitle}</p>")
        parts.append("<p style='margin-top:170pt'></p>")
        for line in [tpl.cover.org, tpl.cover.date, *tpl.cover.extra_lines]:
            if line:
                parts.append(f"<p style='text-align:center;text-indent:0'>{line}</p>")
        has_front = True

    # ---- 目录（独立页；行数超一页时按 _toc_chunks 分页） ----
    if tpl.toc_enabled:
        brk = "page-break-before:always;" if tpl.cover.enabled else ""
        parts.append(f"<h1 style='text-align:center;text-indent:0;{brk}'>{tpl.toc_title}</h1>")
        # Qt 不认 width=100%，用显式像素宽让页码列顶到版心右缘
        content_px = int((tpl.page_width_mm - tpl.margin_left_mm - tpl.margin_right_mm)
                         / 25.4 * 96)
        text_w = content_px - 60
        rows = []
        for toc in (toc_pages or []):
            for text, level, page in toc:
                page_str = "00" if toc_placeholder else (str(page) if page > 0 else "—")
                rows.append(
                    f"<tr><td width='{text_w}' "
                    f"style='padding-left:{(level-1)*indent_px}px'>{_esc(text)}</td>"
                    f"<td width='60' align='right'>{page_str}</td></tr>")
        # 分片渲染：Qt 表格不跨页，长目录拆成多页
        chunks = _toc_chunks(len(rows))
        pos = 0
        for ci, size in enumerate(chunks):
            if ci > 0:
                parts.append("<p style='page-break-before:always;font-size:2pt;"
                             "margin:0'>&nbsp;</p>")
            parts.append(f"<table class='toc' border='0'>"
                         f"{''.join(rows[pos:pos + size])}</table>")
            pos += size
        has_front = True

    # ---- 红头（正文首页顶部；无封面时紧随目录） ----
    if tpl.red_header.enabled:
        brk = "page-break-before:always;" if has_front else ""
        parts.append(f"<p class='red-org' style='{brk}'>{tpl.red_header.org}</p>")
        if tpl.red_header.doc_number:
            parts.append(f"<p class='doc-no' style='text-indent:0'>{tpl.red_header.doc_number}</p>")
        if tpl.red_header.red_line:
            # Qt 不支持段落边框，用 2pt 高的红色单元格模拟红色分隔线
            parts.append("<table width='100%' border='0' cellspacing='0' cellpadding='0'>"
                         "<tr><td bgcolor='#ff0000' style='font-size:2pt;line-height:2pt'>"
                         "&nbsp;</td></tr></table>"
                         "<p style='margin:0 0 6pt 0;font-size:4pt'>&nbsp;</p>")
        has_front = True

    for idx, tree in enumerate(trees):
        # 红头已开启正文首页，首份材料不再分页；否则有前置内容时分页
        first_break = (idx > 0) or (has_front and not tpl.red_header.enabled)
        blocks = tree.effective_blocks(tpl.insert_material_titles)
        # 组装 (tag, inner_html, extra_style) 元素序列
        elems: list[tuple[str, str, str]] = []
        if tpl.insert_material_titles and tree.title:
            label = material_label(tpl.material_title_prefix, idx + 1)
            elems.append(("h1", _esc(f"{label}{tree.title}"), ""))
        for blk in blocks:
            if blk.type == TABLE and blk.rows:
                ncols = max(len(r) for r in blk.rows)
                trs = []
                for row in blk.rows:
                    tds = "".join(
                        f"<td>{_esc(row[j]) if j < len(row) else ''}</td>"
                        for j in range(ncols))
                    trs.append(f"<tr>{tds}</tr>")
                elems.append(("table", "".join(trs), ""))
                continue
            t = _esc(blk.text)
            if blk.type == HEADING:
                elems.append((f"h{min(blk.level, 3)}", t, ""))
            elif blk.type == LIST_ITEM:
                elems.append(("p", f"• {t}", f"text-indent:{indent_px}px"))
            else:
                elems.append(("p", t, ""))
        for i, (tag, inner, extra) in enumerate(elems):
            style = extra
            if i == 0 and first_break:
                style = ("page-break-before:always;" + style)
            if tag == "table":
                parts.append(f"<table border='1' cellspacing='0' cellpadding='2' "
                             f"style='{style}'>{inner}</table>")
            elif style:
                parts.append(f"<{tag} style='{style}'>{inner}</{tag}>")
            else:
                parts.append(f"<{tag}>{inner}</{tag}>")
    return "".join(parts)


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _render_html_to_pdf(html: str, tpl: DocTemplate, out_pdf: str) -> int:
    """手动分页绘制 QTextDocument。

    不能用 doc.print_(writer)：Qt 会自动在每页尾部附加页码且无法关闭。
    手动方式：按内容区分页，逐页 translate + clip 后调用 documentLayout().draw。
    """
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QAbstractTextDocumentLayout

    writer = QPdfWriter(out_pdf)
    margins = QMarginsF(tpl.margin_left_mm, tpl.margin_top_mm,
                        tpl.margin_right_mm, tpl.margin_bottom_mm)
    writer.setPageLayout(QPageLayout(
        QPageSize(QSizeF(tpl.page_width_mm, tpl.page_height_mm),
                  QPageSize.Unit.Millimeter),
        QPageLayout.Portrait, margins, QPageLayout.Unit.Millimeter))
    res = 96  # 设备像素与 QTextDocument 逻辑像素 1:1，避免单位换算
    writer.setResolution(res)
    page_rect = writer.pageLayout().paintRectPixels(res)      # 内容区（设备像素）

    doc = QTextDocument()
    doc.setDefaultFont(QFont(_font_family(tpl.body_font), int(tpl.body_size_pt)))
    doc.setHtml(html)
    doc.setPageSize(QSizeF(page_rect.width(), page_rect.height()))

    painter = QPainter(writer)
    ctx = QAbstractTextDocumentLayout.PaintContext()
    try:
        n = max(1, doc.pageCount())
        for page in range(n):
            if page > 0:
                writer.newPage()
            painter.save()
            # QPdfWriter 画笔原点已在内容区左上角（边距已含），仅需纵向逐页偏移
            painter.translate(0, -page * page_rect.height())
            ctx.clip = QRectF(0, page * page_rect.height(),
                              page_rect.width(), page_rect.height())
            doc.documentLayout().draw(painter, ctx)
            painter.restore()
    finally:
        painter.end()
    return _page_count(out_pdf)


def _page_count(pdf_path: str) -> int:
    d = fitz.open(pdf_path)
    n = d.page_count
    d.close()
    return n


def _locate_headings(pdf_path: str, headings: list[str], start_page: int = 0) -> list[int]:
    """按顺序在 PDF 中定位标题所在页（1 基，从 start_page 起跳过前置页）。

    未找到返回 0（目录页码显示“—”），不再强归第 1 页；
    匹配先全标题、再 12 字前缀，重名标题靠单调游标按顺序消歧。
    """
    d = fitz.open(pdf_path)
    pages = []
    cursor = start_page
    for h in headings:
        found = 0
        needle = h.strip()
        for pno in range(cursor, d.page_count):
            page = d[pno]
            hits = page.search_for(needle)
            if not hits and len(needle) > 12:
                hits = page.search_for(needle[:12])
            if hits:
                found = pno + 1
                cursor = pno  # 标题单调不回退
                break
        pages.append(found)
    d.close()
    return pages


def collect_headings(trees: list[DocTree], tpl: DocTemplate) -> list[tuple[str, int]]:
    """[(标题文本, 级别)]，含材料标题（若启用）；与渲染时相同的去重与编号逻辑。"""
    out = []
    for mat_no, tree in enumerate(trees, 1):
        if tpl.insert_material_titles and tree.title:
            label = material_label(tpl.material_title_prefix, mat_no)
            out.append((f"{label}{tree.title}", 1))
        for blk in tree.effective_blocks(tpl.insert_material_titles):
            if blk.type == HEADING and blk.level <= 3:
                out.append((blk.text, blk.level))
    return out


def render_compiled_pdf(trees: list[DocTree], tpl: DocTemplate, out_pdf: str) -> str:
    """两遍渲染：输出带真实目录页码与外侧页码的 A4 PDF。"""
    headings = collect_headings(trees, tpl)

    # ---- 第一遍：占位页码渲染（目录页数与最终一致，分页稳定） ----
    toc_placeholder = [[(t, lv, 0) for (t, lv) in headings]]
    html1 = trees_to_html(trees, tpl, toc_pages=toc_placeholder, toc_placeholder=True)
    _render_html_to_pdf(html1, tpl, out_pdf)

    # 前置页数解析式计算（与 trees_to_html 的分片逻辑一致）：
    # 封面固定 1 页；目录页数 = _toc_chunks 的分片数
    body_start = 0
    if tpl.cover.enabled:
        body_start += 1
    if tpl.toc_enabled:
        body_start += max(1, len(_toc_chunks(len(headings))))

    # 定位正文标题页码（占位渲染已含完整目录页，页码无需偏移）
    pages = _locate_headings(out_pdf, [t for t, _ in headings], start_page=body_start)

    # ---- 第二遍：真实页码渲染（未命中的标题目录页码显示“—”） ----
    toc_real = [[(t, lv, p) for (t, lv), p in zip(headings, pages)]]
    html2 = trees_to_html(trees, tpl, toc_pages=toc_real, toc_placeholder=False)
    _render_html_to_pdf(html2, tpl, out_pdf)

    # ---- 盖页码（外侧：奇右偶左） ----
    if tpl.page_number_enabled:
        stamp_page_numbers(out_pdf, tpl)
    # ---- 水印/密级标注 ----
    if (tpl.watermark_text or "").strip():
        from .watermark import stamp_watermark_pdf
        stamp_watermark_pdf(out_pdf, tpl.watermark_text.strip(),
                            opacity=tpl.watermark_opacity,
                            angle=tpl.watermark_angle)
    return out_pdf


def stamp_page_numbers(pdf_path: str, tpl: DocTemplate, skip_pages: int = 0) -> None:
    """奇数页右下角、偶数页左下角盖页码 “— N —”（从 1 起）。"""
    d = fitz.open(pdf_path)
    fmt = tpl.page_number_format or "— {page} —"
    font = _cjk_font()
    size = tpl.page_number_size_pt
    gray = 0.25
    for pno in range(d.page_count):
        page = d[pno]
        label = fmt.replace("{page}", str(pno + 1))
        rect = page.rect
        bottom = rect.height - max(12, tpl.margin_bottom_mm * _MM * 0.45)
        try:
            tw = fitz.get_text_length(label, fontname=font, fontsize=size)
        except Exception:
            tw = len(label) * size * 0.85  # 宽度估算兜底
        margin = tpl.margin_left_mm * _MM
        if (pno + 1) % 2 == 1:  # 奇数页：右侧
            x = rect.width - margin - tw
        else:                   # 偶数页：左侧
            x = margin
        page.insert_text((x, bottom), label, fontname=font, fontsize=size,
                         color=(gray, gray, gray))
    tmp = pdf_path + ".tmp"
    d.save(tmp, garbage=3, deflate=True)
    d.close()
    import os
    os.replace(tmp, pdf_path)


def _cjk_font() -> str:
    """PyMuPDF 内置中日韩字体名。"""
    return "china-s"
