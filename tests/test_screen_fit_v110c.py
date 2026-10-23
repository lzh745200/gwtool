# -*- coding: utf-8 -*-
"""v1.11 C4：小屏/DPI 最小落地（P1-31）的回归。

覆盖：
  · 主窗口 minimumSizeHint ≤ 1024×700（三栏显式最小宽度生效）；
  · 5 个"设计尺寸可能超屏"的对话框 show 之后被夹进可用区域、且不抛异常；
  · 夹取幂等（重复 fit / 再 show 不抖动）；
  · 已销毁的对话框不抛异常；
  · 小屏一次性提示（settings 记忆，不弹窗）。
"""
from __future__ import annotations

import pytest

from gwtool.db import dao

# 「设计尺寸 > 常见小屏」的对话框（审计清单里的 5 个）
_DIALOG_NAMES = ("AnyDoc", "Receive", "Security", "Registry", "Snapshots")


def _make_dialog(name: str):
    if name == "AnyDoc":
        from gwtool.ui.correct_dialog import AnyDocCorrectDialog
        return AnyDocCorrectDialog(None)
    if name == "Receive":
        from gwtool.ui.receive_dialog import ReceiveDialog
        return ReceiveDialog(None)
    if name == "Security":
        from gwtool.ui.feature_dialogs import SecurityDialog
        return SecurityDialog(None)
    if name == "Registry":
        from gwtool.ui.registry_dialog import RegistryDialog
        return RegistryDialog(None)
    if name == "Snapshots":
        from gwtool.ui.feature_dialogs import SnapshotsDialog
        return SnapshotsDialog(None, "当前正文", None)
    raise AssertionError(f"未知对话框：{name}")


def _pump(app, ms: int = 30) -> None:
    """让 singleShot(0) 的夹取任务跑完。"""
    from PySide6.QtTest import QTest
    app.processEvents()
    QTest.qWait(ms)
    app.processEvents()


# ============================================================ 主窗口最低声明
class TestMainWindowMinSize:
    def test_minimum_size_hint_fits_1024x700(self, tmp_db, qapp):
        from gwtool.ui.main_window import MainWindow
        win = MainWindow()
        try:
            hint = win.minimumSizeHint()
            assert hint.width() <= 1024, f"minHint 宽 {hint.width()} > 1024"
            assert hint.height() <= 700, f"minHint 高 {hint.height()} > 700"
        finally:
            win.close()
            win.deleteLater()

    def test_three_panels_declared_minimums_sum_within_1024(self, tmp_db, qapp):
        from gwtool.ui.main_window import MainWindow
        win = MainWindow()
        try:
            total = (win.library.minimumWidth() + win.editor.minimumWidth()
                     + win.reference.minimumWidth())
            assert total <= 1024, f"三栏显式最小宽度合计 {total} > 1024"
            # 右栏必须仍可折叠（小屏让位给正文）
            assert win._split.isCollapsible(2) is True
        finally:
            win.close()
            win.deleteLater()


# ============================================================ 对话框夹取
@pytest.mark.parametrize("name", _DIALOG_NAMES)
def test_dialog_clamped_after_show(name, tmp_db, qapp):
    from gwtool.ui.screenfit import install_dialog_autofit
    assert install_dialog_autofit(qapp) is True
    dlg = _make_dialog(name)
    try:
        dlg.show()
        _pump(qapp)
        avail = dlg.screen().availableGeometry()
        assert dlg.width() <= avail.width(), (
            f"{name}: 宽 {dlg.width()} > 可用宽 {avail.width()}")
        assert dlg.height() <= avail.height(), (
            f"{name}: 高 {dlg.height()} > 可用高 {avail.height()}")
        frame = dlg.frameGeometry()
        # 允许系统装饰误差，但不允许整块窗口跑出可用区域
        assert frame.left() >= avail.left() - 4
        assert frame.top() >= avail.top() - 4
        assert frame.right() <= avail.right() + 8
        assert frame.bottom() <= avail.bottom() + 8
    finally:
        dlg.close()
        dlg.deleteLater()


class _FakeScreen:
    """伪屏幕：把可用区域固定成 1024×640，用来验证小屏口径（离屏平台只有 800×800）。"""

    def __init__(self, rect):
        self._rect = rect

    def availableGeometry(self):
        return self._rect


@pytest.mark.parametrize("name", _DIALOG_NAMES)
def test_dialog_fits_simulated_1024x640(name, tmp_db, qapp, monkeypatch):
    from PySide6.QtCore import QRect

    from gwtool.ui import screenfit
    dlg = _make_dialog(name)
    try:
        dlg.show()
        _pump(qapp)
        monkeypatch.setattr(screenfit, "_target_screen",
                            lambda _w: _FakeScreen(QRect(0, 0, 1024, 640)))
        size = screenfit.fit_dialog_to_screen(dlg)
        assert size is not None
        assert dlg.width() <= 1024, f"{name}: 宽 {dlg.width()} > 1024"
        assert dlg.height() <= 640, f"{name}: 高 {dlg.height()} > 640"
        frame = dlg.frameGeometry()
        assert frame.left() >= -4 and frame.top() >= -4
        assert frame.right() <= 1024 + 8
        assert frame.bottom() <= 640 + 8
    finally:
        dlg.close()
        dlg.deleteLater()


def test_oversized_plain_dialog_is_clamped(tmp_db, qapp):
    """不依赖具体业务对话框的公式级验证：2000×1500 也必须落进可用区。"""
    from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

    from gwtool.ui.screenfit import fit_dialog_to_screen
    dlg = QDialog()
    lay = QVBoxLayout(dlg)
    lay.addWidget(QLabel("超屏对话框"))
    dlg.resize(2000, 1500)
    dlg.show()
    _pump(qapp)
    size = fit_dialog_to_screen(dlg)
    avail = dlg.screen().availableGeometry()
    assert size is not None
    assert dlg.width() <= avail.width()
    assert dlg.height() <= avail.height()
    dlg.close()
    dlg.deleteLater()


# ============================================================ 幂等 / 销毁
def test_fit_is_idempotent(tmp_db, qapp):
    from gwtool.ui.screenfit import fit_dialog_to_screen, install_dialog_autofit
    install_dialog_autofit(qapp)
    dlg = _make_dialog("AnyDoc")
    try:
        dlg.show()
        _pump(qapp)
        first = dlg.size()
        fit_dialog_to_screen(dlg)
        assert dlg.size() == first, "第二次夹取改变了尺寸（会抖动）"
        dlg.show()
        _pump(qapp)
        assert dlg.size() == first, "再次 show 后尺寸发生变化（会抖动）"
        assert dlg.pos() == dlg.pos()   # 位置稳定
    finally:
        dlg.close()
        dlg.deleteLater()


def test_fit_after_dialog_destroyed_does_not_raise(tmp_db, qapp):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QDialog

    from gwtool.ui.screenfit import fit_dialog_to_screen
    dlg = QDialog()
    dlg.deleteLater()
    # deleteLater 的延迟删除要显式投递 DeferredDelete 事件才真的销毁 C++ 对象
    # （processEvents 单独调用不保证处理它）——这正是"Show 与 singleShot 之间
    # 对话框被关掉"的真实时序。
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()
    # C++ 对象已销毁：必须返回 None 而不是抛 RuntimeError
    assert fit_dialog_to_screen(dlg) is None


# ============================================================ 一次性小屏提示
def test_small_screen_notice_once(tmp_db, qapp):
    from gwtool.ui.main_window import MainWindow
    avail = qapp.primaryScreen().availableGeometry()
    if avail.width() >= MainWindow.MIN_SCREEN_W and \
            avail.height() >= MainWindow.MIN_SCREEN_H:
        pytest.skip(f"屏幕 {avail.width()}×{avail.height()} 不低于最低声明，无需提示")

    win1 = MainWindow()
    try:
        _pump(qapp)
        assert dao.get_setting(MainWindow._SMALL_SCREEN_SETTING) == "1"
        assert "低于建议的" in win1.status.currentMessage()
    finally:
        win1.close()
        win1.deleteLater()

    win2 = MainWindow()
    try:
        _pump(qapp)
        # 已提示过：第二次不再打扰（仍是持久记忆，不弹窗）
        assert "低于建议的" not in win2.status.currentMessage()
    finally:
        win2.close()
        win2.deleteLater()
