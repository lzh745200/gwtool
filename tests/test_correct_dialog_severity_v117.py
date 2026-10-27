# -*- coding: utf-8 -*-
"""校对界面 v1.17 P0：分级着色/理由/接受此类/撤销（离屏）。"""
from __future__ import annotations

from gwtool.ui.correct_dialog import _scan_blocks


def _prepare(qapp, monkeypatch, text: str):
    from PySide6.QtWidgets import QMessageBox

    from gwtool.ui.correct_dialog import AnyDocCorrectDialog, _scan_blocks

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.No))
    dlg = AnyDocCorrectDialog()
    dlg._load_text(text, "测试")
    dlg._on_checked(_scan_blocks(dlg._blocks))
    return dlg


def _rescan(dlg, _scan_blocks):
    """模拟一次扫描结果回填（界面按钮状态随结果刷新）。"""
    dlg._on_checked(_scan_blocks(dlg._blocks))


def _row_by_wrong(dlg, wrong: str) -> int:
    for i, h in enumerate(dlg._hits):
        if h["corr"].wrong == wrong:
            return i
    return -1


def test_list_shows_severity_and_reason(qapp, monkeypatch):
    text = "文件写着“布署工作”，实际应为部署。\n要按排专人负责。"
    dlg = _prepare(qapp, monkeypatch, text)
    try:
        quoted = _row_by_wrong(dlg, "布署")
        plain = _row_by_wrong(dlg, "按排")
        assert quoted >= 0 and plain >= 0
        assert dlg.listw.item(quoted).text().startswith("【提示】")
        assert dlg.listw.item(plain).text().startswith("【错误】")

        dlg.listw.setCurrentRow(plain)
        assert "理由：" in dlg.lbl_reason.text()
        assert "级别：错误" in dlg.lbl_reason.text()
        assert dlg.btn_apply_cat.isEnabled()

        dlg.listw.setCurrentRow(quoted)
        assert "受保护片段" in dlg.lbl_reason.text()
        assert not dlg.btn_apply_cat.isEnabled(), "提示级不能批量应用"
    finally:
        dlg.close()


def test_apply_category_then_undo(qapp, monkeypatch):
    text = "第一段：布署有关工作。\n第二段：再次布署任务。"
    dlg = _prepare(qapp, monkeypatch, text)
    try:
        row = _row_by_wrong(dlg, "布署")
        assert row >= 0
        dlg.listw.setCurrentRow(row)
        dlg.apply_category()
        _rescan(dlg, _scan_blocks)
        joined = "\n".join(b["text"] for b in dlg._blocks)
        assert "布署" not in joined and joined.count("部署") == 2
        assert dlg.btn_undo.isEnabled()

        dlg.undo_last()
        _rescan(dlg, _scan_blocks)
        joined = "\n".join(b["text"] for b in dlg._blocks)
        assert joined.count("布署") == 2, "撤销应恢复两处原文"
        assert not dlg.btn_undo.isEnabled()
    finally:
        dlg.close()


def test_undo_after_single_apply(qapp, monkeypatch):
    text = "要按排专人负责。"
    dlg = _prepare(qapp, monkeypatch, text)
    try:
        row = _row_by_wrong(dlg, "按排")
        dlg.listw.setCurrentRow(row)
        dlg.apply_selected()
        _rescan(dlg, _scan_blocks)
        assert "安排" in "\n".join(b["text"] for b in dlg._blocks)
        dlg.undo_last()
        _rescan(dlg, _scan_blocks)
        assert "按排" in "\n".join(b["text"] for b in dlg._blocks)
    finally:
        dlg.close()


def test_protected_only_category_cannot_batch(qapp, monkeypatch):
    text = "文件写着“布署工作”。"
    dlg = _prepare(qapp, monkeypatch, text)
    try:
        row = _row_by_wrong(dlg, "布署")
        dlg.listw.setCurrentRow(row)
        assert not dlg.btn_apply_cat.isEnabled()
        dlg.apply_category()
        assert "提示级" in dlg.lbl_stat.text()
        assert "布署" in "\n".join(b["text"] for b in dlg._blocks)
    finally:
        dlg.close()
