# -*- coding: utf-8 -*-
"""编辑器内的纠错波浪线。

与「标记视图」的区别
--------------------
`correct_dialog` 的那套是**另一份渲染**：用 `to_marked_html` 生成一份带底色
的 HTML 给 QTextBrowser 看。本模块做的是**在用户正在编辑的正文上原地画线**，
不动文字内容、不进撤销栈，用户可以直接对着波浪线改。

坐标换算（最容易踩的坑）
------------------------
`QSyntaxHighlighter.highlightBlock(text)` 收到的 `text` 是**一个 QTextBlock**
的纯文本，不是整篇文档。而 `Correction.start/end` 是**全文坐标**。
所以必须用 `self.currentBlock().position()` 把块内偏移还原成全文坐标：

    全局位置 = 块起始位置 + 块内偏移

忘了这一步的典型症状：只有第一段能对上，后面所有段落的高亮错位到别的行上。
另外原文里的 `\\n` 属于块间分隔符，不计入块文本，块内偏移必须重新算。

性能
----
`rehighlight()` 会重跑所有块的 `highlightBlock`。纠错结果通常只有几十处，
按 `start` 排序后每块做一次二分定位即可，不需要更复杂的数据结构。
"""
from __future__ import annotations

from PySide6.QtGui import (QColor, QSyntaxHighlighter, QTextCharFormat)

from ..core import corrector


def _style_for(category: str) -> tuple[str, str]:
    return corrector._MARK_STYLE.get(category, corrector._MARK_FALLBACK)


class CorrectionHighlighter(QSyntaxHighlighter):
    """把 `Correction` 列表画成正文上的波浪线。

    用法：创建时挂到编辑器的 document 上，之后每次拿到新的纠错结果就调
    `set_corrections()`；清空传空列表即可。
    """

    def __init__(self, document):
        super().__init__(document)
        self._corrections: list[corrector.Correction] = []
        self._fmt_cache: dict[str, QTextCharFormat] = {}

    # ------------------------------------------------------------ 对外
    def set_corrections(self, corrections) -> None:
        """更新要标记的命中并重绘。

        传 None 视为清空。异常吞掉：画线失败绝不能影响用户编辑。
        """
        try:
            self._corrections = sorted(
                list(corrections or []),
                key=lambda c: (c.start, c.end))
            self.rehighlight()
        except Exception:
            self._corrections = []

    def clear(self) -> None:
        self.set_corrections([])

    def corrections(self) -> list:
        return list(self._corrections)

    # ------------------------------------------------------------ 绘制
    def _format(self, category: str) -> QTextCharFormat:
        fmt = self._fmt_cache.get(category)
        if fmt is None:
            bg, fg = _style_for(category)
            fmt = QTextCharFormat()
            # 只画波浪下划线，不改字色/底色 —— 编辑器是写作区，
            # 大面积改底色会干扰阅读，下划线足够提示又不喧宾夺主。
            #
            # 顺序有讲究：`setFontUnderline(True)` 等价于把下划线样式设回
            # SingleUnderline，必须**先**打开下划线开关，**再**设波浪样式。
            # 反过来的话波浪会被覆盖成直线（这是实测踩到的坑）。
            fmt.setFontUnderline(True)
            fmt.setUnderlineColor(QColor(fg))
            fmt.setUnderlineStyle(QTextCharFormat.WaveUnderline)
            self._fmt_cache[category] = fmt
        return fmt

    def _block_position(self) -> int:
        """当前块在全文中的起始位置。

        抽成方法是为了给测试留一个注入点：`QSyntaxHighlighter` 的格式化
        属于视图层行为，无头环境下文档不布局、`currentBlock()` 也未必
        指向预期块，直接断言"文档里有没有波浪线"测不出东西。
        测试里替换掉本方法即可在纯逻辑层验证坐标换算。
        """
        return self.currentBlock().position()

    def highlightBlock(self, text: str) -> None:
        if not self._corrections or not text:
            return
        blk_start = self._block_position()
        blk_end = blk_start + len(text)
        # 命中区间与当前块有交集的才处理；列表已按 start 排序，
        # 靠后的命中可直接 break。
        for c in self._corrections:
            if c.end <= blk_start:
                continue
            if c.start >= blk_end:
                break
            # 裁剪到块内，算出块内偏移
            s = max(c.start, blk_start) - blk_start
            e = min(c.end, blk_end) - blk_start
            if e <= s:
                continue          # 零宽区间（纯插入类）不画线，避免误标整行
            try:
                self.setFormat(s, e - s, self._format(c.category))
            except Exception:
                continue          # 单个命中画失败不影响其他
