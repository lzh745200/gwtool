# -*- coding: utf-8 -*-
"""XLSX 读取器（`gwtool.core.xlsx_read`）的护栏测试。

**为什么必须自测**：这是"零新增依赖"约束下自己实现的 OOXML 解析，
Excel 真实产出的文件形态比项目写入器复杂得多（共享字符串 / 富文本 /
工作表路径与顺序解耦）。错误的表现是"导入了一堆空词条"，静默且难查。

**往返用现有写入器当 oracle**：`core/xlsx.py` 只写 inlineStr，
若只测它，覆盖率会停在"最简单那一半"。故另用手工构造的
sharedStrings 文件补齐 —— 那才是 Excel 真实产出的形态。
"""
from __future__ import annotations

import zipfile

import pytest

from gwtool.core import xlsx, xlsx_read

# ------------------------------------------------------------------ 构造器
_CT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '</Types>')
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
    '</Relationships>')
_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _workbook(sheets):
    """sheets: [(表名, rId)]"""
    body = "".join('<sheet name="%s" sheetId="%d" r:id="%s"/>'
                   % (n, i + 1, rid) for i, (n, rid) in enumerate(sheets))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="%s" xmlns:r="%s"><sheets>%s</sheets></workbook>'
            % (_NS_MAIN, _NS_REL, body))


def _workbook_rels(pairs):
    body = "".join(
        '<Relationship Id="%s" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/worksheet" Target="%s"/>' % (r, t)
        for r, t in pairs)
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/'
            'package/2006/relationships">%s</Relationships>' % body)


def _sheet(data_rows):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="%s"><sheetData>%s</sheetData></worksheet>'
            % (_NS_MAIN, "".join(data_rows)))


def _shared(items):
    body = "".join(items)
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<sst xmlns="%s" count="%d" uniqueCount="%d">%s</sst>'
            % (_NS_MAIN, len(items), len(items), body))


def _make(path, sheets, shared_xml=None):
    """sheets: [(表名, rId, zip 内路径, sheetData 片段列表)]"""
    with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("xl/workbook.xml",
                   _workbook([(n, r) for n, r, _, _ in sheets]))
        z.writestr("xl/_rels/workbook.xml.rels",
                   _workbook_rels([(r, t) for _, r, t, _ in sheets]))
        if shared_xml is not None:
            z.writestr("xl/sharedStrings.xml", shared_xml)
        for _, _, target, rows in sheets:
            z.writestr(target, _sheet(rows))


# ------------------------------------------------------------------ 往返
class TestRoundTripWithWriter:
    """用项目自己的写入器造样本 —— 最直接的 oracle。"""

    def test_write_then_read_matches(self, tmp_path):
        p = tmp_path / "a.xlsx"
        headers = ["错误写法", "正确写法", "类别"]
        rows = [["布署", "部署", "错别字"], ["截止", "截至", "错别字"]]
        xlsx.write_table(str(p), headers, rows, sheet_name="纠错对")
        got = xlsx_read.read_sheet(p)
        assert got[0] == headers
        assert [r[:3] for r in got[1:]] == rows

    def test_sheet_name_preserved(self, tmp_path):
        p = tmp_path / "b.xlsx"
        xlsx.write_table(str(p), ["词"], [["甲"]], sheet_name="保护词")
        assert xlsx_read.sheet_names(p) == ["保护词"]

    def test_multi_sheet_order(self, tmp_path):
        p = tmp_path / "c.xlsx"
        xlsx.write_xlsx(str(p), [
            xlsx.Sheet(name="第一张", headers=["a"], rows=[["1"]]),
            xlsx.Sheet(name="第二张", headers=["b"], rows=[["2"]]),
        ])
        assert xlsx_read.sheet_names(p) == ["第一张", "第二张"]
        allrows = xlsx_read.read_all(p)
        assert [n for n, _ in allrows] == ["第一张", "第二张"]

    def test_empty_cells_preserved_as_empty_string(self, tmp_path):
        p = tmp_path / "d.xlsx"
        xlsx.write_table(str(p), ["甲", "乙", "丙"],
                         [["有", "", "有"]])
        got = xlsx_read.read_sheet(p)
        assert got[1][1] == ""


# ------------------------------------------------------------------ Excel 真实形态
class TestExcelStyleFiles:
    """共享字符串 / 富文本 / 间接寻址 —— 项目写入器不会产出这些。"""

    def test_shared_strings_resolved(self, tmp_path):
        p = tmp_path / "s.xlsx"
        _make(p, [("术语表", "rId1", "xl/worksheets/sheet1.xml", [
            '<row r="1"><c r="A1" t="s"><v>0</v></c>'
            '<c r="B1" t="s"><v>1</v></c></row>',
        ])], _shared(["<si><t>规范名</t></si>", "<si><t>异名</t></si>"]))
        assert xlsx_read.read_sheet(p) == [["规范名", "异名"]]

    def test_rich_text_runs_are_concatenated(self, tmp_path):
        """`<si><r><t>碳</t></r><r><t>达峰</t></r></si>` 应拼成「碳达峰」。"""
        p = tmp_path / "r.xlsx"
        _make(p, [("表", "rId1", "xl/worksheets/sheet1.xml", [
            '<row r="1"><c r="A1" t="s"><v>0</v></c></row>',
        ])], _shared(["<si><r><t>碳</t></r><r><t>达峰</t></r></si>"]))
        assert xlsx_read.read_sheet(p)[0][0] == "碳达峰"

    def test_indirect_sheet_path_and_name(self, tmp_path):
        """工作表真名与文件名解耦（r:id → rels → target）。"""
        p = tmp_path / "i.xlsx"
        _make(p, [("术语表", "rId9", "xl/worksheets/sheet7.xml", [
            '<row r="1"><c r="A1" t="inlineStr"><is><t>甲</t></is></c></row>',
        ])])
        assert xlsx_read.sheet_names(p) == ["术语表"]
        assert xlsx_read.read_sheet(p, "术语表")[0][0] == "甲"

    def test_cell_types_bool_number_inline(self, tmp_path):
        p = tmp_path / "t.xlsx"
        _make(p, [("表", "rId1", "xl/worksheets/sheet1.xml", [
            '<row r="1"><c r="A1" t="b"><v>1</v></c>'
            '<c r="B1"><v>42</v></c>'
            '<c r="C1" t="inlineStr"><is><t>文</t></is></c></row>',
        ])])
        assert xlsx_read.read_sheet(p)[0] == ["TRUE", "42", "文"]

    def test_formula_without_cached_value_is_empty(self, tmp_path):
        """`<f>` 有、`<v>` 无 → 记空，不猜。"""
        p = tmp_path / "f.xlsx"
        _make(p, [("表", "rId1", "xl/worksheets/sheet1.xml", [
            '<row r="1"><c r="A1"><f>SUM(B1:B2)</f></c>'
            '<c r="B1"><v>7</v></c></row>',
        ])])
        assert xlsx_read.read_sheet(p)[0] == ["", "7"]

    def test_sparse_rows_are_padded(self, tmp_path):
        """跳过的行/列要补空串，保持二维形状。"""
        p = tmp_path / "sp.xlsx"
        _make(p, [("表", "rId1", "xl/worksheets/sheet1.xml", [
            '<row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c>'
            '<c r="C1" t="inlineStr"><is><t>c</t></is></c></row>',
            '<row r="3"><c r="B3" t="inlineStr"><is><t>x</t></is></c></row>',
        ])])
        got = xlsx_read.read_sheet(p)
        assert got[0] == ["a", "", "c"]
        assert got[1] == ["", "", ""]
        assert got[2] == ["", "x", ""]

    def test_multi_letter_columns(self, tmp_path):
        """`AA` 这类双字母列号不能被算成单字母。"""
        p = tmp_path / "aa.xlsx"
        _make(p, [("表", "rId1", "xl/worksheets/sheet1.xml", [
            '<row r="1"><c r="A1" t="inlineStr"><is><t>1</t></is></c>'
            '<c r="AA1" t="inlineStr"><is><t>27</t></is></c></row>',
        ])])
        assert xlsx_read.read_sheet(p)[0][26] == "27"


# ------------------------------------------------------------------ 错误路径
class TestErrorPaths:
    def test_missing_sheet_name_raises_readable(self, tmp_path):
        p = tmp_path / "n.xlsx"
        _make(p, [("甲", "rId1", "xl/worksheets/sheet1.xml", [])])
        with pytest.raises(xlsx_read.XlsxError) as ei:
            xlsx_read.read_sheet(p, "乙")
        assert "找不到工作表" in str(ei.value)

    def test_not_a_zip_raises_readable(self, tmp_path):
        p = tmp_path / "x.xlsx"
        p.write_bytes(b"this is not a zip at all")
        with pytest.raises(Exception) as ei:
            xlsx_read.read_sheet(p)
        assert "xlsx" in str(ei.value).lower() or "zip" in str(ei.value).lower()

    def test_zip_without_workbook_raises_readable(self, tmp_path):
        p = tmp_path / "y.xlsx"
        with zipfile.ZipFile(str(p), "w") as z:
            z.writestr("hello.txt", "hi")
        with pytest.raises(xlsx_read.XlsxError) as ei:
            xlsx_read.read_sheet(p)
        assert "workbook" in str(ei.value)

    def test_oversized_member_rejected(self, tmp_path, monkeypatch):
        """单成员解压上限：防 zip 炸弹。"""
        p = tmp_path / "big.xlsx"
        _make(p, [("表", "rId1", "xl/worksheets/sheet1.xml", [
            '<row r="1"><c r="A1" t="inlineStr"><is><t>x</t></is></c></row>',
        ])])
        monkeypatch.setattr(xlsx_read, "_MAX_MEMBER", 10)
        with pytest.raises(xlsx_read.XlsxError) as ei:
            xlsx_read.read_sheet(p)
        assert "过大" in str(ei.value)
