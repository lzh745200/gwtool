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

import os


try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore

from PySide6.QtCore import QMarginsF, QByteArray, QSizeF
from PySide6.QtGui import QFont, QPainter, QPageLayout, QPageSize, QPdfWriter, QTextDocument

from . import compile_norm
from .model import DocTree, HEADING, LIST_ITEM, TABLE
from .template import DocTemplate, cover_visible, material_label

_MM = 2.834645669  # mm -> pt

# 目录每页行数（28磅行距 A4 经验值；首页扣除目录标题约占 2 行）
_TOC_LINES_PER_PAGE = 22

# 强制分页的 HTML 占位块（Qt 富文本只支持 page-break-before）。
# v1.15：目录/正文分节、封面与目录之间都靠它分页；必须定义在模块级，
# 早前重构时它被误删，导致 render 时 NameError（已由单测覆盖）。
_PAGE_BREAK = ("<p style='page-break-before:always;font-size:1pt;"
               "line-height:0;margin:0'>&nbsp;</p>")


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

    ⚠ 行距：下面 CSS 里的 `line-height:{line_spacing_pt}pt` 只为外部消费者
    （如编辑器预览的 HTML）保留语义，**Qt 富文本并不支持该属性**（实测
    line_spacing 20/28/40 三档渲染结果逐页相同）。真正生效的是
    `_apply_fixed_line_height()` 里的 QTextBlockFormat 固定行高。
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
      /* v1.15：封面题字与材料/公文标题都用小标宋，与 docx 侧同字体，
         否则同一份汇编导出两个格式，标题字形不一样。 */
      .cover-title {{ font-family:'{_font_family(tpl.doc_title.font, "宋体")}';
                     font-size:30pt; text-align:center; margin-top:110pt; }}
      table.toc {{ width:100%; }}
      table.toc td {{ font-size:{tpl.body_size_pt}pt; padding:1pt 0; }}
    </style>"""
    parts = [css]
    has_front = False

    # ---- 封面 ----
    # 封面文字全部来自用户输入（标题、单位、落款，以及 P1-6 新增的文种/密级/
    # 发文字号），**必须逐项 `_esc`**：Qt 的 `setHtml` 会把 `<b>` 当标签渲染，
    # 用户本想打「<试行>」结果整段被吞掉；更糟的是内容里若带 `<img src=...>`
    # 之类的标签，就成了一个可被外部文本触发的本地渲染面。
    # `cover_visible` 与 docx 共用同一判据（要素全空时不出封面页），
    # 保证同一份内容导出 docx/PDF 的封面与页数一致。
    has_cover = cover_visible(tpl)
    if has_cover:
        parts.append(f"<p class='cover-title'>{_esc(tpl.cover.title or tpl.name)}</p>")
        if tpl.cover.subtitle:
            parts.append("<p style='text-align:center;text-indent:0'>"
                         f"{_esc(tpl.cover.subtitle)}</p>")
        parts.append("<p style='margin-top:170pt'></p>")
        for line in [tpl.cover.org, tpl.cover.date, *tpl.cover.extra_lines]:
            # `strip()` 而非直接判真：只含空格的行会被渲染成一个空段落，
            # 悄悄把封面下沿往下顶（P1-6 的"留空行为不变"要求连这种情况也一致）。
            if (line or "").strip():
                parts.append("<p style='text-align:center;text-indent:0'>"
                             f"{_esc(line)}</p>")
        has_front = True

    # ---- 目录（独立页；行数超一页时按 _toc_chunks 分页） ----
    if tpl.toc_enabled:
        if has_cover:
            parts.append(_PAGE_BREAK)
        parts.append(f"<h1 style='text-align:center;text-indent:0'>{tpl.toc_title}</h1>")
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
                parts.append(_PAGE_BREAK)
            parts.append(f"<table class='toc' border='0'>"
                         f"{''.join(rows[pos:pos + size])}</table>")
            pos += size
        has_front = True

    # ---- 红头（正文首页顶部；无封面时紧随目录） ----
    if tpl.red_header.enabled:
        if has_front:
            parts.append(_PAGE_BREAK)
        parts.append(f"<p class='red-org'>{tpl.red_header.org}</p>")
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
        has_mat_title = bool(tpl.insert_material_titles and (tree.title or "").strip())
        # 组装 (tag, inner_html, extra_style) 元素序列
        elems: list[tuple[str, str, str]] = []
        if has_mat_title:
            label = material_label(tpl.material_title_prefix, idx + 1)
            # v1.15：材料/公文标题用小标宋二号并居中（GB/T 9704），与 docx 侧
            # 同一判据（`tpl.doc_title`）；内联样式而非 class，因为下面的
            # 页首分页逻辑要往同一个 style 串前面拼 page-break-before。
            elems.append(("h1", _esc(f"{label}{tree.title}"), _doc_title_style(tpl)))
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
                # level 可能是 0 或 >4（导入侧未规范化）：夹到 1..4，
                # 否则会拼出 Qt 不认识的 <h0> 标签，标题静默丢失。
                raw = min(max(int(blk.level or 0), 1), 4)
                # 字体按**公文序号**判定（一、黑体 / （一）楷体 / 1. 仿宋加粗），
                # 与 docx 侧同一判据；序号判不出时才退回"层级 − shift"。
                off = compile_norm.official_level(blk.text)
                style_lv = off if off else min(max(raw - (1 if has_mat_title else 0), 1), 3)
                style_lv = min(max(style_lv, 1), 3)
                elems.append((f"h{style_lv}", t, ""))
            elif blk.type == LIST_ITEM:
                elems.append(("p", f"• {t}", f"text-indent:{indent_px}px"))
            else:
                elems.append(("p", t, ""))
        for i, (tag, inner, extra) in enumerate(elems):
            style = extra
            if i == 0 and first_break:
                # v1.15 根治：分页一律由**独立的空占位块**承担，内容块本身不再
                # 带 page-break-before —— 带分页属性的块会被 Qt 在上一页底部
                # 再画一遍（材料标题因此重影到上一页）；占位块是空的，
                # 即便真有重复也看不见。
                parts.append(_PAGE_BREAK)
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


def _doc_title_style(tpl: DocTemplate) -> str:
    """材料/公文标题的 CSS（小标宋二号、居中）——与 docx 的 `tpl.doc_title` 对齐。"""
    weight = "bold" if tpl.doc_title.bold else "normal"
    return (f"font-family:'{_font_family(tpl.doc_title.font, '宋体')}';"
            f"font-size:{tpl.doc_title.size_pt}pt;font-weight:{weight};"
            "text-align:center;text-indent:0;margin:10pt 0 8pt 0")


# ------------------------------------------------------------------ 行距（P1-02）
# Qt 富文本**不支持** CSS `line-height`：实测模板 line_spacing 20/28/40 三档
# 渲染出的 PDF 逐页完全相同（恒 ~20.6pt、30~31 行/页），版心"22 行 × 28 字"
# 在 PDF 通路上根本不成立。真正生效的是块级格式
# `QTextBlockFormat.setLineHeight(FixedHeight)`，见 _apply_fixed_line_height。
_PX_PER_PT = 96.0 / 72.0      # 与 _render_html_to_pdf 里的 writer.setResolution(96) 一致


def _fixed_height_enum() -> int:
    """`QTextBlockFormat.FixedHeight` 的 int 值（跨 PySide6 版本的取值方式）。

    PySide6 6.x 把它暴露成真枚举（需要 `.value` 才能过 setLineHeight 的
    `heightType: int` 重载），老版本/其他绑定可能本身就是 int —— 两种都兼容。
    """
    from PySide6.QtGui import QTextBlockFormat
    val = QTextBlockFormat.FixedHeight
    return int(getattr(val, "value", val))


def _table_block_ranges(doc) -> list[tuple[int, int]]:
    """文档里所有表格占用的块序号区间（含嵌套表）。

    为什么要把表格挑出来：目录与正文表格的行高由单元格内容决定，套上固定
    行高会把表格撑高甚至撑破一页（目录表还依赖 ~22 行/页的分片假设）；而
    表格的"行"本来就不是正文行距的语义。
    """
    from PySide6.QtGui import QTextTable

    ranges: list[tuple[int, int]] = []
    stack = [doc.rootFrame()]
    while stack:
        frame = stack.pop()
        for child in frame.childFrames():
            if isinstance(child, QTextTable):
                first = child.firstCursorPosition().blockNumber()
                last = child.lastCursorPosition().blockNumber()
                ranges.append((first, last))
            else:
                stack.append(child)
    return ranges


def _apply_fixed_line_height(doc, tpl: DocTemplate) -> int:
    """把段落行距真正固定为模板的 `line_spacing_pt`，返回改动的块数。

    Qt 的 HTML/CSS 子集里没有 `line-height`，模板行距此前是**完全没生效**的
    （三档参数输出一致）。这里逐块写 `QTextBlockFormat`：

        fmt.setLineHeight(line_spacing_pt * 96/72, FixedHeight)

    只改满足两个条件的块，避免把版式改坏：
      · **排除表格块**（见 `_table_block_ranges`，目录/正文表格保持自然行高，
        目录分页假设与两遍渲染的页数计算都不受影响）；
      · **字号必须能塞进固定行高**：`字号 × 1.3 ≤ 行距`（1.3 是 CJK 字体自然
        行高系数）。封面题字(28pt)、红头机关标志(36pt) 因此保持自然行高，
        不会被固定行高压扁；而 2pt/4pt 的红色分隔线占位、空段落也按字号下限
        排除，避免把 2pt 的线撑成一行 28pt 的空白。
    """
    from PySide6.QtGui import QTextCursor

    fixed_px = tpl.line_spacing_pt * _PX_PER_PT
    height_type = _fixed_height_enum()
    ranges = _table_block_ranges(doc)
    min_pt = tpl.body_size_pt * 0.75          # 小于正文字号的 3/4：装饰性占位
    max_pt = tpl.line_spacing_pt / 1.3        # 再大就会被固定行高压扁
    changed = 0
    block = doc.begin()
    while block.isValid():
        number = block.blockNumber()
        point = block.charFormat().fontPointSize() or tpl.body_size_pt
        if (block.text().strip()
                and min_pt <= point <= max_pt
                and not any(lo <= number <= hi for lo, hi in ranges)):
            fmt = block.blockFormat()
            if fmt.lineHeightType() != height_type or abs(fmt.lineHeight() - fixed_px) > 0.5:
                fmt.setLineHeight(fixed_px, height_type)
                QTextCursor(block).setBlockFormat(fmt)
                changed += 1
        block = block.next()
    return changed



def _render_html_to_pdf(html: str, tpl: DocTemplate, out_pdf: str) -> int:
    """手动分页绘制 QTextDocument。

    不能用 doc.print_(writer)：Qt 会自动在每页尾部附加页码且无法关闭。
    手动方式：按内容区分页，逐页 translate + clip 后调用 documentLayout().draw。

    行距（P1-02）：`setHtml` 之后、`setPageSize` 之前必须调用
    `_apply_fixed_line_height` —— Qt 不吃 CSS `line-height`，只有块级
    `setLineHeight(FixedHeight)` 才会改变实际行高与分页；放在 setPageSize
    之前是为了让分页按新行高重算（`doc.pageCount()` 才准）。
    """
    from PySide6.QtCore import QRectF

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
    # 版心（P1-02）：QTextDocument 默认带 4px 的 documentMargin，内容区因此比
    # 模板版心窄 8px（实测红色分隔线——`width=100%` 的全宽元素——只到
    # 435.8pt / 154.0mm，左边界 82.5pt / 29.1mm；置 0 后 441.8pt / 155.9mm、
    # 左边界 79.5pt / 28.0mm）。"版心 156mm × 225mm"因此才算真正成立。
    doc.setDocumentMargin(0)
    doc.setHtml(html)
    _apply_fixed_line_height(doc, tpl)
    doc.setPageSize(QSizeF(page_rect.width(), page_rect.height()))

    painter = QPainter(writer)
    try:
        n = max(1, doc.pageCount())
        for page in range(n):
            if page > 0:
                writer.newPage()
            painter.save()
            # QPdfWriter 画笔原点已在内容区左上角（边距已含），仅需纵向逐页偏移
            painter.translate(0, -page * page_rect.height())
            # v1.15 根治：`drawContents(rect)` 逐页只绘制该页区域。
            # 旧实现是"整篇文档 + 只给 PaintContext 一个 clip"，而 ctx.clip
            # **只裁剪绘制区域、不阻止落笔** —— 下一页的行会被画进上一页底部：
            # 60 段样张实测第 4 页 23 行（超出版心 22 行容量）、封面页底部还
            # 印出了目录表。drawContents 在布局层按矩形裁剪，同一样张实测
            # 恢复为每页 ≤22 行、正文页无跨页文字外溢。
            doc.drawContents(painter, QRectF(0, page * page_rect.height(),
                                             page_rect.width(),
                                             page_rect.height()))
            painter.restore()
    finally:
        painter.end()
    return _page_count(out_pdf)


# ---------------------------------------------------------- 封面页重影清理（v1.15）
# 现象：手动逐页绘制时，**下一页的第一行文字会在上一页底部再画一遍**。
# 实测（探针）：带封面的 3 页产物里，封面页文本 = "材料汇编 目 录" ——
# 目录标题既在目录页首行（正确），又被画到封面页版心最下方（错误、且像素级
# 可见）；换成 doc.print_()/drawContents() 同样复现，属 Qt 富文本分页绘制的
# 表现，绘制层（ctx.clip 只裁剪绘制区域、不阻止落笔）无法消除。
#
# 这里只清理**封面页**：封面是唯一有"确定性判据"的页面 —— 封面要素（汇编名/
# 单位/日期）只出现在页面中上部，若封面页**底部窄带**里出现了与下一页顶部
# 完全相同的文字，那必然是跨页重影，删掉它不会伤到任何封面要素。
# 正文页不做同样处理：正文里同一句话在相邻两页重复出现是完全正常的（公文
# 套话），按"文本相同"删会误删正文，代价远大于收益 —— 已在审计报告里作为
# 既有问题另行记录。
_BLEED_BAND_PT = 80.0      # 底部判定窄带（约 3 行 28 磅行距）


def _text_spans(page) -> list[tuple["fitz.Rect", str]]:
    out: list[tuple[fitz.Rect, str]] = []
    for blk in page.get_text("dict").get("blocks", []):
        for line in blk.get("lines", []):
            for span in line.get("spans", []):
                text = (span.get("text") or "").strip()
                if text:
                    out.append((fitz.Rect(span["bbox"]), text))
    return out


def _strip_cover_page_bleed(pdf_path: str, tpl: DocTemplate) -> int:
    """抹掉封面页底部的跨页重影文字，返回抹掉的行数。

    只在"模板带封面且产物 ≥2 页"时生效；判据是**封面页底部窄带**里的文字与
    **下一页顶部**的文字相同（比较前去掉所有空白：重影是同一段文字第二次落笔，
    字号/字距会有细微差异，实测同一串"目  录"在上一页抽出来是"目录"、在下一页
    是"目 录"，按字面比对会漏掉）。封面要素不会出现在底部窄带里，因此不会误删。
    覆盖写盘走 tmp + os.replace（与 stamp_page_numbers 同一纪律）。
    """
    if not cover_visible(tpl):
        return 0
    doc = fitz.open(str(pdf_path))
    tmp = ""
    removed = 0
    try:
        if doc.page_count < 2:
            return 0
        top = tpl.margin_top_mm * _MM
        bottom = doc[0].rect.height - tpl.margin_bottom_mm * _MM

        def _key(text: str) -> str:
            return "".join(text.split())

        next_top = {_key(t) for r, t in _text_spans(doc[1])
                    if r.y0 < top + _BLEED_BAND_PT}
        if not next_top:
            return 0
        targets = [r for r, t in _text_spans(doc[0])
                   if r.y1 > bottom - _BLEED_BAND_PT and _key(t) in next_top]
        if not targets:
            return 0
        page = doc[0]
        for rect in targets:
            page.add_redact_annot(rect, fill=False)
        # PyMuPDF 1.2x：redaction 是**页级** API（Document 上的同名方法已移除）
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
        removed = len(targets)
        tmp = pdf_path + ".bleed.tmp"
        doc.save(tmp, garbage=3, deflate=True)
    finally:
        doc.close()
    if tmp:
        os.replace(tmp, pdf_path)
    return removed


def _page_count(pdf_path: str) -> int:
    d = fitz.open(pdf_path)
    try:
        return d.page_count
    finally:
        d.close()


def _locate_headings(pdf_path: str, headings: list[str], start_page: int = 0) -> list[int]:
    """按顺序在 PDF 中定位标题所在页（1 基，从 start_page 起跳过前置页）。

    未找到返回 0（目录页码显示“—”），不再强归第 1 页；
    匹配先全标题、再 12 字前缀，重名标题靠单调游标按顺序消歧。
    """
    d = fitz.open(pdf_path)
    try:
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
        return pages
    finally:
        d.close()


def collect_headings(trees: list[DocTree], tpl: DocTemplate) -> list[tuple[str, int]]:
    """[(标题文本, 级别)]，含材料标题（若启用）；与渲染时相同的去重与编号逻辑。

    v1.15 健壮性：只认 `Block.type == HEADING`，并且
      · 空标题跳过（否则目录里出现空行、还会占一行分页预算）；
      · `level` 夹到 1..3（导入侧没规范化时常见 0）；>3 的块不进目录，
        与 docx 侧 `TOC \\o "1-3"` 的口径一致。
    规范化（T2）未接入、标题全是 paragraph 时，这里自然只收到材料标题，
    不会崩。
    """
    out = []
    for mat_no, tree in enumerate(trees, 1):
        has_mat_title = bool(tpl.insert_material_titles and (tree.title or "").strip())
        if has_mat_title:
            label = material_label(tpl.material_title_prefix, mat_no)
            out.append((f"{label}{tree.title}".strip(), 1))
        shift = 1 if has_mat_title else 0
        for blk in tree.effective_blocks(tpl.insert_material_titles):
            if blk.type != HEADING:
                continue
            text = (blk.text or "").strip()
            if not text:
                continue
            raw = max(int(blk.level or 1), 1)
            # 目录按**公文层级**过滤（材料标题另占一级）：默认 2 收
            # 一、/（一），1. 枚举不进目录；toc_max_level=3 时三级才进。
            # 显示层级用 raw（含材料标题的偏移），保证目录缩进能体现
            # 材料标题 → 一、 → （一）的父子关系；封顶 4 防止异常层级撑破缩进。
            pl = min(max(raw - shift, 1), 3)
            max_level = max(1, min(3, int(getattr(tpl, "toc_max_level", 2))))
            if pl > max_level:
                continue
            out.append((text, min(raw, 4)))
    return out


def render_compiled_pdf(trees: list[DocTree], tpl: DocTemplate, out_pdf: str) -> str:
    """两遍渲染：输出带真实目录页码与外侧页码的 A4 PDF。

    页序：封面 → 目录 → 正文（红头与正文首页同页）。
    页码（v1.15）：封面与目录**不编号**，正文首页为"— 1 —"并连续到最后；
    目录里写的是**正文页序**（与成品纸面页码一致），不是 PDF 的物理页序。
    """
    headings = collect_headings(trees, tpl)

    # ---- 第一遍：占位页码渲染（目录页数与最终一致，分页稳定） ----
    toc_placeholder = [[(t, lv, 0) for (t, lv) in headings]]
    html1 = trees_to_html(trees, tpl, toc_pages=toc_placeholder, toc_placeholder=True)
    _render_html_to_pdf(html1, tpl, out_pdf)
    # 封面页重影清理（v1.15）：第一遍也清 —— 否则"下一页首行"被重复画到封面
    # 底部时，_locate_headings 可能把标题定位到早一页。
    _strip_cover_page_bleed(out_pdf, tpl)

    # 前置页数解析式计算（与 trees_to_html 的分片逻辑一致）：
    # 封面按 cover_visible 判（要素全空不出页，见 template.cover_visible）；
    # 目录页数 = _toc_chunks 的分片数
    body_start = 0
    if cover_visible(tpl):
        body_start += 1
    if tpl.toc_enabled:
        body_start += max(1, len(_toc_chunks(len(headings))))

    # 定位正文标题页码（占位渲染已含完整目录页，页码无需偏移）
    pages = _locate_headings(out_pdf, [t for t, _ in headings], start_page=body_start)
    # 物理页号 -> 纸面页码：正文从 1 起算，前置页（封面/目录）不计入
    printed = [(p - body_start) if p > body_start else 0 for p in pages]

    # ---- 第二遍：真实页码渲染（未命中的标题目录页码显示“—”） ----
    toc_real = [[(t, lv, p) for (t, lv), p in zip(headings, printed)]]
    html2 = trees_to_html(trees, tpl, toc_pages=toc_real, toc_placeholder=False)
    _render_html_to_pdf(html2, tpl, out_pdf)
    _strip_cover_page_bleed(out_pdf, tpl)   # 成品同样清理（封面页不得混入目录标题）

    # ---- 盖页码（封面/目录跳过；外侧：奇右偶左，按**纸面页码**判奇偶） ----
    if tpl.page_number_enabled:
        stamp_page_numbers(out_pdf, tpl, skip_pages=body_start)
    # ---- 水印/密级标注 ----
    if (tpl.watermark_text or "").strip():
        from .watermark import stamp_watermark_pdf
        stamp_watermark_pdf(out_pdf, tpl.watermark_text.strip(),
                            opacity=tpl.watermark_opacity,
                            angle=tpl.watermark_angle)
    return out_pdf


def stamp_page_numbers(pdf_path: str, tpl: DocTemplate, skip_pages: int = 0) -> None:
    """盖页码"— N —"：奇数页右下、偶数页左下（外侧）。

    参数
    ----
    skip_pages : 前置页数（封面 + 目录）。前 skip_pages 页**不盖任何页码**，
                 之后从"— 1 —"开始连续编号；左右位置按**纸面页码**的奇偶判定
                 （规范里"奇数页在右"指的是页码为奇数的页，不是物理页序）。

    写临时文件再 os.replace：save 中途失败（磁盘满、文件被占用）时原 PDF
    保持完整，不会留下半截文件；文档对象用 try/finally 关闭，避免句柄泄漏。
    """
    d = fitz.open(pdf_path)
    try:
        fmt = tpl.page_number_format or "— {page} —"
        font = _cjk_font()
        size = tpl.page_number_size_pt
        gray = 0.25
        skip = max(0, int(skip_pages or 0))
        for pno in range(d.page_count):
            if pno < skip:
                continue                       # 封面/目录：不编号
            page = d[pno]
            printed = pno - skip + 1           # 纸面页码：正文从 1 起
            label = fmt.replace("{page}", str(printed))
            rect = page.rect
            bottom = rect.height - max(12, tpl.margin_bottom_mm * _MM * 0.45)
            try:
                tw = fitz.get_text_length(label, fontname=font, fontsize=size)
            except Exception:
                tw = len(label) * size * 0.85  # 宽度估算兜底
            margin = tpl.margin_left_mm * _MM
            if printed % 2 == 1:  # 奇数页：右侧
                x = rect.width - margin - tw
            else:                 # 偶数页：左侧
                x = margin
            page.insert_text((x, bottom), label, fontname=font, fontsize=size,
                             color=(gray, gray, gray))
        tmp = pdf_path + ".tmp"
        try:
            d.save(tmp, garbage=3, deflate=True)
        except BaseException:
            import os
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    finally:
        d.close()
    import os
    os.replace(tmp, pdf_path)


def _cjk_font() -> str:
    """PyMuPDF 内置中日韩字体名。"""
    return "china-s"
