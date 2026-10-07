# -*- coding: utf-8 -*-
"""导入对话框：拖拽/选择多文件，选择分类，后台线程导入，去重。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QHBoxLayout,
                               QLabel, QListWidget, QPlainTextEdit,
                               QProgressBar, QPushButton, QVBoxLayout)

from ..core.importer import IMAGE_EXTS, SUPPORTED_EXTS
from ..db import dao
from .widgets import Banner, ThreadSafeDialog, info
from .workers import ImportWorker

FILE_FILTER = ("支持的文件 (*.docx *.doc *.wps *.txt *.rtf *.pdf *.md *.markdown *.html *.htm"
               " *.png *.jpg *.jpeg *.bmp *.tif *.tiff);;"
               "Word/WPS (*.docx *.doc *.wps);;PDF (*.pdf);;图片-需OCR (*.png *.jpg *.jpeg *.bmp *.tif *.tiff);;"
               "文本 (*.txt);;RTF (*.rtf);;Markdown (*.md);;HTML (*.html *.htm);;全部文件 (*)")


class _DropListWidget(QListWidget):
    """接受拖放的列表：把拖入的文件/文件夹交给宿主对话框处理。

    QListWidget 默认的拖放实现会把 URL 当成纯文本项插进来（拖文件夹只会
    插入一条 "file:///…" 文本行）。这里改写了 dragEnter/dragMove/drop，
    把事件转给自己的宿主，由宿主完成扩展名过滤与目录递归展开。
    """

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._owner = owner
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DropOnly)
        self.setSelectionMode(QListWidget.ExtendedSelection)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._owner._drop_urls(e.mimeData().urls())
        else:
            super().dropEvent(e)


class ImportDialog(ThreadSafeDialog, QDialog):
    def __init__(self, category_id: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导入材料")
        self.resize(560, 420)
        self.category_id = category_id
        self._worker: ImportWorker | None = None
        self._build_ui()
        self._fill_categories()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        tip = QLabel("把文件拖到下方列表（支持 .docx .doc .txt .rtf .pdf .md .html），\n"
                     "也可拖入整个文件夹；内容重复的文件将自动跳过。")
        layout.addWidget(tip)

        self.file_list = _DropListWidget(self)
        layout.addWidget(self.file_list, 1)

        row = QHBoxLayout()
        btn_add = QPushButton("添加文件…")
        btn_add.clicked.connect(self._pick_files)
        btn_rm = QPushButton("移除所选")
        btn_rm.clicked.connect(self._remove_selected)
        btn_clear = QPushButton("清空")
        btn_clear.clicked.connect(self.file_list.clear)
        row.addWidget(btn_add)
        row.addWidget(btn_rm)
        row.addWidget(btn_clear)
        row.addStretch(1)
        layout.addLayout(row)

        cat_row = QHBoxLayout()
        cat_row.addWidget(QLabel("导入到分类："))
        self.cat_combo = QComboBox()
        cat_row.addWidget(self.cat_combo, 1)
        layout.addLayout(cat_row)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        # 阶段文案（UI 方案 §7.5 阶段式）：显示当前第几篇与文件名，
        # 扫描件 OCR 时带页级进度（数据来自 ImportWorker.progress 既有信号，
        # 仅 UI 呈现，不动线程与信号契约）。
        self.lbl_stage = QLabel("")
        self.lbl_stage.setVisible(False)
        layout.addWidget(self.lbl_stage)

        # 完成横幅与失败明细（UI 方案 §8.1）：结果就地展示、失败逐条可读，
        # 不再用弹窗打断；对话框由用户点「关闭」收起（与汇编向导结果面板
        # 同一条纪律）。
        self.banner = Banner()
        layout.addWidget(self.banner)
        self.fail_view = QPlainTextEdit()
        self.fail_view.setReadOnly(True)
        self.fail_view.setMaximumHeight(120)
        self.fail_view.setPlaceholderText("未能导入的文件会逐条列在这里（含原因）")
        self.fail_view.setVisible(False)
        layout.addWidget(self.fail_view)

        btns = QHBoxLayout()
        self.btn_start = QPushButton("开始导入")
        self.btn_start.clicked.connect(self._start)
        # 协作式取消（§7.5）：导入是逐篇进行的长任务，必须能中途停下；
        # 已导入的保留，未处理的标为"未处理"。
        self.btn_cancel = QPushButton("取消导入")
        self.btn_cancel.setToolTip("停止后续文件的导入；已经导入的材料会保留")
        self.btn_cancel.clicked.connect(self._cancel_import)
        self.btn_cancel.setVisible(False)
        self._cancel_requested = False
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.reject)
        btns.addStretch(1)
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_start)
        btns.addWidget(btn_close)
        layout.addLayout(btns)

    def _cancel_import(self) -> None:
        """请求取消导入（协作式：worker 在文件循环里检查停止标志）。"""
        worker = getattr(self, "_worker", None)
        if worker is None or not worker.isRunning():
            return
        self._cancel_requested = True
        worker.stop()
        self.btn_cancel.setEnabled(False)
        self.lbl_stage.setText("正在取消（已导入的材料会保留）…")

    def _fill_categories(self):
        self.cat_combo.clear()
        self.cat_combo.addItem("未分类", 0)
        def walk(parent_id, prefix):
            for c in dao.list_categories():
                if c.parent_id != parent_id:
                    continue
                self.cat_combo.addItem(prefix + c.name, c.id)
                walk(c.id, prefix + "　")
        walk(0, "")
        if self.category_id:
            idx = self.cat_combo.findData(self.category_id)
            if idx >= 0:
                self.cat_combo.setCurrentIndex(idx)

    # ------------------------------------------------ 文件选择
    def _pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择材料文件", "", FILE_FILTER)
        self._add_files(files)

    def _add_files(self, files):
        for f in files:
            if Path(f).suffix.lower() in (SUPPORTED_EXTS | IMAGE_EXTS):
                if not self.file_list.findItems(f, Qt.MatchExactly):
                    self.file_list.addItem(f)

    def _remove_selected(self):
        for item in self.file_list.selectedItems():
            self.file_list.takeItem(self.file_list.row(item))

    def _drop_urls(self, urls):
        """把拖入的 URL 列表展开为待导入文件（目录递归、按扩展名过滤）。"""
        files = []
        for url in urls:
            p = url.toLocalFile()
            if not p:
                continue
            if Path(p).is_dir():
                for ext in (SUPPORTED_EXTS | IMAGE_EXTS):
                    files.extend(str(x) for x in Path(p).rglob(f"*{ext}"))
            else:
                files.append(p)
        self._add_files(files)

    def dragEnterEvent(self, e):
        # 对话框空白处的拖放（列表之外的区域）
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drop_urls(e.mimeData().urls())

    # ------------------------------------------------ 导入
    def _start(self):
        files = [self.file_list.item(i).text() for i in range(self.file_list.count())]
        if not files:
            info(self, "请先添加要导入的文件。")
            return
        self.progress.setVisible(True)
        self.progress.setRange(0, len(files))
        self.lbl_stage.setVisible(True)
        self.banner.clear()            # 新一轮：收起上一轮结论（§8.1）
        self.fail_view.setVisible(False)
        self._cancel_requested = False
        self._finished_round = False   # 新一轮：复位完成幂等守卫
        self.btn_cancel.setEnabled(True)
        self.btn_cancel.setVisible(True)
        self.btn_start.setEnabled(False)
        cat_id = self.cat_combo.currentData() or 0
        self._worker = ImportWorker(files, cat_id, self)
        self._worker.progress.connect(
            lambda i, total, p: (self.progress.setValue(i),
                                 self.setWindowTitle(f"导入中 {i}/{total}"),
                                 self.lbl_stage.setText(
                                     f"正在导入 {i}/{total}：{Path(p).name}")))
        self._worker.finished_ok.connect(self._done)
        self._worker.finished_detail.connect(self._done_detail)
        self._worker.failed.connect(self._failed)
        self._worker.start()

    def _failed(self, msg: str):
        # 必须复位按钮与进度条，否则一次失败后对话框永久卡在“导入中”，无法重试
        self.btn_start.setEnabled(True)
        self.progress.setVisible(False)
        self.lbl_stage.setVisible(False)
        info(self, f"导入出错：{msg}")

    def _done(self, ok: int, skip: int):
        # 简版回调（无明细信号时兜底）。注意 worker 会先发 finished_ok 再发
        # finished_detail（见 workers.py ImportWorker.run），两条回调都会到达；
        # 完成处理只允许执行一次，由 _finish_common 的幂等守卫统一保证。
        self._finish_common(ok, skip, None)

    def _done_detail(self, ok: int, skip: int, failures: list):
        self._finish_common(ok, skip, failures)

    def _finish_common(self, ok: int, skip: int, failures):
        # 幂等守卫：finished_ok 与 finished_detail 先后到达（Qt 队列按发射序
        # 投递），同一轮只处理第一笔；失败清单写盘与结论横幅因此不会重复。
        if getattr(self, "_finished_round", False):
            return
        self._finished_round = True
        self.btn_start.setEnabled(True)
        self.progress.setVisible(False)
        self.lbl_stage.setVisible(False)
        self.btn_cancel.setVisible(False)
        real_failures = [f for f in (failures or []) if "重复" not in f[1]]
        overflow = 0
        # 完成横幅（§8.1 / §7.5）：成功 / 跳过 / 失败三段统计；
        # 若是用户主动取消，结论按"已取消，已完成部分保留"表述。
        chips = [(f"成功 {ok}", "success")]
        if skip:
            chips.append((f"跳过 {skip}", "warn"))
        if real_failures:
            chips.append((f"失败 {len(real_failures)}", "danger"))
        cancelled = getattr(self, "_cancel_requested", False)
        self._cancel_requested = False
        if cancelled:
            headline = (f"已取消，已完成部分保留：成功 {ok} 篇"
                        + (f"，跳过 {skip} 篇" if skip else "") + "。")
            self.banner.show_result("warn", headline, chips)
        elif real_failures:
            self.banner.show_result(
                "danger",
                f"导入完成：成功 {ok} 篇；重复/失败/扫描版跳过 {skip} 篇。",
                chips)
        elif skip:
            self.banner.show_result(
                "warn", f"导入完成：成功 {ok} 篇；重复/扫描版跳过 {skip} 篇。",
                chips)
        else:
            self.banner.show_result("success", f"导入完成：成功 {ok} 篇。", chips)
        # 失败明细就地可读（原先只在弹窗里列，关了对话框就没法回看）。
        # 逐条列出（至多 8 条），超出部分写入导出目录的文本文件留档。
        if real_failures:
            preview = real_failures[:8]
            overflow = len(real_failures) - len(preview)
            lines = [f"· {name}：{why}" for name, why in preview]
            if overflow > 0:
                lines.append(f"…另有 {overflow} 份，完整清单见导出目录")
            self.fail_view.setPlainText("以下文件未能导入：\n" + "\n".join(lines))
            self.fail_view.setVisible(True)
        else:
            self.fail_view.clear()
            self.fail_view.setVisible(False)
        if overflow > 0:
            self._export_failure_list(real_failures)
        self.file_list.clear()
        # 不再 accept()：结果面板留在屏幕上，用户看完自己关（§8.1）

    def _export_failure_list(self, failures: list) -> None:
        """失败清单落盘：条目多时弹窗列不全，给一份可留存的 TXT。"""
        try:
            from ..paths import export_dir
            out = export_dir() / "导入失败清单.txt"
            with open(out, "w", encoding="utf-8-sig") as fh:
                fh.write("以下文件未能导入：\n")
                for name, why in failures:
                    fh.write(f"{name}\t{why}\n")
            info(self, f"完整失败清单已保存：\n{out}")
        except Exception:
            pass  # 清单落盘失败不影响导入结果展示
