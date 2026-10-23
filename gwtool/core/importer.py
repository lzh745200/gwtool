# -*- coding: utf-8 -*-
"""材料导入调度器：按扩展名分发到对应解析器，统一产出 DocTree。

入库前统一做两件事（v1.9b）
--------------------------
1. **控制字符清洗**：``\\x00`` 这类空字节会一路进库，直到汇编/导出 DOCX 时
   由 python-docx 抛英文 ``ValueError: All strings must be XML compatible``，
   而且**整批汇编一起失败**（一篇坏材料拖垮整批）。清洗收口在本调度器，
   所有格式（含 OCR 结果）都过同一道闸，规则只写一遍。
2. **异常中文化**：``ImportResult.error`` 会原样显示给用户（导入对话框的状态
   列），``ValueError: ...`` 这种英文串对公文场景的用户没有信息量。
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from .formatter import strip_control_chars
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


def sanitize_tree(tree: DocTree) -> int:
    """就地清洗 DocTree 里的控制字符（标题/正文/表格单元格），返回处数。

    只保留 ``\\t`` ``\\n`` ``\\r``；其余 C0 控制符与 DEL 一律去掉
    （``\\x0b``/``\\x0c`` 先转成换行，避免两段粘成一段）。
    规则实现复用 `formatter.strip_control_chars` —— 同一件事只写一遍，
    否则"导入时清一套、一键排版时清另一套"迟早漂移。
    """
    n = 0
    if tree.title:
        tree.title, k = strip_control_chars(tree.title)
        n += k
    for b in tree.blocks:
        if b.text:
            b.text, k = strip_control_chars(b.text)
            n += k
        rows = getattr(b, "rows", None)
        if rows:
            for row in rows:
                for i, cell in enumerate(row):
                    row[i], k = strip_control_chars(cell or "")
                    n += k
    return n


# 常见异常 → 面向用户的中文提示（保留类名与原始信息，便于排障）
_ERR_HINTS: tuple[tuple[type, str], ...] = (
    (FileNotFoundError, "文件不存在或已被移动"),
    (IsADirectoryError, "选中的是文件夹，不是文件"),
    (PermissionError, "没有读取权限（文件可能正被其他程序占用）"),
    (MemoryError, "文件太大，内存不足"),
    (UnicodeError, "文件编码无法识别（可另存为 UTF-8 后重试）"),
    (zipfile.BadZipFile,
     "文件不是有效的 Office 文档（可能已损坏，或扩展名与实际格式不符）"),
)


def friendly_import_error(exc: BaseException) -> str:
    """把解析异常转成一句中文；原始类名与信息附在括号里便于排障。"""
    hint = "文件无法解析（可能已损坏、被加密或是伪装扩展名）"
    for cls, text in _ERR_HINTS:
        if isinstance(exc, cls):
            hint = text
            break
    detail = str(exc).strip()
    tail = (f"（{type(exc).__name__}: {detail}）" if detail
            else f"（{type(exc).__name__}）")
    return hint + tail


def parse_any(path: str, ocr_progress_cb=None, *,
              prefer_doc_com: bool | None = None) -> ImportResult:
    """解析单文件。ocr_progress_cb(page, total)：扫描件 OCR 逐页进度（可为 None）。

    ``prefer_doc_com``：.doc / .wps(OLE) 是否优先走 COM 保真转换。
    None=按 `doc_parser.prefer_com_enabled()`（默认关，见其模块 docstring）；
    显式 True 才拉起 Word/WPS —— 只取纯文本时 COM 毫无收益却慢 2000 倍。
    """
    ext = Path(path).suffix.lower()
    # 先做存在性检查：python-docx 对不存在的文件抛的是
    # ``PackageNotFoundError: Package not found at ...``（英文、且看不出是
    # "文件没了"），这里给一句明确的中文，用户才知道该去确认路径。
    if not Path(path).is_file():
        return ImportResult(
            path, False, None,
            "文件不存在或已被移动（可能已被删除、重命名，或所在盘未连接）")
    try:
        if ext == ".docx":
            tree = parse_docx(path)
        elif ext == ".doc":
            tree = parse_doc(path, prefer_com=prefer_doc_com)
        elif ext == ".wps":
            # WPS 文字格式。两种现实形态，按内容嗅探而非轻信扩展名：
            #   1) OOXML zip（部分金山版本/WPS 兼容模式）-> 直接走 docx 解析；
            #   2) OLE 复合文档（含 MS Works 同扩展名的历史文件）-> 走 .doc
            #      分级链（默认纯 Python；prefer_doc_com=True 时 COM 优先，
            #      装了 WPS 的机器保真度最高，但单份要 1.5~6 秒）。
            with Path(path).open("rb") as fh:
                head = fh.read(4)
            if head == b"PK" + bytes([3, 4]):  # ZIP 魔数 PK
                tree = parse_docx(path)
            else:
                tree = parse_doc(path, prefer_com=prefer_doc_com)
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
        if tree is not None:
            # 控制字符必须在**入库前**清掉：否则 \x00 会一路进库，到最后
            # 汇编导出时才由 python-docx 抛英文 ValueError（整批一起失败）。
            sanitize_tree(tree)
        if tree is None or not tree.blocks:
            # 文字版提取失败的 PDF 且有 OCR 时已兜底；走到这里说明无法识别
            from .ocr import available as _av
            hint = "（扫描件需安装 Tesseract OCR 后重试）" if ext == ".pdf" and not _av() else ""
            return ImportResult(path, False, None, "未提取到文字" + hint)
        return ImportResult(path, True, tree)
    except Exception as exc:  # 单文件失败不影响批处理
        return ImportResult(path, False, None, friendly_import_error(exc))


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
