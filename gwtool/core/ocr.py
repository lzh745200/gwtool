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


def _tess_env(tess: str) -> dict:
    """构造子进程环境：TESSDATA_PREFIX 只传给 tesseract，不改进程全局。

    为什么不写 os.environ：那是**进程级**全局状态，而 OCR 会在后台线程里跑
    （ImportWorker / FnWorker 批量导入扫描件），用户同时可能点"检测中文包"。
    两条路径并发写同一个键时，subprocess 读到的可能是对方刚写进去的值，
    还会覆盖用户手工设置。改成随 env 传参后，各次调用互不影响。
    """
    env = dict(os.environ)
    td = _bundled_tessdata()
    if td:
        env["TESSDATA_PREFIX"] = td
    return env


def has_chi_sim(tess: str = "") -> bool:
    tess = tess or tesseract_path()
    if not tess:
        return False
    try:
        out = subprocess.run([tess, "--list-langs"], capture_output=True,
                             timeout=30, text=True, check=False,
                             env=_tess_env(tess))
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
        capture_output=True, timeout=300, check=False, env=_tess_env(tess))
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
            for raw_line in text.splitlines():
                # 不覆盖循环变量本身：那是"改了别名而非元素"的经典写法，
                # 后续若在循环体里再用 line 就会拿到被裁剪过的值（PLW2901）。
                stripped = raw_line.strip()
                if not stripped or len(stripped) < 2:
                    continue
                tree.blocks.append(Block(type=PARAGRAPH, text=stripped))
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
