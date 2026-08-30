# -*- coding: utf-8 -*-
"""中部编辑面板：编辑（含大纲侧栏+自动保存+工具箱右键）+ 预览 + 输出预览。"""
from __future__ import annotations

import re

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QFont, QImage, QPixmap, QTextCursor
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMenu, QPushButton,
                               QScrollArea, QSplitter, QTabWidget, QTextBrowser,
                               QTextEdit, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from ..core import toolbox
from ..core.model import DocTree, HEADING, PARAGRAPH

_HEADING_RE = re.compile(
    r"^([一二三四五六七八九十]+、|（[一二三四五六七八九十]+）|\d{1,2}[、..]|"
    r"第[一二三四五六七八九十百\d]+[章节条])")


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
        ev = QHBoxLayout(edit_page)
        ev.setContentsMargins(0, 0, 0, 0)
        ev.addWidget(self.outline)
        ev.addWidget(self.editor, 1)
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

    # ------------------------------------------------ 大纲
    def _on_text_changed(self):
        if not self._dirty:
            self._dirty = True
            self._update_status("● 修改未保存")
        self._outline_timer.start()

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
        self.doc_id = doc_id
        self.editor.setPlainText(d.content_text)
        self._dirty = False
        self._update_status(f"已打开：{d.title}")
        self.rebuild_outline()
        self.update_preview()

    def save_to_db(self):
        if self.doc_id is None:
            from ..ui.widgets import warn
            warn(self, "当前内容未关联资料库文档（请在左侧双击打开或先导入）。")
            return
        from ..db import dao
        d = dao.get_document(self.doc_id)
        title = d.title if d else "未命名"
        # 保存前快照（覆盖前留档）
        if d and d.content_text != self.editor.toPlainText():
            dao.add_snapshot(self.doc_id, title, d.content_text, reason="保存前")
        dao.update_document_content(self.doc_id, title, self.editor.toPlainText())
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

    def _update_status(self, text: str):
        self.lbl_status.setText(text)

    # ------------------------------------------------ 文秘工具箱右键
    def _editor_menu(self, pos):
        from .widgets import info
        menu = QMenu(self)
        cursor = self.editor.textCursor()
        selected = cursor.selectedText()

        def op(fn, note):
            def run():
                if not selected:
                    info(f"请先选中要处理的文字（{note}）。")
                    return
                try:
                    result = fn(selected)
                except Exception as exc:
                    info(f"处理失败：{exc}")
                    return
                cursor.insertText(result)
            return run

        menu.addAction("📝 金额转大写（选区）", op(self._op_amount, "金额"))
        menu.addAction("📅 日期转大写（选区）", op(self._op_date, "日期"))
        menu.addAction("🔢 数字转大写码（选区）", op(self._op_num, "数字"))
        menu.addSeparator()
        menu.addAction("简→繁（选区）", op(self._op_s2t, "文字"))
        menu.addAction("繁→简（选区）", op(self._op_t2s, "文字"))
        menu.addSeparator()
        menu.addAction("全角→半角（全文）",
                       lambda: self._apply_transform(toolbox.full_to_half))
        menu.addAction("半角→全角（全文）",
                       lambda: self._apply_transform(toolbox.half_to_full))
        menu.addSeparator()
        menu.addAction("🧹 一键排版微调（全文）", self.run_formatter)
        menu.exec(self.editor.viewport().mapToGlobal(pos))

    def _apply_transform(self, fn):
        text = self.editor.toPlainText()
        self.editor.setPlainText(fn(text))

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
        self.editor.setPlainText(new_text)
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
        """把生成的 PDF 渲染成页面图片展示。"""
        try:
            import pymupdf as fitz
        except ImportError:  # pragma: no cover
            import fitz as fitz  # type: ignore
        doc = fitz.open(pdf_path)
        n = min(doc.page_count, 8)  # 最多预览前8页
        images = []
        for i in range(n):
            pix = doc[i].get_pixmap(dpi=80)
            img = QImage(pix.samples, pix.width, pix.height,
                         pix.stride, QImage.Format_RGB888)
            images.append(img.copy())
        doc.close()
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
