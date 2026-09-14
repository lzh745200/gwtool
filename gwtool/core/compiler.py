# -*- coding: utf-8 -*-
"""一键汇编编排：所选材料 -> 合并 -> 模板渲染 -> docx / PDF。"""
from __future__ import annotations

import os
import subprocess
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..db import dao
from .model import DocTree
from .template import DocTemplate
from . import docxgen


@dataclass
class CompileRequest:
    doc_ids: list[int] = field(default_factory=list)      # 资料库文档
    extra_paths: list[str] = field(default_factory=list)  # 未入库文件
    template: DocTemplate | None = None
    out_docx: str = ""
    material_titles: list[str] | None = None              # 覆盖材料标题


def load_trees(doc_ids: list[int], extra_paths: list[str]) -> list[DocTree]:
    trees: list[DocTree] = []
    for did in doc_ids:
        d = dao.get_document(did)
        if not d:
            continue
        tree = DocTree.from_json(d.title, d.blocks_json)
        if not tree.blocks:  # 旧数据兜底：按纯文本段落
            from .model import Block
            for line in (d.content_text or "").splitlines():
                if line.strip():
                    tree.blocks.append(Block(text=line.strip()))
        trees.append(tree)
    from .importer import parse_any
    for p in extra_paths:
        r = parse_any(p)
        if r.ok and r.tree:
            trees.append(r.tree)
    return trees


def compile_docx(req: CompileRequest) -> str:
    tpl = req.template or DocTemplate()
    trees = load_trees(req.doc_ids, req.extra_paths)
    if not trees:
        raise ValueError("没有可汇编的材料")
    if req.material_titles:
        for t, title in zip(trees, req.material_titles):
            if title:
                t.title = title
    return docxgen.generate_docx(trees, tpl, req.out_docx)


# ------------------------------------------------------------------ PDF 输出
def docx_to_pdf(docx_path: str, out_pdf: str = "") -> str:
    """docx -> PDF。离线策略：优先调用本机 Word/WPS COM（Windows），
    其次 LibreOffice；都不可用则抛出提示（可用内置 PDF 渲染器替代）。

    两处收尾都不能省：
      - LibreOffice 分支的临时目录必须 rmtree（老实现每次转 PDF 都在 %TEMP%
        留一个 gwtool_pdf_* 目录，批量转 200 份就是 200 个残留目录）；
      - COM 分支必须 finally 里 Quit + Close，否则 SaveAs2 抛异常时会留下
        看不见的 WPS/Word 后台进程，反复导出后进程越堆越多。
    """
    out_pdf = out_pdf or str(Path(docx_path).with_suffix(".pdf"))
    if shutil.which("soffice") or shutil.which("libreoffice"):
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        outdir = tempfile_dir()
        try:
            subprocess.run([soffice, "--headless", "--convert-to", "pdf",
                            "--outdir", outdir, docx_path],
                           capture_output=True, timeout=180, check=False)
            produced = Path(outdir) / (Path(docx_path).stem + ".pdf")
            if produced.exists():
                shutil.move(str(produced), out_pdf)
                return out_pdf
        finally:
            shutil.rmtree(outdir, ignore_errors=True)
    if os.name == "nt":
        try:
            import win32com.client  # type: ignore
        except ImportError:
            pass
        else:
            for progid in ("kwps.Application", "wps.Application", "Word.Application"):
                app = None
                doc = None
                try:
                    app = win32com.client.Dispatch(progid)
                    app.Visible = False
                    doc = app.Documents.Open(str(Path(docx_path).resolve()), ReadOnly=True)
                    doc.SaveAs2(str(Path(out_pdf).resolve()), FileFormat=17)  # wdFormatPDF
                    if Path(out_pdf).exists():
                        return out_pdf
                except Exception:
                    continue
                finally:
                    # 顺序与 COM 约定一致：先关文档再退应用；任一步失败都继续退应用
                    if doc is not None:
                        try:
                            doc.Close(False)
                        except Exception:
                            pass
                    if app is not None:
                        try:
                            app.Quit()
                        except Exception:
                            pass
    raise RuntimeError(
        "本机未找到可用的 docx->PDF 转换组件（WPS/Word/LibreOffice）。\n"
        "请使用程序内置的「PDF 预览/导出」功能（内置渲染器直接生成 PDF）。")


def tempfile_dir() -> str:
    import tempfile
    return tempfile.mkdtemp(prefix="gwtool_pdf_")
