# -*- coding: utf-8 -*-
"""XLSX 读取（零依赖，只读）。

**为什么自己写**：项目红线是"零新增依赖"，而 openpyxl/pandas 都不在允许清单里；
`core/xlsx.py` 只有写入器（且为了简单只用 inlineStr），接不了用户的 Excel 文件。
行业术语表在实务中大量以 Excel 下发，所以这一块必须自己实现。

必须支持的单元格形态（**Excel 真实产出的与项目写入器产出的都要吃**）
----------------------------------------------------------------------
========================  ==========================================
``t="s"``                 共享字符串：``<v>`` 是 ``sharedStrings.xml`` 的下标
``t="inlineStr"``         内联字符串：``<is>`` 下取文本（含富文本 ``<r><t>``）
``t="str"``               公式的字符串结果，直接放在 ``<v>``
``t="b"``                 布尔，``<v>`` 为 0/1
无 ``t``                  数值，``<v>`` 即字面量
``<f>`` 有、``<v>`` 无     公式无缓存值 → 记空（调用方按空单元格处理）
========================  ==========================================

刻意不做：``.xls``（旧 BIFF 二进制）、日期序列号还原、样式、图表、批注、超链接
—— 词表用不到，而每一项都要成倍增加出错面。日期在导出侧本就是文本（见
`core/xlsx.py` 说明），故读回来也不需要用序列号。

安全
----
· 只按**白名单成员名**读取，不落盘解包，天然无 zip-slip 风险；
· 单成员解压上限 ``_MAX_MEMBER``、总解压上限 ``_MAX_TOTAL``，防 zip 炸弹；
· 用标准库 `xml.etree.ElementTree`：它**不解析外部实体**、遇未定义实体直接报错，
  故不存在 XXE 与十亿笑声（billion laughs）的展开路径。
"""
from __future__ import annotations

import re
import zipfile
from xml.etree import ElementTree as ET

# 解压上限：词表文件不可能有这么大，超过即判异常输入
_MAX_MEMBER = 64 * 1024 * 1024
_MAX_TOTAL = 256 * 1024 * 1024

_CELL_RE = re.compile(r"([A-Z]+)(\d+)")


class XlsxError(ValueError):
    """不是有效的 xlsx / 结构缺失 / 超出安全上限。"""


def _localname(tag: str) -> str:
    """去掉 XML 命名空间前缀（``{uri}tag`` → ``tag``）。

    不按固定命名空间匹配：真实文件里 spreadsheetml 命名空间有 transitional
    与 strict 两种，硬编码其一会把另一类文件全部判为"格式不对"。
    """
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _col_index(ref: str) -> int:
    """``B3`` → 列下标 1（0-based）。"""
    m = _CELL_RE.match(ref or "")
    if not m:
        return 0
    letters = m.group(1)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def _row_index(ref: str) -> int:
    m = _CELL_RE.match(ref or "")
    return int(m.group(2)) if m else 0


def _text_of(node) -> str:
    """取一个节点的全部文本，兼容富文本（多个 ``<r><t>`` 片段）。"""
    parts = []
    for t in node.iter():
        if _localname(t.tag) == "t" and t.text:
            parts.append(t.text)
    return "".join(parts)


def _read_member(zf: zipfile.ZipFile, name: str, budget: list) -> bytes:
    """按名读一个成员，带大小上限。"""
    try:
        info = zf.getinfo(name)
    except KeyError:
        raise XlsxError("xlsx 缺少部件：%s" % name) from None
    if info.file_size > _MAX_MEMBER:
        raise XlsxError("部件 %s 过大（%.1f MB），疑似异常文件"
                        % (name, info.file_size / 1048576))
    budget[0] += info.file_size
    if budget[0] > _MAX_TOTAL:
        raise XlsxError("解压内容超过上限，疑似异常文件")
    return zf.read(name)


def _parse_shared_strings(zf: zipfile.ZipFile, budget: list) -> list:
    """共享字符串表；没有该部件时返回空表（内联字符串的文件就没有它）。"""
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(_read_member(zf, "xl/sharedStrings.xml", budget))
    out = []
    for si in root:
        if _localname(si.tag) == "si":
            out.append(_text_of(si))
    return out


def _sheet_targets(zf: zipfile.ZipFile, budget: list) -> list:
    """返回 ``[(工作表名, zip 内路径)]``，顺序与工作簿一致。

    ⚠️ **必须走 r:id → rels 这一层**，不能假定 ``xl/worksheets/sheet1.xml``：
    真实文件里工作表路径可以任意（如 ``xl/worksheets/sheet5.xml``），
    而且工作表顺序与文件编号**不保证一致**（用户拖拽过顺序就会错位）。
    """
    root = ET.fromstring(_read_member(zf, "xl/workbook.xml", budget))
    rels = {}
    if "xl/_rels/workbook.xml.rels" in zf.namelist():
        rroot = ET.fromstring(
            _read_member(zf, "xl/_rels/workbook.xml.rels", budget))
        for rel in rroot:
            if _localname(rel.tag) != "Relationship":
                continue
            rid = rel.get("Id") or ""
            tgt = rel.get("Target") or ""
            if tgt.startswith("/"):
                rels[rid] = tgt.lstrip("/")
            elif tgt.startswith("xl/"):
                rels[rid] = tgt
            else:
                rels[rid] = "xl/" + tgt
    out = []
    for node in root.iter():
        if _localname(node.tag) != "sheet":
            continue
        rid = ""
        for k, v in node.attrib.items():
            if _localname(k) == "id":
                rid = v
        name = node.get("name") or ""
        path = rels.get(rid)
        if path:
            out.append((name, path))
    if not out:
        # 兜底：没有 rels 的畸形文件，退回按文件名枚举（顺序可能不准，
        # 但比直接报"读不了"更有用）
        for n in sorted(zf.namelist()):
            if n.startswith("xl/worksheets/") and n.endswith(".xml"):
                out.append((n.rsplit("/", 1)[-1], n))
    return out


def _cell_value(c, shared: list) -> str:
    ctype = c.get("t") or ""
    if ctype == "inlineStr":
        for child in c:
            if _localname(child.tag) == "is":
                return _text_of(child)
        return ""
    v = None
    for child in c:
        if _localname(child.tag) == "v":
            v = child.text or ""
            break
    if v is None:
        return ""                    # 公式无缓存值：按空处理，不猜
    if ctype == "s":
        try:
            return shared[int(v)]
        except (ValueError, IndexError):
            return ""
    if ctype == "b":
        return "TRUE" if v == "1" else "FALSE"
    return v


def read_sheet_rows(zf: zipfile.ZipFile, path: str, shared: list,
                    budget: list, max_rows: int = 200000) -> list:
    """读一张工作表为二维字符串（按行、按列补齐）。

    ⚠️ **必须按 `r` 属性还原行号、把中间的空行补出来**，不能用"收集到的行"
    直接紧凑输出：那样第 3 行会挤到第 2 行，之后所有行号都错位 ——
    而 `Entry.src_line` 正是拿它报"第几行有问题"，错位会让用户按提示
    去 Excel 里找**找到别的行**。行号是权威信息，不能因为稀疏就丢掉。
    """
    root = ET.fromstring(_read_member(zf, path, budget))
    rows: dict = {}
    width = 0
    for cell in root.iter():
        if _localname(cell.tag) != "c":
            continue
        ref = cell.get("r") or ""
        r = _row_index(ref)
        ci = _col_index(ref)
        if r <= 0:
            continue
        if r > max_rows:
            raise XlsxError("工作表行数超过 %d，疑似异常文件" % max_rows)
        val = _cell_value(cell, shared)
        rows.setdefault(r, {})
        # 同一格重复出现时以**非空**为准（有的写入器会写一个空占位）
        if val or ci not in rows[r]:
            rows[r][ci] = val
        if ci + 1 > width:
            width = ci + 1
    if not rows:
        return []
    top = max(rows)
    return [[rows.get(r, {}).get(i, "") for i in range(width)]
            for r in range(1, top + 1)]


def sheet_names(path) -> list:
    """工作簿内全部工作表名（按工作簿顺序）。"""
    with zipfile.ZipFile(str(path)) as zf:
        budget = [0]
        return [n for n, _ in _sheet_targets(zf, budget)]


def read_sheet(path, name: str = "", max_rows: int = 200000) -> list:
    """读一张工作表（``name`` 为空则取第一张），返回二维字符串。"""
    with zipfile.ZipFile(str(path)) as zf:
        if "xl/workbook.xml" not in zf.namelist():
            raise XlsxError("这不是有效的 xlsx（缺 xl/workbook.xml）；"
                            "若是旧版 .xls，请在 Excel 里另存为 .xlsx 再导入")
        budget = [0]
        shared = _parse_shared_strings(zf, budget)
        targets = _sheet_targets(zf, budget)
        if not targets:
            raise XlsxError("工作簿里没有任何工作表")
        if name:
            hit = [p for n, p in targets if n == name]
            if not hit:
                raise XlsxError("找不到工作表「%s」（现有：%s）"
                                % (name, "、".join(n for n, _ in targets)))
            target = hit[0]
        else:
            target = targets[0][1]
        return read_sheet_rows(zf, target, shared, budget, max_rows)


def read_all(path, max_rows: int = 200000) -> list:
    """读全部工作表，返回 ``[(表名, 二维字符串)]``。"""
    out = []
    for n in sheet_names(path):
        out.append((n, read_sheet(path, n, max_rows)))
    return out
