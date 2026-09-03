# -*- coding: utf-8 -*-
"""发文登记台账：发文字号解析与自动取号、统计聚合、CSV 导出。

纯逻辑层，不含 UI。应用原先只覆盖"写作与汇编"，公文发出后的登记、查询、
统计完全缺失；本模块补上这一段，与 dao 的 dispatch_register 表配合使用。

导出用标准库 csv 写 UTF-8-BOM（Excel 双击即可正确显示中文），不引入
openpyxl——本项目铁律是完全离线单机运行，多一个依赖就多一分安装包体积
与麒麟适配风险，而台账导出用 CSV 已经够用。
"""
from __future__ import annotations

import csv
import re
from dataclasses import asdict
from datetime import date

from ..db import dao
from .skeletons import kinds as skeleton_kinds

# 密级（GB/T 7156 及党政机关常用口径）
SECRET_LEVELS = ("公开", "内部", "秘密", "机密", "绝密")
# 紧急程度
URGENCY_LEVELS = ("", "平急", "急件", "特急", "特提")
# 办理状态流转
STATUSES = ("拟稿", "核稿", "签发", "已印发", "已归档")

# 发文字号形如「×政办发〔2026〕12号」：机关代字 + 〔年份〕 + 序号 + 号。
# 标准写法是六角括号〔〕，但实际录入中圆括号、全角圆括号、方括号都常见，一并认下。
_DOC_NO_RE = re.compile(r"^\s*(?P<prefix>.+?)[〔\[（(](?P<year>\d{4})[〕\]）)]"
                        r"\s*(?P<serial>\d+)\s*号\s*$")

# CSV 导出的列与中文表头（顺序即列顺序）
EXPORT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("doc_no", "发文字号"),
    ("title", "标题"),
    ("doc_type", "文种"),
    ("org", "发文机关"),
    ("main_send", "主送"),
    ("cc", "抄送"),
    ("secret_level", "密级"),
    ("urgency", "紧急程度"),
    ("sign_date", "成文日期"),
    ("print_date", "印发日期"),
    ("pages", "页数"),
    ("copies", "印数"),
    ("drafter", "拟稿人"),
    ("reviewer", "核稿人"),
    ("approver", "签发人"),
    ("status", "状态"),
    ("remark", "备注"),
)


def doc_types() -> list[str]:
    """可选文种：直接复用 15 种法定文种骨架的名称，避免两处清单漂移。"""
    return list(skeleton_kinds())


def parse_doc_no(doc_no: str) -> tuple[str, str, int]:
    """拆解发文字号 -> (机关代字, 年份, 序号)。无法识别时返回 ("", "", 0)。"""
    m = _DOC_NO_RE.match(doc_no or "")
    if not m:
        return "", "", 0
    return m.group("prefix").strip(), m.group("year"), int(m.group("serial"))


def format_doc_no(prefix: str, year: str | int, serial: int) -> str:
    """组装规范发文字号，如 format_doc_no("×政办发", 2026, 12)。"""
    return f"{(prefix or '').strip()}〔{int(year)}〕{int(serial)}号"


def next_serial(prefix: str, year: str | int | None = None) -> int:
    """某机关代字某年度的下一个发文序号（该年度尚无记录时为 1）。"""
    y = str(year or date.today().year)
    return dao.max_doc_no_serial((prefix or "").strip(), y) + 1


def next_doc_no(prefix: str, year: str | int | None = None) -> str:
    """直接给出下一个可用发文字号。"""
    y = str(year or date.today().year)
    return format_doc_no(prefix, y, next_serial(prefix, y))


def year_of(d: dao.Dispatch) -> str:
    """登记记录所属年度：优先成文日期，其次发文字号里的年份。"""
    if (d.sign_date or "")[:4].isdigit():
        return d.sign_date[:4]
    _prefix, year, _serial = parse_doc_no(d.doc_no)
    return year


def summarize(rows: list[dao.Dispatch]) -> dict[str, object]:
    """台账概览：总件数、总印数、各状态与各文种件数、涉及年度。"""
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    years: set[str] = set()
    for r in rows:
        by_status[r.status or "未填写"] = by_status.get(r.status or "未填写", 0) + 1
        by_type[r.doc_type or "未填写"] = by_type.get(r.doc_type or "未填写", 0) + 1
        y = year_of(r)
        if y:
            years.add(y)
    return {
        "total": len(rows),
        "copies": sum(int(r.copies or 0) for r in rows),
        "pages": sum(int(r.pages or 0) for r in rows),
        "by_status": dict(sorted(by_status.items(), key=lambda kv: -kv[1])),
        "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
        "years": sorted(years, reverse=True),
    }


def export_csv(rows: list[dao.Dispatch], out_path: str) -> int:
    """导出台账为 CSV（UTF-8-BOM），返回写出的行数。

    用 utf-8-sig 而非 utf-8：Excel 打开无 BOM 的 UTF-8 CSV 会把中文显示成乱码，
    而台账导出后基本都是拿去 Excel 里看的。
    """
    with open(out_path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([label for _key, label in EXPORT_COLUMNS])
        for r in rows:
            data = asdict(r)
            writer.writerow([data.get(key, "") for key, _label in EXPORT_COLUMNS])
    return len(rows)


def validate(d: dao.Dispatch) -> list[str]:
    """登记前的字段校验，返回问题列表（空列表表示可保存）。"""
    problems: list[str] = []
    if not (d.title or "").strip():
        problems.append("标题不能为空")
    if (d.doc_no or "").strip():
        prefix, year, serial = parse_doc_no(d.doc_no)
        if not prefix:
            problems.append("发文字号格式不规范，应形如「×政办发〔2026〕12号」")
        else:
            if not (1949 <= int(year) <= 2999):
                problems.append(f"发文字号年份异常：{year}")
            if serial <= 0:
                problems.append("发文字号序号应为正整数")
    if d.secret_level and d.secret_level not in SECRET_LEVELS:
        problems.append(f"密级取值异常：{d.secret_level}")
    if d.urgency and d.urgency not in URGENCY_LEVELS:
        problems.append(f"紧急程度取值异常：{d.urgency}")
    if d.status and d.status not in STATUSES:
        problems.append(f"状态取值异常：{d.status}")
    for field_name, label in (("sign_date", "成文日期"), ("print_date", "印发日期")):
        value = (getattr(d, field_name) or "").strip()
        if value and not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
            problems.append(f"{label}应为 YYYY-MM-DD 格式：{value}")
    if d.sign_date and d.print_date and d.print_date < d.sign_date:
        problems.append("印发日期早于成文日期")
    if int(d.pages or 0) < 0 or int(d.copies or 0) < 0:
        problems.append("页数与印数不能为负")
    return problems
