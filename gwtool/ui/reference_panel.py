# -*- coding: utf-8 -*-
"""右侧面板：纠错建议 + 写作参考。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QSplitter, QTextEdit, QVBoxLayout,
                               QWidget)

from ..core import corrector, reference
from ..db import dao


class ReferencePanel(QWidget):
    """纠错建议列表（可一键替换）+ 写作参考（检索+一键插入）。"""
    insert_text = Signal(str)
    apply_edit = Signal(int, int, str)   # start, end, replacement -> 编辑器

    def __init__(self, editor_getter, parent=None):
        super().__init__(parent)
        self._editor_getter = editor_getter   # () -> str，当前编辑器文本
        self._corrections: list[corrector.Correction] = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        split = QSplitter(Qt.Vertical)

        # ---- 纠错区 ----
        corr_widget = QWidget()
        v1 = QVBoxLayout(corr_widget)
        v1.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.btn_check = QPushButton("检查当前文档")
        self.btn_check.clicked.connect(self.run_check)
        self.chk_min_conf = QDoubleSpinBox()
        self.chk_min_conf.setRange(0.0, 1.0)
        self.chk_min_conf.setValue(0.5)
        self.chk_min_conf.setPrefix("置信度≥")
        self.chk_min_conf.setToolTip("低于该置信度的建议不显示；精标词库≥0.85，程序生成混淆对0.55")
        self.lbl_count = QLabel("")
        bar.addWidget(self.btn_check)
        bar.addWidget(self.chk_min_conf)
        bar.addWidget(self.lbl_count)
        bar.addStretch(1)
        v1.addLayout(bar)

        self.corr_list = QListWidget()
        self.corr_list.itemDoubleClicked.connect(self._apply_one)
        v1.addWidget(self.corr_list, 1)
        bar2 = QHBoxLayout()
        btn_apply = QPushButton("替换所选")
        btn_apply.clicked.connect(self._apply_one)
        btn_apply_all = QPushButton("全部替换")
        btn_apply_all.clicked.connect(self._apply_all)
        btn_ignore = QPushButton("忽略所选")
        btn_ignore.clicked.connect(self._ignore_one)
        bar2.addWidget(btn_apply)
        bar2.addWidget(btn_apply_all)
        bar2.addWidget(btn_ignore)
        bar2.addStretch(1)
        v1.addLayout(bar2)
        split.addWidget(corr_widget)

        # ---- 写作参考区 ----
        ref_widget = QWidget()
        v2 = QVBoxLayout(ref_widget)
        v2.setContentsMargins(0, 0, 0, 0)
        sbar = QHBoxLayout()
        self.ref_input = QLineEdit()
        self.ref_input.setPlaceholderText("输入词语/主题，检索资料、词典与句式…")
        self.ref_input.returnPressed.connect(self.run_reference)
        btn_ref = QPushButton("检索")
        btn_ref.clicked.connect(self.run_reference)
        sbar.addWidget(self.ref_input, 1)
        sbar.addWidget(btn_ref)
        v2.addLayout(sbar)

        self.ref_list = QListWidget()
        self.ref_list.itemDoubleClicked.connect(self._insert_ref)
        v2.addWidget(self.ref_list, 1)
        bar3 = QHBoxLayout()
        btn_insert = QPushButton("插入到光标处")
        btn_insert.clicked.connect(self._insert_ref)
        btn_add_phrase = QPushButton("存为常用句式")
        btn_add_phrase.clicked.connect(self._save_as_phrase)
        bar3.addWidget(btn_insert)
        bar3.addWidget(btn_add_phrase)
        bar3.addStretch(1)
        v2.addLayout(bar3)
        self.lbl_ref = QLabel("")
        v2.addWidget(self.lbl_ref)
        split.addWidget(ref_widget)

        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)

    # ------------------------------------------------ 纠错
    def run_check(self):
        self._checked_text = self._editor_getter()
        self._corrections = corrector.check_text(self._checked_text)
        self._fill_corr_list()

    def _fill_corr_list(self):
        min_conf = self.chk_min_conf.value()
        text = getattr(self, "_checked_text", "") or self._editor_getter()
        self.corr_list.clear()
        shown = 0
        for i, c in enumerate(self._corrections):
            if c.confidence < min_conf:
                continue
            ctx_a = max(0, c.start - 10)
            ctx = ("…" if ctx_a > 0 else "") + \
                text[ctx_a:c.end + 12].replace("\n", " ")
            item = QListWidgetItem(f"[{c.category}] {c.wrong} → {c.suggestion}\n"
                                   f"    上下文：{ctx}…  ({c.confidence:.2f})")
            item.setData(Qt.UserRole, i)
            item.setToolTip(c.reason)
            self.corr_list.addItem(item)
            shown += 1
        self.lbl_count.setText(f"共 {len(self._corrections)} 项，显示 {shown} 项")

    def _selected_corrections(self) -> list[corrector.Correction]:
        idxs = [item.data(Qt.UserRole) for item in self.corr_list.selectedItems()]
        return [self._corrections[i] for i in idxs if i < len(self._corrections)]

    def _apply_one(self, *_):
        for c in reversed(self._selected_corrections()):
            self.apply_edit.emit(c.start, c.end, c.suggestion)
        self.run_check()

    def _apply_all(self):
        min_conf = self.chk_min_conf.value()
        to_apply = [c for c in self._corrections
                    if c.confidence >= min_conf and c.category not in ("数字用法",)]
        for c in sorted(to_apply, key=lambda x: x.start, reverse=True):
            self.apply_edit.emit(c.start, c.end, c.suggestion)
        self.run_check()

    def _ignore_one(self):
        for item in self.corr_list.selectedItems():
            i = item.data(Qt.UserRole)
            if i < len(self._corrections):
                self._corrections[i] = None  # 占位
        self._corrections = [c for c in self._corrections if c is not None]
        self._fill_corr_list()

    # ------------------------------------------------ 写作参考
    def run_reference(self):
        q = self.ref_input.text().strip()
        self.ref_list.clear()
        if not q:
            return
        items = reference.lookup(q)
        for it in items:
            label = f"[{it.source_label}] {it.title}\n    {it.snippet}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, (it.source, it.ref_id))
            item.setToolTip(it.snippet)
            self.ref_list.addItem(item)
        self.lbl_ref.setText(f"相关结果 {len(items)} 条（双击插入；按相关度排序）")

    def _insert_ref(self, *_):
        items = self.ref_list.selectedItems()
        if not items:
            return
        src, ref_id = items[0].data(Qt.UserRole)
        text = ""
        if src == "documents":
            text = reference.document_full_text(ref_id)
        elif src == "phrases":
            for p in dao.list_phrases():
                if p.id == ref_id:
                    text = p.context or p.phrase
                    break
        elif src == "dictionary":
            # 按 ref_id 查词典条目：取词与释义
            from ..db.connection import get_conn
            row = get_conn().execute(
                "SELECT word,pinyin,definition FROM dictionary WHERE id=?",
                (ref_id,)).fetchone()
            if row:
                text = f"{row['word']}" + (f"（{row['pinyin']}）" if row["pinyin"] else "")
                if row["definition"]:
                    text += f"：{row['definition']}"
        if text:
            self.insert_text.emit(text)

    def _save_as_phrase(self):
        items = self.ref_list.selectedItems()
        if not items:
            return
        src, ref_id = items[0].data(Qt.UserRole)
        if src == "documents":
            text = reference.document_full_text(ref_id)
        else:
            return
        if text:
            dao.add_phrase(text[:2000], context=text[:2000],
                           source="收藏", tag="来自资料库")
            self.lbl_ref.setText("已存为常用句式。")
