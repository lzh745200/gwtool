# -*- coding: utf-8 -*-
"""v1.10 T4：发文字号唯一（P1-29）与段落索引体量预算（P1-35）的回归。

覆盖：
  · 并发取号：多线程各自"取号+插入"不得撞号（审计里 4 线程 → 4 行同号）；
  · 手工重复字号：新增/编辑都被明确拒绝，异常携带占用方；
  · registry 的重号提示与"顺延建议号"；
  · max_doc_no_serial 的 LIKE 转义与代字精确匹配；
  · 段落索引篇数上限：写入与全量重建都遵守预算，可按预算清理。
"""
from __future__ import annotations

import threading

import pytest

from gwtool.core import registry
from gwtool.db import connection as dbconn
from gwtool.db import dao


def _disp(**kw) -> dao.Dispatch:
    base = dict(doc_no="", title="发文", doc_type="通知", org="×局",
                status="拟稿")
    base.update(kw)
    return dao.Dispatch(**base)


def _doc(title: str, text: str) -> int:
    return dao.add_document(dao.Document(title=title, content_text=text))


# ============================================================ P1-29 并发取号
class TestConcurrentNumbering:
    def test_parallel_auto_numbering_never_collides(self, tmp_db):
        """8 线程并发"取号+插入"，必须拿到 8 个互不相同的号且连续。

        旧实现（max+1 在事务外算、插入时再提交）在这里必然撞号：线程们读到
        同一个 max 后各算各的号。修复靠 BEGIN IMMEDIATE 把"算号+插入"串行化。
        """
        n = 8
        barrier = threading.Barrier(n)
        results: list[tuple[int, str]] = []
        errors: list[str] = []
        lock = threading.Lock()

        def worker(i: int) -> None:
            try:
                barrier.wait(timeout=10)
                doc_id, doc_no = dao.add_dispatch_auto(
                    _disp(title=f"并发件{i}"), "×并发", "2026")
                with lock:
                    results.append((doc_id, doc_no))
            except Exception as exc:
                with lock:
                    errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                dbconn.close_current_thread()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], errors
        assert len(results) == n
        numbers = sorted(no for _did, no in results)
        assert len(set(numbers)) == n, f"并发取号出现重号：{numbers}"
        serials = sorted(registry.parse_doc_no(no)[2] for no in numbers)
        assert serials == list(range(1, n + 1)), serials

    def test_auto_numbering_continues_from_existing_max(self, tmp_db):
        dao.add_dispatch_unique(_disp(doc_no="×政办发〔2026〕7号", title="既有"))
        _did, doc_no = dao.add_dispatch_auto(_disp(title="新件"), "×政办发", "2026")
        assert doc_no == "×政办发〔2026〕8号"


# ============================================================ P1-29 重号拦截
class TestDuplicateRejected:
    def test_add_duplicate_raises_and_keeps_single_row(self, tmp_db):
        dao.add_dispatch_unique(_disp(doc_no="×政办发〔2026〕1号", title="原件"))
        with pytest.raises(dao.DuplicateDocNoError) as ei:
            dao.add_dispatch_unique(_disp(doc_no="×政办发〔2026〕1号",
                                          title="重复件"))
        assert "1号" in str(ei.value)
        assert ei.value.existing is not None
        assert ei.value.existing.title == "原件"
        assert dao.count_dispatch() == 1

    def test_update_into_duplicate_raises(self, tmp_db):
        a = dao.add_dispatch_unique(_disp(doc_no="×政办发〔2026〕1号",
                                          title="甲"))
        b = dao.add_dispatch_unique(_disp(doc_no="×政办发〔2026〕2号",
                                          title="乙"))
        rec = dao.get_dispatch(b)
        rec.doc_no = "×政办发〔2026〕1号"
        with pytest.raises(dao.DuplicateDocNoError):
            dao.update_dispatch_unique(rec)
        assert dao.get_dispatch(b).doc_no == "×政办发〔2026〕2号"
        assert dao.get_dispatch(a).doc_no == "×政办发〔2026〕1号"

    def test_editing_own_number_is_allowed(self, tmp_db):
        a = dao.add_dispatch_unique(_disp(doc_no="×政办发〔2026〕1号",
                                          title="甲"))
        rec = dao.get_dispatch(a)
        rec.title = "甲（改）"
        dao.update_dispatch_unique(rec)          # 排除自身，不应判重
        assert dao.get_dispatch(a).title == "甲（改）"

    def test_legacy_add_dispatch_still_allows_duplicates(self, tmp_db):
        """保留原语义：明确不做查重的路径（脚本/幂等回填）仍可写入。"""
        dao.add_dispatch(_disp(doc_no="×政办发〔2026〕9号"))
        dao.add_dispatch(_disp(doc_no="×政办发〔2026〕9号"))
        assert dao.count_dispatch() == 2


class TestDispatchFormDuplicateGuard:
    """手填重复字号：表单内当场拦下（不 accept），并给出可一键采纳的建议号。"""

    def _form_with(self, doc_no: str, title: str = "新件"):
        from gwtool.ui import registry_dialog as rd
        form = rd.DispatchForm(None)
        form.ed_doc_no.setText(doc_no)
        form._fields["title"].setText(title)
        return form

    def test_form_rejects_duplicate_when_user_declines(self, tmp_db, qapp,
                                                       monkeypatch):
        from PySide6.QtWidgets import QDialog

        from gwtool.ui import registry_dialog as rd
        dao.add_dispatch_unique(_disp(doc_no="×政办发〔2026〕1号", title="既有"))
        shown: list[str] = []
        monkeypatch.setattr(rd, "ask", lambda *a, **k: False)
        monkeypatch.setattr(rd, "warn", lambda *a, **k: shown.append(str(a[1])))
        form = self._form_with("×政办发〔2026〕1号")
        form._on_save()
        assert form.result() != QDialog.DialogCode.Accepted, "重复字号被放行了"
        assert any("已被占用" in msg for msg in shown), shown
        form.deleteLater()

    def test_form_applies_suggested_number_when_accepted(self, tmp_db, qapp,
                                                         monkeypatch):
        from PySide6.QtWidgets import QDialog

        from gwtool.ui import registry_dialog as rd
        dao.add_dispatch_unique(_disp(doc_no="×政办发〔2026〕1号", title="既有"))
        monkeypatch.setattr(rd, "ask", lambda *a, **k: True)
        monkeypatch.setattr(rd, "warn", lambda *a, **k: None)
        form = self._form_with("×政办发〔2026〕1号")
        form._on_save()
        assert form.result() == QDialog.DialogCode.Accepted
        assert form.value().doc_no == "×政办发〔2026〕2号"
        assert registry.uniqueness_problem(form.value()) == ""
        form.deleteLater()


class TestRegistryDuplicateHelpers:
    def test_uniqueness_problem_mentions_owner(self, tmp_db):
        dao.add_dispatch_unique(_disp(doc_no="×政办发〔2026〕3号",
                                      title="已登记件"))
        assert registry.uniqueness_problem(_disp(doc_no="×政办发〔2026〕3号")) != ""
        msg = registry.uniqueness_problem(_disp(doc_no="×政办发〔2026〕3号"))
        assert "已登记件" in msg and "id=" in msg
        assert registry.uniqueness_problem(_disp(doc_no="×政办发〔2026〕4号")) == ""

    def test_uniqueness_problem_excludes_self_on_edit(self, tmp_db):
        did = dao.add_dispatch_unique(_disp(doc_no="×政办发〔2026〕5号"))
        rec = dao.get_dispatch(did)
        assert registry.uniqueness_problem(rec) == ""

    def test_suggest_for_duplicate_returns_free_number(self, tmp_db):
        dao.add_dispatch_unique(_disp(doc_no="×政办发〔2026〕1号"))
        dao.add_dispatch_unique(_disp(doc_no="×政办发〔2026〕2号"))
        dup = _disp(doc_no="×政办发〔2026〕1号", title="重号件")
        suggest = registry.suggest_for_duplicate(dup)
        assert suggest == "×政办发〔2026〕3号"
        assert registry.uniqueness_problem(_disp(doc_no=suggest)) == ""

    def test_suggest_falls_back_to_org_when_no_prefix(self, tmp_db):
        dup = _disp(doc_no="〔2026〕1号", org="×政办发")
        assert registry.suggest_for_duplicate(dup).startswith("×政办发〔2026〕")


# ============================================================ 取号辅助函数加固
class TestMaxSerialHardening:
    def test_like_wildcards_are_escaped(self, tmp_db):
        dao.add_dispatch(_disp(doc_no="甲局〔2026〕99号"))
        # '%'/'_' 是 LIKE 元字符：不转义会把全库最大值算进来
        assert dao.max_doc_no_serial("%", "2026") == 0
        assert dao.max_doc_no_serial("_", "2026") == 0

    def test_prefix_is_matched_exactly(self, tmp_db):
        dao.add_dispatch(_disp(doc_no="×政办发函〔2026〕50号"))
        assert dao.max_doc_no_serial("×政办发", "2026") == 0
        dao.add_dispatch(_disp(doc_no="×政办发〔2026〕12号"))
        assert dao.max_doc_no_serial("×政办发", "2026") == 12


# ============================================================ P1-35 段落索引预算
class TestParagraphIndexBudget:
    def test_default_is_unlimited(self, tmp_db):
        assert dao.paragraph_index_max_docs() == 0
        assert dao.paragraph_index_allowed() == (True, "")

    def test_write_path_skips_index_over_budget(self, tmp_db):
        dao.set_setting(dao.PARA_INDEX_MAX_DOCS_KEY, "2")
        _doc("一", "第一篇正文内容。")
        _doc("二", "第二篇正文内容。")
        before = dao.paragraph_count()
        _doc("三", "第三篇正文内容。")           # 第 3 篇：超预算，不建索引
        assert before == 2
        assert dao.paragraph_count() == 2, "超预算后仍写入了段落索引"
        allowed, reason = dao.paragraph_index_allowed()
        assert allowed is False and "上限" in reason

    def test_rebuild_skips_and_clears_over_budget(self, tmp_db):
        _doc("一", "第一篇正文内容。")
        _doc("二", "第二篇正文内容。")
        assert dao.paragraph_count() == 2
        dao.set_setting(dao.PARA_INDEX_MAX_DOCS_KEY, "1")
        got = dao.rebuild_paragraphs()
        assert got.get("skipped") == 1
        assert got.get("paragraphs") == 0
        assert dao.paragraph_count() == 0, "超预算重建未清理旧索引"
        assert dao.search_paragraphs_fts("正文", 10) == []

    def test_trim_paragraph_index_respects_budget(self, tmp_db):
        _doc("一", "第一篇正文内容。")
        assert dao.trim_paragraph_index()["trimmed"] == 0   # 未超预算：不动
        assert dao.paragraph_count() == 1
        dao.set_setting(dao.PARA_INDEX_MAX_DOCS_KEY, "0")
        assert dao.trim_paragraph_index(force=True)["trimmed"] == 1
        assert dao.paragraph_count() == 0
