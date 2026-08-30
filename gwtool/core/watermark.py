# -*- coding: utf-8 -*-
"""水印与密级标注：PDF（PyMuPDF 旋转平铺）+ DOCX（页眉 VML 艺术字）。"""
from __future__ import annotations

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore


def stamp_watermark_pdf(pdf_path: str, text: str, out_path: str = "",
                        opacity: float = 0.12, angle: float = 45.0,
                        fontsize: float = 42.0, tile: bool = True) -> str:
    """在每页盖文字水印（斜向平铺）。默认覆盖原文件。"""
    out = out_path or pdf_path
    import math
    src = fitz.open(pdf_path)
    font = "china-s"
    mat = fitz.Matrix(math.cos(math.radians(angle)), math.sin(math.radians(angle)),
                      -math.sin(math.radians(angle)), math.cos(math.radians(angle)),
                      0, 0)
    for page in src:
        w, h = page.rect.width, page.rect.height
        tw = fitz.get_text_length(text, fontname=font, fontsize=fontsize)
        if tile:
            positions = [(w * f, h * f) for f in (0.25, 0.55, 0.8)]
            positions += [(w * f, h * (f + 0.45)) for f in (0.15, 0.45, 0.75)]
        else:
            positions = [(w / 2 - tw / 2, h / 2)]
        for (px, py) in positions:
            page.insert_text(
                (px, py), text, fontname=font, fontsize=fontsize,
                fill_opacity=opacity, stroke_opacity=opacity,
                color=(0.5, 0.5, 0.5), morph=(fitz.Point(px, py), mat))
    if out != pdf_path:
        src.save(out, garbage=3, deflate=True)
        src.close()
    else:
        tmp = pdf_path + ".wm.tmp"
        src.save(tmp, garbage=3, deflate=True)
        src.close()
        import os
        os.replace(tmp, pdf_path)
    return out


_VML_TPL = """<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:pPr><w:pStyle w:val="a3"/></w:pPr>
<w:r><w:rPr><w:noProof/></w:rPr>
<w:pict xmlns:v="urn:schemas-microsoft-com:vml"
       xmlns:o="urn:schemas-microsoft-com:office:office">
<v:shape id="PowerPlusWaterMarkObject%d" o:spid="_x0000_s20%02d"
  type="#_x0000_t136" style="position:absolute;margin-left:0;margin-top:0;
  width:%dpt;height:%dpt;rotation:%d;z-index:-251654144;
  mso-position-horizontal:center;mso-position-horizontal-relative:margin;
  mso-position-vertical:center;mso-position-vertical-relative:margin"
  o:allowincell="f" fillcolor="silver" stroked="f">
<v:fill opacity=".5"/>
<v:textpath style="font-family:&quot;宋体&quot;;font-size:%dpt" string="%s"/>
</v:shape>
</w:pict>
</w:r></w:p>"""


def add_watermark_docx(docx_path: str, text: str, out_path: str = "",
                       angle: float = 315.0, fontsize: float = 1) -> str:
    """页眉 VML 艺术字水印（Word/WPS 标准做法），默认覆盖原文件。"""
    import re
    from docx import Document
    from docx.oxml import parse_xml

    doc = Document(docx_path)
    for i, sec in enumerate(doc.sections):
        header = sec.header
        header.is_linked_to_previous = False
        xml = _VML_TPL % (i + 1, 49 + i, 420, 180, angle, fontsize, text)
        header._element.append(parse_xml(xml))
    out = out_path or docx_path
    if out != docx_path:
        doc.save(out)
    else:
        doc.save(docx_path)
    return out
