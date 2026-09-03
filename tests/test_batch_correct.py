# -*- coding: utf-8 -*-
"""按分类批量纠错：预览不写库、确认后才写库、写库后 FTS 与结构化块同步、
单份失败不中断整批。"""
import json

import pytest

from gwtool.core import batch
from gwtool.db import dao

WRONG_TEXT = "做好今年的布署工作，报名截止本月底。"


def _mk(title="季度工作报告", text=WRONG_TEXT, category_id=0, blocks_json="[]"):
    return dao.add_document(dao.Document(title=title, content_text=text,
                                         category_id=category_id,
                                         blocks_json=blocks_json))


def _labels(res):
    return {p.doc_id: [h.label for h in p.hits] for p in res.plans}


# ------------------------------------------------------------------ 预览
def test_preview_reports_hits_without_writing(tmp_db):
    did = _mk()
    res = batch.batch_correct()
    assert res.scanned == 1
    assert res.hit_total >= 2
    hits = res.plans[0].hits
    labels = {h.label for h in hits}
    assert "布署 → 部署" in labels and "截止 → 截至" in labels
    # 位置与上下文都要给出来，用户才敢确认
    text = dao.get_document(did).content_text
    for h in hits:
        assert text[h.start:h.end] == h.wrong, "命中位置对不上原文"
        assert h.context and h.wrong in h.context
        assert h.category and h.confidence > 0
    # 预览阶段一个字都不能写进库
    assert dao.get_document(did).content_text == WRONG_TEXT
    assert res.applied == [] and res.changes == 0
    assert res.failures == []


def test_preview_respects_scope_and_filters(tmp_db):
    cat_a = dao.add_category("通知类")
    cat_b = dao.add_category("纪要类")
    a1 = _mk(title="A1", category_id=cat_a)
    _mk(title="A2", text="安全生产人人有责。", category_id=cat_a)      # 无命中
    b1 = _mk(title="B1", text="会议布署了下一步工作。", category_id=cat_b)

    assert sorted(p.doc_id for p in batch.batch_correct().plans) == sorted([a1, b1])
    only_a = batch.batch_correct(category_id=cat_a)
    assert [p.doc_id for p in only_a.plans] == [a1]
    assert only_a.scanned == 2, "扫描篇数应按分类统计"
    assert [p.doc_id for p in batch.batch_correct(doc_ids=[b1]).plans] == [b1]

    # 置信度门槛：布署(0.98) 留下，截止(0.90) 被挡掉
    strict = batch.batch_correct(min_confidence=0.95)
    assert {h.wrong for p in strict.plans for h in p.hits} == {"布署"}
    # 类别过滤：只要"易混词"就只剩 截止→截至
    kinds = batch.batch_correct(categories=("易混词",))
    assert {h.wrong for p in kinds.plans for h in p.hits} == {"截止"}


def test_preview_skips_recycle_bin(tmp_db):
    kept = _mk(title="留着的")
    gone = _mk(title="删掉的", text="会议布署了下一步工作。")
    dao.delete_document(gone)
    res = batch.batch_correct()
    assert [p.doc_id for p in res.plans] == [kept]
    assert res.scanned == 1


def test_number_usage_hints_are_never_applied(tmp_db):
    """「数字用法」类的 suggestion 是提示标签而不是替换文本，批量写回必须整类排除。"""
    text = "二〇二六年三月五日召开会议，会议布署了工作。"
    did = _mk(text=text)
    from gwtool.core.corrector import check_text
    assert any(c.category == "数字用法" for c in check_text(text)), \
        "本用例依赖 corrector 会给出数字用法提示"

    res = batch.batch_correct(min_confidence=0.0)
    assert all(h.category != "数字用法" for p in res.plans for h in p.hits)
    applied = batch.batch_correct(apply=True, plans=res.plans)
    new_text = dao.get_document(did).content_text
    assert "二〇二六年" in new_text, "年份被替换成了提示标签"
    assert "部署" in new_text


# ------------------------------------------------------------------ 执行
def test_apply_writes_back_and_syncs_fts(tmp_db):
    did = _mk(title="季度工作报告", text="布署安排好各项工作。")
    prev = batch.batch_correct()
    res = batch.batch_correct(apply=True, plans=prev.plans)

    assert res.applied == [did] and res.changes == 1 and res.failures == []
    d = dao.get_document(did)
    assert "布署" not in d.content_text and "部署" in d.content_text
    # 写回后 FTS 必须同步：新词搜得到、旧词搜不到
    assert dao.search_documents("部署"), "写回后全文索引没同步（搜不到新词）"
    assert not dao.search_documents("布署"), "全文索引里还留着改前的旧词"
    # 改前留了快照，可在「历史版本」里回滚
    reasons = [s["reason"] for s in dao.list_snapshots(did)]
    assert "批量纠错前" in reasons
    assert any(s["content"] == "布署安排好各项工作。" for s in dao.list_snapshots(did))


def test_apply_requires_plans(tmp_db):
    _mk()
    with pytest.raises(ValueError):
        batch.batch_correct(apply=True, plans=[])


def test_apply_fixes_blocks_and_keeps_structure(tmp_db):
    """正文改了，结构化块也要跟着改，否则汇编出来的公文还是错的。

    update_document_content(blocks_json=None) 会把块清成 []（标题层级全丢），
    所以必须显式回写；块里的未知字段也要原样保留。
    """
    blocks = [
        {"type": "heading", "level": 1, "text": "关于布署工作的通知",
         "align": "center", "rows": None, "未来版本才有的字段": "保留我"},
        {"type": "paragraph", "level": 0, "text": "请各单位按布署抓好落实。",
         "align": "left", "rows": None},
        {"type": "table", "level": 0, "text": "", "align": "left",
         "rows": [["项目", "布署情况"], ["安全生产", "已完成"]]},
    ]
    content = "关于布署工作的通知\n请各单位按布署抓好落实。\n项目 | 布署情况\n安全生产 | 已完成"
    did = _mk(text=content, blocks_json=json.dumps(blocks, ensure_ascii=False))

    prev = batch.batch_correct()
    res = batch.batch_correct(apply=True, plans=prev.plans)
    assert res.applied == [did]

    d = dao.get_document(did)
    assert "布署" not in d.content_text
    out = json.loads(d.blocks_json)
    assert len(out) == 3, "块结构被写坏了"
    assert [b["type"] for b in out] == ["heading", "paragraph", "table"]
    assert out[0]["level"] == 1 and out[0]["align"] == "center"
    assert out[0]["未来版本才有的字段"] == "保留我", "未知字段被丢掉了"
    assert "布署" not in out[0]["text"] and "部署" in out[0]["text"]
    assert "部署" in out[1]["text"]
    assert out[2]["rows"][0] == ["项目", "部署情况"]
    assert out[2]["rows"][1] == ["安全生产", "已完成"], "没命中的单元格被改动了"


def test_apply_relocates_hits_after_document_changed(tmp_db):
    """预览与执行之间文档被编辑过：位置对不上时按原词重新定位，仍然改对。"""
    did = _mk()
    prev = batch.batch_correct()
    dao.update_document_content(did, "季度工作报告", "【已核稿】" + WRONG_TEXT)

    res = batch.batch_correct(apply=True, plans=prev.plans)
    text = dao.get_document(did).content_text
    assert res.changes == 2 and res.skipped == 0
    assert text == "【已核稿】做好今年的部署工作，报名截至本月底。"


def test_apply_skips_hits_that_no_longer_exist(tmp_db):
    """内容已被改得对不上、又无法唯一定位时：跳过而不乱改，也不写库。"""
    did = _mk()
    prev = batch.batch_correct()
    dao.update_document_content(did, "季度工作报告", " completely different content ")

    res = batch.batch_correct(apply=True, plans=prev.plans)
    assert res.applied == [] and res.changes == 0
    assert res.skipped == prev.hit_total
    d = dao.get_document(did)
    assert d.content_text == " completely different content "


def test_apply_never_touches_book_title_quotes(tmp_db):
    """《》里引用的原文标题按 corrector 的规则不纠错，批量执行同样不能改。"""
    text = "《关于加强布署工作的通知》已印发，请照布署执行。"
    did = _mk(text=text)
    prev = batch.batch_correct()
    hits = [h for p in prev.plans for h in p.hits]
    assert len(hits) == 1, "书名号内的引用不该被当成命中"
    assert hits[0].start == text.index("请照布署") + 2
    batch.batch_correct(apply=True, plans=prev.plans)
    out = dao.get_document(did).content_text
    assert "《关于加强布署工作的通知》" in out, "书名号内引用的标题被改了"
    assert "照部署执行" in out


# ------------------------------------------------------------------ 失败隔离
def test_single_document_failure_does_not_abort_batch(tmp_db, monkeypatch):
    d1 = _mk(title="会失败的那篇")
    d2 = _mk(title="正常的那篇", text="会议布署了下一步工作。")
    prev = batch.batch_correct()
    assert sorted(p.doc_id for p in prev.plans) == sorted([d1, d2])

    real = dao.update_document_content

    def flaky(doc_id, title, content, blocks_json=None):
        if doc_id == d1:
            raise RuntimeError("模拟写库失败")
        return real(doc_id, title, content, blocks_json=blocks_json)

    monkeypatch.setattr(dao, "update_document_content", flaky)
    res = batch.batch_correct(apply=True, plans=prev.plans)

    assert res.applied == [d2], "单篇失败不该拖垮整批"
    assert [t for t, _r in res.failures] == ["会失败的那篇"]
    assert "模拟写库失败" in res.failures[0][1]
    assert "部署" in dao.get_document(d2).content_text
    assert "布署" in dao.get_document(d1).content_text, "失败那篇不该被改一半"


def test_plan_for_missing_or_deleted_doc_is_reported(tmp_db):
    did = _mk(title="正常的那篇")
    prev = batch.batch_correct()
    ghost = batch.DocCorrection(
        999999, "幽灵文档",
        [batch.CorrectHit(0, 2, "布署", "部署", "错别字", 0.98, "")])
    gone = _mk(title="已进回收站", text="会议布署了工作。")
    dao.delete_document(gone)
    gone_plan = batch.DocCorrection(
        gone, "已进回收站",
        [batch.CorrectHit(2, 4, "布署", "部署", "错别字", 0.98, "")])

    res = batch.batch_correct(apply=True, plans=[ghost, gone_plan] + prev.plans)
    assert res.applied == [did]
    reasons = dict(res.failures)
    assert "幽灵文档" in reasons and "不存在" in reasons["幽灵文档"]
    assert "已进回收站" in reasons, "回收站里的文档不该被批量纠错改动"
    assert "布署" in dao.get_document(gone).content_text


def test_progress_callback_reports_each_document(tmp_db):
    ids = [_mk(title=f"材料{i}", text=f"第{i}篇：会议布署了工作。") for i in range(3)]
    seen: list[tuple[int, int]] = []
    res = batch.batch_correct(progress_cb=lambda i, n: seen.append((i, n)))
    assert res.scanned == 3
    assert seen == [(1, 3), (2, 3), (3, 3)]

    seen.clear()
    batch.batch_correct(apply=True, plans=res.plans,
                        progress_cb=lambda i, n: seen.append((i, n)))
    assert seen == [(1, 3), (2, 3), (3, 3)]
    assert all("部署" in dao.get_document(i).content_text for i in ids)


# ------------------------------------------------------------------ UI
@pytest.fixture(autouse=True)
def _no_modal(monkeypatch):
    from PySide6.QtWidgets import QDialog, QMessageBox
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))


def _drain(worker, qapp):
    """等工作线程跑完并让信号回到主线程（测试里不跑事件循环）。"""
    assert worker is not None
    assert worker.wait(60000), "后台线程未在时限内结束"
    qapp.processEvents()


def test_batch_correct_dialog_preview_then_apply(tmp_db, qapp):
    from gwtool.ui.feature_dialogs import BatchCorrectDialog

    cat = dao.add_category("通知类")
    did = _mk(category_id=cat)
    dlg = BatchCorrectDialog(cat)
    try:
        assert dlg.scope_combo.currentData() == cat, "应默认选中传入的分类"
        assert not dlg.btn_apply.isEnabled(), "没预览就能执行，太危险"

        dlg._preview()
        _drain(dlg._scan_worker, qapp)
        assert dlg.tree.topLevelItemCount() >= 1
        assert "布署 → 部署" in dlg.tree.topLevelItem(0).child(0).text(0)
        assert dlg.btn_apply.isEnabled()
        # 预览阶段库里一个字都没改
        assert dao.get_document(did).content_text == WRONG_TEXT

        dlg._apply()
        _drain(dlg._apply_worker, qapp)
        assert "部署" in dao.get_document(did).content_text
        assert dao.search_documents("部署")
    finally:
        dlg.close()


def test_batch_correct_dialog_filter_change_invalidates_plan(tmp_db, qapp):
    """改了范围/类别/置信度后旧计划必须作废，不能按过期预览写回。"""
    from gwtool.ui.feature_dialogs import BatchCorrectDialog

    _mk()
    dlg = BatchCorrectDialog(None)
    try:
        dlg._preview()
        _drain(dlg._scan_worker, qapp)
        assert dlg._plans and dlg.btn_apply.isEnabled()
        dlg.sp_conf.setValue(0.99)
        assert dlg._plans == [] and not dlg.btn_apply.isEnabled()
        # 没有计划时点执行只提示，不写库
        dlg._apply()
        assert dao.get_document(1).content_text == WRONG_TEXT
    finally:
        dlg.close()


def test_batch_correct_dialog_db_failure_keeps_ui_usable(tmp_db, qapp, monkeypatch):
    """读库失败时只提示不崩：不启线程、按钮与进度条复位，界面仍可继续用。"""
    from gwtool.ui import feature_dialogs as fd

    _mk()
    dlg = fd.BatchCorrectDialog(None)
    try:
        dlg._preview()
        _drain(dlg._scan_worker, qapp)
        first_worker = dlg._scan_worker

        monkeypatch.setattr(dao, "count_documents",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("库坏了")))
        dlg._preview()                       # 读库失败 -> warn 后 return
        assert dlg._scan_worker is first_worker, "失败时不该启动新的后台线程"
        assert dlg.btn_preview.isEnabled()
        assert not dlg.progress.isVisible()

        dlg._on_failed("模拟失败")            # 失败回调把界面复位，不留卡死状态
        assert not dlg.progress.isVisible() and dlg.btn_preview.isEnabled()
    finally:
        dlg.close()
