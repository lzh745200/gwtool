# -*- coding: utf-8 -*-
"""OCR 扫描件识别（可选增强）：Tesseract 5 + chi_sim。

设计：检测到 tesseract 时启用；PDF 无文字层时 importer 自动走 OCR。
不捆绑二进制——用户在设置中指定 tesseract 路径即可（Win 安装器 /
麒麟 `sudo apt install tesseract-ocr tesseract-ocr-chi-sim`）。
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from ..db import dao
from .model import Block, DocTree, HEADING, PARAGRAPH

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore


def tesseract_path() -> str:
    """tesseract 可执行文件路径：设置优先，其次 PATH。"""
    configured = dao.get_setting("tesseract_path", "")
    if configured and Path(configured).exists():
        return configured
    return shutil.which("tesseract") or ""


def available() -> bool:
    return bool(tesseract_path())


def has_chi_sim(tess: str = "") -> bool:
    tess = tess or tesseract_path()
    if not tess:
        return False
    try:
        out = subprocess.run([tess, "--list-langs"], capture_output=True,
                             timeout=30, text=True, check=False)
        langs = (out.stdout or "") + (out.stderr or "")
        return "chi_sim" in langs
    except Exception:
        return False


def ocr_image(image_path: str, tess: str = "") -> str:
    tess = tess or tesseract_path()
    if not tess:
        raise RuntimeError("未找到 tesseract，请先安装并在设置中指定路径")
    r = subprocess.run(
        [tess, image_path, "stdout", "-l", "chi_sim", "--psm", "6"],
        capture_output=True, timeout=300, check=False)
    return (r.stdout or b"").decode("utf-8", errors="replace").strip()


def ocr_pdf(path: str, dpi: int = 220, progress_cb=None) -> DocTree:
    """整本 PDF 逐页渲染为图片后 OCR，返回结构化 DocTree。"""
    tess = tesseract_path()
    if not tess:
        raise RuntimeError("未找到 tesseract，请先安装并在设置中指定路径")
    tmpdir = Path(tempfile.mkdtemp(prefix="gwtool_ocr_"))
    doc = fitz.open(path)
    tree = DocTree(title=Path(path).stem)
    try:
        for pno in range(doc.page_count):
            if progress_cb:
                progress_cb(pno + 1, doc.page_count)
            pix = doc[pno].get_pixmap(dpi=dpi)
            img = tmpdir / f"p{pno}.png"
            pix.save(str(img))
            text = ocr_image(str(img), tess)
            for line in text.splitlines():
                line = line.strip()
                if not line or len(line) < 2:
                    continue
                tree.blocks.append(Block(type=PARAGRAPH, text=line))
    finally:
        doc.close()
        import shutil as _sh
        _sh.rmtree(tmpdir, ignore_errors=True)
    # 简单标题识别：首行
    if tree.blocks:
        tree.title = tree.blocks[0].text[:50]
        tree.blocks[0].type = HEADING
        tree.blocks[0].level = 1
    return tree
