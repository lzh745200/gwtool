# -*- coding: utf-8 -*-
"""OCR 扫描件识别（v1.5.0 起为**内置功能**）：Tesseract 5 + chi_sim。

解析顺序：设置中用户指定的路径 > 随包捆绑（Windows 安装器把 Tesseract
整体打进 _MEIPASS/tesseract；Linux deb 内嵌到 /opt/gwtool/ocr/，启动器
已设 PATH/TESSDATA_PREFIX）> 系统 PATH。三种来源都无需用户手工配置。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ..db import dao
from .model import Block, DocTree, HEADING, PARAGRAPH

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore


def _bundled() -> tuple[str, str]:
    """随包捆绑的 (tesseract 可执行文件, TESSDATA_PREFIX)；不存在返回空。"""
    cands: list[tuple[Path, Path]] = []
    exe_dir = Path(sys.executable).resolve().parent
    # Windows onedir / Inno 安装布局：{app}	esseract	esseract.exe
    cands.append((exe_dir / "tesseract" / "tesseract.exe",
                  exe_dir / "tesseract" / "tessdata"))
    # Linux：{install}/ocr/bin/tesseract（deb 装到 /opt/gwtool，.run/便携随目录）
    cands.append((exe_dir / "ocr" / "bin" / "tesseract",
                  exe_dir / "ocr" / "tessdata"))
    cands.append((Path("/opt/gwtool/ocr/bin/tesseract"),
                  Path("/opt/gwtool/ocr/tessdata")))
    for exe, td in cands:
        if exe.exists():
            return str(exe), str(td)
    return "", ""


def _bundled_tessdata() -> str:
    exe, td = _bundled()
    return td if exe and Path(td).exists() else ""


def tesseract_path() -> str:
    """tesseract 可执行文件路径：设置优先，其次随包捆绑，最后系统 PATH。"""
    configured = dao.get_setting("tesseract_path", "")
    if configured and Path(configured).exists():
        return configured
    bundled, _ = _bundled()
    if bundled:
        return bundled
    return shutil.which("tesseract") or ""


def available() -> bool:
    return bool(tesseract_path())


def has_chi_sim(tess: str = "") -> bool:
    tess = tess or tesseract_path()
    if not tess:
        return False
    td = _bundled_tessdata()
    if td:
        os.environ["TESSDATA_PREFIX"] = td
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
    td = _bundled_tessdata()
    if td:
        os.environ["TESSDATA_PREFIX"] = td
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
