# -*- coding: utf-8 -*-
"""小册子（骑马钉）2-up 拼版：A4 纵向页 → A3 横向对开页，基于 PyMuPDF 重排。

拼版规则（与 `booklet_order` 一致，1 基页码，0 = 空白）：
  先把页数补齐到 4 的倍数（末尾补空白），设补齐后 N 页，第 k 张纸：
    正面 = [N-2k, 2k+1]（左|右）
    背面 = [2k+2, N-2k-1]
  例：8 页小册子输出顺序 8,1 | 2,7 | 6,3 | 4,5。

印张推导（`imposition_plan`）：
  源 N 页 → 补白 P = ceil(N/4)*4 页 → 印张 = P/4 张 A3 纸（双面）= P/2 个 A3 页面。

装订边安全（`make_booklet` 的 `gutter_pt`/`safety_pt`）：
  骑马钉对折后，书脊在 A3 正中间。左右两页靠近书脊的那条边各让出 `gutter_pt`，
  避免正文/页码被折线吃掉；`safety_pt` 再给外缘留一点余量（打印裁切误差）。
  源页按 `keep_proportion` 等比缩放并居中放进让出的框内 —— 缩放只缩不放，
  A3 一半=595×842pt 与 A4 同尺寸，正常情况是 1:1。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore

A4_WIDTH_PT = 595.2755905511812
A4_HEIGHT_PT = 841.8897637795277
# A3 横向 = 两张 A4 纵向并排（精确值，不靠源页尺寸推导）
A3_LANDSCAPE = (A4_WIDTH_PT * 2, A4_HEIGHT_PT)          # (1190.55, 841.89)
A4_LANDSCAPE = (A4_HEIGHT_PT, A4_WIDTH_PT)              # 备用：A4 横向对开

DEFAULT_GUTTER_PT = 6.0     # 书脊侧让出的装订边
DEFAULT_SAFETY_PT = 2.0     # 外缘安全边（裁切/打印误差）


def booklet_order(n_pages: int) -> list[list[int]]:
    """返回各输出页的 (左页码, 右页码)（1 基页码；0 表示空白）。

    输出顺序：第1张正面 [N,1]、第1张背面 [2,N-1]、第2张正面 [N-2,3] ...
    """
    if n_pages <= 0:
        return []
    n = math.ceil(n_pages / 4) * 4
    out: list[list[int]] = []
    k = 0
    while 2 * k + 1 <= n // 2:
        front = [n - 2 * k if n - 2 * k >= 1 else 0, 2 * k + 1]
        back = [2 * k + 2 if 2 * k + 2 <= n else 0, n - 2 * k - 1 if n - 2 * k - 1 >= 1 else 0]
        out.append(front)
        out.append(back)
        k += 1
    return out


@dataclass
class ImpositionPlan:
    """页数 → 印张的推导结果（给 UI/报告用，便于解释"为什么多了两页空白"）。"""

    src_pages: int
    padded_pages: int          # 补齐到 4 的倍数后的页数
    sheets: int                # A3 纸张数（双面）
    output_pages: int          # A3 产物页数（= sheets * 2，必为偶数）
    blank_slots: int           # 补出来的空白位


def imposition_plan(n_pages: int) -> ImpositionPlan:
    """页数 → 印张推导：补白页数、张数、A3 页数、空白位。"""
    n = max(0, int(n_pages))
    padded = math.ceil(n / 4) * 4 if n else 0
    order = booklet_order(n)
    return ImpositionPlan(
        src_pages=n,
        padded_pages=padded,
        sheets=padded // 4,
        output_pages=len(order),
        blank_slots=padded - n,
    )


def _slot_rect(sheet_w: float, sheet_h: float, slot: int,
               gutter_pt: float, safety_pt: float) -> "fitz.Rect":
    """一个 A3 页面上左/右半幅里"允许放内容"的矩形（已扣除装订边与安全边）。"""
    half = sheet_w / 2
    left = slot * half
    right = left + half
    # 书脊侧：左半幅的右缘、右半幅的左缘，各让出 gutter
    inner_left = left + (gutter_pt if slot == 1 else safety_pt)
    inner_right = right - (gutter_pt if slot == 0 else safety_pt)
    return fitz.Rect(inner_left, safety_pt, inner_right, sheet_h - safety_pt)


def make_booklet(src_pdf: str, out_pdf: str, a3_landscape: bool = True,
                 gutter_pt: float = DEFAULT_GUTTER_PT,
                 safety_pt: float = DEFAULT_SAFETY_PT) -> int:
    """把 A4 纵向 PDF 重排为骑马钉小册子，返回**输出页数**。

    a3_landscape=True（默认，公文场景）：输出 A3 横向 1190.55×841.89pt，
        每页左右各放一页 A4；
    a3_landscape=False：输出 A4 横向对开（841.89×595.28pt），用于只有 A4
        打印机、打算"对折装订"的场景 —— 两页被等比缩小到一半幅面。

    `show_pdf_page` 等比缩放（`keep_proportion`），横向源页也能正确放入；
    空白位（页号 0）不画任何东西，保持纸张全白。

    两个文档都用 try/finally 关闭：save/show_pdf_page 抛异常（磁盘满、
    源页损坏、输出被占用）时若不关，PyMuPDF 文档与其底层句柄会一起泄漏，
    长会话里反复导出会累积到句柄耗尽。
    """
    src = fitz.open(src_pdf)
    try:
        n = src.page_count
        if n == 0:
            raise ValueError("源 PDF 为空")
        order = booklet_order(n)
        out_w, out_h = A3_LANDSCAPE if a3_landscape else A4_LANDSCAPE
        dst = fitz.open()
        try:
            for pair in order:
                page = dst.new_page(width=out_w, height=out_h)
                for slot, pageno in enumerate(pair):
                    if pageno < 1 or pageno > n:
                        continue  # 空白位：保持白纸
                    sp = src[pageno - 1]
                    clip = fitz.Rect(0, 0, sp.rect.width, sp.rect.height)
                    target = _slot_rect(out_w, out_h, slot, gutter_pt, safety_pt)
                    page.show_pdf_page(target, src, pageno - 1, clip=clip,
                                       keep_proportion=True)
            dst.save(out_pdf, garbage=3, deflate=True)
        finally:
            dst.close()
        return len(order)
    finally:
        src.close()


def export_a4_pdf(src_pdf: str, out_pdf: str) -> int:
    """原样另存（A4 单页输出）；同路径调用直接返回页数。"""
    src = fitz.open(src_pdf)
    try:
        n = src.page_count
        if str(Path(src_pdf).resolve()) != str(Path(out_pdf).resolve()):
            src.save(out_pdf, garbage=3, deflate=True)
        return n
    finally:
        src.close()


def make_booklet_pair(src_pdf: str, out_a4_pdf: str, out_a3_pdf: str,
                      gutter_pt: float = DEFAULT_GUTTER_PT,
                      safety_pt: float = DEFAULT_SAFETY_PT) -> dict:
    """A4 与 A3 双产物一次性输出（"一键汇编"两个格式都要）。

    返回 `{a4_pages, a3_pages, plan}`：
      · A4：手工装订/正式存档用，页序保持阅读顺序；
      · A3：骑马钉对开，页序按 `booklet_order` 重排。
    """
    a4_pages = export_a4_pdf(src_pdf, out_a4_pdf)
    a3_pages = make_booklet(src_pdf, out_a3_pdf,
                            gutter_pt=gutter_pt, safety_pt=safety_pt)
    return {"a4_pages": a4_pages, "a3_pages": a3_pages,
            "plan": imposition_plan(a4_pages)}
