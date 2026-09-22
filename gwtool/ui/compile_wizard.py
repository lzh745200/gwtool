# -*- coding: utf-8 -*-
"""一键汇编向导：三步 —— 选材料 -> 选模板+封面信息 -> 生成输出。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QProgressBar, QPushButton, QWizard, QWizardPage, QVBoxLayout)

from ..core.template import DocTemplate, default_template
from ..db import dao
from ..paths import export_dir
from .widgets import ThreadSafeDialog, info
from .workers import BookletWorker, CompileWorker, PdfRenderWorker, _close_thread_conn


class CompileWizard(ThreadSafeDialog, QWizard):
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

        # U6：材料一多，"在长列表里逐条找要编的"很费劲，而"默认全勾"又容易
        # 把无关材料编进正式公文。分类/标签筛选 + 全选/全不选/反选，让用户
        # 先缩小范围再一次性勾对。
        bar = QHBoxLayout()
        bar.addWidget(QLabel("筛选："))
        self.filt_cat = QComboBox()
        # 全部分类必须用 None（dao.list_documents(None) 才是"全部"；
        # 传 0 表示"仅默认分类"，会把有分类的材料整批筛掉）
        self.filt_cat.addItem("全部分类", None)
        for c in dao.list_categories():
            self.filt_cat.addItem(c.name, c.id)
        self.filt_cat.setToolTip("只显示该分类下的材料；勾选状态与汇编顺序不受筛选影响")
        self.filt_cat.currentIndexChanged.connect(self._reload_materials)
        bar.addWidget(self.filt_cat)
        self.filt_tag = QLineEdit()
        self.filt_tag.setPlaceholderText("按标签/标题关键字筛选")
        self.filt_tag.setToolTip("按标签或标题关键字过滤；清空即显示全部材料")
        self.filt_tag.textChanged.connect(self._reload_materials)
        bar.addWidget(self.filt_tag, 1)
        v.addLayout(bar)

        self.material_list = QListWidget()
        self.material_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.material_list.setDragDropMode(QListWidget.InternalMove)
        # U5：向导三步的关键控件都配一句悬停说明 —— 目标用户在离线内网，
        # 没有在线帮助可查，"勾选是什么意思、顺序怎么定"只能靠提示。
        self.material_list.setToolTip(
            "勾选要汇编的材料；拖动条目或用下方「上移/下移」调整顺序，\n"
            "汇编按列表顺序合并（把主件放最前，附件紧跟其后）")
        self._order: list[int] = [d.id for d in dao.list_documents()]
        self._checked: set[int] = set(self._order)      # 默认全勾（可一键取消）
        self._labels: dict[int, str] = {
            d.id: f"{d.title}  [{d.file_type or '文本'}]"
            for d in dao.list_documents()}
        self._render_materials()
        self.material_list.itemChanged.connect(self._on_item_changed)
        self.material_list.model().rowsMoved.connect(self._on_rows_moved)
        v.addWidget(self.material_list, 1)
        row = QHBoxLayout()
        btn_all = QPushButton("全选")
        btn_all.setToolTip("勾选**当前筛选出的**全部材料（不影响被筛掉的那些）")
        btn_all.clicked.connect(lambda: self._check_visible(Qt.Checked))
        btn_none = QPushButton("全不选")
        btn_none.setToolTip("取消勾选**当前筛选出的**全部材料（不影响被筛掉的那些）")
        btn_none.clicked.connect(lambda: self._check_visible(Qt.Unchecked))
        btn_inv = QPushButton("反选")
        btn_inv.setToolTip("把当前筛选出的材料的勾选状态逐条取反")
        btn_inv.clicked.connect(self._invert_visible)
        btn_up = QPushButton("上移")
        btn_up.setToolTip("把选中的材料在汇编顺序里前移一位")
        btn_up.clicked.connect(lambda: self._move(-1))
        btn_down = QPushButton("下移")
        btn_down.setToolTip("把选中的材料在汇编顺序里后移一位")
        btn_down.clicked.connect(lambda: self._move(1))
        for b in (btn_all, btn_none, btn_inv):
            row.addWidget(b)
        row.addSpacing(12)
        row.addWidget(btn_up)
        row.addWidget(btn_down)
        row.addStretch(1)
        v.addLayout(row)
        self.chk_titles = QCheckBox("将每份材料的标题作为一级标题")
        self.chk_titles.setToolTip(
            "勾选后每份材料的标题会成为正式公文的章标题（便于自动生成目录）")
        self.chk_titles.setChecked(True)
        v.addWidget(self.chk_titles)
        return page

    # ---- 选材筛选与勾选（U6）----
    def _visible_ids(self) -> list[int]:
        """当前筛选（分类 + 关键字）下应当显示的材料 id，保持完整顺序。

        `currentData()` 为 None 即"全部分类"——必须原样传给 dao，
        传 0 会变成"仅默认分类"（有分类的材料会被整批隐藏）。
        """
        cat = self.filt_cat.currentData()
        kw = self.filt_tag.text().strip().lower()
        keep = {d.id for d in dao.list_documents(cat)}
        out = []
        for did in self._order:
            if did not in keep:
                continue
            if kw and kw not in self._labels.get(did, "").lower():
                continue
            out.append(did)
        return out

    def _render_materials(self) -> None:
        """按当前筛选重建列表视图；**勾选状态与顺序都取自 `_order`/`_checked`**。"""
        self.material_list.blockSignals(True)
        self.material_list.clear()
        for did in self._visible_ids():
            item = QListWidgetItem(self._labels.get(did, str(did)))
            item.setData(Qt.UserRole, did)
            item.setCheckState(Qt.Checked if did in self._checked else Qt.Unchecked)
            self.material_list.addItem(item)
        self.material_list.blockSignals(False)

    def _reload_materials(self, *_a) -> None:
        """筛选条件变化：只重画视图，不动勾选与汇编顺序。"""
        self._render_materials()

    def _on_item_changed(self, item) -> None:
        did = item.data(Qt.UserRole)
        if did is None:
            return
        if item.checkState() == Qt.Checked:
            self._checked.add(did)
        else:
            self._checked.discard(did)

    def _check_visible(self, state) -> None:
        """全选/全不选：只作用于**当前可见**条目。

        用户先筛出一批再"全不选"，不该把别的分类里已勾好的选择一起清掉。
        """
        self.material_list.blockSignals(True)
        for i in range(self.material_list.count()):
            self.material_list.item(i).setCheckState(state)
        self.material_list.blockSignals(False)
        for did in self._visible_ids():
            if state == Qt.Checked:
                self._checked.add(did)
            else:
                self._checked.discard(did)

    def _invert_visible(self) -> None:
        self.material_list.blockSignals(True)
        for i in range(self.material_list.count()):
            item = self.material_list.item(i)
            item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked
                               else Qt.Checked)
        self.material_list.blockSignals(False)
        for did in self._visible_ids():
            if did in self._checked:
                self._checked.discard(did)
            else:
                self._checked.add(did)

    def _on_rows_moved(self, *_a) -> None:
        """拖拽排序后，把可见段的新顺序"并回"完整顺序。

        为什么不能直接以可见列表为新顺序：筛选时看不见的材料也占着位置，
        直接覆盖会把它们的相对位置丢掉 —— 取消筛选后顺序就乱了，而汇编
        顺序是用户一篇篇拖出来的。
        """
        visible_now = [self.material_list.item(i).data(Qt.UserRole)
                       for i in range(self.material_list.count())]
        if len(visible_now) != len(set(visible_now)):
            return                      # 异常视图（重复项）不动顺序，宁可保守
        vis = set(visible_now)
        it = iter(visible_now)
        merged = [next(it) if did in vis else did for did in self._order]
        if sorted(merged) == sorted(self._order):
            self._order = merged

    def _current_id(self):
        item = self.material_list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _select_id(self, did) -> None:
        for i in range(self.material_list.count()):
            if self.material_list.item(i).data(Qt.UserRole) == did:
                self.material_list.setCurrentRow(i)
                return

    def _move(self, delta: int):
        """上移/下移：在**完整顺序**里移动，然后重画视图。

        直接在可见列表里换位，会在筛选状态下把材料移到"看不见的邻居"之外 ——
        统一改 `_order`（汇编顺序的唯一依据）再重画，筛选前后顺序才一致。
        """
        did = self._current_id()
        if did is None:
            return
        try:
            idx = self._order.index(did)
        except ValueError:
            return
        target = idx + delta
        if not (0 <= target < len(self._order)):
            return
        self._order.insert(target, self._order.pop(idx))
        self._render_materials()
        self._select_id(did)

    def selected_doc_ids(self) -> list[int]:
        """已勾选材料的 id，按**汇编顺序**返回。

        顺序取自 `_order` 而不是当前视图：筛选只影响可见性，不该改变汇编次序，
        也不该让被筛掉的材料悄悄掉出汇编清单（用户勾过的就是勾过的）。
        """
        return [did for did in self._order if did in self._checked]

    # ------------------------------------------------ 第2步：模板与封面
    def _page_template(self):
        page = QWizardPage()
        page.setTitle("选择模板与封面信息")
        page.setSubTitle("模板可在「模板管理」中自定义。")
        v = QVBoxLayout(page)

        row = QHBoxLayout()
        row.addWidget(QLabel("模板："))
        self.tpl_combo = QComboBox()
        self.tpl_combo.setToolTip(
            "选用哪套版式（字体/字号/行距/页边距）。缺字体的模板会在生成结果里提示")
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
        self.chk_docx.setToolTip("生成可直接编辑、打印的 Word 文档（推荐）")
        self.chk_docx.setChecked(True)
        self.chk_pdf = QCheckBox("生成 A4 PDF（内置渲染器，含目录页码与外侧页码）")
        self.chk_pdf.setToolTip("用内置渲染器生成固定版式的 PDF（不依赖本机 Office）")
        self.chk_pdf.setChecked(False)
        self.chk_booklet = QCheckBox("生成 A3 横向小册子 PDF（骑马钉，需先选 A4 PDF）")
        self.chk_booklet.setToolTip("把 A4 双面打印后对折成 A3 骑马钉小册子")
        self.chk_booklet.setChecked(False)
        self.chk_batch = QCheckBox("批量模式：每份材料单独生成一份规范公文")
        self.chk_batch.setToolTip("勾选后按第1步顺序，对每份材料独立输出一份 docx")
        v.addWidget(self.chk_docx)
        v.addWidget(self.chk_pdf)
        v.addWidget(self.chk_booklet)
        v.addWidget(self.chk_batch)
        # 来源清单：汇编类公文需要可追溯性（"内容从哪来"要答得出来）。
        # 默认勾选——正式对外行文可在向导里取消。
        self.chk_sources = QCheckBox("附「汇编材料来源清单」（便于追溯与归档）")
        self.chk_sources.setChecked(True)
        self.chk_sources.setToolTip(
            "在成稿末尾附一张表，列出每份材料的标题、原文件名、导入时间与分类")
        v.addWidget(self.chk_sources)

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
        # 用**字符串拼接**扩展名，绝不用 Path.with_suffix()：
        # with_suffix 会把最后一个点之后的内容当扩展名替换掉，
        # 标题里的小圆点（日期、版本号）会被整段吞掉 —— 实测
        # 「关于2026.08重点工作的通知」→「关于2026.docx」、
        # 「报告 v1.2 终稿」→「报告 v1.docx」，用户拿到一个名字莫名其妙
        # 甚至互相覆盖的产物。
        title = self.ed_title.text().strip() or "汇编成果"
        out_base = export_dir() / title
        out_base.parent.mkdir(parents=True, exist_ok=True)
        stem = str(out_base)
        any_output = self.chk_docx.isChecked() or self.chk_pdf.isChecked()
        if not any_output:
            info(self, "请至少勾选一种输出。")
            return
        # 防静默覆盖：同名输出已存在时追加时间戳
        if ((self.chk_docx.isChecked() and Path(stem + ".docx").exists())
                or (self.chk_pdf.isChecked() and Path(stem + ".pdf").exists())):
            from datetime import datetime as _dt
            stem = str(export_dir() / f"{title}_"
                       f"{_dt.now().strftime('%Y%m%d_%H%M%S')}")
            info(self, "同名输出已存在，本次输出将追加时间戳。")
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)  # 忙碌指示
        self.btn_start.setEnabled(False)
        self.lbl_result.setText("正在生成…")
        # 清掉上一轮的结果：否则只出 docx 时横幅仍会报告上一轮的 PDF 路径
        self.last_docx = ""
        self.last_pdf = ""
        self.last_booklet = ""

        if self.chk_docx.isChecked():
            tpl2 = tpl
            tpl2.cover.enabled = self.chk_cover.isChecked()
            tpl2.cover.title = self.ed_title.text().strip()
            tpl2.cover.org = self.ed_org.text().strip()
            tpl2.cover.date = self.ed_date.text().strip()
            tpl2.insert_material_titles = self.chk_titles.isChecked()
            out_docx = stem + ".docx"
            self._worker = CompileWorker(ids, [], tpl2, out_docx,
                                         include_sources=self.chk_sources.isChecked(),
                                         parent=self)
            self._worker.done.connect(lambda p: self._after_docx(p, tpl, ids))
            self._worker.error.connect(self._fail)
            self._worker.start()
        elif self.chk_pdf.isChecked():
            self._run_pdf(ids, tpl, stem)

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
            done = Signal(list, list)      # 成功路径, 失败清单[(标题, 原因)]
            error = Signal(str)

            def run(self_inner):
                try:
                    paths, failures = batch_compile_each(
                        ids, tpl, str(outdir), cover=cover,
                        progress_cb=lambda i, n: self_inner.progress.emit(i, n))
                    self_inner.done.emit(paths, failures)
                except Exception as exc:
                    self_inner.error.emit(str(exc))
                finally:
                    # 批量汇编里每份材料都走 dao（thread-local 连接）：线程结束
                    # 不关连接，-wal/-shm 句柄与连接登记项都会残留——长会话下
                    # 既可能 too many open files，也会让恢复备份误判"被占用"。
                    _close_thread_conn()

        self._batch_worker = _BatchThread(self)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(ids))
        self.btn_start.setEnabled(False)

        def on_prog(i, n):
            self.progress.setValue(i)
            self.lbl_result.setText(f"批量生成中 {i}/{n}…")

        def on_done(paths, failures):
            self.progress.setVisible(False)
            self.btn_start.setEnabled(True)
            msg = f"批量生成完成：成功 {len(paths)} 份"
            if failures:
                msg += f"，失败 {len(failures)} 份：\n" + "\n".join(
                    f"· {t}：{r}" for t, r in failures[:10])
                if len(failures) > 10:
                    msg += f"\n… 共失败 {len(failures)} 份"
            listing = "\n".join(Path(p).name for p in paths[:10])
            if listing:
                msg += "\n\n成功输出（前10份）：\n" + listing
                if len(paths) > 10:
                    msg += f"\n… 共 {len(paths)} 份"
            self.lbl_result.setText(msg + f"\n输出目录：{outdir}")
            info(self, msg + f"\n\n输出目录：\n{outdir}")

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
            # 去掉扩展名同样用字符串切片：with_suffix("") 会把标题里最后一个
            # 点之后的内容当成扩展名删掉（"关于2026.08…" -> "关于2026"）。
            stem = path[:-len(".docx")] if path.lower().endswith(".docx") else path
            self._run_pdf(ids, tpl, stem)
        else:
            self._finish(path)

    def _run_pdf(self, ids, tpl: DocTemplate, stem):
        tpl.cover.enabled = self.chk_cover.isChecked()
        tpl.cover.title = self.ed_title.text().strip()
        tpl.cover.org = self.ed_org.text().strip()
        tpl.cover.date = self.ed_date.text().strip()
        tpl.insert_material_titles = self.chk_titles.isChecked()
        out_pdf = f"{stem}.pdf" if not str(stem).lower().endswith(".pdf") else str(stem)
        self._pdf_worker = PdfRenderWorker(ids, [], tpl, out_pdf, parent=self)
        self._pdf_worker.done.connect(self._after_pdf)
        self._pdf_worker.error.connect(self._fail)
        self._pdf_worker.start()

    def _after_pdf(self, path: str):
        self.last_pdf = path
        if self.chk_booklet.isChecked():
            stem = path[:-len(".pdf")] if path.lower().endswith(".pdf") else path
            out_bk = f"{stem}_小册子A3.pdf"
            self._booklet_worker = BookletWorker(path, out_bk, parent=self)
            self._booklet_worker.done.connect(lambda p: self._finish(path, p))
            self._booklet_worker.error.connect(self._fail)
            self._booklet_worker.start()
        else:
            self._finish(path)

    def _finish(self, docx_path: str, booklet_path: str = ""):
        self.progress.setVisible(False)
        self.btn_start.setEnabled(True)
        msg = []
        if docx_path:
            msg.append(f"Word 文档：{docx_path}")
        # 只在**本轮**真的生成了 PDF 时才报告 PDF 路径。
        # last_pdf 是跨轮次的字段，若不清空/不判断，用户第二次取消勾选 PDF
        # 只出 docx，横幅仍会显示上一轮那份 PDF，让人以为文件也重新生成了。
        if self.last_pdf and self.chk_pdf.isChecked():
            msg.append(f"A4 PDF：{self.last_pdf}")
        if booklet_path:
            msg.append(f"小册子：{booklet_path}")
        # 字体可观测性：缺字体时 Word/WPS 静默替换，成品排版可能不合规。
        # 首启弹窗已改为状态栏常驻（N7），这里是**每次生成**都给的一次性核对机会。
        from ..core.fontcheck import missing_note
        note = missing_note()
        if note:
            msg.append(note)
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
