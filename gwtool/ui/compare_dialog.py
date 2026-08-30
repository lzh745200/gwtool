# -*- coding: utf-8 -*-
"""文档对比对话框：选两个文档或文件，红绿差异视图。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QHBoxLayout,
                               QLabel, QPushButton, QTextBrowser, QVBoxLayout)

from ..core import differ
from ..core.importer import parse_any
from ..db import dao


class CompareDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("文档对比")
        self.resize(960, 640)
        self._build_ui()

    def _build_ui(self):
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("文档一："))
        self.combo_a = QComboBox()
        self._fill_docs(self.combo_a)
        btn_a = QPushButton("选择文件…")
        btn_a.clicked.connect(lambda: self._pick_file(self.combo_a))
        row.addWidget(self.combo_a, 1)
        row.addWidget(btn_a)
        v.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("文档二："))
        self.combo_b = QComboBox()
        self._fill_docs(self.combo_b)
        btn_b = QPushButton("选择文件…")
        btn_b.clicked.connect(lambda: self._pick_file(self.combo_b))
        row2.addWidget(self.combo_b, 1)
        row2.addWidget(btn_b)
        v.addLayout(row2)

        self.btn_run = QPushButton("开始对比")
        self.btn_run.clicked.connect(self._run)
        v.addWidget(self.btn_run)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        v.addWidget(self.browser, 1)
        self.lbl_stat = QLabel("从下拉框选择资料库文档，或选择本地文件。")
        v.addWidget(self.lbl_stat)

    def _fill_docs(self, combo: QComboBox):
        combo.clear()
        combo.addItem("（未选择）", None)
        for d in dao.list_documents():
            combo.addItem(f"资料库：{d.title}", ("db", d.id))

    def _pick_file(self, combo: QComboBox):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择文件", "", "支持格式 (*.docx *.doc *.txt *.rtf *.pdf *.md *.html)")
        if path:
            combo.addItem(f"文件：{Path(path).name}", ("file", path))
            combo.setCurrentIndex(combo.count() - 1)

    def _text_of(self, combo: QComboBox) -> tuple[str, str]:
        data = combo.currentData()
        if not data:
            return "", ""
        kind, val = data
        if kind == "db":
            d = dao.get_document(val)
            return (d.title, d.content_text) if d else ("", "")
        r = parse_any(val)
        if r.ok and r.tree:
            title = r.tree.title or Path(val).stem
            return title, r.tree.plain_text()
        return Path(val).name, ""

    def _run(self):
        from .workers import FnWorker
        t1, txt1 = self._text_of(self.combo_a)
        t2, txt2 = self._text_of(self.combo_b)
        if not txt1 and not txt2:
            self.lbl_stat.setText("请先选择两个有效文档。")
            return
        # 文本抽取已在主线程完成（本地文件解析较快），diff 与 HTML 生成放后台
        self.btn_run.setEnabled(False)
        self.lbl_stat.setText("正在对比…")
        self._worker = FnWorker(differ.diff_to_html, txt1, txt2,
                                t1 or "文档一", t2 or "文档二", parent=self)

        def on_ok(html):
            self.browser.setHtml(html)
            self.lbl_stat.setText("红色=删除/原文，绿色=新增/修改后；双栏对应。")

        def on_done():
            self.btn_run.setEnabled(True)

        self._worker.ok.connect(on_ok)
        self._worker.failed.connect(
            lambda m: self.lbl_stat.setText(f"对比失败：{m}"))
        self._worker.finished.connect(on_done)
        self._worker.start()
