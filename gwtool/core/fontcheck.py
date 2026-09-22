# -*- coding: utf-8 -*-
"""公文标准字体探测（可观测性层，**不改生成行为**）。

为什么放在 core
---------------
字体缺失的后果在 core 侧（docxgen 写入的字体名在用户机器上不存在时，
Word/WPS 会**静默替换**字体 → 字宽/行距变化 → GB/T 9704 / GJB 5100A 要求的
版心「22 行×28 字、固定行距 28 磅」实际不成立 → 成品不合规）。而探测此前
只存在于 UI 层（widgets.missing_official_fonts），生成路径完全感知不到 ——
PDF 侧有完整回退链（pdfrender.ensure_cjk_font），docx 侧什么都没有，
这种不对称本身就是缺陷。

本模块只**探测与解释**，不替换字体、不改变生成结果：把「生成的文件可能
不合规」从无声变成有声（日志 + 生成完成提示），交由用户决定是否安装字体。
"""
from __future__ import annotations

from .template import FONT_BODY, FONT_HEI, FONT_KAI, FONT_SONG, FONT_XBS

# 生成公文实际会用到的字体全集（模板各处引用的并集）。
_REQUIRED = (FONT_XBS, FONT_BODY, FONT_HEI, FONT_KAI, FONT_SONG)


def missing_fonts() -> list[str]:
    """返回本机缺失的公文标准字体名（有序）。

    用 QtGui.QFontDatabase 而非 QtWidgets：只读字体枚举，无窗口依赖，
    离屏/打包环境均可用。
    """
    try:
        from PySide6.QtGui import QFontDatabase
        installed = set(QFontDatabase.families())
    except Exception:
        # Qt 不可用（纯逻辑测试环境）时不谎报"全缺失"——宁可装作全部可用，
        # 让上层保持安静（与"探测失败返回安全默认值"的降级纪律一致）
        return []
    return [f for f in _REQUIRED if f not in installed]


def missing_note(missing: list[str] | None = None) -> str:
    """给生成完成提示用的中文说明；字体齐全时返回空串。"""
    if missing is None:
        missing = missing_fonts()
    if not missing:
        return ""
    names = "、".join(missing)
    return (f"⚠ 本机缺少公文标准字体：{names}。\n"
            "Word/WPS 会用替代字体显示，字宽与行距可能出现偏差，"
            "版心「22 行 × 28 字」可能不再成立。"
            "建议安装上述字体后重新生成。")
