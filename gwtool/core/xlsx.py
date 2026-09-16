# -*- coding: utf-8 -*-
"""XLSX 导出（**零新依赖**）。

为什么不引 openpyxl
-------------------
本项目铁律是完全离线单机运行，多一个依赖就多一分安装包体积与麒麟 ARM64
适配风险（见 `requirements.txt` 的分档纪律）。但"不能引依赖"**不等于**
"不能出 xlsx"——xlsx 本质就是 **ZIP + 若干 XML 部件**，`zipfile` 与
`xml.sax.saxutils` 都是标准库。本模块即为此而写。

相比 CSV 解决什么
-----------------
CSV 有三个无法回避的局限，而台账/统计报表恰好都踩在上面：
  1. **无格式** —— 列宽、表头加粗、冻结首行全丢，用户每次都要重排；
  2. **无多表** —— 按文种/按机关/按月统计只能塞进同一张表或拆成多个文件；
  3. **Excel 会误解数据** —— 发文字号这类长数字串被转成科学计数法。
本模块逐条对应解决（列宽自动估算、表头样式、冻结首行、多 sheet、
长数字强制文本）。

实现要点
--------
· 一律用 **inlineStr**（内联字符串）而非 sharedStrings —— 少一个部件、
  少一层索引，导出场景没有重复字符串压缩的收益；
· **必须转义** XML 特殊字符并剔除 XML 1.0 不允许的控制字符：公文里
  「〔〕《》」属正常字符，但用户从别处粘贴的内容可能夹带 \x00-\x08 等，
  不清洗会让 Excel 报"文件已损坏"；
· 日期一律写成**文本**（YYYY-MM-DD）：比折腾日期序列号与 numFmt 稳得多，
  且台账里的日期本来就只用于阅读与筛选。
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

# XML 1.0 允许的字符：\t \n \r 与 >= 0x20 的合法码位。
# 其余（\x00-\x08、\x0b、\x0c、\x0e-\x1f）会直接让 Excel 判定文件损坏。
_ILLEGAL_XML = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
# sheet 名禁止字符（Excel 规定），并限长 31
_BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")

_MAX_COL_WIDTH = 60
_MIN_COL_WIDTH = 8


def _clean(value) -> str:
    """转义并清洗为可安全写入 XML 的文本。"""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = _ILLEGAL_XML.sub("", text)
    return escape(text)


def _col_name(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA（Excel 列名，1 基换算后为 26 进制）。"""
    name = ""
    n = index
    while True:
        name = chr(ord("A") + n % 26) + name
        n = n // 26 - 1
        if n < 0:
            break
    return name


def _sheet_name(name: str, used: set) -> str:
    """规范化 sheet 名：去非法字符、限长 31、去重。"""
    clean = _BAD_SHEET_CHARS.sub("", (name or "Sheet")).strip() or "Sheet"
    clean = clean[:31]
    candidate = clean
    n = 2
    while candidate in used:
        suffix = f"({n})"
        candidate = clean[:31 - len(suffix)] + suffix
        n += 1
    used.add(candidate)
    return candidate


def _est_width(values, header: str) -> int:
    """估算列宽（字符数）。中文按 2 个字符宽计——近似但够用。"""
    def width_of(s: str) -> int:
        return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)

    widest = width_of(str(header or ""))
    for v in values:
        if v is None:
            continue
        widest = max(widest, width_of(str(v)))
    return max(_MIN_COL_WIDTH, min(_MAX_COL_WIDTH, widest + 2))


@dataclass
class Sheet:
    """一张工作表。

    text_columns：需要**强制按文本**写入的列下标（0 基）。
    用于发文字号一类"由数字和符号组成、但语义是编码"的字段——
    不强制的话 Excel 会把它显示成 5.01234E+13 之类。
    """
    name: str
    headers: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    text_columns: set = field(default_factory=set)


def _cell(ref: str, value, style: int, force_text: bool) -> str:
    if value is None or value == "":
        return f'<c r="{ref}"{style}/>'
    if not force_text and isinstance(value, bool):
        # bool 是 int 的子类，必须先判——否则 True 会写成 1
        text = "是" if value else "否"
        return f'<c r="{ref}" t="inlineStr"{style}><is><t>{_clean(text)}</t></is></c>'
    if not force_text and isinstance(value, (int, float)):
        return f'<c r="{ref}"{style}><v>{value}</v></c>'
    return (f'<c r="{ref}" t="inlineStr"{style}>'
            f'<is><t xml:space="preserve">{_clean(value)}</t></is></c>')


def _sheet_xml(sheet: Sheet) -> str:
    headers = list(sheet.headers or [])
    rows = list(sheet.rows or [])
    ncols = max([len(headers)] + [len(r) for r in rows] + [1])

    # 列宽：先把每列的取值收集起来再估算
    cols_xml = []
    for ci in range(ncols):
        values = [r[ci] for r in rows if ci < len(r)]
        header = headers[ci] if ci < len(headers) else ""
        width = _est_width(values, header)
        cols_xml.append(
            f'<col min="{ci + 1}" max="{ci + 1}" width="{width}" '
            f'customWidth="1"/>')

    body = []
    # 起始行号取决于有无表头：无表头时数据必须从第 1 行开始，
    # 否则文件首行会凭空空出一行（曾由此处写死 start=2 引入）。
    start_row = 2 if headers else 1
    if headers:
        cells = "".join(
            _cell(f"{_col_name(ci)}1", h, ' s="1"', False)
            for ci, h in enumerate(headers))
        body.append(f'<row r="1">{cells}</row>')
    for ri, row in enumerate(rows, start=start_row):
        cells = "".join(
            _cell(f"{_col_name(ci)}{ri}", v, "", ci in (sheet.text_columns or set()))
            for ci, v in enumerate(row))
        body.append(f'<row r="{ri}">{cells}</row>')

    nrows = len(rows) + (1 if headers else 0)
    last_ref = f"{_col_name(ncols - 1)}{max(1, nrows)}"
    freeze = ('<sheetViews><sheetView workbookViewId="0">'
              '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
              'state="frozen"/></sheetView></sheetViews>') if headers else ""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:{last_ref}"/>'
        f'{freeze}'
        f'<sheetFormatPr defaultRowHeight="14.5"/>'
        f'<cols>{"".join(cols_xml)}</cols>'
        f'<sheetData>{"".join(body)}</sheetData>'
        '</worksheet>')


_CONTENT_TYPES_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
)
_CONTENT_TYPES_SHEET = (
    '<Override PartName="/xl/worksheets/sheet{n}.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')

_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
    '</Relationships>')

# 表头用加粗字体（fontId=1 / cellXfs 的 s="1"）
_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="2">'
    '<font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/></font>'
    '</fonts>'
    '<fills count="2"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill></fills>'
    '<borders count="1"><border/></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="2">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '</cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '</styleSheet>')


def write_table(path, headers, rows, sheet_name: str = "Sheet1",
                text_columns=None) -> int:
    """单表导出的便捷封装（台账、体检报告、纠错清单都用这一条通路）。"""
    return write_xlsx(path, [Sheet(
        name=sheet_name,
        headers=list(headers or []),
        rows=[list(r) for r in (rows or [])],
        text_columns=set(text_columns or ()),
    )])


def write_xlsx(path, sheets) -> int:
    """把若干 Sheet 写成 xlsx，返回写入的数据总行数（不含表头）。

    调用方传入的每个 Sheet 都会成为一个独立工作表。

    实现上**不使用临时文件**：全部部件在内存中生成后一次性写盘，
    避免「写了一半失败」留下半成品（用户会得到一个打不开的文件，
    却以为导出成功了）。
    """
    sheets = [s for s in (sheets or [])]
    if not sheets:
        sheets = [Sheet(name="Sheet1")]
    used: set = set()
    normalized = []
    for s in sheets:
        normalized.append((_sheet_name(s.name, used), s))

    parts = {}
    sheet_overrides = []
    workbook_sheets = []
    workbook_rels = []
    for idx, (name, sheet) in enumerate(normalized, start=1):
        parts[f"xl/worksheets/sheet{idx}.xml"] = _sheet_xml(sheet)
        sheet_overrides.append(_CONTENT_TYPES_SHEET.format(n=idx))
        workbook_sheets.append(
            f'<sheet name="{_clean(name)}" sheetId="{idx}" r:id="rId{idx}"/>')
        workbook_rels.append(
            f'<Relationship Id="rId{idx}" '
            f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{idx}.xml"/>')
    # styles 的 rId 必须排在所有 sheet 之后，避免与 sheet 的 rId 冲突
    style_rid = len(normalized) + 1
    workbook_rels.append(
        f'<Relationship Id="rId{style_rid}" '
        f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        f'Target="styles.xml"/>')

    parts["[Content_Types].xml"] = (
        _CONTENT_TYPES_HEAD + "".join(sheet_overrides) + "</Types>")
    parts["_rels/.rels"] = _ROOT_RELS
    parts["xl/workbook.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{"".join(workbook_sheets)}</sheets>'
        '</workbook>')
    parts["xl/_rels/workbook.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(workbook_rels) + '</Relationships>')
    parts["xl/styles.xml"] = _STYLES

    with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as zf:
        for part_name, payload in parts.items():
            zf.writestr(part_name, payload.encode("utf-8"))
    return sum(len(s.rows or []) for _n, s in normalized)
