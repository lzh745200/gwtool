# -*- coding: utf-8 -*-
"""P0-1 回归：任意文档纠错的"扫描中改文本 → 旧结果套新文本"竞态。

缺陷链路（main 上可复现）：
  扫描结果按**块内偏移**保存 -> 用户在扫描期间改输入框 -> `_on_input_changed`
  重建 `_blocks` -> worker 回调 `_on_checked` 不校验快照就 `_render`
  -> 点「应用此修正」把旧偏移套到新文本上：
  `这是新文件内容。` + 旧命中（布署@2..4→部署）= `这是部署件内容。`。

本文件守住四件事：
  1. 每次扫描绑定 token + 输入指纹，过期结果整批丢弃并提示（可自动补扫一次）；
  2. 扫描期间输入框只读，结束恢复；
  3. apply_selected / apply_all_blocks 前再校验指纹（外加 offset 原片段复核）；
  4. 正常路径（含既有"直接喂结果"的同步调用）行为不变。
"""
from __future__ import annotations

import time

from gwtool.ui.correct_dialog import AnyDocCorrectDialog, _scan_blocks

OLD_TEXT = "工作布署已经完成。"
NEW_TEXT = "这是新文件内容。"


def _drain(dlg, qapp, timeout: float = 15.0) -> bool:
    """等当前 worker 与补扫定时器都收工，并把队列里的信号跑完。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        worker = getattr(dlg, "_worker", None)
        timer = getattr(dlg, "_rescan_timer", None)
        running = worker is not None and worker.isRunning()
        pending = timer is not None and timer.isActive()
        if running:
            worker.wait(50)
            continue
        if pending:
            time.sleep(0.005)
            continue
        qapp.processEvents()
        worker = getattr(dlg, "_worker", None)
        if (worker is None or not worker.isRunning()):
            return True
        time.sleep(0.005)
    return False


def _dialog_with_live_scan(qapp):
    """构造对话框并**真实启动**一次扫描（用于模拟扫描期间的编辑）。"""
    dlg = AnyDocCorrectDialog()
    dlg._load_text(OLD_TEXT, "粘贴")
    return dlg


# ------------------------------------------------------------ 只读防护
def test_scan_makes_input_readonly_and_restores(tmp_db, qapp, wait_bg):
    dlg = _dialog_with_live_scan(qapp)
    try:
        # _load_text 已同步进入 busy 态：输入框只读、按钮置灰
        assert dlg.input_box.isReadOnly(), "扫描期间输入框必须只读（P0-1）"
        assert not dlg.btn_apply_one.isEnabled()

        assert wait_bg(dlg._worker), "扫描任务未在限期内完成"
        qapp.processEvents()

        assert not dlg.input_box.isReadOnly(), "扫描结束后必须恢复可编辑"
        assert "完成：" in dlg.lbl_stat.text()
        assert dlg._hits, "正常扫描应产生命中"
        assert dlg._scan_fp == dlg._content_fingerprint()
        assert dlg._results_stale() is False
    finally:
        _drain(dlg, qapp)
        dlg.deleteLater()


# ------------------------------------------------------------ 核心竞态
def test_stale_callback_during_scan_is_discarded(tmp_db, qapp):
    """扫描中改文本 -> 旧回调用旧 token 回来：必须整批丢弃，不能污染 UI。"""
    dlg = _dialog_with_live_scan(qapp)
    try:
        old_blocks = [dict(b) for b in dlg._blocks]
        old_token = dlg._scan_token
        stale_corrs = _scan_blocks(old_blocks)
        assert stale_corrs and stale_corrs[0], "样例应至少命中一处（布署）"

        # 模拟"扫描期间输入框被改"：setReadOnly 只挡用户键入，不挡程序性写入；
        # textChanged 会走 _on_input_changed 重建 _blocks。
        dlg.input_box.setPlainText(NEW_TEXT)
        assert dlg.input_box.isReadOnly(), "此刻仍在扫描，输入框应保持只读"
        assert dlg._blocks[0]["text"] == NEW_TEXT

        # 旧结果带回旧 token：必须被判定过期
        dlg._on_checked(stale_corrs, old_token)

        assert dlg._corrs == [], "过期结果不得进入 _corrs"
        assert dlg._hits == [], "过期结果不得渲染成命中"
        assert dlg.listw.count() == 0
        assert "文本已变更" in dlg.lbl_stat.text()

        # 关键断言：旧偏移（布署 2..4）绝不能被套到新文本上
        dlg.listw.setCurrentRow(0)       # 已无行，no-op
        dlg.apply_selected()
        assert dlg.input_box.toPlainText() == NEW_TEXT
        assert "部署" not in dlg.input_box.toPlainText()
        assert dlg._blocks[0]["text"] == NEW_TEXT

        # 自动补扫（单次）应当把新文本重新扫完，仍然不能篡改正文
        assert _drain(dlg, qapp)
        assert dlg.input_box.toPlainText() == NEW_TEXT
        assert "".join(b["text"] for b in dlg._blocks) == NEW_TEXT
        assert "部署" not in "".join(b["text"] for b in dlg._blocks)
    finally:
        _drain(dlg, qapp)
        dlg.deleteLater()


def test_stale_result_does_not_override_newer_scan_status(tmp_db, qapp):
    """旧 token 的结果不得改动新一轮扫描的 busy/只读状态。"""
    dlg = AnyDocCorrectDialog()
    try:
        dlg._blocks = [{"kind": "para", "text": OLD_TEXT, "rows": None}]
        old_token = dlg._scan_token
        stale = _scan_blocks(dlg._blocks)

        # 模拟"已经发起了更新一轮扫描"
        dlg._scan_token += 1
        dlg._scan_fp = dlg._content_fingerprint()
        dlg._set_busy(True)
        dlg.lbl_stat.setText("纠错中…")

        dlg._on_checked(stale, old_token)

        assert dlg._hits == [], "旧结果必须丢弃"
        assert dlg._corrs == []
        assert dlg.lbl_stat.text() == "纠错中…", "不得用旧结果覆盖新一轮状态"
        assert dlg.input_box.isReadOnly(), "不得提前解除新一轮的只读态"
    finally:
        dlg._set_busy(False)
        dlg.deleteLater()


# ------------------------------------------------------------ 应用前校验
def test_apply_selected_refuses_when_blocks_changed(tmp_db, qapp):
    """结果落后于正文（未重扫）时，apply_selected 必须拒绝而不是越界改写。"""
    dlg = AnyDocCorrectDialog()
    try:
        dlg._blocks = [{"kind": "para", "text": OLD_TEXT, "rows": None}]
        dlg._source = "粘贴"
        dlg._on_checked(_scan_blocks(dlg._blocks))       # token=None 兼容路径
        assert dlg.listw.count() >= 1
        assert any(h["corr"].wrong == "布署" for h in dlg._hits)

        # 模拟外部同步路径改了块文本、但结果未刷新
        dlg._blocks[0]["text"] = NEW_TEXT
        dlg.listw.setCurrentRow(0)
        dlg.apply_selected()

        assert dlg._blocks[0]["text"] == NEW_TEXT, "过期结果不得改写正文"
        assert "文本已变更" in dlg.lbl_stat.text()
        assert dlg._hits == [] and dlg.listw.count() == 0
    finally:
        dlg.deleteLater()


def test_apply_all_refuses_when_stale(tmp_db, qapp):
    dlg = AnyDocCorrectDialog()
    try:
        dlg._blocks = [{"kind": "para", "text": OLD_TEXT, "rows": None}]
        dlg._source = "粘贴"
        dlg._on_checked(_scan_blocks(dlg._blocks))

        dlg._blocks[0]["text"] = "工作布署与截止日期都变了。"
        dlg.apply_all_blocks()

        assert dlg._blocks[0]["text"] == "工作布署与截止日期都变了。", \
            "过期结果不得触发整篇应用"
        assert "文本已变更" in dlg.lbl_stat.text()
        assert dlg._worker is None, "拒绝时不应顺带启动新扫描"
    finally:
        dlg.deleteLater()


def test_apply_selected_rejects_wrong_offset(tmp_db, qapp):
    """指纹相同但偏移/原文片段不符（防御性复核）：拒绝并提示。"""
    dlg = AnyDocCorrectDialog()
    try:
        dlg._blocks = [{"kind": "para", "text": OLD_TEXT, "rows": None}]
        dlg._source = "粘贴"
        dlg._on_checked(_scan_blocks(dlg._blocks))
        assert dlg.listw.count() >= 1

        hit = dlg._hits[0]
        hit["corr"].start += 1           # 人为把偏移错开一位
        dlg.listw.setCurrentRow(0)
        dlg.apply_selected()

        assert dlg._blocks[0]["text"] == OLD_TEXT, "偏移不符时不得改写正文"
        assert "文本已变更" in dlg.lbl_stat.text()
    finally:
        dlg.deleteLater()


# ------------------------------------------------------------ 正常路径
def test_normal_apply_still_works(tmp_db, qapp):
    dlg = AnyDocCorrectDialog()
    try:
        dlg._blocks = [{"kind": "para", "text": OLD_TEXT, "rows": None}]
        dlg._source = "粘贴"
        dlg._on_checked(_scan_blocks(dlg._blocks))
        assert dlg.listw.count() >= 1

        dlg.listw.setCurrentRow(0)
        dlg.apply_selected()
        assert "部署" in dlg._blocks[0]["text"], "正常路径必须照常应用"
        assert "布署" not in dlg._blocks[0]["text"]
        assert dlg.input_box.toPlainText() == dlg._blocks[0]["text"]
        assert _drain(dlg, qapp)
    finally:
        _drain(dlg, qapp)
        dlg.deleteLater()


def test_manual_result_feed_still_accepted(tmp_db, qapp):
    """既有同步调用（token=None，测试/内部直接喂结果）保持兼容。"""
    dlg = AnyDocCorrectDialog()
    try:
        dlg._blocks = [{"kind": "para", "text": OLD_TEXT, "rows": None}]
        dlg._source = "粘贴"
        dlg._on_checked(_scan_blocks(dlg._blocks))
        assert dlg.listw.count() >= 1
        assert dlg._scan_fp == dlg._content_fingerprint()
        assert "完成：" in dlg.lbl_stat.text()
    finally:
        dlg.deleteLater()
