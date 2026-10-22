# -*- coding: utf-8 -*-
"""v1.9 T5：台账/资料库五项修复的回归（P1-24 ~ P1-28）。

覆盖：
  · P1-24 归档档号接续（两批不重号）、年度按记录算、LIKE 通配符转义；
  · P1-25 收文日期真日历校验、days_left 对非法日期留痕；
  · P1-26 「全部文档」计数包含未分类；
  · P1-27 检索返回 total/has_more，UI 文案如实写"共 N 篇，显示前 M 篇"；
  · P1-28 快照列表收紧空 doc_id 语义 + 对话框空态禁用。
"""
from __future__ import annotations

import pytest

from gwtool.core import archive, receive
from gwtool.db import dao


def _rec(**kw) -> dao.Receive:
    base = dict(reg_no="", incoming_no="×政发〔2026〕1号", title="来文",
                doc_type="通知", from_org="上级机关",
                receive_date="2026-03-01", done_date="2026-03-10",
                status="已办结")
    base.update(kw)
    return dao.Receive(**base)


def _add_receive(**kw) -> int:
    return dao.add_receive(_rec(**kw))


def _add_doc(title: str, text: str, category_id: int = 0) -> int:
    return dao.add_document(dao.Document(title=title, content_text=text,
                                         category_id=category_id))


# ============================================================ P1-24 档号接续
class TestArchiveNoContinuation:
    """档号必须接续本年度已用最大号；年度按每条记录自己的日期算。"""

    def test_two_batches_same_year_do_not_collide(self, tmp_db):
        first = [_add_receive(title=f"首批{i}") for i in range(2)]
        rep1 = archive.batch_archive([dao.get_receive(i) for i in first],
                                     "×局", "永久")
        second = [_add_receive(title=f"二批{i}", receive_date="2026-04-01",
                               done_date="2026-04-10") for i in range(2)]
        rep2 = archive.batch_archive([dao.get_receive(i) for i in second],
                                     "×局", "30年")
        assert rep1["archived"] == 2 and rep2["archived"] == 2
        numbers = [dao.get_receive(i).archive_no for i in first + second]
        # 旧实现每批从 0001 重新开始 → 第二批会与第一批完全重号
        assert numbers == ["×局2026-0001", "×局2026-0002",
                           "×局2026-0003", "×局2026-0004"]
        assert len(set(numbers)) == 4

    def test_year_per_record_not_batch_head(self, tmp_db):
        old = _add_receive(title="上年件", receive_date="2025-12-01",
                           done_date="2025-12-02")
        new = _add_receive(title="本年件", receive_date="2026-01-05",
                           done_date="2026-01-06")
        rep = archive.batch_archive(
            [dao.get_receive(old), dao.get_receive(new)], "×局", "10年")
        assert rep["archived"] == 2
        # 旧实现统一取 rows[0] 的年份 → 两件都会被编成 2025
        assert dao.get_receive(old).archive_no == "×局2025-0001"
        assert dao.get_receive(new).archive_no == "×局2026-0001"

    def test_continuation_uses_existing_max(self, tmp_db):
        """已有 0007 时，新归档必须从 0008 起（而不是回到 0001）。"""
        _add_receive(title="既有件", archive_no="×局2026-0007", status="已归档")
        rid = _add_receive(title="新件")
        archive.batch_archive([dao.get_receive(rid)], "×局", "永久")
        assert dao.get_receive(rid).archive_no == "×局2026-0008"

    def test_explicit_year_overrides_record_year(self, tmp_db):
        rid = _add_receive(title="指定年度", receive_date="2025-06-01",
                           done_date="2025-06-02")
        got = [(r.title, no) for r, no in
               archive.suggest_batch([dao.get_receive(rid)], "×局", "2026")]
        assert got == [("指定年度", "×局2026-0001")]

    def test_existing_number_not_renumbered(self, tmp_db):
        rid = _add_receive(archive_no="旧号-001")
        rep = archive.batch_archive([dao.get_receive(rid)], "×局", "永久")
        assert rep["skipped"] == 1
        assert dao.get_receive(rid).archive_no == "旧号-001"

    def test_max_archive_seq_escapes_like_wildcards(self, tmp_db):
        # 前缀里的 % / _ 是 LIKE 通配符：不转义会把无关档号算进最大值
        assert dao.max_archive_seq("%", "2026") == 0
        _add_receive(archive_no="A2026-0005")
        assert dao.max_archive_seq("A", "2026") == 5
        assert dao.max_archive_seq("A%", "2026") == 0
        assert dao.max_archive_seq("A", "2025") == 0


# ============================================================ P1-25 收文日期
class TestReceiveDateCalendar:
    """形状合规 ≠ 日期存在：2026-02-30 必须在入库前被拒。"""

    @pytest.mark.parametrize("bad", ["2026-02-30", "2026-13-01", "2026-00-10"])
    def test_impossible_dates_rejected(self, bad):
        problems = receive.validate(_rec(due_date=bad))
        assert any("不是一个存在的日期" in p for p in problems), problems

    def test_real_dates_pass(self):
        assert receive.validate(
            _rec(receive_date="2026-01-01", due_date="2026-02-28",
                 done_date="")) == []
        assert receive.validate(
            _rec(receive_date="2028-01-01", due_date="2028-02-29",
                 done_date="")) == []

    def test_shape_error_still_reported(self):
        got = receive.validate(_rec(due_date="2026/02/28"))
        assert any("应为 YYYY-MM-DD 格式" in p for p in got), got

    def test_invalid_date_would_have_been_dropped_by_reminder(self, tmp_db,
                                                             monkeypatch):
        """旧数据里的非法日期：days_left 返回 None，但必须留痕（不得静默）。"""
        calls: list[tuple] = []
        monkeypatch.setattr(receive.log, "warning",
                            lambda *a, **k: calls.append(a))
        rid = _add_receive(title="非法日期件", due_date="2026-02-30",
                           done_date="", status="承办")
        rec = dao.get_receive(rid)
        assert receive.days_left(rec, today="2026-03-01") is None
        assert calls, "非法日期被 days_left 静默吞掉了"
        assert "日期非法" in str(calls[0][0])


# ============================================================ P1-26 分类计数
class TestCategoryCount:
    def test_root_count_includes_uncategorized(self, tmp_db, qapp):
        cat = dao.add_category("科室材料")
        _add_doc("分类内材料", "分类内正文内容甲", category_id=cat)
        for i in range(3):
            _add_doc(f"未分类{i}", f"未分类正文内容{i}")
        assert dao.count_documents() == 4

        from gwtool.ui.library_panel import LibraryPanel
        panel = LibraryPanel()
        try:
            panel._fill_categories()
            root = panel.cat_tree.topLevelItem(0)
            assert root.text(0) == "全部文档"
            # 旧实现 sum(own) 只数已分类 → 显示 1
            assert root.text(1) == "4", "根节点必须是全部在库篇数（含未分类）"
            assert root.child(0).text(1) == "1", "分类节点仍只数本分类"
        finally:
            panel.deleteLater()

    def test_count_by_category_still_excludes_zero(self, tmp_db):
        cat = dao.add_category("C1")
        _add_doc("a", "甲正文", category_id=cat)
        _add_doc("b", "乙正文")
        counts = dao.count_documents_by_category()
        assert counts.get(cat) == 1 and 0 not in counts


# ============================================================ P1-27 检索截断
def _fill_search_docs(n: int = 150) -> None:
    for i in range(n):
        _add_doc(f"安全生产通知{i}", f"安全生产责任落实第{i}号文件，内容各不相同。")


class TestSearchTruncation:
    def test_page_reports_total_and_has_more(self, tmp_db):
        _fill_search_docs(150)
        page = dao.search_documents_page("安全生产", limit=100)
        assert page.total == 150
        assert len(page.results) == 100
        assert page.has_more is True

    def test_page_without_hit(self, tmp_db):
        _fill_search_docs(3)
        page = dao.search_documents_page("绝不可能命中zzz", limit=100)
        assert page.total == 0 and page.results == [] and page.has_more is False

    def test_page_total_excludes_recycle_bin(self, tmp_db):
        """计数必须与检索同一道保险：回收站里的材料不计入 total。"""
        ids = [_add_doc(f"回收站材料{i}", f"安全生产回收站{ii}号")
               for i, ii in enumerate(range(3))]
        dao.delete_document(ids[0])
        page = dao.search_documents_page("安全生产", limit=100)
        assert page.total == 2
        assert all(r.ref_id != ids[0] for r in page.results)

    def test_ui_label_says_total_and_truncation(self, tmp_db, qapp):
        _fill_search_docs(150)
        from gwtool.ui.library_panel import LibraryPanel
        panel = LibraryPanel()
        try:
            panel.search_box.setText("安全生产")
            panel._on_search()
            assert panel.doc_list.count() == 100
            text = panel.count_label.text()
            assert "共 150 篇" in text, text
            assert "显示前 100 篇" in text, text
        finally:
            panel.deleteLater()


# ============================================================ P1-28 快照串档
class TestSnapshotIsolation:
    def test_list_snapshots_requires_doc_id(self, tmp_db):
        d = _add_doc("快照件", "快照正文")
        dao.add_snapshot(d, "快照件", "旧正文")
        assert len(dao.list_snapshots(d)) == 1
        # 旧实现 doc_id=None 返回全库快照；现在默认必须给 doc_id
        assert dao.list_snapshots(None) == []
        assert len(dao.list_snapshots(None, all_docs=True)) == 1

    def test_dialog_without_doc_is_disabled(self, tmp_db, qapp):
        from gwtool.ui import feature_dialogs as fd
        d = _add_doc("快照件2", "快照正文2")
        dao.add_snapshot(d, "快照件2", "旧正文2")
        dlg = fd.SnapshotsDialog(doc_id=None, current_text="",
                                 apply_callback=lambda t: None)
        try:
            assert dlg.list_w.count() == 0, "未打开文档时不得列出别篇的快照"
            assert not dlg._btn_restore.isEnabled()
            assert not dlg._btn_del.isEnabled()
            assert dlg.lbl_tip.isVisibleTo(dlg)
            # 这两个入口在无文档时必须是 no-op，不能把别人的内容回滚进来
            dlg._restore()
            dlg._delete()
        finally:
            dlg.deleteLater()

    def test_dialog_with_doc_enabled(self, tmp_db, qapp):
        from gwtool.ui import feature_dialogs as fd
        d = _add_doc("快照件3", "快照正文3")
        dao.add_snapshot(d, "快照件3", "旧正文3")
        dlg = fd.SnapshotsDialog(doc_id=d, current_text="当前",
                                 apply_callback=lambda t: None)
        try:
            assert dlg.list_w.count() == 1
            assert dlg._btn_restore.isEnabled()
            assert dlg._btn_del.isEnabled()
            assert not dlg.lbl_tip.isVisibleTo(dlg)
        finally:
            dlg.deleteLater()
