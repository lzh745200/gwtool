# -*- coding: utf-8 -*-
"""v1.9 B 批 P1-15/21：大文档交互不再阻塞 UI。

守住三件事：
1. `WritingHintsDialog` 构造不跑 `hints()`（25k 字首调实测 ~803 ms），先显示
   "正在分析…"，结果由后台 worker 回填；超长文本在对话框侧采样首尾各 3000 字，
   界面注明"已采样首尾"（`writing_hints` 契约不变）。
2. `AnyDocCorrectDialog` 的大文档「扫描 + 标记渲染」与「整篇应用」都在
   `FnWorker` 里完成，主线程只合并纯数据、更新列表/浏览器/lbl_stat。
3. P0-1 不回归：扫描期间正文变化时，旧结果（含大文档 payload）仍整批丢弃，
   不会被应用到新文本上。

用假 worker 断言"构造快 + 异步回填"；真实 worker 路径用 `wait_bg` 驱动事件
循环，断言结果正确。数据全部走 `tmp_db` 隔离库。
"""
from __future__ import annotations

import time

from gwtool.ui.correct_dialog import (
    AnyDocCorrectDialog,
    _apply_all_blocks_worker,
    _scan_and_render_worker,
    _scan_blocks,
)
from gwtool.ui.feature_dialogs import (
    WritingHintsDialog,
    _hints_worker,
    _sample_hint_text,
)

BIG_TEXT = "工作布署已经完成，报名截止本月底，务必抓好落实。" * 300
assert len(BIG_TEXT) > 6000, BIG_TEXT
OLD_TEXT = "工作布署已经完成。"
NEW_TEXT = "这是新文件内容。"


class _FakeSignal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


class _FakeWorker:
    """只记录调用、由测试手动触发 ok/failed 的 worker 替身。"""

    instances: list["_FakeWorker"] = []

    def __init__(self, fn, *args, parent=None, **kwargs):
        self.fn = fn
        self.args = args
        self.parent = parent
        self.ok = _FakeSignal()
        self.failed = _FakeSignal()
        self._running = True
        type(self).instances.append(self)

    def start(self):
        return None

    def isRunning(self):
        return self._running

    def wait(self, _ms=0):
        return True

    def finish(self):
        self._running = False


def _drain(dlg, qapp, timeout: float = 30.0) -> bool:
    """等 worker / 补扫定时器收工，并把队列信号跑完（不动别人的断言）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        worker = getattr(dlg, "_worker", None)
        apply_worker = getattr(dlg, "_apply_worker", None)
        timer = getattr(dlg, "_rescan_timer", None)
        running = [w for w in (worker, apply_worker)
                   if w is not None and w.isRunning()]
        if running:
            running[0].wait(50)
            continue
        if timer is not None and timer.isActive():
            time.sleep(0.005)
            continue
        qapp.processEvents()
        worker = getattr(dlg, "_worker", None)
        apply_worker = getattr(dlg, "_apply_worker", None)
        if all(w is None or not w.isRunning() for w in (worker, apply_worker)):
            return True
        time.sleep(0.005)
    return False


# ================================================================ 写作提示
def test_hint_sampling_keeps_head_and_tail():
    sample, note = _sample_hint_text(BIG_TEXT)
    assert len(BIG_TEXT) > 6000
    assert sample.startswith(BIG_TEXT[:3000])
    assert sample.endswith(BIG_TEXT[-3000:])
    assert "中间省略" in sample and "已采样首尾" in note
    # 短文本不采样、无说明
    short = "各单位要抓好落实。"
    assert _sample_hint_text(short) == (short, "")


def test_writing_hints_async_with_fake_worker(tmp_db, qapp, monkeypatch):
    """构造 <200 ms；先显示载入态；结果由 worker 异步回填。"""
    _FakeWorker.instances.clear()
    monkeypatch.setattr("gwtool.ui.feature_dialogs.FnWorker", _FakeWorker)

    t0 = time.perf_counter()
    dlg = WritingHintsDialog(lambda: BIG_TEXT)
    construct_s = time.perf_counter() - t0
    try:
        assert construct_s < 0.2, f"写作提示构造阻塞了 {construct_s:.2f}s"
        assert "正在分析" in dlg.report.toPlainText()
        assert "正在分析" in dlg.lbl_stat.text()
        assert not dlg.btn_hint.isEnabled(), "分析期间生成按钮应禁用"

        assert len(_FakeWorker.instances) == 1, "构造后应启动一个后台 worker"
        worker = _FakeWorker.instances[0]
        assert worker.fn is _hints_worker
        sample, kind = worker.args
        assert sample.startswith(BIG_TEXT[:3000])
        assert sample.endswith(BIG_TEXT[-3000:])
        assert kind == ""
        assert "已采样首尾" in dlg.lbl_sample.text()

        # 模拟后台完成：主线程回填
        worker.ok.emit({"lines": ["【结构】缺少结束语。", "【文风】偏口语。"],
                        "warn_n": 1})
        assert "缺少结束语" in dlg.report.toPlainText()
        assert "提示 2 条" in dlg.lbl_stat.text()
        assert "缺失项 1 条" in dlg.lbl_stat.text()
        assert dlg.btn_hint.isEnabled(), "结果回填后按钮应恢复"
    finally:
        dlg.deleteLater()


def test_writing_hints_real_worker_fills_async(tmp_db, qapp, wait_bg):
    """真实 FnWorker：构造快、事件循环驱动后提示回填、界面注明采样。"""
    t0 = time.perf_counter()
    dlg = WritingHintsDialog(lambda: BIG_TEXT)
    construct_s = time.perf_counter() - t0
    try:
        assert construct_s < 0.2, f"构造阻塞 {construct_s:.2f}s（应 <200ms）"
        assert "正在分析" in dlg.report.toPlainText()
        worker = dlg._hints_worker
        assert worker is not None and worker.isRunning(), "提示应在后台计算"
        assert wait_bg(worker), "写作提示 worker 未在限期内完成"
        qapp.processEvents()

        text = dlg.report.toPlainText()
        assert text and "正在分析" not in text
        assert "提示 " in dlg.lbl_stat.text()
        assert "已采样首尾" in dlg.lbl_sample.text()
        assert dlg.btn_hint.isEnabled()
    finally:
        worker = getattr(dlg, "_hints_worker", None)
        if worker is not None:
            worker.wait(20000)
        dlg.deleteLater()


def test_writing_hints_empty_draft_is_instant(tmp_db, qapp):
    """空稿走同步骨架提示（0 ms 级），不产生 worker。"""
    t0 = time.perf_counter()
    dlg = WritingHintsDialog(lambda: "")
    construct_s = time.perf_counter() - t0
    try:
        assert construct_s < 0.2
        assert dlg._hints_worker is None
        assert dlg.report.toPlainText(), "空稿也应给出骨架提示"
        assert "提示 " in dlg.lbl_stat.text()
        assert dlg.lbl_sample.text() == ""
    finally:
        dlg.deleteLater()


# ================================================================ 任意文档纠错
def _big_dialog(qapp):
    dlg = AnyDocCorrectDialog()
    dlg._blocks = [{"kind": "para", "text": BIG_TEXT, "rows": None}]
    dlg._source = "粘贴"
    return dlg


def test_large_scan_and_render_runs_in_worker(tmp_db, qapp, wait_bg):
    dlg = _big_dialog(qapp)
    try:
        t0 = time.perf_counter()
        dlg.start_check()
        dispatch_s = time.perf_counter() - t0
        assert dispatch_s < 0.2, f"start_check 阻塞 {dispatch_s:.2f}s"
        assert dlg._worker is not None and dlg._worker.isRunning()
        assert dlg._worker._fn is _scan_and_render_worker, \
            "大文档应走「扫描+渲染」单 worker"
        assert dlg._hits == [] and dlg.browser.toPlainText() == "", \
            "渲染结果尚未回填（主线程只接收 worker 结果）"
        assert dlg.input_box.isReadOnly(), "扫描期间输入仍应只读"

        assert wait_bg(dlg._worker), "大文档扫描 worker 未完成"
        qapp.processEvents()
        assert dlg._hits, "回填后应有命中"
        assert "<a name=" in dlg.browser.toHtml()
        assert dlg.listw.count() == len(dlg._hits)
        assert "完成：" in dlg.lbl_stat.text()
        assert not dlg.input_box.isReadOnly()
        # 结果来自纯数据 payload，与同步渲染路径一致：命中偏移仍在原文上
        for h in dlg._hits:
            c = h["corr"]
            assert h["text"][c.start:c.end] == c.wrong
    finally:
        _drain(dlg, qapp)
        dlg.deleteLater()


def test_large_apply_runs_in_worker_and_result_correct(tmp_db, qapp):
    dlg = _big_dialog(qapp)
    try:
        dlg._on_checked(_scan_blocks(dlg._blocks))    # 同步喂结果，拿到可应用命中
        assert dlg._hits
        t0 = time.perf_counter()
        dlg.apply_all_blocks()
        dispatch_s = time.perf_counter() - t0
        assert dispatch_s < 0.2, f"apply_all_blocks 阻塞 {dispatch_s:.2f}s"
        assert dlg._apply_worker is not None and dlg._apply_worker.isRunning()
        assert dlg._apply_worker._fn is _apply_all_blocks_worker
        assert dlg.input_box.isReadOnly(), "应用期间输入应只读"

        assert dlg._apply_worker.wait(30000)
        qapp.processEvents()                       # 投递 ok：合并结果 + 启动重扫
        assert "部署" in dlg._blocks[0]["text"], "整篇应用结果必须回填"
        assert "布署" not in dlg._blocks[0]["text"]
        assert dlg.input_box.toPlainText() == dlg._blocks[0]["text"]
        assert _drain(dlg, qapp)
        assert "完成：" in dlg.lbl_stat.text() or "整篇应用完成" in dlg.lbl_stat.text()
    finally:
        _drain(dlg, qapp)
        dlg.deleteLater()


def test_large_scan_stale_payload_is_discarded(tmp_db, qapp):
    """P0-1 回归（大文档 payload 路径）：扫描中改文本，旧结果不得应用。"""
    dlg = _big_dialog(qapp)
    try:
        dlg.start_check()
        old_blocks = [dict(b) for b in dlg._blocks]
        old_token = dlg._scan_token
        stale = {"corrs": _scan_blocks(old_blocks), "html": "<p>stale</p>",
                 "hits": [], "blocks": old_blocks, "title": ""}

        # 模拟扫描期间文本被程序性改写（readonly 只挡用户键入）
        dlg.input_box.setPlainText(NEW_TEXT)
        assert dlg._blocks[0]["text"] == NEW_TEXT

        dlg._on_checked(stale, old_token)
        assert dlg._hits == [] and dlg._corrs == []
        assert "文本已变更" in dlg.lbl_stat.text()
        assert dlg.listw.count() == 0

        dlg.apply_selected()                       # 无命中，也不得篡改正文
        assert dlg.input_box.toPlainText() == NEW_TEXT
        assert "部署" not in dlg.input_box.toPlainText()
        assert _drain(dlg, qapp)
        assert dlg.input_box.toPlainText() == NEW_TEXT
    finally:
        _drain(dlg, qapp)
        dlg.deleteLater()


def test_large_apply_stale_snapshot_discarded(tmp_db, qapp):
    """P0-1 回归：整篇应用 worker 回填前正文被改，结果必须丢弃。"""
    dlg = _big_dialog(qapp)
    try:
        dlg._on_checked(_scan_blocks(dlg._blocks))
        dlg.apply_all_blocks()
        worker = dlg._apply_worker
        assert worker is not None and worker.isRunning()

        # worker 跑的同时正文被改（程序性写入）
        dlg.input_box.setPlainText(NEW_TEXT)
        assert worker.wait(30000)
        qapp.processEvents()

        assert dlg.input_box.toPlainText() == NEW_TEXT
        assert dlg._blocks[0]["text"] == NEW_TEXT
        assert "文本已变更" in dlg.lbl_stat.text()
    finally:
        _drain(dlg, qapp)
        dlg.deleteLater()
