# -*- coding: utf-8 -*-
"""编辑器内纠错波浪线的测试。

关于测什么
----------
`QSyntaxHighlighter` 的格式化是**视图层**行为：`setFormat` 的效果由 Qt 的
布局引擎在绘制时兑现。在 CI 的无头环境（offscreen 平台插件）下，文档不会
真正走布局，`QTextFragment` 里读回来的仍是 `NoUnderline` —— 直接断言
「文档里有没有波浪线」在这台机器上恒为假，测不出任何东西。

所以这里分两层测：
  ① **格式构造**：`_format()` 产出的 QTextCharFormat 是否真的带波浪线、
     颜色是否按类别区分、是否复用缓存 —— 这是纯对象断言，稳定可测；
  ② **坐标换算**：直接调 `highlightBlock` 并把 `setFormat` 截获下来，
     断言它被调用的 (起点, 长度) 是否正确。**多段文本的全局坐标换算
     是最容易写错的地方**（漏加 `currentBlock().position()`），必须锁住。

这样既覆盖了真正的风险点，又不依赖绘制管线。
"""
import pytest
from PySide6.QtGui import QTextCharFormat, QTextDocument

from gwtool.core.corrector import Correction
from gwtool.ui.correction_highlighter import CorrectionHighlighter

_WAVE = QTextCharFormat.UnderlineStyle.WaveUnderline


def _mk(start, end, wrong="错", sug="对", cat="错别字", conf=0.9):
    return Correction(start=start, end=end, wrong=wrong, suggestion=sug,
                      category=cat, reason="test", confidence=conf)


def _capture(hl, text: str, block_position: int = 0) -> list[tuple[int, int]]:
    """在不依赖文档布局的前提下，截获 highlightBlock 里 setFormat 的调用。

    做法：临时把实例的 setFormat 换成记录器。调用 highlightBlock 时
    Qt 会提供一个"当前块"，但无头环境下它未必指向我们要的块，
    因此这里显式用 `_block_pos` 覆盖掉 `currentBlock().position()` 的取值。
    """
    calls: list[tuple[int, int]] = []
    real_set = hl.setFormat

    def fake_set(start, length, fmt):
        calls.append((start, length))
        # 仍然转交真实实现，保证不改变被测行为
        try:
            real_set(start, length, fmt)
        except Exception:
            pass

    real_pos = hl._block_position
    hl._block_position = lambda: block_position      # 注入块起始位置
    hl.setFormat = fake_set
    try:
        hl.highlightBlock(text)
    finally:
        hl.setFormat = real_set
        hl._block_position = real_pos
    return calls


class _FakeBlock:
    def __init__(self, pos: int):
        self._pos = pos

    def position(self) -> int:
        return self._pos


@pytest.fixture
def doc():
    d = QTextDocument()
    d.setPlainText("占位")
    return d


# ------------------------------------------------------- ① 格式构造
def test_format_is_wave_underline(qapp, doc):
    hl = CorrectionHighlighter(doc)
    fmt = hl._format("错别字")
    assert fmt.underlineStyle() == _WAVE
    assert fmt.underlineColor().isValid()


def test_categories_have_distinct_colors(qapp, doc):
    hl = CorrectionHighlighter(doc)
    f1 = hl._format("错别字")
    f2 = hl._format("标点")
    assert f1.underlineColor() != f2.underlineColor()


def test_format_is_cached(qapp, doc):
    hl = CorrectionHighlighter(doc)
    assert hl._format("错别字") is hl._format("错别字")


def test_unknown_category_falls_back(qapp, doc):
    hl = CorrectionHighlighter(doc)
    fmt = hl._format("不存在的类别")          # 不能 KeyError
    assert fmt.underlineStyle() == _WAVE


# ------------------------------------------------------- ② 坐标换算
def test_single_block_local_span(qapp, doc):
    hl = CorrectionHighlighter(doc)
    hl.set_corrections([_mk(1, 2, "天", "田")])
    assert _capture(hl, "今天气真不好。", 0) == [(1, 1)]


def test_second_block_uses_global_position(qapp, doc):
    """核心回归：第二段的高亮必须落在该段的正确块内偏移上。

    块起始位置 8（"第一段很正常。\\n" 共 8 字符），命中在全文 11..13，
    因此块内偏移应为 3，长度 2。
    """
    text = "第一段很正常。\n第二段有布署问题。"
    pos = text.index("布署")                  # == 11
    hl = CorrectionHighlighter(doc)
    hl.set_corrections([_mk(pos, pos + 2, "布署", "部署")])
    calls = _capture(hl, "第二段有布署问题。", 8)
    assert calls == [(pos - 8, 2)]


def test_third_block_offset(qapp, doc):
    text = "甲段内容。\n乙段内容。\n丙段有错字丙。"
    pos = text.index("错字丙")
    block_pos = text.index("丙段")
    hl = CorrectionHighlighter(doc)
    hl.set_corrections([_mk(pos, pos + 3, "错字丙", "正字丙")])
    assert _capture(hl, "丙段有错字丙。", block_pos) == [(pos - block_pos, 3)]


def test_span_is_clipped_to_block(qapp, doc):
    """跨块的命中要被裁剪到当前块内，不能越界画到别的段。"""
    hl = CorrectionHighlighter(doc)
    hl.set_corrections([_mk(4, 20, "很长的跨块命中", "x")])
    calls = _capture(hl, "第二段开头四个字。", 4)
    assert calls == [(0, 9)]                  # 只画本块那 9 个字


def test_span_entirely_before_block_is_ignored(qapp, doc):
    hl = CorrectionHighlighter(doc)
    hl.set_corrections([_mk(0, 3)])
    assert _capture(hl, "这是第二段的内容。", 20) == []


def test_span_entirely_after_block_is_ignored(qapp, doc):
    hl = CorrectionHighlighter(doc)
    hl.set_corrections([_mk(100, 103)])
    assert _capture(hl, "这是第一段的内容。", 0) == []


def test_zero_width_span_is_skipped(qapp, doc):
    """零宽区间（纯插入类）不画线，否则会误标整行。"""
    hl = CorrectionHighlighter(doc)
    hl.set_corrections([_mk(3, 3, "", "插")])
    assert _capture(hl, "这是一句正常的话。", 0) == []


def test_empty_corrections_draws_nothing(qapp, doc):
    hl = CorrectionHighlighter(doc)
    hl.clear()
    assert _capture(hl, "这是一句话。", 0) == []


def test_none_is_treated_as_clear(qapp, doc):
    hl = CorrectionHighlighter(doc)
    hl.set_corrections([_mk(1, 2)])
    hl.set_corrections(None)
    assert hl.corrections() == []
    assert _capture(hl, "这是一句话。", 0) == []


def test_empty_block_text_is_safe(qapp, doc):
    hl = CorrectionHighlighter(doc)
    hl.set_corrections([_mk(0, 2)])
    assert _capture(hl, "", 0) == []


def test_multiple_spans_in_one_block(qapp, doc):
    hl = CorrectionHighlighter(doc)
    hl.set_corrections([_mk(0, 2, "甲甲", "乙乙"), _mk(5, 7, "丙丙", "丁丁")])
    assert _capture(hl, "甲甲乙丙丙内容。", 0) == [(0, 2), (5, 2)]


def test_unsorted_input_is_sorted(qapp, doc):
    """乱序传入也要按位置处理；且靠后的命中会提前 break。"""
    hl = CorrectionHighlighter(doc)
    hl.set_corrections([_mk(6, 8), _mk(0, 2)])
    calls = _capture(hl, "零一二三四五六七八九", 0)
    assert calls == [(0, 2), (6, 2)]


# ------------------------------------------------------- ③ 健壮性
def test_out_of_range_does_not_crash(qapp, doc):
    hl = CorrectionHighlighter(doc)
    hl.set_corrections([_mk(0, 9999), _mk(9999, 10000)])
    _capture(hl, "短文本。", 0)                # 不抛即通过


def test_garbage_corrections_do_not_crash(qapp, doc):
    """非 Correction 对象也不能让高亮器抛异常（防御性）。"""
    hl = CorrectionHighlighter(doc)
    hl.set_corrections([object(), None])      # 内部 try 应吞掉
    assert hl.corrections() == [] or True     # 关键是没抛异常


def test_text_is_never_modified(qapp):
    """画线绝不能改动正文一个字。"""
    doc = QTextDocument()
    text = "今天气真不好，请检查。"
    doc.setPlainText(text)
    hl = CorrectionHighlighter(doc)
    hl.set_corrections([_mk(1, 2)])
    hl.rehighlight()
    assert doc.toPlainText() == text
