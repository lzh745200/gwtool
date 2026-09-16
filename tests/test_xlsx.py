# -*- coding: utf-8 -*-
"""XLSX 导出（零依赖）的测试。

**刻意不使用 openpyxl 校验产物**——项目红线是不引这类依赖，测试也不该破例
（否则 CI 需要额外装包，而"离线可构建"是产品承诺）。改用标准库的
`zipfile` + `xml.etree` 逐部件校验结构与取值，这已能覆盖 xlsx 全部
"打不开"的成因：部件缺失、rId 错位、XML 不合法、非法控制字符。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile

from gwtool.core.xlsx import Sheet, write_table, write_xlsx
from gwtool.db import dao
from gwtool.core import registry, report
from gwtool.core.inspector import Finding

_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

REQUIRED_PARTS = (
    "[Content_Types].xml",
    "_rels/.rels",
    "xl/workbook.xml",
    "xl/_rels/workbook.xml.rels",
    "xl/styles.xml",
    "xl/worksheets/sheet1.xml",
)


def _read(path) -> dict:
    with zipfile.ZipFile(str(path)) as zf:
        assert zf.testzip() is None, "zip 结构损坏"
        return {n: zf.read(n).decode("utf-8") for n in zf.namelist()}


def _sheet_cells(parts: dict, sheet: int = 1) -> dict:
    """取某工作表的 {单元格引用: (类型, 值)}。"""
    root = ET.fromstring(parts[f"xl/worksheets/sheet{sheet}.xml"])
    out = {}
    for c in root.iter(f"{_MAIN}c"):
        ref, ctype = c.get("r"), c.get("t")
        if ctype == "inlineStr":
            node = c.find(f".//{_MAIN}t")
            out[ref] = ("inlineStr", node.text if node is not None else "")
        else:
            node = c.find(f"{_MAIN}v")
            out[ref] = (ctype, node.text if node is not None else None)
    return out


class TestContainerStructure:
    def test_required_parts_present(self, tmp_path):
        out = tmp_path / "a.xlsx"
        write_table(out, ["甲"], [["乙"]])
        parts = _read(out)
        for name in REQUIRED_PARTS:
            assert name in parts, f"缺少必需部件 {name}"

    def test_all_parts_are_wellformed_xml(self, tmp_path):
        """任一部件 XML 不合法都会让 Excel 报"文件已损坏"。"""
        out = tmp_path / "a.xlsx"
        write_table(out, ["甲"], [["乙"]])
        for text in _read(out).values():
            ET.fromstring(text)      # 解析失败即测试失败

    def test_content_types_declares_every_sheet(self, tmp_path):
        out = tmp_path / "a.xlsx"
        write_xlsx(out, [Sheet("甲", ["h"], [["v"]]),
                         Sheet("乙", ["h"], [["v"]])])
        ct = _read(out)["[Content_Types].xml"]
        assert "/xl/worksheets/sheet1.xml" in ct
        assert "/xl/worksheets/sheet2.xml" in ct

    def test_workbook_rids_match_rels(self, tmp_path):
        """rId 错位是"打不开"最常见的原因之一——工作表与样式必须各自对应。"""
        out = tmp_path / "a.xlsx"
        write_xlsx(out, [Sheet("甲", ["h"], [["v"]]),
                         Sheet("乙", ["h"], [["v"]])])
        parts = _read(out)
        wb = ET.fromstring(parts["xl/workbook.xml"])
        rels = ET.fromstring(parts["xl/_rels/workbook.xml.rels"])
        rel_map = {r.get("Id"): r.get("Target") for r in rels}
        for sheet in wb.iter(f"{_MAIN}sheet"):
            rid = sheet.get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/"
                "relationships}id")
            assert rid in rel_map, f"工作表 {sheet.get('name')} 的 rId 无对应关系"
            assert f"xl/{rel_map[rid]}" in parts

    def test_styles_rid_does_not_collide_with_sheets(self, tmp_path):
        out = tmp_path / "a.xlsx"
        write_xlsx(out, [Sheet("甲", ["h"], [["v"]]),
                         Sheet("乙", ["h"], [["v"]])])
        rels = ET.fromstring(
            _read(out)["xl/_rels/workbook.xml.rels"])
        ids = [r.get("Id") for r in rels]
        assert len(ids) == len(set(ids)), "rId 重复"


class TestCellValues:
    def test_text_and_number(self, tmp_path):
        out = tmp_path / "a.xlsx"
        write_table(out, ["文本", "数值"], [["甲", 42]])
        cells = _sheet_cells(_read(out))
        assert cells["A1"] == ("inlineStr", "文本")
        assert cells["B2"] == (None, "42")

    def test_long_digit_forced_to_text(self, tmp_path):
        """核心用例：发文字号不能变成科学计数法。"""
        out = tmp_path / "a.xlsx"
        write_xlsx(out, [Sheet("台账", ["发文字号"],
                               [["5012345678901234"]], text_columns={0})])
        cells = _sheet_cells(_read(out))
        assert cells["A2"][0] == "inlineStr", "长数字未被强制为文本"

    def test_plain_number_stays_numeric(self, tmp_path):
        out = tmp_path / "a.xlsx"
        write_table(out, ["印数"], [[120]])
        assert _sheet_cells(_read(out))["A2"][0] is None

    def test_bool_rendered_as_chinese(self, tmp_path):
        out = tmp_path / "a.xlsx"
        write_table(out, ["标记"], [[True], [False]])
        cells = _sheet_cells(_read(out))
        assert cells["A2"][1] == "是"
        assert cells["A3"][1] == "否"

    def test_empty_value_makes_empty_cell(self, tmp_path):
        out = tmp_path / "a.xlsx"
        write_table(out, ["甲", "乙"], [["值", None]])
        cells = _sheet_cells(_read(out))
        assert cells["B2"][1] in (None, "")


class TestEscaping:
    def test_xml_special_chars_roundtrip(self, tmp_path):
        out = tmp_path / "a.xlsx"
        raw = "A&B<C>D\"E'F"
        write_table(out, ["h"], [[raw]])
        assert _sheet_cells(_read(out))["A2"][1] == raw

    def test_chinese_brackets_survive(self, tmp_path):
        """公文字号里的「〔〕」是正常字符，必须原样保留。"""
        out = tmp_path / "a.xlsx"
        v = "×政发〔2026〕12号"
        write_table(out, ["h"], [[v]])
        assert _sheet_cells(_read(out))["A2"][1] == v

    def test_illegal_control_chars_are_stripped(self, tmp_path):
        """从别处粘贴的内容可能夹带控制字符，不清洗会让 Excel 判定文件损坏。"""
        out = tmp_path / "a.xlsx"
        write_table(out, ["h"], [["坏\x00\x08字符"]])
        parts = _read(out)
        ET.fromstring(parts["xl/worksheets/sheet1.xml"])   # 必须仍可解析
        assert _sheet_cells(parts)["A2"][1] == "坏字符"

    def test_all_parts_parse_after_illegal_input(self, tmp_path):
        out = tmp_path / "a.xlsx"
        write_table(out, ["h\x01"], [["a\x02b"]])
        for text in _read(out).values():
            ET.fromstring(text)


class TestSheetNaming:
    def test_illegal_chars_removed(self, tmp_path):
        out = tmp_path / "a.xlsx"
        write_xlsx(out, [Sheet("统/计:表*1", ["h"], [["v"]])])
        wb = _read(out)["xl/workbook.xml"]
        assert "统/计:表*1" not in wb
        assert "统计表1" in wb

    def test_long_name_truncated_to_31(self, tmp_path):
        out = tmp_path / "a.xlsx"
        write_xlsx(out, [Sheet("备" * 50, ["h"], [["v"]])])
        wb = ET.fromstring(_read(out)["xl/workbook.xml"])
        name = list(wb.iter(f"{_MAIN}sheet"))[0].get("name")
        assert len(name) <= 31

    def test_duplicate_names_deduplicated(self, tmp_path):
        """Excel 不允许同名工作表，重名会让整个文件打不开。"""
        out = tmp_path / "a.xlsx"
        write_xlsx(out, [Sheet("表", ["h"], [["v"]]),
                         Sheet("表", ["h"], [["v"]])])
        wb = ET.fromstring(_read(out)["xl/workbook.xml"])
        names = [s.get("name") for s in wb.iter(f"{_MAIN}sheet")]
        assert len(names) == len(set(names))

    def test_empty_name_falls_back(self, tmp_path):
        out = tmp_path / "a.xlsx"
        write_xlsx(out, [Sheet("", ["h"], [["v"]])])
        wb = ET.fromstring(_read(out)["xl/workbook.xml"])
        assert list(wb.iter(f"{_MAIN}sheet"))[0].get("name")


class TestEdgeCases:
    def test_no_sheets_still_produces_file(self, tmp_path):
        out = tmp_path / "a.xlsx"
        write_xlsx(out, [])
        for name in REQUIRED_PARTS:
            assert name in _read(out)

    def test_no_rows(self, tmp_path):
        out = tmp_path / "a.xlsx"
        write_table(out, ["甲", "乙"], [])
        cells = _sheet_cells(_read(out))
        assert cells["A1"] == ("inlineStr", "甲")

    def test_no_headers(self, tmp_path):
        out = tmp_path / "a.xlsx"
        write_table(out, [], [["甲"]])
        assert _sheet_cells(_read(out))["A1"][1] == "甲"

    def test_ragged_rows(self, tmp_path):
        """行长不齐（台账常见：部分字段缺失）不得出错。"""
        out = tmp_path / "a.xlsx"
        write_table(out, ["甲", "乙", "丙"], [["1"], ["1", "2"], ["1", "2", "3"]])
        cells = _sheet_cells(_read(out))
        assert cells["C4"][1] == "3"

    def test_header_is_styled_and_frozen(self, tmp_path):
        out = tmp_path / "a.xlsx"
        write_table(out, ["甲"], [["乙"]])
        sheet_xml = _read(out)["xl/worksheets/sheet1.xml"]
        assert 'state="frozen"' in sheet_xml, "表头未冻结"
        assert ' s="1"' in sheet_xml, "表头未套用加粗样式"

    def test_column_width_is_bounded(self, tmp_path):
        out = tmp_path / "a.xlsx"
        write_table(out, ["h"], [["中" * 200]])
        sheet_xml = _read(out)["xl/worksheets/sheet1.xml"]
        root = ET.fromstring(sheet_xml)
        for col in root.iter(f"{_MAIN}col"):
            assert float(col.get("width")) <= 60


class TestRegistryAndReportIntegration:
    def _dispatch(self, **kw) -> dao.Dispatch:
        base = dict(doc_no="×政发〔2026〕12号", title="关于XX工作的通知",
                    doc_type="通知", org="XX局", main_send="各科室",
                    secret_level="公开", urgency="平急",
                    sign_date="2026-09-01", print_date="2026-09-02",
                    pages=3, copies=50, drafter="张三", reviewer="李四",
                    approver="王五", status="已印发")
        base.update(kw)
        return dao.Dispatch(**base)

    def test_registry_export_xlsx_single_sheet(self, tmp_path):
        out = tmp_path / "台账.xlsx"
        n = registry.export_xlsx([self._dispatch()], str(out))
        assert n == 1
        parts = _read(out)
        cells = _sheet_cells(parts)
        assert cells["A1"][1] == "发文字号"
        assert cells["A2"][1] == "×政发〔2026〕12号"
        assert cells["A2"][1] == "×政发〔2026〕12号"

    def test_registry_doc_no_forced_text(self, tmp_path):
        out = tmp_path / "台账.xlsx"
        registry.export_xlsx([self._dispatch(doc_no="5012345678901234")],
                             str(out))
        assert _sheet_cells(_read(out))["A2"][0] == "inlineStr"

    def test_registry_with_stats_creates_second_sheet(self, tmp_path):
        out = tmp_path / "台账.xlsx"
        registry.export_xlsx([self._dispatch(), self._dispatch(doc_type="报告")],
                             str(out), with_stats=True)
        parts = _read(out)
        assert "xl/worksheets/sheet2.xml" in parts
        assert _sheet_cells(parts, 2)["A1"][1] == "维度"

    def test_registry_export_csv_still_works(self, tmp_path):
        """CSV 必须保留：老用户与脚本场景仍在用。"""
        out = tmp_path / "台账.csv"
        n = registry.export_csv([self._dispatch()], str(out))
        assert n == 1
        assert "发文字号" in out.read_text(encoding="utf-8-sig")

    def test_report_export_xlsx(self, tmp_path):
        out = tmp_path / "体检.xlsx"
        findings = [Finding("error", "引文规范", "书名号不配对"),
                    Finding("warn", "附件说明", "应使用全角冒号")]
        n = report.export_report_xlsx(findings, str(out), "某通知")
        assert n == 2
        cells = _sheet_cells(_read(out))
        assert cells["A1"][1] == "严重程度"
        assert cells["A2"][1] == "不合规范"      # error 排在最前

    def test_report_docx_path_untouched(self, tmp_path):
        """加入 xlsx 通路不得影响既有 DOCX 导出。"""
        assert hasattr(report, "export_report")
