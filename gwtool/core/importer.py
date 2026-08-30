# -*- coding: utf-8 -*-
"""材料导入调度器：按扩展名分发到对应解析器，统一产出 DocTree。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .model import DocTree
from .parsers.doc_parser import parse_doc
from .parsers.docx_parser import parse_docx
from .parsers.md_html_parser import parse_html, parse_md
from .parsers.pdf_parser import parse_pdf
from .parsers.rtf_parser import parse_rtf
from .parsers.txt_parser import parse_txt

SUPPORTED_EXTS = {".docx", ".doc", ".txt", ".rtf", ".pdf", ".md", ".markdown",
                  ".html", ".htm"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}


@dataclass
class ImportResult:
    path: str
    ok: bool
    tree: DocTree | None
    error: str = ""


def parse_any(path: str) -> ImportResult:
    ext = Path(path).suffix.lower()
    try:
        if ext == ".docx":
            tree = parse_docx(path)
        elif ext == ".doc":
            tree = parse_doc(path)
        elif ext == ".pdf":
            tree = parse_pdf(path)
            if (tree is None or not tree.blocks) and ocr_available():
                # 扫描版 PDF -> OCR
                from .ocr import ocr_pdf
                tree = ocr_pdf(path)
        elif ext in IMAGE_EXTS:
            if not ocr_available():
                return ImportResult(
                    path, False, None,
                    "图片识别需安装 Tesseract OCR（含中文包 chi_sim），"
                    "安装后在「设置 → 系统与安全」中指定路径")
            from .ocr import ocr_image
            text = ocr_image(path)
            tree = _text_to_tree(text, title_hint=Path(path).stem)
        elif ext == ".txt":
            tree = parse_txt(path)
        elif ext == ".rtf":
            tree = parse_rtf(path)
        elif ext in (".md", ".markdown"):
            tree = parse_md(path)
        elif ext in (".html", ".htm"):
            tree = parse_html(path)
        else:
            return ImportResult(path, False, None, f"不支持的格式：{ext}")
        if tree is None or not tree.blocks:
            # 文字版提取失败的 PDF 且有 OCR 时已兜底；走到这里说明无法识别
            from .ocr import available as _av
            hint = "（扫描件需安装 Tesseract OCR 后重试）" if ext == ".pdf" and not _av() else ""
            return ImportResult(path, False, None, "未提取到文字" + hint)
        return ImportResult(path, True, tree)
    except Exception as exc:  # 单文件失败不影响批处理
        return ImportResult(path, False, None, f"{type(exc).__name__}: {exc}")


def ocr_available() -> bool:
    try:
        from .ocr import available
        return available()
    except Exception:
        return False


def _text_to_tree(text: str, title_hint: str = "") -> DocTree:
    from .model import Block, HEADING, PARAGRAPH
    from .parsers.txt_parser import _HEADING_RE
    tree = DocTree(title=title_hint)
    first = True
    for para in [p.strip() for p in text.split("\n")]:
        if not para:
            continue
        if first and len(para) <= 50:
            tree.title = para
            tree.blocks.append(Block(type=HEADING, level=1, text=para))
            first = False
        elif _HEADING_RE.match(para) and len(para) <= 40:
            tree.blocks.append(Block(type=HEADING, level=2, text=para))
        else:
            tree.blocks.append(Block(type=PARAGRAPH, text=para))
        first = False
    return tree


def batch_import(paths: list[str], progress_cb=None) -> list[ImportResult]:
    """同步批量导入。UI 层请放到工作线程调用，progress_cb(i, total, path)。"""
    results = []
    total = len(paths)
    for i, p in enumerate(paths, 1):
        results.append(parse_any(p))
        if progress_cb:
            progress_cb(i, total, p)
    return results
