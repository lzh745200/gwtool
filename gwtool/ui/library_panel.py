# -*- coding: utf-8 -*-
"""左侧资料库面板：分类树 + 标签 + 文档列表 + 全文检索 + 附件/回收站入口。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout,
                               QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QPushButton, QSplitter,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from .. import paths
from ..db import dao
from ..ui.widgets import EmptyState, ask, info, warn
from .material_dialogs import AttachmentDialog, RecycleBinDialog


def _is_subsequence(needle: list[int], haystack: list[int]) -> bool:
    """needle 是否为 haystack 的子序列（顺序保持、可不连续）。

    文档列表增量刷新用它区分三种情况：老 id 是新表的子序列（纯新增）、
    新 id 是老表的子序列（纯删除）；两者都不是才是真乱序（需要整表重建）。
    """
    it = iter(haystack)
    return all(item in it for item in needle)


class LibraryPanel(QWidget):
    """信号：open_document(int)、import_requested()、search_requested(str) 由
    搜索框回车触发（主窗口联动右侧参考面板）。"""
    open_document = Signal(int)
    import_requested = Signal(int)          # 导入到该分类
    selection_changed = Signal()
    # 检索结果单次最多展示的篇数。超出时底部文案会如实写"共 N 篇，显示前 100 篇"
    # —— 硬截断本身可以接受（列表控件不适合塞几千项），**不告诉用户被截断**不行。
    _SEARCH_LIMIT = 100
    # 输入停止后多久再检索（毫秒）。逐字符全库检索在 3000 篇规模下每次约 10ms，
    # 但连续输入会叠加成可感知的卡顿；250ms 是"人已经停下、又不会觉得迟"的折中。
    _SEARCH_DEBOUNCE_MS = 250

    def __init__(self, parent=None):
        super().__init__(parent)
        # 增量刷新缓存（v1.10.0）：分类树结构签名/节点索引、文档列表行签名。
        # 结构/行内容没变时只更新计数或跳过，避免每次增删改都全量重建控件。
        self._cat_sig = None
        self._root_item = None
        self._cat_items: dict[int, QTreeWidgetItem] = {}
        self._doc_sig = None
        # 上一次同步后的行（id → (id, 文本, 提示)），用于只给"真正变了"的行
        # 调 QListWidget.item()/setText()（3000 行逐行取 item 约 30ms，只改 1 行
        # 时没必要付这个成本）。
        self._doc_rows_by_id: dict[int, tuple[int, str, str]] | None = None
        # 当前列表的 id 顺序缓存：增量比对不必逐行回调 QListWidget.item()
        # （3000 行约 30ms）。检索会整表清空，届时置 None 强制重读。
        self._doc_ids: list[int] | None = None
        self._build_ui()
        self.reload()

    # ------------------------------------------------ UI
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.search_box = QLineEdit()
        # §6.3：圆角 6px + SURFACE 底由 QSS 按对象名施加（不写死色值）
        self.search_box.setObjectName("search_box")
        self.search_box.setPlaceholderText("全文检索（回车搜索，Esc 清空，F3 聚焦）")
        self.search_box.returnPressed.connect(self._on_search)
        # 输入防抖：只在用户**编辑**（textEdited）时排定检索；setText/Esc 等
        # 程序性改动不触发，保持既有"回车才搜"的确定性，也避免测试里挂定时器。
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(self._SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._on_search)
        self.search_box.textEdited.connect(self._schedule_search)
        # §6.3：检索框左侧放大镜图标 + Esc 一键清空
        from . import icons
        ic = icons.icon("search")
        if not ic.isNull():
            self.search_box.addAction(ic, QLineEdit.LeadingPosition)
        # 显式两行：PySide6 的 QShortcut 不接受 activated= 关键字（会误传给
        # QWidget 构造），必须拿到对象后再 connect。
        self._esc_shortcut = QShortcut(QKeySequence(Qt.Key_Escape),
                                       self.search_box)
        self._esc_shortcut.activated.connect(self._clear_search)
        layout.addWidget(self.search_box)

        self.cat_tree = QTreeWidget()
        # §6.3：分类树两列（名称 / 篇数右对齐），QSS 用对象名定位选中指示条
        self.cat_tree.setObjectName("cat_tree")
        self.cat_tree.setColumnCount(2)
        self.cat_tree.setHeaderLabels(["分类", "篇数"])
        self.cat_tree.header().setStretchLastSection(False)
        self.cat_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.cat_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.cat_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.cat_tree.customContextMenuRequested.connect(self._cat_menu)
        self.cat_tree.itemSelectionChanged.connect(self._reload_docs)

        self.doc_list = QListWidget()
        # §6.3：行高 32px 下限由 QSS 按对象名施加
        self.doc_list.setObjectName("doc_list")
        self.doc_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.doc_list.customContextMenuRequested.connect(self._doc_menu)
        self.doc_list.itemDoubleClicked.connect(
            lambda item: self.open_document.emit(item.data(Qt.UserRole)))
        self.doc_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        # §6.3：多选计数显示在列表底部工具行（_set_count_text）
        self.doc_list.itemSelectionChanged.connect(self._on_doc_selection)
        # 空状态（UI 方案 §7.6）：覆盖层挂在列表上，不占列表条目、
        # 不改任何既有布局；显隐由 _add_empty_hint 按数据情况控制。
        self._empty_no_data = EmptyState(
            "inbox", "资料库还是空的",
            hint="① 点上方「导入」把公文/Word/PDF 导入资料库\n"
                 "② 双击材料打开编辑\n"
                 "③ 用「一键汇编」生成正式公文").mount(self.doc_list)
        self._empty_no_match = EmptyState(
            "search", "没有匹配的材料",
            hint="换个关键词试试？").mount(self.doc_list)

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
        self.btn_bin = QPushButton("回收站")
        self.btn_bin.setToolTip("删除的材料先进回收站，可在此恢复或彻底删除")
        self.btn_bin.clicked.connect(self.open_recycle_bin)
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["按导入时间", "按标题", "按字数", "按最近更新"])
        self.sort_combo.setToolTip("列表排序方式")
        self.type_filter = QComboBox()
        self.type_filter.addItem("全部类型")
        self.type_filter.setToolTip("按文件类型过滤")
        self.count_label = QLabel("0 篇")
        self._count_base = "0 篇"      # §6.3 底部工具行基础文案（多选时追加已选数）
        bottom.addWidget(self.btn_import)
        bottom.addWidget(self.btn_all)
        bottom.addWidget(self.btn_bin)
        bottom.addStretch(1)
        bottom.addWidget(QLabel("排序"))
        bottom.addWidget(self.sort_combo)
        bottom.addWidget(self.type_filter)
        bottom.addWidget(self.count_label)
        layout.addLayout(bottom)
        self.sort_combo.currentTextChanged.connect(lambda _t: self._reload_docs())
        self.type_filter.currentTextChanged.connect(lambda _t: self._reload_docs())

    # ------------------------------------------------ 数据
    def reload(self):
        self._fill_categories()
        self._reload_docs()

    def current_category(self) -> int | None:
        item = self.cat_tree.currentItem()
        if item is None:
            return None
        return item.data(0, Qt.UserRole)

    def focus_search(self):
        """F3 兑现：聚焦全文检索框（主窗口 QShortcut 调用）。"""
        self.search_box.setFocus(Qt.ShortcutFocusReason)
        self.search_box.selectAll()

    def selected_doc_ids(self) -> list[int]:
        return [item.data(Qt.UserRole) for item in self.doc_list.selectedItems()]

    # ------------------------------------------------ 内部
    def _fill_categories(self):
        """刷新分类树：结构未变只更新计数，结构变化才重建并恢复展开/选中。

        v1.9.0 的实现在每次增删改/导入后都 ``clear()`` 重建整棵树并在末尾
        ``setCurrentItem(root)`` —— 3000 篇规模下实测 318ms，且用户展开的
        分支被收起、选中项被打回「全部文档」。现在：
          · 分类的 (id, 名称, 父级, 排序) 签名不变 → 只查两次计数并写两列文本；
          · 签名变化（新建/重命名/删除分类）→ 重建树，但恢复展开状态与当前选中。
        """
        cats = dao.list_categories()
        sig = tuple((c.id, c.name, c.parent_id, getattr(c, "sort", 0))
                    for c in cats)
        if (sig == self._cat_sig and self._root_item is not None
                and self.cat_tree.topLevelItemCount() > 0):
            self._apply_category_counts(cats)
            return
        self._rebuild_category_tree(cats, sig)

    def _category_counts(self, cats) -> tuple[int, dict[int, int]]:
        """一次查询取各分类篇数，自下而上累加；返回 (在库总数, 各节点计数)。"""
        try:
            counts = dao.count_documents_by_category()
        except Exception:
            counts = {}
        by_parent: dict[int, list] = {}
        for c in cats:
            by_parent.setdefault(c.parent_id, []).append(c)
        own: dict[int, int] = {c.id: counts.get(c.id, 0) for c in cats}
        memo: dict[int, int] = {}

        def total(cat_id: int) -> int:
            if cat_id in memo:
                return memo[cat_id]
            value = own.get(cat_id, 0) + sum(
                total(ch.id) for ch in by_parent.get(cat_id, []))
            memo[cat_id] = value
            return value

        # 「全部文档」必须是**在库总篇数**。count_documents_by_category() 按分类
        # 聚合且只统计 category_id>0，未分类文档不在返回里 —— 用 sum(own) 会让
        # 根节点少掉所有未分类材料（实测 3000 篇里 1000 篇未分类时显示 2000）。
        try:
            root_total = dao.count_documents()
        except Exception:
            root_total = sum(own.values())
        return root_total, {c.id: total(c.id) for c in cats}

    def _apply_category_counts(self, cats) -> None:
        """只改两列计数，不重建节点（保留展开状态与选中，O(分类数)）。"""
        if self._root_item is None:
            return
        root_total, totals = self._category_counts(cats)
        self._root_item.setText(1, str(root_total))
        self._root_item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        for c in cats:
            node = self._cat_items.get(c.id)
            if node is None:
                continue
            node.setText(1, str(totals.get(c.id, 0)))
            node.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)

    def _expanded_category_ids(self) -> set[int]:
        return {cid for cid, node in self._cat_items.items() if node.isExpanded()}

    def _rebuild_category_tree(self, cats, sig) -> None:
        """结构变化时重建分类树，并恢复用户展开的分支与当前选中分类。"""
        expanded = self._expanded_category_ids()
        current_id = self.current_category()
        by_parent: dict[int, list] = {}
        for c in cats:
            by_parent.setdefault(c.parent_id, []).append(c)

        # 重建期间屏蔽信号：clear() 会先把 currentItem 变空，不禁用会触发
        # 一次"无选中"的 _reload_docs（3000 篇下约 186ms 的无谓全量刷新）。
        self.cat_tree.blockSignals(True)
        try:
            self.cat_tree.clear()
            root = QTreeWidgetItem(["全部文档", ""])
            root.setData(0, Qt.UserRole, None)
            self.cat_tree.addTopLevelItem(root)
            self._root_item = root
            self._cat_items: dict[int, QTreeWidgetItem] = {}

            def add_children(parent_item, parent_id):
                for c in by_parent.get(parent_id, []):
                    node = QTreeWidgetItem([c.name, ""])
                    node.setData(0, Qt.UserRole, c.id)
                    parent_item.addChild(node)
                    self._cat_items[c.id] = node
                    add_children(node, c.id)

            add_children(root, 0)
            self._apply_category_counts(cats)
            root.setExpanded(True)
            for cid, node in self._cat_items.items():
                if cid in expanded:
                    node.setExpanded(True)
            self._cat_sig = sig
        finally:
            self.cat_tree.blockSignals(False)
        # 恢复选中（删除分类时回落到根节点）。这里只触发一次 _reload_docs；
        # 文档列表若没变会在 _sync_doc_list 里直接返回。
        target = self._cat_items.get(current_id) if current_id is not None else root
        self.cat_tree.setCurrentItem(target if target is not None else root)

    def _reload_docs(self):
        cat = self.current_category()
        docs = dao.list_documents(cat)
        # 类型过滤下拉随数据更新（保留当前选择；blockSignals 防回环）
        types = sorted({(d.file_type or "文本") for d in docs})
        cur_type = self.type_filter.currentText()
        self.type_filter.blockSignals(True)
        try:
            self.type_filter.clear()
            self.type_filter.addItem("全部类型")
            for t in types:
                self.type_filter.addItem(t)
            if cur_type in types:
                self.type_filter.setCurrentText(cur_type)
        finally:
            self.type_filter.blockSignals(False)
        if self.type_filter.currentText() != "全部类型":
            docs = [d for d in docs
                    if (d.file_type or "文本") == self.type_filter.currentText()]
        key = self.sort_combo.currentText()
        if key == "按标题":
            docs.sort(key=lambda d: d.title)
        elif key == "按字数":
            docs.sort(key=lambda d: -d.word_count)
        elif key == "按最近更新":
            docs.sort(key=lambda d: (d.updated_time or d.import_time or ""),
                      reverse=True)
        self._sync_doc_list(self._build_doc_rows(docs))

    def _build_doc_rows(self, docs) -> list[tuple[int, str, str]]:
        """把文档对象转成列表行 (id, 主文本, 悬停提示)，便于增量比对。"""
        try:
            att_counts = dao.attachment_counts([d.id for d in docs])
        except Exception:
            att_counts = {}
        rows: list[tuple[int, str, str]] = []
        for d in docs:
            # §6.3：首行标题（列表自动省略），副行放字数/时间等次要信息。
            # "附件N"等既有信息一并保留在副行（外部依赖其文本，如附件测试）。
            meta = f"{d.file_type or '文本'} · {d.word_count}字"
            if att_counts.get(d.id):
                meta += f" · 附件{att_counts[d.id]}"
            when = (getattr(d, "updated_time", "") or "")[:16]
            if when:
                meta += f" · {when}"
            if d.tags:
                meta += "  " + d.tags.replace(",", " #").replace("，", " #")
            text = f"{d.title}\n    {meta}"
            tip = f"{d.title}\n{meta}\n标签：{d.tags or '无'}"
            rows.append((d.id, text, tip))
        return rows

    @staticmethod
    def _make_doc_item(row: tuple[int, str, str]) -> QListWidgetItem:
        doc_id, text, tip = row
        item = QListWidgetItem(text)
        item.setData(Qt.UserRole, doc_id)
        item.setToolTip(tip)
        return item

    def _sync_doc_list(self, rows: list[tuple[int, str, str]]) -> None:
        """把文档列表同步到 rows：完全相同跳过、同序改文本、增删只动相关行。

        全量重建 3000 项 QListWidgetItem 实测约 140ms；标签/重命名/附件变化
        只需要改文本，导入/删除只需插入/移除受影响行。只有排序、类型筛选或
        跨分类移动这类真正改变顺序的操作才走整表重建（并恢复选中与滚动）。
        """
        sig = tuple(rows)
        if sig == self._doc_sig:
            return
        if (self._doc_ids is not None
                and len(self._doc_ids) == self.doc_list.count()):
            current_ids = list(self._doc_ids)
        else:
            current_ids = [self.doc_list.item(i).data(Qt.UserRole)
                           for i in range(self.doc_list.count())]
        new_ids = [r[0] for r in rows]
        previous = self._doc_rows_by_id
        if current_ids == new_ids:
            self._update_doc_item_texts(rows, previous)
        elif _is_subsequence(current_ids, new_ids):
            # 纯新增（导入新文档）：老行顺序不变，只把新行插到对应位置
            self._insert_doc_rows(rows, current_ids)
            self._update_doc_item_texts(rows, previous)
        elif _is_subsequence(new_ids, current_ids):
            # 纯删除：只移除消失的行
            self._remove_doc_rows(current_ids, set(new_ids))
            self._update_doc_item_texts(rows, previous)
        else:
            # 排序/筛选/跨分类移动：整表重建（新行文本已随构造写入，无需再改）
            self._rebuild_doc_list(rows)
        self._doc_sig = sig
        self._doc_rows_by_id = {row[0]: row for row in rows}
        self._doc_ids = new_ids
        if rows:
            self._empty_no_data.set_visible(False)
            self._empty_no_match.set_visible(False)
        else:
            self._add_empty_hint()
        self._set_count_text(f"{len(rows)} 篇")

    def _update_doc_item_texts(self, rows: list[tuple[int, str, str]],
                               previous: dict[int, tuple] | None = None) -> None:
        """只更新与上一次同步结果不同的行（未变行不碰 QListWidget）。"""
        for index, row in enumerate(rows):
            if previous is not None and previous.get(row[0]) == row:
                continue
            item = self.doc_list.item(index)
            if item is None:
                continue
            if item.text() != row[1]:
                item.setText(row[1])
            if item.toolTip() != row[2]:
                item.setToolTip(row[2])

    def _insert_doc_rows(self, rows: list[tuple[int, str, str]],
                         current_ids: list[int]) -> None:
        """rows 是 current_ids 的超序列：只把新行插到对应位置，老行原样保留。"""
        pos = 0
        for index, row in enumerate(rows):
            if pos < len(current_ids) and row[0] == current_ids[pos]:
                pos += 1
                continue
            self.doc_list.insertItem(index, self._make_doc_item(row))

    def _remove_doc_rows(self, current_ids: list[int],
                         keep_ids: set[int]) -> None:
        """new_ids 是 current_ids 的子序列：从后往前摘掉消失的行。"""
        for index in range(len(current_ids) - 1, -1, -1):
            if current_ids[index] not in keep_ids:
                self.doc_list.takeItem(index)

    def _rebuild_doc_list(self, rows: list[tuple[int, str, str]]) -> None:
        selected = set(self.selected_doc_ids())
        current = self.doc_list.currentItem()
        current_id = current.data(Qt.UserRole) if current is not None else None
        scroll = self.doc_list.verticalScrollBar().value()
        self.doc_list.blockSignals(True)
        self.doc_list.setUpdatesEnabled(False)
        try:
            self.doc_list.clear()
            for row in rows:
                self.doc_list.addItem(self._make_doc_item(row))
        finally:
            self.doc_list.setUpdatesEnabled(True)
            self.doc_list.blockSignals(False)
        if selected:
            for index in range(self.doc_list.count()):
                item = self.doc_list.item(index)
                if item is not None and item.data(Qt.UserRole) in selected:
                    item.setSelected(True)
        if current_id is not None:
            for index in range(self.doc_list.count()):
                item = self.doc_list.item(index)
                if item is not None and item.data(Qt.UserRole) == current_id:
                    self.doc_list.setCurrentItem(item)
                    break
        self.doc_list.verticalScrollBar().setValue(scroll)

    def select_document(self, doc_id: int) -> bool:
        """在材料列表里选中并滚动到指定文档；找不到返回 False。

        供「发文登记台账 → 打开原文」跳转使用：编辑器已经加载了内容，
        若资料库列表还停在别处，用户看到的是"编辑区换了、左栏没动"的错位感。
        找不到时不报错也不清空选择 —— 目标可能被分类/关键词筛选挡住了，
        此时保持现状比把用户的选择清掉更好。
        """
        for i in range(self.doc_list.count()):
            item = self.doc_list.item(i)
            if item is None or item.data(Qt.UserRole) != int(doc_id):
                continue
            self.doc_list.setCurrentItem(item)
            self.doc_list.scrollToItem(item)
            return True
        return False

    def _add_empty_hint(self, searching: bool = False):
        """空列表也要告诉用户下一步做什么。

        空库是**每个新用户的第一屏**：老实现只把 count_label 写成"0 篇"，
        列表区一片空白 —— 用户分不清是"自己没导入"、"被筛掉了"还是"程序坏了"。
        两种空给两种话：库确实是空的（引导导入）／检索没命中（提示换关键词）。

        v1.7 起改用统一空状态组件 ``EmptyState``（覆盖层，不占列表条目），
        文案与此前逐字一致；组件无行动按钮时对鼠标透明，右键菜单等
        既有交互不受影响。

        注：**不存在"被类型筛选筛空"这一种**——类型下拉的候选项就是按当前
        分类下的材料重建的（`_reload_docs` 内），选中的类型若已无对应材料，
        下拉会自动落回「全部类型」。为此加分支只会得到一段永不执行的代码。
        """
        self._empty_no_data.set_visible(not searching)
        self._empty_no_match.set_visible(bool(searching))

    def _show_all(self):
        self.cat_tree.setCurrentItem(self.cat_tree.topLevelItem(0))

    def _clear_search(self) -> None:
        """Esc 一键清空检索条件并回到完整列表（§6.3）。"""
        if not self.search_box.text():
            return
        self.search_box.clear()
        self._on_search()

    def _set_count_text(self, base: str) -> None:
        """底部工具行计数（§6.3）：基础文案 + 多选时追加「已选 N 篇」。

        基础文案在文档列表与检索结果两种模式下不同（"N 篇" / "检索到 N 处"），
        故先记下来，选中项变化时按同一基数重算 —— 避免多选计数把另一模式的
        计数覆盖成不相干的值。
        """
        self._count_base = base
        picked = len(self.doc_list.selectedItems())
        self.count_label.setText(f"{base}　已选 {picked} 篇" if picked else base)

    def _on_doc_selection(self) -> None:
        """列表选中项变化 → 刷新底部工具行的多选计数（§6.3）。"""
        self._set_count_text(getattr(self, "_count_base", "0 篇"))

    def _schedule_search(self, _text: str = "") -> None:
        """输入防抖：用户停止输入 250ms 后才真正检索（回车仍立即检索）。"""
        self._search_timer.start()

    def _on_search(self):
        self._search_timer.stop()
        kw = self.search_box.text().strip()
        self.doc_list.clear()
        # 检索结果与分类列表是两种内容：清掉行签名，回到分类列表时强制重算。
        self._doc_sig = None
        self._doc_rows_by_id = None
        self._doc_ids = None
        if not kw:
            self._reload_docs()
            return
        # 取"前 100 条 + 命中总篇数"：只回传 100 条时界面只能写"检索到 100 处"，
        # 命中 300 篇的用户会把截断误读成"库里只有 100 篇"。
        page = dao.search_documents_page(kw, limit=self._SEARCH_LIMIT)
        results = page.results
        for r in results:
            item = QListWidgetItem(f"{r.title}\n    {r.snippet}")
            item.setData(Qt.UserRole, r.ref_id)
            self.doc_list.addItem(item)
        if not results:
            # 检索无命中与"库是空的"是两回事，提示要能区分开
            self._add_empty_hint(searching=True)
        if page.has_more:
            self._set_count_text(
                f"共 {page.total} 篇，显示前 {len(results)} 篇")
        else:
            self._set_count_text(f"共 {page.total} 篇")

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
        # QMenu(self) 的父对象是面板，不显式销毁就会一直挂在父对象下：
        # 实测每次右键 +1 个存活 QMenu（50 次右键 50 个全在），长会话会持续涨内存。
        menu.deleteLater()

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
        if len(self.doc_list.selectedItems()) == 1:
            menu.addAction("重命名", self._rename_selected)
            menu.addAction("移动到分类…", self._move_selected)
            menu.addAction("编辑标签…", self._tag_selected)
            menu.addAction("分类建议…", self._suggest_category)
            menu.addAction("附件…", self._open_attachments)
            menu.addSeparator()
        menu.addAction("批量添加标签…", self._bulk_add_tags)
        menu.addAction("批量移除标签…", self._bulk_remove_tags)
        menu.addSeparator()
        menu.addAction("导出为 TXT", self._export_selected_txt)
        menu.addAction("回收站…", self.open_recycle_bin)
        menu.addAction("删除（移入回收站）", self._delete_selected)
        menu.exec(self.doc_list.mapToGlobal(pos))
        menu.deleteLater()      # 同上：不销毁会随每次右键累积

    def _bulk_add_tags(self):
        ids = self.selected_doc_ids()
        if not ids:
            warn(self, "请先选中要加标签的文档（可按住 Ctrl/Shift 多选）。")
            return
        text, ok = QInputDialog.getText(
            self, "批量添加标签",
            f"为选中的 {len(ids)} 篇文档添加标签（多个用逗号分隔）：")
        if not ok:
            return
        tags = tuple(t.strip() for t in text.replace("，", ",").split(",") if t.strip())
        if not tags:
            warn(self, "未填写有效标签。")
            return
        n = dao.bulk_update_tags(ids, add=tags)
        self._reload_docs()
        info(self, f"已为 {n} 篇文档添加标签：{'、'.join(tags)}")

    def _bulk_remove_tags(self):
        ids = self.selected_doc_ids()
        if not ids:
            warn(self, "请先选中要移除标签的文档（可按住 Ctrl/Shift 多选）。")
            return
        text, ok = QInputDialog.getText(
            self, "批量移除标签",
            f"从选中的 {len(ids)} 篇文档移除标签（多个用逗号分隔）：")
        if not ok:
            return
        tags = tuple(t.strip() for t in text.replace("，", ",").split(",") if t.strip())
        if not tags:
            warn(self, "未填写有效标签。")
            return
        n = dao.bulk_update_tags(ids, remove=tags)
        self._reload_docs()
        info(self, f"已从 {n} 篇文档移除标签：{'、'.join(tags)}")

    def _suggest_category(self):
        """按内容给出分类建议——只提示，不自动改，归档口径由人定。"""
        did = self._single_doc()
        if not did:
            return
        from ..core import classify
        doc = dao.get_document(did)
        suggestions = classify.suggest_for_document(did, top_n=3)
        if not suggestions:
            info(self, "暂无可用建议：需要已有分类下存在文档，"
                       "且本文与它们的用词有交集。")
            return
        top_score = suggestions[0][2] or 1.0
        names = [f"{name}（匹配度 {score / top_score:.0%}）"
                 for _id, name, score in suggestions]
        lines = [f"「{doc.title}」当前分类："
                 f"{next((c.name for c in dao.list_categories() if c.id == doc.category_id), '未分类')}",
                 "", "按内容匹配度推荐："]
        lines += [f"  {i}. {n}" for i, n in enumerate(names, 1)]
        lines += ["", "是否移动到推荐的首位分类？"]
        if ask(self, "\n".join(lines)):
            dao.update_document_meta(did, category_id=suggestions[0][0])
            self._reload_docs()

    def _single_doc(self):
        items = self.doc_list.selectedItems()
        return items[0].data(Qt.UserRole) if len(items) == 1 else None

    def _rename_selected(self):
        did = self._single_doc()
        if not did:
            return
        d = dao.get_document(did)
        if not d:
            return
        name, ok = QInputDialog.getText(self, "重命名文档", "新标题：", text=d.title)
        if ok and name.strip():
            dao.update_document_meta(did, title=name.strip())
            self._reload_docs()

    def _move_selected(self):
        did = self._single_doc()
        if not did:
            return
        cats = dao.list_categories()
        names = [c.name for c in cats] + ["全部文档（未分类）"]
        cat_ids = [c.id for c in cats] + [0]
        name, ok = QInputDialog.getItem(self, "移动到分类", "选择目标分类：",
                                        names, 0, False)
        if ok:
            dao.update_document_meta(did, category_id=cat_ids[names.index(name)])
            self._reload_docs()

    def _tag_selected(self):
        did = self._single_doc()
        if not did:
            return
        d = dao.get_document(did)
        if not d:
            return
        text, ok = QInputDialog.getText(self, "编辑标签",
                                        "标签（逗号分隔，可留空清除）：",
                                        text=d.tags or "")
        if ok:
            dao.update_document_meta(did, tags=text.strip())
            self._reload_docs()

    def _open_selected(self):
        items = self.doc_list.selectedItems()
        if items:
            self.open_document.emit(items[0].data(Qt.UserRole))

    def _open_attachments(self):
        """附件管理：文件复制进数据目录，随备份与便携模式一起走。"""
        did = self._single_doc()
        if not did:
            warn(self, "请先选中一篇材料（附件挂在单篇材料下）。")
            return
        d = dao.get_document(did)
        try:
            dlg = AttachmentDialog(did, self, doc_title=d.title if d else "")
            dlg.exec()
        except Exception as exc:
            warn(self, f"打开附件管理失败：{exc}")
            return
        self._reload_docs()          # 列表上的「附件N」标记要跟着变

    def open_recycle_bin(self):
        """回收站：查看、恢复、彻底删除已删除的材料。"""
        try:
            dlg = RecycleBinDialog(self)
            dlg.exec()
        except Exception as exc:
            warn(self, f"打开回收站失败：{exc}")
            return
        self.reload()                # 可能恢复了材料，分类计数一并刷新

    def _delete_selected(self):
        """把选中材料移入回收站（后台线程执行）。

        每篇删除都要更新 FTS 索引、写回收站记录；选几十篇时逐篇同步执行
        会把面板冻住。放后台线程并在完成后刷新列表 —— 删除期间面板仍可
        滚动（用户能确认自己选的是不是都要删）。
        """
        from .workers import FnWorker

        items = self.doc_list.selectedItems()
        if not items:
            return
        if not ask(self, f"把选中的 {len(items)} 篇材料移入回收站？\n"
                         "移入后不再出现在列表、计数与全文检索里，"
                         "可在「回收站」中恢复；彻底删除才会真删数据与附件。"):
            return
        targets = [(item.data(Qt.UserRole), item.text()) for item in items]
        worker = getattr(self, "_delete_worker", None)
        if worker is not None and worker.isRunning():
            warn(self, "上一次删除仍在进行，请稍候。")
            return

        def work():
            failed: list[str] = []
            for did, label in targets:
                try:
                    dao.delete_document(did)
                except Exception as exc:
                    failed.append(f"{label}：{exc}")
            return failed

        w = FnWorker(work, parent=self)
        w.ok.connect(self._delete_done)
        w.failed.connect(lambda msg: warn(self, f"删除失败：{msg}"))
        self._delete_worker = w
        w.start()

    def _delete_done(self, failed: list):
        self._reload_docs()
        if failed:
            warn(self, "以下材料未能移入回收站：\n" + "\n".join(failed[:5]))

    def _export_selected_txt(self):
        from PySide6.QtWidgets import QFileDialog
        items = self.doc_list.selectedItems()
        if not items:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出为 TXT", str(paths.documents_dir() / "导出.txt"),
            "文本文件 (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                for item in items:
                    d = dao.get_document(item.data(Qt.UserRole))
                    if d:
                        f.write(d.content_text + "\n\n")
        except OSError as exc:
            warn(self, f"导出失败：{exc}")
            return
        info(self, f"已导出到：\n{path}")

    def _import(self):
        cat = self.current_category()
        self.import_requested.emit(cat if cat else 0)
