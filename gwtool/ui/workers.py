# -*- coding: utf-8 -*-
"""后台工作线程：批量导入、汇编、PDF 生成等耗时任务。"""
from __future__ import annotations

import traceback
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .. import logs
from ..core import compiler, importer
from ..core.booklet import make_booklet
from ..db import connection as dbconn
from ..db import dao
from . import errmsg

log = logs.get_logger("workers")


def _log_exc(where: str) -> None:
    """记录后台任务异常：写日志 + 保留 stderr 输出。

    此前这里只有 `traceback.print_exc()`，而打包版是 `--windowed`（无控制台），
    异常信息实际**完全丢失** —— 用户报"点了一下没反应"时我们无可查证据。
    """
    try:
        log.exception("%s 执行失败", where)
    except Exception:
        pass
    traceback.print_exc()


def _close_thread_conn() -> None:
    """收尾：关闭本工作线程持有的 SQLite 连接。

    SQLite 连接按 thread-local 缓存，QThread 每次 start() 都是新 OS 线程，
    不主动关闭的话线程结束后连接对象与 -wal/-shm 句柄会随线程一起泄漏。
    长会话里反复「查重/批量纠错/对比/PDF 预览」会持续堆积句柄，
    麒麟上容易触发 too many open files。
    """
    try:
        dbconn.close_current_thread()
    except Exception:
        pass


class FnWorker(QThread):
    """通用函数工作器：在后台线程执行 fn(*args, **kwargs)。

    用于纠错检查、查重、对比、PDF 预览渲染等原本阻塞主线程的调用，
    结果经 ok 信号回到主线程（回调里只做 UI 更新）。

    `want_progress=True` 时额外把 `progress_cb=<进度文本信号>` 注入给被调
    函数：供「备份/恢复、全库打包、数据库维护、重建索引」这类**有阶段可报**
    的长任务把"正在做什么"回传到状态栏 —— 它们实测秒级到十秒级，光有
    结果信号，用户在整个过程中看到的仍是一个不动的窗口。
    """
    ok = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, fn, *args, parent=None, want_progress=False, **kwargs):
        super().__init__(parent)
        self._fn, self._args, self._kwargs = fn, args, kwargs
        self._want_progress = bool(want_progress)

    def stop(self):
        """请求中断（协作式）。

        Fn/Compile/PdfRender/Booklet 四类 worker 的 run() 都是**单次长调用**
        （compile_docx / render_pdf / 任意 fn），没有可插入检查点的循环，
        故这里只能表达中断意图（requestInterruption）；真正兜住"退出时
        线程还活着"的是 closeEvent 里对超时线程的 terminate —— 两层合起来
        才能避免 QThread 存活时进程退出触发 Qt qFatal（0xC0000409）。
        """
        self.requestInterruption()

    def run(self):
        try:
            if self._want_progress:
                out = self._fn(*self._args, progress_cb=self.progress.emit,
                               **self._kwargs)
            else:
                out = self._fn(*self._args, **self._kwargs)
            self.ok.emit(out)
        except Exception as exc:
            _log_exc("后台任务")
            self.failed.emit(errmsg.friendly(exc, action="后台任务"))
        finally:
            _close_thread_conn()


class ImportWorker(QThread):
    """批量导入：解析 -> 入库（去重）。"""
    progress = Signal(int, int, str)      # i, total, path
    finished_ok = Signal(int, int)        # 成功数, 重复/失败数
    # 完整明细：成功数, 跳过数, [(文件名, 原因), ...]
    finished_detail = Signal(int, int, list)
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
            failures: list = []
            total = len(self.files)
            for i, path in enumerate(self.files, 1):
                if self._stop:
                    failures.append((Path(path).name, "已手动停止，未处理"))
                    continue
                self.progress.emit(i, total, path)

                def ocr_progress(page, n_pages, _i=i, _path=path):
                    # 扫描件整本 OCR 可能耗时数分钟，把页级进度并入进度文本
                    self.progress.emit(_i, total, f"{_path}（OCR 第{page}/{n_pages}页）")

                r = importer.parse_any(path, ocr_progress_cb=ocr_progress)
                if not r.ok or r.tree is None:
                    skip += 1
                    # 失败原因必须随清单回传：此前这里 `continue` 把 r.error
                    # 直接丢弃，导 200 份后用户只知道"跳过 N 篇"，
                    # 不知道**哪几份、为什么、接下来做什么**。
                    # r.error 可能含异常类名/英文（importer 保留原文供日志），
                    # 交给 errmsg 统一翻成可执行文案。
                    reason = errmsg.friendly_parse_error(r.error)
                    failures.append((Path(path).name, reason))
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
                    failures.append((Path(path).name,
                                     "内容与已有材料重复，未重复入库"))
                else:
                    ok += 1
            self.finished_ok.emit(ok, skip)
            self.finished_detail.emit(ok, skip, failures)
        except Exception as exc:
            _log_exc("批量导入")
            self.failed.emit(errmsg.friendly(exc, action="批量导入"))
        finally:
            _close_thread_conn()


class CompileWorker(QThread):
    """一键汇编 -> docx。"""
    progress = Signal(str)
    done = Signal(str)          # 输出路径
    error = Signal(str)

    def __init__(self, doc_ids, extra_paths, template, out_docx,
                 material_titles=None, include_sources=False, parent=None):
        super().__init__(parent)
        self.req = compiler.CompileRequest(
            doc_ids=list(doc_ids), extra_paths=list(extra_paths),
            template=template, out_docx=out_docx,
            material_titles=material_titles,
            include_sources=bool(include_sources))

    def stop(self):
        """请求中断（协作式）；单次长调用无检查点，说明见 FnWorker.stop。"""
        self.requestInterruption()

    def run(self):
        try:
            self.progress.emit("正在合并材料并生成公文…")
            out = compiler.compile_docx(self.req)
            self.done.emit(out)
        except Exception as exc:
            _log_exc("一键汇编")
            self.error.emit(errmsg.friendly(exc, action="汇编"))
        finally:
            _close_thread_conn()


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

    def stop(self):
        """请求中断（协作式）；单次长调用无检查点，说明见 FnWorker.stop。"""
        self.requestInterruption()

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
        except Exception as exc:
            _log_exc("PDF 渲染")
            self.error.emit(errmsg.friendly(exc, action="生成 PDF"))
        finally:
            _close_thread_conn()


class BookletWorker(QThread):
    """A4 PDF -> A3 骑马钉小册子 PDF。"""
    progress = Signal(str)
    done = Signal(str)
    error = Signal(str)

    def __init__(self, src_pdf, out_pdf, parent=None):
        super().__init__(parent)
        self.src_pdf = src_pdf
        self.out_pdf = out_pdf

    def stop(self):
        """请求中断（协作式）；单次长调用无检查点，说明见 FnWorker.stop。"""
        self.requestInterruption()

    def run(self):
        try:
            self.progress.emit("正在重排小册子页面…")
            n = make_booklet(self.src_pdf, self.out_pdf)
            self.done.emit(f"{self.out_pdf}\n共 {n} 页（A3横向，骑马钉）")
        except Exception as exc:
            _log_exc("小册子重排")
            self.error.emit(errmsg.friendly(exc, action="生成小册子"))
        finally:
            _close_thread_conn()


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
        except Exception as exc:
            _log_exc("朗读引擎初始化")
            self.failed.emit(errmsg.friendly(exc, action="初始化朗读"))
            return
        try:
            sentences = tts_core.split_sentences(self.text)
            for i, s in enumerate(sentences):
                if self._stop:
                    break
                self.sentence.emit(i, len(sentences), s)
                self._engine._stopped = False
                self._engine.speak(s)
            self.finished_ok.emit()
        finally:
            _close_thread_conn()
