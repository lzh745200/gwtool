# -*- coding: utf-8 -*-
"""左侧资料库面板：分类树 + 标签 + 文档列表 + 全文检索。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMenu, QPushButton, QSplitter, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from ..db import dao
from ..ui.widgets import ask, info


class LibraryPanel(QWidget):
    """信号：open_document(int)、import_requested()、search_requested(str) 由
    搜索框回车触发（主窗口联动右侧参考面板）。"""
    open_document = Signal(int)
    import_requested = Signal(int)          # 导入到该分类
    selection_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.reload()

    # ------------------------------------------------ UI
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("全文检索（回车搜索，F3 聚焦）")
        self.search_box.returnPressed.connect(self._on_search)
        layout.addWidget(self.search_box)

        self.cat_tree = QTreeWidget()
        self.cat_tree.setHeaderLabel("分类")
        self.cat_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.cat_tree.customContextMenuRequested.connect(self._cat_menu)
        self.cat_tree.itemSelectionChanged.connect(self._reload_docs)

        self.doc_list = QListWidget()
        self.doc_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.doc_list.customContextMenuRequested.connect(self._doc_menu)
        self.doc_list.itemDoubleClicked.connect(
            lambda item: self.open_document.emit(item.data(Qt.UserRole)))
        self.doc_list.setSelectionMode(QAbstractItemView.ExtendedSelection)

        split = QSplitter(Qt.Vertical)
        split.addWidget(self.cat_tree)
        split.addWidget(self.doc_list)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        layout.addWidget(split, 1)

        bottom = QHBoxLayout()
        self.btn_import = QPushButton("导入材料")
        self.btn_import.clicked.connect(self._import)
        self.btn_all = QPushButton("全部文档")
        self.btn_all.clicked.connect(self._show_all)
        self.count_label = QLabel("0 篇")
        bottom.addWidget(self.btn_import)
        bottom.addWidget(self.btn_all)
        bottom.addStretch(1)
        bottom.addWidget(self.count_label)
        layout.addLayout(bottom)

    # ------------------------------------------------ 数据
    def reload(self):
        self._fill_categories()
        self._reload_docs()

    def current_category(self) -> int | None:
        item = self.cat_tree.currentItem()
        if item is None:
            return None
        return item.data(0, Qt.UserRole)

    def selected_doc_ids(self) -> list[int]:
        return [item.data(Qt.UserRole) for item in self.doc_list.selectedItems()]

    # ------------------------------------------------ 内部
    def _fill_categories(self):
        self.cat_tree.clear()
        root = QTreeWidgetItem(["全部文档"])
        root.setData(0, Qt.UserRole, None)
        self.cat_tree.addTopLevelItem(root)
        cats = dao.list_categories()
        by_parent: dict[int, list] = {}
        for c in cats:
            by_parent.setdefault(c.parent_id, []).append(c)
        nodes: dict[int, QTreeWidgetItem] = {}

        def add_children(parent_item, parent_id):
            for c in by_parent.get(parent_id, []):
                node = QTreeWidgetItem([c.name])
                node.setData(0, Qt.UserRole, c.id)
                parent_item.addChild(node)
                nodes[c.id] = node
                add_children(node, c.id)

        add_children(root, 0)
        root.setExpanded(True)
        self.cat_tree.setCurrentItem(root)

    def _reload_docs(self):
        cat = self.current_category()
        docs = dao.list_documents(cat)
        self.doc_list.clear()
        for d in docs:
            label = f"{d.title}    [{d.file_type or '文本'}] {d.word_count}字"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, d.id)
            item.setToolTip(d.title)
            self.doc_list.addItem(item)
        self.count_label.setText(f"{len(docs)} 篇")

    def _show_all(self):
        self.cat_tree.setCurrentItem(self.cat_tree.topLevelItem(0))

    def _on_search(self):
        kw = self.search_box.text().strip()
        self.doc_list.clear()
        if not kw:
            self._reload_docs()
            return
        results = dao.search_documents(kw, limit=100)
        for r in results:
            item = QListWidgetItem(f"{r.title}\n    {r.snippet}")
            item.setData(Qt.UserRole, r.ref_id)
            self.doc_list.addItem(item)
        self.count_label.setText(f"检索到 {len(results)} 处")

    # ------------------------------------------------ 右键菜单
    def _cat_menu(self, pos):
        menu = QMenu(self)
        menu.addAction("新建分类", self._add_category)
        item = self.cat_tree.currentItem()
        cat_id = item.data(0, Qt.UserRole) if item else None
        if cat_id:
            menu.addAction("重命名分类", self._rename_category)
            menu.addAction("删除分类", self._delete_category)
        menu.addAction("导入材料到该分类", self._import)
        menu.exec(self.cat_tree.mapToGlobal(pos))

    def _add_category(self):
        parent_item = self.cat_tree.currentItem()
        parent_id = parent_item.data(0, Qt.UserRole) if parent_item else 0
        if parent_id is None:
            parent_id = 0
        name, ok = QInputDialog.getText(self, "新建分类", "分类名称：")
        if ok and name.strip():
            dao.add_category(name.strip(), parent_id)
            self._fill_categories()

    def _rename_category(self):
        item = self.cat_tree.currentItem()
        cat_id = item.data(0, Qt.UserRole)
        name, ok = QInputDialog.getText(self, "重命名分类", "新名称：",
                                        text=item.text(0))
        if ok and name.strip():
            dao.rename_category(cat_id, name.strip())
            self._fill_categories()

    def _delete_category(self):
        item = self.cat_tree.currentItem()
        cat_id = item.data(0, Qt.UserRole)
        if ask(self, f"删除分类「{item.text(0)}」？其中文档将移入“全部文档”。"):
            dao.delete_category(cat_id)
            self._fill_categories()

    def _doc_menu(self, pos):
        menu = QMenu(self)
        menu.addAction("打开", lambda: self._open_selected())
        menu.addAction("导出为 TXT", self._export_selected_txt)
        menu.addAction("删除", self._delete_selected)
        menu.exec(self.doc_list.mapToGlobal(pos))

    def _open_selected(self):
        items = self.doc_list.selectedItems()
        if items:
            self.open_document.emit(items[0].data(Qt.UserRole))

    def _delete_selected(self):
        items = self.doc_list.selectedItems()
        if not items:
            return
        if ask(self, f"删除选中的 {len(items)} 篇材料？"):
            for item in items:
                dao.delete_document(item.data(Qt.UserRole))
            self._reload_docs()

    def _export_selected_txt(self):
        from PySide6.QtWidgets import QFileDialog
        items = self.doc_list.selectedItems()
        if not items:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出为 TXT", str(Path.home() / "Documents" / "导出.txt"),
            "文本文件 (*.txt)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            for item in items:
                d = dao.get_document(item.data(Qt.UserRole))
                if d:
                    f.write(d.content_text + "\n\n")
        info(self, f"已导出到：\n{path}")

    def _import(self):
        cat = self.current_category()
        self.import_requested.emit(cat if cat else 0)
