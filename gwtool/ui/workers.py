# -*- coding: utf-8 -*-
"""后台工作线程：批量导入、汇编、PDF 生成等耗时任务。"""
from __future__ import annotations

import traceback
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..core import compiler, importer
from ..core.booklet import make_booklet
from ..db import dao
from ..core.model import Block


class ImportWorker(QThread):
    """批量导入：解析 -> 入库（去重）。"""
    progress = Signal(int, int, str)      # i, total, path
    finished_ok = Signal(int, int)        # 成功数, 重复/失败数
    failed = Signal(str)

    def __init__(self, files: list[str], category_id: int, parent=None):
        super().__init__(parent)
        self.files = files
        self.category_id = category_id
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            ok = skip = 0
            total = len(self.files)
            for i, path in enumerate(self.files, 1):
                if self._stop:
                    break
                self.progress.emit(i, total, path)

                def ocr_progress(page, n_pages, _i=i, _path=path):
                    # 扫描件整本 OCR 可能耗时数分钟，把页级进度并入进度文本
                    self.progress.emit(_i, total, f"{_path}（OCR 第{page}/{n_pages}页）")

                r = importer.parse_any(path, ocr_progress_cb=ocr_progress)
                if not r.ok or r.tree is None:
                    skip += 1
                    continue
                doc = dao.Document(
                    title=r.tree.title or Path(path).stem,
                    content_text=r.tree.plain_text(),
                    blocks_json=r.tree.to_json(),
                    file_path=path,
                    file_type=Path(path).suffix.lower().lstrip("."),
                    category_id=self.category_id,
                )
                if dao.add_document(doc) < 0:
                    skip += 1  # 内容重复
                else:
                    ok += 1
            self.finished_ok.emit(ok, skip)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.failed.emit(str(exc))


class CompileWorker(QThread):
    """一键汇编 -> docx。"""
    progress = Signal(str)
    done = Signal(str)          # 输出路径
    error = Signal(str)

    def __init__(self, doc_ids, extra_paths, template, out_docx,
                 material_titles=None, parent=None):
        super().__init__(parent)
        self.req = compiler.CompileRequest(
            doc_ids=list(doc_ids), extra_paths=list(extra_paths),
            template=template, out_docx=out_docx,
            material_titles=material_titles)

    def run(self):
        try:
            self.progress.emit("正在合并材料并生成公文…")
            out = compiler.compile_docx(self.req)
            self.done.emit(out)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.error.emit(str(exc))


class PdfRenderWorker(QThread):
    """内置渲染器：汇编内容 -> A4 PDF（含目录页码与外侧页码）。"""
    progress = Signal(str)
    done = Signal(str)
    error = Signal(str)

    def __init__(self, doc_ids, extra_paths, template, out_pdf, parent=None):
        super().__init__(parent)
        self.doc_ids = list(doc_ids)
        self.extra_paths = list(extra_paths)
        self.template = template
        self.out_pdf = out_pdf

    def run(self):
        try:
            from ..core import pdfrender
            self.progress.emit("正在加载材料…")
            trees = compiler.load_trees(self.doc_ids, self.extra_paths)
            if not trees:
                raise ValueError("没有可汇编的材料")
            self.progress.emit("第一遍渲染（计算目录页码）…")
            self.progress.emit("第二遍渲染与页码标注…")
            pdfrender.render_compiled_pdf(trees, self.template, self.out_pdf)
            self.done.emit(self.out_pdf)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.error.emit(str(exc))


class BookletWorker(QThread):
    """A4 PDF -> A3 骑马钉小册子 PDF。"""
    progress = Signal(str)
    done = Signal(str)
    error = Signal(str)

    def __init__(self, src_pdf, out_pdf, parent=None):
        super().__init__(parent)
        self.src_pdf = src_pdf
        self.out_pdf = out_pdf

    def run(self):
        try:
            self.progress.emit("正在重排小册子页面…")
            n = make_booklet(self.src_pdf, self.out_pdf)
            self.done.emit(f"{self.out_pdf}\n共 {n} 页（A3横向，骑马钉）")
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.error.emit(str(exc))


class TTSWorker(QThread):
    """朗读校对：逐句朗读并回报句号位置（供高亮）。"""
    sentence = Signal(int, int, str)     # 序号, 总数, 句子
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.text = text
        self._stop = False
        self._engine = None

    def stop(self):
        self._stop = True
        if self._engine is not None:
            try:
                self._engine.stop()
            except Exception:
                pass

    def run(self):
        try:
            from ..core import tts as tts_core
            self._engine = tts_core.TTSEngine()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        sentences = tts_core.split_sentences(self.text)
        for i, s in enumerate(sentences):
            if self._stop:
                break
            self.sentence.emit(i, len(sentences), s)
            self._engine._stopped = False
            self._engine.speak(s)
        self.finished_ok.emit()
