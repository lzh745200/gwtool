# -*- coding: utf-8 -*-
"""导入对话框：拖拽/选择多文件，选择分类，后台线程导入，去重。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QHBoxLayout,
                               QLabel, QListWidget, QProgressBar, QPushButton,
                               QVBoxLayout)

from ..core.importer import IMAGE_EXTS, SUPPORTED_EXTS
from ..db import dao
from .widgets import ThreadSafeDialog, info
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

        btns = QHBoxLayout()
        self.btn_start = QPushButton("开始导入")
        self.btn_start.clicked.connect(self._start)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.reject)
        btns.addStretch(1)
        btns.addWidget(self.btn_start)
        btns.addWidget(btn_close)
        layout.addLayout(btns)

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
        if files and not self.progress.isVisible():
            pass
        self.progress.setVisible(True)
        self.progress.setRange(0, len(files))
        self.btn_start.setEnabled(False)
        cat_id = self.cat_combo.currentData() or 0
        self._worker = ImportWorker(files, cat_id, self)
        self._worker.progress.connect(
            lambda i, total, p: (self.progress.setValue(i),
                                 self.setWindowTitle(f"导入中 {i}/{total}")))
        self._worker.finished_ok.connect(self._done)
        self._worker.failed.connect(self._failed)
        self._worker.start()

    def _failed(self, msg: str):
        # 必须复位按钮与进度条，否则一次失败后对话框永久卡在“导入中”，无法重试
        self.btn_start.setEnabled(True)
        self.progress.setVisible(False)
        info(self, f"导入出错：{msg}")

    def _done(self, ok: int, skip: int):
        self.btn_start.setEnabled(True)
        self.progress.setVisible(False)
        info(self, f"导入完成：成功 {ok} 篇；重复/失败/扫描版跳过 {skip} 篇。")
        self.file_list.clear()
        self.accept()
