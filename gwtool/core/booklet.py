# -*- coding: utf-8 -*-
"""小册子（骑马钉）排版：基于 PyMuPDF 页面重排。

规则：先补齐页数为 4 的倍数（末尾补空白页）；每张 A3 横向纸正反面各放两页 A4：
  设总页数 N，第 k 张（0 基）：
    正面 = [N-2k, 2k+1]（左|右）
    背面 = [2k+2, N-2k-1]
  例：8 页小册子输出顺序 8,1 | 2,7 | 6,3 | 4,5。
"""
from __future__ import annotations

import math
from pathlib import Path

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore

A4_WIDTH_PT = 595.2755905511812
A4_HEIGHT_PT = 841.8897637795277
A3_LANDSCAPE = (A4_HEIGHT_PT * 2 * 0 + 1190.5511811023622, A4_HEIGHT_PT)  # (w,h)


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


def make_booklet(src_pdf: str, out_pdf: str, a3_landscape: bool = True) -> int:
    """把 A4 纵向 PDF 重排为骑马钉小册子。返回输出页数。

    输出页 = 两页并排（纵向源页 x2 = A3 横向）。show_pdf_page 等比缩放，
    横向源页也能正确放入（按比例缩小居中）。
    """
    src = fitz.open(src_pdf)
    n = src.page_count
    if n == 0:
        src.close()
        raise ValueError("源 PDF 为空")
    order = booklet_order(n)
    w, h = src[0].rect.width, src[0].rect.height
    # 两页并排：宽 = 2*页宽，高 = 页高
    out_w, out_h = w * 2, h
    dst = fitz.open()
    for pair in order:
        page = dst.new_page(width=out_w, height=out_h)
        half = out_w / 2
        for slot, pageno in enumerate(pair):
            if pageno < 1 or pageno > n:
                continue  # 空白
            sp = src[pageno - 1]
            clip = fitz.Rect(0, 0, sp.rect.width, sp.rect.height)
            target = fitz.Rect(slot * half, 0, (slot + 1) * half, out_h)
            page.show_pdf_page(target, src, pageno - 1, clip=clip,
                               keep_proportion=True)
    dst.save(out_pdf, garbage=3, deflate=True)
    dst.close()
    src.close()
    return len(order)


def export_a4_pdf(src_pdf: str, out_pdf: str) -> int:
    """原样另存（用于统一入口的 A4 输出）；同路径调用直接返回页数。"""
    src = fitz.open(src_pdf)
    n = src.page_count
    if str(Path(src_pdf).resolve()) != str(Path(out_pdf).resolve()):
        src.save(out_pdf, garbage=3, deflate=True)
    src.close()
    return n
