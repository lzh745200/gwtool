# -*- coding: utf-8 -*-
"""UI 公共组件与工具函数。"""
from __future__ import annotations

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QDoubleSpinBox, QMessageBox, QPushButton, QWidget)

from ..core.template import FONT_BODY, FONT_HEI, FONT_KAI, FONT_SONG, FONT_XBS

# 公文常用字体（缺失时在界面上给出提示，仍允许选择）
OFFICIAL_FONTS = [FONT_XBS, FONT_BODY, FONT_HEI, FONT_KAI, FONT_SONG,
                  "仿宋", "楷体", "SimSun", "SimHei", "FangSong", "KaiTi"]


def font_families_official_first() -> list[str]:
    installed = set(QFontDatabase.families())
    ordered = [f for f in OFFICIAL_FONTS if f in installed]
    rest = sorted(f for f in installed if f not in set(ordered))
    return ordered + ["（以下为系统全部字体）"] + rest if rest else ordered


def missing_official_fonts() -> list[str]:
    installed = set(QFontDatabase.families())
    return [f for f in (FONT_BODY, FONT_HEI, FONT_KAI) if f not in installed]


def make_font_combo(current: str = "") -> QComboBox:
    cb = QComboBox()
    fams = font_families_official_first()
    cb.addItems(fams)
    if current:
        idx = cb.findText(current)
        if idx >= 0:
            cb.setCurrentIndex(idx)
    return cb


def make_size_combo(sizes: list[float], current: float = 0) -> QComboBox:
    cb = QComboBox()
    for s in sizes:
        cb.addItem(f"{s:g} pt", s)
        cb.setItemData(cb.count() - 1, s)
    if current:
        for i in range(cb.count()):
            if abs(cb.itemData(i) - current) < 0.01:
                cb.setCurrentIndex(i)
                break
    return cb


def make_mm_spin(value: float = 0, lo=0.0, hi=300.0) -> QDoubleSpinBox:
    sp = QDoubleSpinBox()
    sp.setRange(lo, hi)
    sp.setSuffix(" mm")
    sp.setDecimals(1)
    sp.setValue(value)
    return sp


def info(parent: QWidget | None, text: str, title: str = "提示") -> None:
    QMessageBox.information(parent, title, text)


def warn(parent: QWidget | None, text: str, title: str = "注意") -> None:
    QMessageBox.warning(parent, title, text)


def ask(parent: QWidget | None, text: str, title: str = "确认") -> bool:
    ret = QMessageBox.question(parent, title, text,
                               QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
    return ret == QMessageBox.Yes


def dialog_buttons(parent, *specs) -> QHBoxLayout:
    """统一按钮行：主按钮在右、次按钮在左、中间 stretch。

    specs 每项 = (text, callback, is_default)；仅一项时全宽（如"关闭"）。
    主按钮自动 setDefault(True) 以获得 QSS 主色底。
    """
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    if len(specs) == 1:
        layout.addStretch(1)
    for text, cb, *rest in specs:
        is_default = rest[0] if rest else False
        btn = QPushButton(text, parent)
        btn.clicked.connect(cb)
        if is_default:
            btn.setDefault(True)
            layout.addStretch(1)
        layout.addWidget(btn)
    return layout


def wait_for_threads(owner, timeout_ms: int = 10000) -> list:
    """等待 owner（含其子对象）启动的 QThread 收工；返回超时未退的线程名。

    为什么必须有这个：QThread 在**仍在运行时**被析构，Qt 会直接 qFatal 强杀
    整个进程（Windows 退出码 0xC0000409，stderr 只有一句
    "QThread: Destroyed while thread is still running"）。
    触发场景很日常：纠错/查重/PDF 渲染还没跑完就关窗口或关对话框。

    对支持的 worker 先调 stop()（协作式中断），再 wait()；超时也不强行终止
    （terminate() 会留下半截文件），而是把线程名交给调用方去提示用户。
    """
    from PySide6.QtCore import QThread
    stuck: list[str] = []
    # owner 可能是轻量替身（测试里驱动 closeEvent 的假窗口），没有 findChildren；
    # 这种情况说明它本来也没有子线程，直接返回空表而不是抛 AttributeError ——
    # 退出路径绝不该因为一个诊断调用而中断（那会让"退出自动备份"整段被跳过）。
    finder = getattr(owner, "findChildren", None)
    if not callable(finder):
        return stuck
    try:
        threads = list(finder(QThread))
    except (RuntimeError, TypeError):
        return stuck                    # owner 已随父对象销毁
    for th in threads:
        try:
            if not th.isRunning():
                continue
            if hasattr(th, "stop"):
                th.stop()
            if not th.wait(timeout_ms):
                stuck.append(th.objectName() or type(th).__name__)
        except RuntimeError:
            continue
    return stuck


class ThreadSafeDialog:
    """给"自己起后台线程"的对话框加一道退出保险。

    用法：``class Foo(ThreadSafeDialog, QDialog)``（混入必须排在 Qt 类之前）。

    不这么做的后果不是"线程泄漏"这么温和——QThread 在运行中被析构会让 Qt
    直接 qFatal 强杀进程。对话框先于线程关闭时（用户按 Esc / 点关闭），
    对话框析构 → 子线程析构 → 进程消失，且没有任何可读的错误提示。
    """

    def closeEvent(self, event):        # noqa: N802  Qt 命名
        stuck = wait_for_threads(self, timeout_ms=8000)
        if stuck:
            try:
                warn(self, "以下后台任务未能在 8 秒内结束，窗口将关闭：\n  "
                           + "\n  ".join(stuck))
            except Exception:
                pass
        super().closeEvent(event)
