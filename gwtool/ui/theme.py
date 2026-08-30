# -*- coding: utf-8 -*-
"""界面调色板常量：统一管理 UI 中的颜色字面量（兼容系统深浅色）。"""
from __future__ import annotations

# 语义色（浅色/深色主题下均有足够对比度的中间调）
DANGER = "#b00020"    # 错误 / 确认错误
WARN = "#b26a00"      # 警告 / 疑似错误
INFO = "#606060"      # 提示
MUTED = "#757575"     # 次要说明文字
ICON = "#5f6368"      # 工具栏图标描边色（深浅背景均可辨识）


def severity_color(severity: str) -> str:
    """error/warn/info -> 颜色。"""
    return {"error": DANGER, "warn": WARN}.get(severity, INFO)
