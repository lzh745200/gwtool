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

SUPPORTED_EXTS = {".docx", ".doc", ".wps", ".txt", ".rtf", ".pdf",
                  ".md", ".markdown", ".html", ".htm"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}


@dataclass
class ImportResult:
    path: str
    ok: bool
    tree: DocTree | None
    error: str = ""


_OCR_LANG_HINT = ("检测到 Tesseract 但缺少中文语言包 chi_sim，无法识别中文扫描件。\n"
                  "安装：Windows 重装 Tesseract 安装包并勾选 Chinese Simplified；\n"
                  "麒麟：sudo apt install tesseract-ocr-chi-sim")


def parse_any(path: str, ocr_progress_cb=None) -> ImportResult:
    """解析单文件。ocr_progress_cb(page, total)：扫描件 OCR 逐页进度（可为 None）。"""
    ext = Path(path).suffix.lower()
    try:
        if ext == ".docx":
            tree = parse_docx(path)
        elif ext == ".doc":
            tree = parse_doc(path)
        elif ext == ".wps":
            # WPS 文字格式。两种现实形态，按内容嗅探而非轻信扩展名：
            #   1) OOXML zip（部分金山版本/WPS 兼容模式）-> 直接走 docx 解析；
            #   2) OLE 复合文档（含 MS Works 同扩展名的历史文件）-> 走 .doc
            #      四级降级链（COM 探测顺序 kwps/wps 优先，装了 WPS 的机器
            #      保真度最高；LibreOffice 的 libwps 兜 Works；纯解析+原始
            #      扫描兜底）。
            head = Path(path).open("rb").read(4)
            if head == b"PK" + bytes([3, 4]):  # ZIP 魔数 PK
                tree = parse_docx(path)
            else:
                tree = parse_doc(path)
        elif ext == ".pdf":
            tree = parse_pdf(path)
            if (tree is None or not tree.blocks) and ocr_available():
                # 扫描版 PDF -> OCR（先预检中文包，避免静默空结果）
                from .ocr import has_chi_sim, ocr_pdf
                if not has_chi_sim():
                    return ImportResult(path, False, None, _OCR_LANG_HINT)
                tree = ocr_pdf(path, progress_cb=ocr_progress_cb)
        elif ext in IMAGE_EXTS:
            if not ocr_available():
                return ImportResult(
                    path, False, None,
                    "图片识别需安装 Tesseract OCR（含中文包 chi_sim），"
                    "安装后在「设置 → 系统与安全」中指定路径")
            from .ocr import has_chi_sim, ocr_image
            if not has_chi_sim():
                return ImportResult(path, False, None, _OCR_LANG_HINT)
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
    """仅批量**解析**（不入库！），返回逐文件 ImportResult。

    入库由 UI 层 ImportWorker 负责（解析 -> dao.add_document 含内容指纹去重），
    验收测试用 _import_like_worker 复刻同一链路。误把本函数当"完整导入"用
    会得到全部 ok 但库内 0 篇的假象（第 8 轮压测实测踩中，故在此写明）。
    UI 层请放到工作线程调用，progress_cb(i, total, path)。"""
    results = []
    total = len(paths)
    for i, p in enumerate(paths, 1):
        results.append(parse_any(p))
        if progress_cb:
            progress_cb(i, total, p)
    return results
