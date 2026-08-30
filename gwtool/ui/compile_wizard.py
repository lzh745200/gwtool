# -*- coding: utf-8 -*-
"""一键汇编向导：三步 —— 选材料 -> 选模板+封面信息 -> 生成输出。"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QProgressBar, QPushButton, QRadioButton,
                               QWizard, QWizardPage, QVBoxLayout, QWidget)

from ..core.template import DocTemplate, default_template
from ..db import dao
from ..paths import export_dir
from .widgets import info
from .workers import BookletWorker, CompileWorker, PdfRenderWorker


class CompileWizard(QWizard):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("一键汇编")
        self.resize(760, 560)
        self.setWizardStyle(QWizard.ModernStyle)
        self._worker = None
        self._booklet_worker = None
        self.last_docx = ""
        self.last_pdf = ""
        self.addPage(self._page_materials())
        self.addPage(self._page_template())
        self.addPage(self._page_output())

    # ------------------------------------------------ 第1步：选材料
    def _page_materials(self):
        page = QWizardPage()
        page.setTitle("选择材料与顺序")
        page.setSubTitle("勾选要汇编的材料；拖拽或“上移/下移”调整先后顺序（汇编按此顺序合并）。")
        v = QVBoxLayout(page)
        self.material_list = QListWidget()
        self.material_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.material_list.setDragDropMode(QListWidget.InternalMove)
        for d in dao.list_documents():
            item = QListWidgetItem(f"{d.title}  [{d.file_type or '文本'}]")
            item.setData(Qt.UserRole, d.id)
            item.setCheckState(Qt.Checked)
            self.material_list.addItem(item)
        v.addWidget(self.material_list, 1)
        row = QHBoxLayout()
        btn_up = QPushButton("上移")
        btn_up.clicked.connect(lambda: self._move(-1))
        btn_down = QPushButton("下移")
        btn_down.clicked.connect(lambda: self._move(1))
        row.addWidget(btn_up)
        row.addWidget(btn_down)
        row.addStretch(1)
        v.addLayout(row)
        self.chk_titles = QCheckBox("将每份材料的标题作为一级标题")
        self.chk_titles.setChecked(True)
        v.addWidget(self.chk_titles)
        return page

    def _move(self, delta: int):
        row = self.material_list.currentRow()
        if row < 0:
            return
        target = row + delta
        if not (0 <= target < self.material_list.count()):
            return
        item = self.material_list.takeItem(row)
        self.material_list.insertItem(target, item)
        self.material_list.setCurrentRow(target)

    def selected_doc_ids(self) -> list[int]:
        return [self.material_list.item(i).data(Qt.UserRole)
                for i in range(self.material_list.count())
                if self.material_list.item(i).checkState() == Qt.Checked]

    # ------------------------------------------------ 第2步：模板与封面
    def _page_template(self):
        page = QWizardPage()
        page.setTitle("选择模板与封面信息")
        page.setSubTitle("模板可在「模板管理」中自定义。")
        v = QVBoxLayout(page)

        row = QHBoxLayout()
        row.addWidget(QLabel("模板："))
        self.tpl_combo = QComboBox()
        self._fill_templates()
        row.addWidget(self.tpl_combo, 1)
        btn_edit = QPushButton("打开模板管理…")
        btn_edit.clicked.connect(lambda: self.parent().open_template_editor()
                                if hasattr(self.parent(), "open_template_editor") else None)
        row.addWidget(btn_edit)
        v.addLayout(row)

        v.addWidget(QLabel("封面标题（可空）："))
        self.ed_title = QLineEdit()
        v.addWidget(self.ed_title)
        v.addWidget(QLabel("汇编单位（可空）："))
        self.ed_org = QLineEdit()
        v.addWidget(self.ed_org)
        v.addWidget(QLabel("落款日期（可空，如 2026年8月）："))
        self.ed_date = QLineEdit()
        v.addWidget(self.ed_date)
        self.chk_cover = QCheckBox("生成封面页")
        self.chk_cover.setChecked(True)
        v.addWidget(self.chk_cover)
        v.addStretch(1)
        return page

    def _fill_templates(self):
        self.tpl_combo.clear()
        tpls = dao.list_templates()
        if not tpls:
            self.tpl_combo.addItem("标准公文（默认）", None)
        for t in tpls:
            self.tpl_combo.addItem(t["name"] + ("（默认）" if t["is_default"] else ""),
                                   t["name"])

    def load_template(self) -> DocTemplate:
        name = self.tpl_combo.currentData()
        if name:
            cfg = dao.get_template_config(name)
            if cfg:
                return DocTemplate.from_json(cfg)
        cfg = dao.default_template_config()
        if cfg:
            return DocTemplate.from_json(cfg)
        return default_template()

    # ------------------------------------------------ 第3步：输出
    def _page_output(self):
        page = QWizardPage()
        page.setTitle("生成")
        page.setSubTitle("选择输出内容，点击开始。")
        v = QVBoxLayout(page)
        self.chk_docx = QCheckBox("生成规范 Word 文档（.docx，WPS 兼容）")
        self.chk_docx.setChecked(True)
        self.chk_pdf = QCheckBox("生成 A4 PDF（内置渲染器，含目录页码与外侧页码）")
        self.chk_pdf.setChecked(False)
        self.chk_booklet = QCheckBox("生成 A3 横向小册子 PDF（骑马钉，需先选 A4 PDF）")
        self.chk_booklet.setChecked(False)
        self.chk_batch = QCheckBox("批量模式：每份材料单独生成一份规范公文")
        self.chk_batch.setToolTip("勾选后按第1步顺序，对每份材料独立输出一份 docx")
        v.addWidget(self.chk_docx)
        v.addWidget(self.chk_pdf)
        v.addWidget(self.chk_booklet)
        v.addWidget(self.chk_batch)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        v.addWidget(self.progress)
        self.lbl_result = QLabel("")
        self.lbl_result.setWordWrap(True)
        v.addWidget(self.lbl_result)

        self.btn_start = QPushButton("开始生成")
        self.btn_start.clicked.connect(self._start)
        v.addWidget(self.btn_start)
        btn_open = QPushButton("打开输出文件夹")
        btn_open.clicked.connect(self._open_dir)
        v.addWidget(btn_open)
        v.addStretch(1)
        return page

    def _start(self):
        ids = self.selected_doc_ids()
        if not ids:
            info(self, "请先在第1步勾选材料。")
            return
        # 批量模式：每份材料独立出一份 docx
        if self.chk_batch.isChecked():
            self._run_batch(ids)
            return
        # 小册子基于 A4 PDF 重排，勾选小册子时自动补选 PDF
        if self.chk_booklet.isChecked() and not self.chk_pdf.isChecked():
            self.chk_pdf.setChecked(True)
        tpl = self.load_template()
        base = export_dir() / (self.ed_title.text().strip() or "汇编成果")
        base = Path(str(base))
        base.parent.mkdir(parents=True, exist_ok=True)
        any_output = self.chk_docx.isChecked() or self.chk_pdf.isChecked()
        if not any_output:
            info(self, "请至少勾选一种输出。")
            return
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)  # 忙碌指示
        self.btn_start.setEnabled(False)
        self.lbl_result.setText("正在生成…")

        if self.chk_docx.isChecked():
            tpl2 = tpl
            tpl2.cover.enabled = self.chk_cover.isChecked()
            tpl2.cover.title = self.ed_title.text().strip()
            tpl2.cover.org = self.ed_org.text().strip()
            tpl2.cover.date = self.ed_date.text().strip()
            tpl2.insert_material_titles = self.chk_titles.isChecked()
            out_docx = str(base.with_suffix(".docx"))
            self._worker = CompileWorker(ids, [], tpl2, out_docx, parent=self)
            self._worker.done.connect(lambda p: self._after_docx(p, tpl, ids))
            self._worker.error.connect(self._fail)
            self._worker.start()
        elif self.chk_pdf.isChecked():
            self._run_pdf(ids, tpl, base)

    def _run_batch(self, ids: list[int]):
        """批量模式：后台线程逐份生成。"""
        from ..core.batch import batch_compile_each
        tpl = self.load_template()
        tpl.cover.enabled = self.chk_cover.isChecked()
        tpl.cover.org = self.ed_org.text().strip()
        tpl.cover.date = self.ed_date.text().strip()
        tpl.insert_material_titles = False  # 材料标题即公文名，不重复
        outdir = export_dir() / (self.ed_title.text().strip() or "批量汇编")
        cover = {"org": tpl.cover.org, "date": tpl.cover.date}

        class _BatchThread(QThread):
            progress = Signal(int, int)
            done = Signal(list)
            error = Signal(str)

            def run(self_inner):
                try:
                    paths = batch_compile_each(
                        ids, tpl, str(outdir), cover=cover,
                        progress_cb=lambda i, n: self_inner.progress.emit(i, n))
                    self_inner.done.emit(paths)
                except Exception as exc:  # noqa: BLE001
                    self_inner.error.emit(str(exc))

        self._batch_worker = _BatchThread(self)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(ids))
        self.btn_start.setEnabled(False)

        def on_prog(i, n):
            self.progress.setValue(i)
            self.lbl_result.setText(f"批量生成中 {i}/{n}…")

        def on_done(paths):
            self.progress.setVisible(False)
            self.btn_start.setEnabled(True)
            self.lbl_result.setText(
                "批量生成完成！\n" + "\n".join(Path(p).name for p in paths[:10])
                + (f"\n… 共 {len(paths)} 份" if len(paths) > 10 else ""))
            info(self, f"批量生成完成：{len(paths)} 份，输出目录：\n{outdir}")

        def on_err(msg):
            self.progress.setVisible(False)
            self.btn_start.setEnabled(True)
            info(self, f"批量生成失败：{msg}")

        self._batch_worker.progress.connect(on_prog)
        self._batch_worker.done.connect(on_done)
        self._batch_worker.error.connect(on_err)
        self._batch_worker.start()

    def _after_docx(self, path: str, tpl: DocTemplate, ids):
        self.last_docx = path
        if self.chk_pdf.isChecked():
            self._run_pdf(ids, tpl, Path(path).with_suffix(""))
        else:
            self._finish(path)

    def _run_pdf(self, ids, tpl: DocTemplate, base: Path):
        tpl.cover.enabled = self.chk_cover.isChecked()
        tpl.cover.title = self.ed_title.text().strip()
        tpl.cover.org = self.ed_org.text().strip()
        tpl.cover.date = self.ed_date.text().strip()
        tpl.insert_material_titles = self.chk_titles.isChecked()
        out_pdf = str(base.with_suffix(".pdf"))
        self._pdf_worker = PdfRenderWorker(ids, [], tpl, out_pdf, parent=self)
        self._pdf_worker.done.connect(self._after_pdf)
        self._pdf_worker.error.connect(self._fail)
        self._pdf_worker.start()

    def _after_pdf(self, path: str):
        self.last_pdf = path
        if self.chk_booklet.isChecked():
            out_bk = str(Path(path).with_name(Path(path).stem + "_小册子A3.pdf"))
            self._booklet_worker = BookletWorker(path, out_bk, parent=self)
            self._booklet_worker.done.connect(lambda p: self._finish(path, p))
            self._booklet_worker.error.connect(self._fail)
        else:
            self._finish(path)

    def _finish(self, docx_path: str, booklet_path: str = ""):
        self.progress.setVisible(False)
        self.btn_start.setEnabled(True)
        msg = []
        if docx_path:
            msg.append(f"Word 文档：{docx_path}")
        if self.last_pdf:
            msg.append(f"A4 PDF：{self.last_pdf}")
        if booklet_path:
            msg.append(f"小册子：{booklet_path}")
        self.lbl_result.setText("生成完成！\n" + "\n".join(msg))
        info(self, "生成完成！\n" + "\n".join(msg))

    def _fail(self, msg: str):
        self.progress.setVisible(False)
        self.btn_start.setEnabled(True)
        self.lbl_result.setText(f"失败：{msg}")
        info(self, f"生成失败：{msg}")

    def _open_dir(self):
        p = str(export_dir())
        if os.name == "nt":
            subprocess.Popen(["explorer", p])
        else:
            subprocess.Popen(["xdg-open", p])
