# -*- coding: utf-8 -*-
"""标题编号识别回归测试（v1.3.0 修复 A6a）。

缺陷：数字序号正则 `^\\d{1,2}[、．.]` 会把小数与日期行误判为编号标题——
`3.5万元`、`8.30印发`、`26.8.30印发` 都会被当成"第3/8/26条"。
误判会污染 DocTree 结构、Word 目录域、PDF 目录、GB/T 9704 编号链条体检
以及编辑器大纲。

修复：在分隔符之后加 (?!\\d) 前瞻——分隔符后紧跟数字即为小数/日期而非编号。
四处正则（解析器、体检、排版、编辑器大纲）同步修改，本测试逐一守住。
"""
import pytest

# 应识别为编号标题
REAL_HEADINGS = ["1、总体要求", "1.总体要求", "12．工作安排", "10.关于xx的通知", "3、"]
# 不应识别为标题（小数、日期、版记行）
FALSE_POSITIVES = ["3.5万元", "8.30印发", "26.8.30印发", "2026.8.30印发",
                   "1.5个百分点", "0.5万元"]


def _txt_parser_regex():
    from gwtool.core.parsers.txt_parser import _HEADING_RE
    return _HEADING_RE


def _inspector_regex():
    from gwtool.core.inspector import _CHAIN
    return next(rx for rx, _label in _CHAIN if rx.pattern.startswith(r"^\d{1,2}"))


def _formatter_h3():
    from gwtool.core.formatter import _H3
    return _H3


def _editor_outline_regex():
    from gwtool.ui.editor_panel import _HEADING_RE
    return _HEADING_RE


ALL_REGEXES = [
    ("txt_parser._HEADING_RE", _txt_parser_regex),
    ("inspector._CHAIN[数字层]", _inspector_regex),
    ("formatter._H3", _formatter_h3),
]


@pytest.mark.parametrize("name,getter", ALL_REGEXES)
@pytest.mark.parametrize("line", REAL_HEADINGS)
def test_real_headings_still_recognised(name, getter, line):
    """修复不能把真标题一起废掉。"""
    assert getter().match(line), f"{name} 漏判真标题：{line}"


@pytest.mark.parametrize("name,getter", ALL_REGEXES)
@pytest.mark.parametrize("line", FALSE_POSITIVES)
def test_decimal_and_date_lines_not_headings(name, getter, line):
    assert not getter().match(line), f"{name} 把小数/日期误判为标题：{line}"


def test_editor_outline_regex_rejects_dates(qapp):
    """编辑器大纲的正则是多分支合并式，单独校验。"""
    rx = _editor_outline_regex()
    for line in FALSE_POSITIVES:
        assert not rx.match(line), f"编辑器大纲误判：{line}"
    for line in ("1、总体要求", "一、总体要求", "（一）主要成效"):
        assert rx.match(line), f"编辑器大纲漏判：{line}"


def test_formatter_heading_normalisation_keeps_decimals():
    """一键排版微调不应把小数行、日期行改写成编号标题样式。"""
    from gwtool.core.formatter import normalize_heading_numbers

    src = "一、总体要求\n投入3.5万元用于整改。\n1、保障措施\n8.30印发\n"
    out, _changed = normalize_heading_numbers(src)
    lines = out.splitlines()
    assert "3.5万元" in lines[1], f"小数被改写：{lines[1]}"
    assert lines[3].startswith("8.30印发"), f"日期被改写：{lines[3]}"


def test_txt_import_does_not_create_heading_from_colophon(tmp_path):
    """导入含版记日期行的 txt，不应多出伪标题块。"""
    from gwtool.core.parsers.txt_parser import parse_txt

    p = tmp_path / "doc.txt"
    p.write_text("关于安全生产的通知\n一、总体要求\n投入3.5万元用于整改。\n"
                 "××单位办公室  8.30印发\n", encoding="utf-8")
    tree = parse_txt(str(p))
    headings = [b.text for b in tree.blocks if b.type == "heading"]
    assert "一、总体要求" in headings
    for bad in ("投入3.5万元用于整改。", "3.5万元", "8.30印发"):
        assert bad not in headings, f"版记/小数行被当成标题：{headings}"
