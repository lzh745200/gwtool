# -*- coding: utf-8 -*-
"""中部编辑面板：编辑（含大纲侧栏+自动保存+工具箱右键）+ 预览 + 输出预览。"""
from __future__ import annotations

import re

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QFont, QImage, QPixmap, QShortcut, QKeySequence, \
    QTextCursor, QTextDocument
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QPushButton, QScrollArea, QSplitter, QTabWidget,
                               QTextBrowser, QTextEdit, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import toolbox
from ..core.model import DocTree, HEADING, PARAGRAPH

_HEADING_RE = re.compile(
    r"^([一二三四五六七八九十]+、|（[一二三四五六七八九十]+）|\d{1,2}[、..]|"
    r"第[一二三四五六七八九十百\d]+[章节条])")


def _render_pdf_images(pdf_path: str, max_pages: int = 8):
    """后台渲染 PDF 页面为 QImage 列表（QImage 可跨线程传递，QPixmap 不行）。"""
    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover
        import fitz as fitz  # type: ignore
    doc = fitz.open(pdf_path)
    n = min(doc.page_count, max_pages)
    images = []
    for i in range(n):
        pix = doc[i].get_pixmap(dpi=80)
        img = QImage(pix.samples, pix.width, pix.height,
                     pix.stride, QImage.Format_RGB888)
        images.append(img.copy())
    doc.close()
    return images, n, pdf_path


class EditorPanel(QWidget):
    """中间编辑器：加载资料库文档、保存回库；预览标签页。"""
    content_modified = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc_id: int | None = None
        self._dirty = False
        self._build_ui()
        # 自动保存：每 3 分钟快照一次（有改动才存）
        self._auto_timer = QTimer(self)
        self._auto_timer.start(180_000)
        self._auto_timer.timeout.connect(self._auto_snapshot)
        self._outline_timer = QTimer(self)
        self._outline_timer.setSingleShot(True)
        self._outline_timer.setInterval(600)
        self._outline_timer.timeout.connect(self.rebuild_outline)
        # 预览防抖刷新：编辑中仅标记，切到预览页或定时器到点且预览页可见时才渲染
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(600)
        self._preview_timer.timeout.connect(self._on_preview_timer)
        self._preview_dirty = False
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.tabs = QTabWidget()

        # 编辑页：左侧大纲 + 编辑器
        self.outline = QTreeWidget()
        self.outline.setHeaderLabel("大纲")
        self.outline.setMaximumWidth(210)
        self.outline.itemClicked.connect(self._jump_to_heading)
        self.editor = QTextEdit()
        self.editor.setPlaceholderText(
            "双击左侧材料打开编辑；或直接粘贴文字。\n"
            "支持：Ctrl+S 保存到资料库；选中文字后右键可用文秘工具箱"
            "（金额大写/简繁转换等）；每 3 分钟自动保存快照。")
        font = QFont("仿宋", 13)
        self.editor.setFont(font)
        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.setContextMenuPolicy(Qt.CustomContextMenu)
        self.editor.customContextMenuRequested.connect(self._editor_menu)

        edit_page = QWidget()
        outer = QVBoxLayout(edit_page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)
        outer.addWidget(self._build_find_bar())
        ev = QHBoxLayout(edit_page)
        ev.setContentsMargins(0, 0, 0, 0)
        ev.addWidget(self.outline)
        ev.addWidget(self.editor, 1)
        outer.addLayout(ev, 1)
        self.tabs.addTab(edit_page, "编辑")

        # 预览
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)

        # PDF 预览区
        self.pdf_area = QScrollArea()
        self.pdf_label = QLabel()
        self.pdf_label.setAlignment(Qt.AlignHCenter)
        self.pdf_area.setWidget(self.pdf_label)

        self.tabs.addTab(self.preview, "预览")
        self.tabs.addTab(self.pdf_area, "输出预览")
        layout.addWidget(self.tabs, 1)

        bar = QHBoxLayout()
        self.lbl_status = QLabel("未打开文档")
        btn_save = QPushButton("保存到资料库")
        btn_save.clicked.connect(self.save_to_db)
        btn_clean = QPushButton("一键排版微调")
        btn_clean.clicked.connect(self.run_formatter)
        bar.addWidget(self.lbl_status)
        bar.addStretch(1)
        bar.addWidget(btn_clean)
        bar.addWidget(btn_save)
        layout.addLayout(bar)

    # ------------------------------------------------ 查找/替换（Ctrl+F / Ctrl+H）
    def _build_find_bar(self) -> QWidget:
        bar = QWidget()
        v = QVBoxLayout(bar)
        v.setContentsMargins(0, 0, 0, 4)
        v.setSpacing(2)

        row1 = QHBoxLayout()
        row1.setSpacing(4)
        self.ed_find = QLineEdit()
        self.ed_find.setPlaceholderText("查找内容（回车=下一个，Shift+回车=上一个）")
        self.ed_find.textChanged.connect(lambda _t: self._update_find_count())
        self.lbl_find_count = QLabel("")
        btn_prev = QPushButton("上一个")
        btn_prev.clicked.connect(lambda: self._find(backward=True))
        btn_next = QPushButton("下一个")
        btn_next.clicked.connect(lambda: self._find())
        btn_close = QPushButton("×")
        btn_close.setFixedWidth(28)
        btn_close.clicked.connect(lambda: self.find_bar.setVisible(False))
        row1.addWidget(self.ed_find, 1)
        row1.addWidget(self.lbl_find_count)
        row1.addWidget(btn_prev)
        row1.addWidget(btn_next)
        row1.addWidget(btn_close)
        v.addLayout(row1)

        self.replace_row = QWidget()
        row2 = QHBoxLayout(self.replace_row)
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(4)
        self.ed_replace = QLineEdit()
        self.ed_replace.setPlaceholderText("替换为…")
        btn_repl = QPushButton("替换")
        btn_repl.clicked.connect(self._replace_one)
        btn_repl_all = QPushButton("全部替换")
        btn_repl_all.clicked.connect(self._replace_all)
        row2.addWidget(self.ed_replace, 1)
        row2.addWidget(btn_repl)
        row2.addWidget(btn_repl_all)
        v.addWidget(self.replace_row)

        self.find_bar = bar
        self.find_bar.setVisible(False)
        self.replace_row.setVisible(False)
        self.ed_find.returnPressed.connect(lambda: self._find())
        sc_prev = QShortcut(QKeySequence("Shift+Return"), self.ed_find)
        sc_prev.activated.connect(lambda: self._find(backward=True))
        sc_esc = QShortcut(QKeySequence("Escape"), self.ed_find)
        sc_esc.activated.connect(lambda: self.find_bar.setVisible(False))
        sc_find = QShortcut(QKeySequence("Ctrl+F"), self.editor)
        sc_find.activated.connect(lambda: self.show_find(replace=False))
        sc_repl = QShortcut(QKeySequence("Ctrl+H"), self.editor)
        sc_repl.activated.connect(lambda: self.show_find(replace=True))
        return bar

    def show_find(self, replace: bool = False):
        self.find_bar.setVisible(True)
        self.replace_row.setVisible(replace)
        cur = self.editor.textCursor()
        if cur.hasSelection() and "\n" not in cur.selectedText():
            self.ed_find.setText(cur.selectedText())
        self.ed_find.setFocus()
        self.ed_find.selectAll()
        self._update_find_count()

    def _find(self, backward: bool = False):
        text = self.ed_find.text()
        if not text:
            return
        flags = QTextDocument.FindFlag(0)
        if backward:
            flags |= QTextDocument.FindFlag.FindBackward
        if not self.editor.find(text, flags):
            # 到头回绕再找一次
            cur = self.editor.textCursor()
            cur.movePosition(QTextCursor.End if backward else QTextCursor.Start)
            self.editor.setTextCursor(cur)
            if not self.editor.find(text, flags):
                self.lbl_find_count.setText("无结果")
                return
        self._update_find_count()

    def _update_find_count(self):
        text = self.ed_find.text()
        if not text:
            self.lbl_find_count.setText("")
            return
        body = self.editor.toPlainText()
        total = body.count(text)
        if not total:
            self.lbl_find_count.setText("无结果")
            return
        pos = self.editor.textCursor().position()
        idx = body[:pos].count(text)
        self.lbl_find_count.setText(f"{max(idx, 1)}/{total}")

    def _replace_one(self):
        text = self.ed_find.text()
        if not text:
            return
        cur = self.editor.textCursor()
        if cur.hasSelection() and cur.selectedText() == text:
            cur.insertText(self.ed_replace.text())
        self._find()

    def _replace_all(self):
        text = self.ed_find.text()
        if not text:
            return
        body = self.editor.toPlainText()
        n = body.count(text)
        if not n:
            self.lbl_find_count.setText("无结果")
            return
        self.replace_document_text(body.replace(text, self.ed_replace.text()))
        self._update_status(f"已替换 {n} 处 ● 修改未保存")
        self._update_find_count()

    # ------------------------------------------------ 大纲
    def _on_text_changed(self):
        if not self._dirty:
            self._dirty = True
            self._update_status("● 修改未保存")
        self._outline_timer.start()
        self._preview_timer.start()

    def _on_preview_timer(self):
        if self.tabs.currentWidget() is self.preview:
            self.update_preview()
        else:
            self._preview_dirty = True

    def _on_tab_changed(self, _idx: int):
        if self.tabs.currentWidget() is self.preview and self._preview_dirty:
            self.update_preview()
        self._preview_dirty = False

    def rebuild_outline(self):
        self.outline.blockSignals(True)
        self.outline.clear()
        text = self.editor.toPlainText()
        stack: list[QTreeWidgetItem] = []
        for line_no, line in enumerate(text.split("\n"), 1):
            s = line.strip()
            m = _HEADING_RE.match(s)
            if not m or len(s) > 50:
                continue
            # 层级：一、=1 （一）=2 1.=3 第x章=1
            prefix = m.group(1)
            if prefix.startswith("第"):
                level = 1
            elif prefix.startswith("（"):
                level = 2 if "一二三四五六七八九十" in prefix else 3
            elif prefix[0] in "一二三四五六七八九十":
                level = 1
            else:
                level = 3
            item = QTreeWidgetItem([s[:30]])
            item.setData(0, Qt.UserRole, line_no)
            while len(stack) >= level:
                stack.pop()
            if stack:
                stack[-1].addChild(item)
            else:
                self.outline.addTopLevelItem(item)
            stack.append(item)
        self.outline.expandAll()
        self.outline.blockSignals(False)

    def _jump_to_heading(self, item: QTreeWidgetItem, _col):
        line_no = item.data(0, Qt.UserRole)
        doc = self.editor.document()
        block = doc.findBlockByNumber(line_no - 1)
        if block.isValid():
            cur = self.editor.textCursor()
            cur.setPosition(block.position())
            self.editor.setTextCursor(cur)
            self.editor.centerCursor()
            self.editor.setFocus()

    def load_document(self, doc_id: int):
        from ..db import dao
        d = dao.get_document(doc_id)
        if not d:
            return
        if not self.confirm_discard_changes():
            return
        self.doc_id = doc_id
        self.editor.setPlainText(d.content_text)
        self._dirty = False
        self._update_status(f"已打开：{d.title}")
        self.rebuild_outline()
        self.update_preview()

    def confirm_discard_changes(self) -> bool:
        """有未保存修改时三选询问。返回 False 表示用户取消，应中断后续动作。"""
        if not self._dirty:
            return True
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setWindowTitle("未保存的修改")
        box.setText("当前内容有未保存的修改，是否保存？")
        b_save = box.addButton("保存并继续", QMessageBox.AcceptRole)
        b_discard = box.addButton("放弃修改", QMessageBox.DestructiveRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_save:
            self.save_to_db()
            return not self._dirty     # 另存被用户取消等情况则中断
        if clicked is b_discard:
            self._dirty = False
            return True
        return False

    def replace_document_text(self, new_text: str):
        """程序化全文替换：整体一个撤销块，Ctrl+Z 可一次还原；光标位置尽量保留。

        供纠错替换、排版微调、全半角、快照回滚等使用，避免 setPlainText
        清空 QTextEdit 撤销历史导致用户无法反悔。
        """
        cur = self.editor.textCursor()
        pos = min(cur.position(), len(new_text))
        cur.beginEditBlock()
        cur.select(QTextCursor.SelectionType.Document)
        cur.insertText(new_text)
        cur.endEditBlock()
        cur.setPosition(pos)
        self.editor.setTextCursor(cur)

    def save_to_db(self):
        from .widgets import ask, warn
        text = self.editor.toPlainText()
        from ..db import dao
        if self.doc_id is None:
            if not text.strip():
                warn(self, "当前内容为空，无可保存。")
                return
            if not ask(self, "当前内容未关联资料库文档，是否另存为新文档？"):
                return
            title = next((ln.strip() for ln in text.splitlines() if ln.strip()),
                         "")[:60] or "未命名"
            did = dao.add_document(dao.Document(title=title, content_text=text))
            if did > 0:
                self.doc_id = did
                self._dirty = False
                self._update_status(f"已另存为新文档：{title}")
                self.content_modified.emit()
            else:
                warn(self, "内容与资料库已有文档重复，未另存。")
            return
        d = dao.get_document(self.doc_id)
        title = d.title if d else "未命名"
        # 保存前快照（覆盖前留档）
        if d and d.content_text != text:
            dao.add_snapshot(self.doc_id, title, d.content_text, reason="保存前")
        dao.update_document_content(self.doc_id, title, text)
        self._dirty = False
        self._update_status(f"已保存：{title}")

    def _auto_snapshot(self):
        """自动保存：有修改且已关联文档时快照当前内容。"""
        if not self._dirty or self.doc_id is None:
            return
        text = self.editor.toPlainText()
        if not text.strip():
            return
        from ..db import dao
        dao.add_snapshot(self.doc_id, "自动快照", text, reason="auto")
        self._update_status("已自动保存快照 ● 修改未保存")

    def _update_status(self, text: str = ""):
        n = len(re.sub(r"\s", "", self.editor.toPlainText()))
        self.lbl_status.setText(f"{text}（{n} 字）" if text else f"当前 {n} 字")

    # ------------------------------------------------ 文秘工具箱右键
    def _editor_menu(self, pos):
        from .widgets import info
        menu = QMenu(self)
        cursor = self.editor.textCursor()
        selected = cursor.selectedText()

        def op(fn, note):
            def run():
                if not selected:
                    info(self, f"请先选中要处理的文字（{note}）。")
                    return
                try:
                    result = fn(selected)
                except Exception as exc:
                    info(self, f"处理失败：{exc}")
                    return
                cursor.insertText(result)
            return run

        menu.addAction("金额转大写（选区）", op(self._op_amount, "金额"))
        menu.addAction("日期转大写（选区）", op(self._op_date, "日期"))
        menu.addAction("数字转大写码（选区）", op(self._op_num, "数字"))
        menu.addSeparator()
        menu.addAction("简→繁（选区）", op(self._op_s2t, "文字"))
        menu.addAction("繁→简（选区）", op(self._op_t2s, "文字"))
        menu.addSeparator()
        menu.addAction("全角→半角（全文）",
                       lambda: self._apply_transform(toolbox.full_to_half))
        menu.addAction("半角→全角（全文）",
                       lambda: self._apply_transform(toolbox.half_to_full))
        menu.addSeparator()
        menu.addAction("一键排版微调（全文）", self.run_formatter)
        menu.exec(self.editor.viewport().mapToGlobal(pos))

    def _apply_transform(self, fn):
        self.replace_document_text(fn(self.editor.toPlainText()))

    @staticmethod
    def _op_amount(s: str):
        return toolbox.amount_to_cn(s.strip().replace("￥", "").replace("¥", ""))

    @staticmethod
    def _op_date(s: str):
        return toolbox.digits_to_cn_date(s)

    @staticmethod
    def _op_num(s: str):
        return toolbox.number_to_upper_cn(s)

    @staticmethod
    def _op_s2t(s: str):
        return toolbox.s2t(s)

    @staticmethod
    def _op_t2s(s: str):
        return toolbox.t2s(s)

    # ------------------------------------------------ 微调
    def run_formatter(self):
        from ..core.formatter import run_full_cleanup
        from ..ui.widgets import info
        text = self.editor.toPlainText()
        if not text.strip():
            return
        new_text, log = run_full_cleanup(text)
        self.replace_document_text(new_text)
        if log:
            info(self, "排版微调完成：\n" + "\n".join(f"· {x}" for x in log))
        else:
            info(self, "未发现需要调整的内容。")

    # ------------------------------------------------ 预览
    def update_preview(self):
        """把编辑器内容按公文样式近似渲染为 HTML 预览。"""
        from ..core.template import default_template
        from ..core.pdfrender import trees_to_html
        tree = DocTree()
        for line in self.editor.toPlainText().split("\n"):
            if line.strip():
                from ..core.model import Block
                tree.blocks.append(Block(text=line.strip()))
        tpl = default_template()
        tpl.red_header.enabled = False
        html = trees_to_html([tree], tpl, toc_pages=[])
        self.preview.setHtml(html)

    def show_pdf(self, pdf_path: str):
        """把生成的 PDF 渲染成页面图片展示（后台渲染，避免长文档卡 UI）。"""
        from .workers import FnWorker
        self._update_status("输出预览渲染中…")
        self._pdf_worker = FnWorker(_render_pdf_images, pdf_path, parent=self)
        self._pdf_worker.ok.connect(self._on_pdf_images)
        self._pdf_worker.failed.connect(
            lambda m: self._update_status(f"输出预览失败：{m}"))
        self._pdf_worker.start()

    def _on_pdf_images(self, result):
        images, n, pdf_path = result
        combined = QImage(0, 0) if not images else None
        from PySide6.QtGui import QPainter
        if images:
            total_h = sum(im.height() for im in images) + 8 * (len(images) - 1)
            combined = QImage(images[0].width(), total_h, QImage.Format_RGB888)
            combined.fill(0xffffff)
            painter = QPainter(combined)
            y = 0
            for im in images:
                painter.drawImage(0, y, im)
                y += im.height() + 8
            painter.end()
        self.pdf_label.setPixmap(QPixmap.fromImage(combined))
        self.pdf_label.resize(combined.size())
        self.tabs.setCurrentWidget(self.pdf_area)
        self._update_status(f"输出预览：{pdf_path}（前{n}页）")
