# -*- coding: utf-8 -*-
"""资料库面板刷新性能（v1.9.0 → v1.10.0）回归测试。

审计实测（3000 篇）：分类树刷新 229ms、面板整刷 271ms。根因有两层：

1. ``_fill_categories`` 每次都 ``clear()`` 重建整棵树，且 ``clear()`` /
   ``setCurrentItem(root)`` 会各自触发一次**全量列表重建**；
2. ``_reload_docs`` 每次都销毁并重建 3000 个 ``QListWidgetItem``（约 140ms）。

本文件守三件事：

* 3000 篇场景下给出"改前（v1.9.0 全量重建，复刻）/改后（增量刷新）"耗时数字；
* 刷新不再丢用户展开的分支、选中分类与选中材料；
* 检索框输入防抖 250ms，程序性 ``setText`` 不触发（保持既有"回车才搜"语义）。

改前基线由本文件内的 ``t7b_legacy_*`` 复刻 v1.9.0 的控件工作量（分类树
clear 重建 + 两次全量列表重建），不修改生产代码路径。
"""
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTreeWidgetItem

from gwtool.db import connection as dbconn
from gwtool.db import dao

BIG_N = 3000
_STAMP = "2026-10-01 10:00:00"


# ---------------------------------------------------------------- 造数
def _add_doc(title: str, text: str, cat: int = 0) -> int:
    return dao.add_document(dao.Document(title=title, content_text=text,
                                         category_id=cat))


def _seed_big_library(n: int = BIG_N):
    """直接批量写 documents 表：面板刷新只读列，无需 FTS/段落派生索引。

    3000 篇若逐篇走 ``dao.add_document``（每篇提交 + FTS + 段落索引）需
    16s+，会让性能用例慢到没人愿意跑；这里一条事务写入同一批行，面板看到的
    数据形状（标题/字数/时间/分类）与真实导入一致。
    """
    tops = [dao.add_category(f"分类{i:02d}") for i in range(30)]
    subs = [dao.add_category(f"子分类{i:02d}", tops[i]) for i in range(30)]
    conn = dbconn.get_conn()
    with conn:
        for i in range(n):
            if i < 900:
                cat = subs[i % 30]
            elif i < 2700:
                cat = tops[i % 30]
            else:
                cat = 0
            conn.execute(
                "INSERT INTO documents(title,content_text,blocks_json,file_path,"
                "file_type,tags,category_id,text_hash,word_count,import_time,"
                "updated_time,simhash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"材料{i:04d}", f"第{i}份材料正文，安全生产责任落实。",
                 "[]", "", "文本", "", cat, f"t7bhash{i}", 20,
                 _STAMP, _STAMP, 0))
    return tops, subs


@pytest.fixture()
def big_panel(tmp_db, qapp):
    _seed_big_library()
    from gwtool.ui.library_panel import LibraryPanel
    panel = LibraryPanel()
    yield panel
    panel.close()
    panel.deleteLater()


def _list_ids(panel) -> list[int]:
    return [panel.doc_list.item(i).data(Qt.UserRole)
            for i in range(panel.doc_list.count())]


def _time_ms(fn, repeat: int = 3) -> tuple[float, object]:
    best = None
    result = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        result = fn()
        elapsed = (time.perf_counter() - t0) * 1000
        best = elapsed if best is None else min(best, elapsed)
    return best, result


# ---------------------------------------------------------------- 改前基线
def t7b_legacy_tree_rebuild(panel) -> None:
    """复刻 v1.9.0 ``_fill_categories`` 的树重建（屏蔽信号以隔离列表刷新）。"""
    tree = panel.cat_tree
    tree.blockSignals(True)
    try:
        tree.clear()
        root = QTreeWidgetItem(["全部文档", ""])
        root.setData(0, Qt.UserRole, None)
        tree.addTopLevelItem(root)
        cats = dao.list_categories()
        by_parent: dict[int, list] = {}
        for c in cats:
            by_parent.setdefault(c.parent_id, []).append(c)
        nodes: dict[int, QTreeWidgetItem] = {}
        counts = dao.count_documents_by_category()
        own = {c.id: counts.get(c.id, 0) for c in cats}

        def add_children(parent_item, parent_id):
            for c in by_parent.get(parent_id, []):
                node = QTreeWidgetItem([c.name, ""])
                node.setData(0, Qt.UserRole, c.id)
                parent_item.addChild(node)
                nodes[c.id] = node
                add_children(node, c.id)

        add_children(root, 0)

        def total(cat_id: int) -> int:
            return own.get(cat_id, 0) + sum(
                total(ch.id) for ch in by_parent.get(cat_id, []))

        root.setText(1, str(dao.count_documents()))
        for c in cats:
            if c.id in nodes:
                nodes[c.id].setText(1, str(total(c.id)))
        root.setExpanded(True)
        tree.setCurrentItem(root)
    finally:
        tree.blockSignals(False)


def t7b_legacy_list_rebuild(panel) -> None:
    """复刻 v1.9.0 ``_reload_docs`` 的列表全量重建（3000 项 QListWidgetItem）。"""
    docs = dao.list_documents(None)
    try:
        att_counts = dao.attachment_counts([d.id for d in docs])
    except Exception:
        att_counts = {}
    panel.doc_list.clear()
    for d in docs:
        meta = f"{d.file_type or '文本'} · {d.word_count}字"
        if att_counts.get(d.id):
            meta += f" · 附件{att_counts[d.id]}"
        when = (getattr(d, "updated_time", "") or "")[:16]
        if when:
            meta += f" · {when}"
        if d.tags:
            meta += "  " + d.tags.replace(",", " #").replace("，", " #")
        from PySide6.QtWidgets import QListWidgetItem
        item = QListWidgetItem(f"{d.title}\n    {meta}")
        item.setData(Qt.UserRole, d.id)
        item.setToolTip(f"{d.title}\n{meta}\n标签：{d.tags or '无'}")
        panel.doc_list.addItem(item)


# ---------------------------------------------------------------- 改前/改后
def test_3000_docs_refresh_before_after(big_panel):
    """3000 篇场景：分类计数/面板整刷/元数据增量 的改前改后耗时对比。"""
    panel = big_panel
    root = panel.cat_tree.topLevelItem(0)
    panel.cat_tree.setCurrentItem(root)
    panel.doc_list.setCurrentRow(0)
    assert panel.doc_list.count() == BIG_N

    new_counts, _ = _time_ms(panel._fill_categories)      # 结构未变 → 只改计数
    new_reload, _ = _time_ms(panel.reload)                # 数据未变 → 跳过列表重建

    # 元数据变化（改标题）：同 id 同序 → 只改对应行文本
    docs = dao.list_documents(None)
    rename_target = docs[0].id
    seq = [0]

    def rename_and_reload():
        seq[0] += 1
        dao.update_document_meta(rename_target, title=f"改名后的材料{seq[0]}")
        panel.reload()

    new_rename, _ = _time_ms(rename_and_reload, repeat=2)

    # 导入 20 篇：纯新增 → 只插入 20 行
    imported = [0]

    def import_then_reload():
        imported[0] += 1
        _add_doc(f"新导入{imported[0]}",
                 f"新导入正文内容{imported[0]}，各不相同。")
        panel.reload()

    new_import, _ = _time_ms(import_then_reload, repeat=2)

    # 改前基线：v1.9.0 树重建 + 两次全量列表重建（clear 与 setCurrentItem 各触发
    # 一次，reload() 末尾再显式重建一次；这里取代表性的两次）
    legacy_tree, _ = _time_ms(lambda: t7b_legacy_tree_rebuild(panel), repeat=2)
    legacy_list, _ = _time_ms(lambda: t7b_legacy_list_rebuild(panel), repeat=2)
    legacy_total = legacy_tree + legacy_list * 2

    print("\n[t7b] 3000 篇资料库刷新（越小越好）")
    print(f"[t7b] 改前 v1.9.0 复刻：树重建 {legacy_tree:.1f}ms + "
          f"列表重建 {legacy_list:.1f}ms×2 = {legacy_total:.1f}ms")
    print(f"[t7b] 改后 分类计数刷新 {new_counts:.2f}ms / "
          f"面板整刷 {new_reload:.1f}ms / 改名增量 {new_rename:.2f}ms / "
          f"导入20篇 {new_import:.1f}ms")

    assert legacy_total > 0
    assert new_counts < legacy_total * 0.10, "分类计数刷新应远快于全量重建"
    assert new_reload < legacy_total * 0.50, "面板整刷应显著快于全量重建"
    assert new_rename < legacy_list * 0.50, "改名应走增量，而不是重建整张列表"
    assert new_import < legacy_list * 0.50, "导入少量新材料不应重建整张列表"


# ---------------------------------------------------------------- 状态保持
def test_refresh_keeps_expanded_tree_and_selection(big_panel):
    """分类计数刷新/结构重建都不得收起展开分支、不得丢选中分类与材料。"""
    panel = big_panel
    cats = dao.list_categories()
    top = next(c for c in cats if c.parent_id == 0 and c.id in panel._cat_items)
    node = panel._cat_items[top.id]
    node.setExpanded(True)
    panel.cat_tree.setCurrentItem(node)
    panel.doc_list.setCurrentRow(0)
    picked_doc = panel.doc_list.currentItem().data(Qt.UserRole)
    docs_before = panel.doc_list.count()
    assert picked_doc in panel.selected_doc_ids()

    # ① 结构未变：只更新计数
    panel._fill_categories()
    assert panel.current_category() == top.id
    assert panel._cat_items[top.id].isExpanded()
    assert panel.doc_list.count() == docs_before
    assert picked_doc in panel.selected_doc_ids()

    # ② 结构变化（新建分类）：重建树但恢复展开/选中
    new_cat = dao.add_category("测试新增分类")
    panel._fill_categories()
    assert panel.current_category() == top.id
    assert panel._cat_items[top.id].isExpanded()
    assert picked_doc in panel.selected_doc_ids()
    assert new_cat in panel._cat_items

    # ②b 删除另一个分类：当前选中与展开仍保持
    dao.delete_category(new_cat)
    panel._fill_categories()
    assert panel.current_category() == top.id
    assert panel._cat_items[top.id].isExpanded()
    assert picked_doc in panel.selected_doc_ids()

    # ③ 排序变化：整表重建也要保住选中项
    panel.sort_combo.setCurrentText("按标题")
    assert picked_doc in panel.selected_doc_ids()
    titles = [panel.doc_list.item(i).text().split("\n")[0]
              for i in range(min(5, panel.doc_list.count()))]
    assert titles == sorted(titles), "按标题排序未生效"

    # ④ 改名后 reload：同序增量刷新并保住选中
    dao.update_document_meta(picked_doc, title="选中材料改名后")
    panel.reload()
    assert picked_doc in panel.selected_doc_ids()
    texts = {panel.doc_list.item(i).data(Qt.UserRole):
             panel.doc_list.item(i).text()
             for i in range(panel.doc_list.count())}
    assert texts[picked_doc].startswith("选中材料改名后")

    # ⑤ 新增/删除当前分类的材料后 reload：选中项仍在
    other = _add_doc("另一篇新材料", "另一篇材料正文，内容唯一。", cat=top.id)
    panel.reload()
    assert picked_doc in panel.selected_doc_ids()
    dao.delete_document(other)
    panel.reload()
    assert picked_doc in panel.selected_doc_ids()


# ---------------------------------------------------------------- 列表增量正确性
def test_incremental_list_matches_dao_order(tmp_db, qapp):
    """导入/删除后的增量刷新结果必须与 DAO 顺序一致（不是"看起来少一行"）。"""
    from gwtool.ui.library_panel import LibraryPanel

    for i in range(5):
        _add_doc(f"文档{i}", f"正文内容{i}，各不相同。")
    panel = LibraryPanel()
    try:
        assert _list_ids(panel) == [d.id for d in dao.list_documents(None)]
        new_id = _add_doc("新导入文档", "新导入正文，内容唯一。")
        panel.reload()
        assert _list_ids(panel) == [d.id for d in dao.list_documents(None)]
        assert panel.doc_list.item(0).data(Qt.UserRole) == new_id, \
            "按导入时间倒序，新材料应在首行"
        oldest = _list_ids(panel)[-1]
        dao.delete_document(oldest)
        panel.reload()
        assert _list_ids(panel) == [d.id for d in dao.list_documents(None)]
        assert oldest not in _list_ids(panel)

        # 移动到其他分类：当前分类列表应同步移除该行（跨分类移动走删除增量）
        cat_a = dao.add_category("源分类A")
        cat_b = dao.add_category("目标分类B")
        moving = _add_doc("待移动材料", "待移动材料的正文内容。", cat=cat_a)
        panel.reload()
        panel.cat_tree.setCurrentItem(panel._cat_items[cat_a])
        assert _list_ids(panel) == [moving]
        dao.update_document_meta(moving, category_id=cat_b)
        panel.reload()
        assert _list_ids(panel) == [d.id for d in dao.list_documents(cat_a)]
        assert moving not in _list_ids(panel)
    finally:
        panel.close()
        panel.deleteLater()


def test_reload_hides_empty_state_after_import(tmp_db, qapp):
    """空库引导在材料出现后必须收起（否则列表上会一直压着"资料库还是空的"）。"""
    from gwtool.ui.library_panel import LibraryPanel

    panel = LibraryPanel()
    try:
        assert panel.doc_list.count() == 0
        assert panel._empty_no_data.isVisibleTo(panel.doc_list)
        _add_doc("第一篇材料", "第一篇材料的正文内容。")
        panel.reload()
        assert panel.doc_list.count() == 1
        assert not panel._empty_no_data.isVisibleTo(panel.doc_list), \
            "有材料后仍显示空库引导"
    finally:
        panel.close()
        panel.deleteLater()


# ---------------------------------------------------------------- 检索防抖
def test_search_debounce_waits_for_typing_pause(tmp_db, qapp):
    from gwtool.ui.library_panel import LibraryPanel

    _add_doc("安全生产甲", "安全生产责任落实。")
    _add_doc("安全生产乙", "安全生产隐患排查。")
    panel = LibraryPanel()
    try:
        assert panel._SEARCH_DEBOUNCE_MS == 250
        panel.search_box.setText("安全生产")
        assert not panel._search_timer.isActive(), \
            "程序性 setText 不应排定检索（保持回车才搜的既有语义）"

        panel.search_box.textEdited.emit("安全生产")
        assert panel._search_timer.isActive(), "用户输入后应进入防抖等待"
        QTest.qWait(panel._SEARCH_DEBOUNCE_MS + 150)
        assert not panel._search_timer.isActive(), "防抖计时结束后应已执行检索"
        assert panel.doc_list.count() == 2
        assert "共 2 篇" in panel.count_label.text(), panel.count_label.text()
    finally:
        panel.close()
        panel.deleteLater()


def test_search_debounce_resets_on_each_keystroke(tmp_db, qapp):
    from gwtool.ui.library_panel import LibraryPanel

    _add_doc("安全生产甲", "安全生产责任落实。")
    _add_doc("安全生产乙", "安全生产隐患排查。")
    panel = LibraryPanel()
    try:
        panel.search_box.setText("安全生产")
        panel.search_box.textEdited.emit("安")
        QTest.qWait(150)
        panel.search_box.textEdited.emit("安全生产")
        QTest.qWait(150)
        assert panel._search_timer.isActive(), "连续输入应重新计时，而不是按第一次算"

        panel._on_search()                       # 回车：立即检索并取消等待
        assert not panel._search_timer.isActive()
        assert panel.doc_list.count() == 2
    finally:
        panel.close()
        panel.deleteLater()
