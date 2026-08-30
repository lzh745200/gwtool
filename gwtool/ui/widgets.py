# -*- coding: utf-8 -*-
"""UI 公共组件与工具函数。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QMessageBox, QWidget)

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
