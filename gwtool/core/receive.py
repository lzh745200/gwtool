# -*- coding: utf-8 -*-
"""收文登记台账：来文字号解析、字段校验、统计聚合、CSV/XLSX 导出。

为什么要它
----------
公文实务是**收 + 发**两个方向，而本产品此前只有发文（`registry.py` +
`dispatch_register`）："我发出去的"管得住，"别人发给我的"完全没有。机关办公室
的日常工作量里，收文办理（签收、拟办、批办、承办、催办、归档）通常**大于**发文。
本模块补齐这一半。纯逻辑层，不含 UI，与 dao 的 `receive_register` 表配合。

常量共享而非各自维护
--------------------
密级、紧急程度、文种三组取值直接引用 `registry.py` 的既有定义——两处各写一份
清单必然日久漂移（改了一处忘了另一处，表现为"发文能选、收文选不到"）。
只有**收文特有的**办理状态与保管期限在本模块定义。
"""
from __future__ import annotations

import csv
import re
from dataclasses import asdict
from datetime import date

from ..db import dao
from .registry import SECRET_LEVELS, URGENCY_LEVELS, doc_types

# 收文办理流程：签收 → 拟办 → 批办 → 承办 → 办结 → 归档。
# 与发文的（拟稿/核稿/签发/已印发/已归档）是两条不同的流程，不可混用。
STATUSES = ("签收", "拟办", "批办", "承办", "已办结", "已归档")
# 办结状态集合：判断"是否还需督办"时用
CLOSED_STATUSES = ("已办结", "已归档")
# 保管期限（依据《机关文件材料归档范围和文书档案保管期限规定》的三档）
RETENTION_LEVELS = ("", "永久", "30年", "10年")

# 来文字号解析。比发文更宽松：
#   · 末尾的"号"可以省略（有些单位只写"〔2026〕12"）；
#   · 括号可能是六角/方/圆括号（外单位来文格式不受本单位约束）。
# 解析不出时**不报错**——来文可能是自由文本（如"内部明电"），
# 强行要求格式化只会逼用户填假数据。
_INCOMING_RE = re.compile(
    r"^\s*(?P<prefix>.+?)[〔\[［（(](?P<year>\d{4})[〕\]］）)]"
    r"\s*第?\s*(?P<serial>\d+)\s*号?\s*$")

# 导出列（顺序即列顺序）
EXPORT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("reg_no", "收文登记号"),
    ("incoming_no", "来文字号"),
    ("title", "标题"),
    ("doc_type", "文种"),
    ("from_org", "来文机关"),
    ("main_send", "主送"),
    ("cc", "抄送"),
    ("secret_level", "密级"),
    ("urgency", "紧急程度"),
    ("receive_date", "收到日期"),
    ("doc_date", "来文成文日期"),
    ("pages", "页数"),
    ("copies", "份数"),
    ("propose", "拟办意见"),
    ("instruction", "领导批示"),
    ("handler_dept", "承办部门"),
    ("handler", "承办人"),
    ("due_date", "应办结日期"),
    ("done_date", "办结日期"),
    ("result", "办理结果"),
    ("status", "状态"),
    ("archive_no", "档号"),
    ("retention", "保管期限"),
    ("archive_date", "归档日期"),
    ("remark", "备注"),
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def doc_types_available() -> list[str]:
    """可选文种：复用 15 种法定文种，避免与本模块各写一份清单。"""
    return list(doc_types())


def parse_incoming_no(text: str) -> tuple[str, str, int]:
    """拆解来文字号 -> (机关代字, 年份, 序号)；识别不出返回 ("", "", 0)。

    不做"格式不规范"的报错：外单位的字号格式我们无权要求，能拆出来就拆，
    拆不出来就按自由文本保存。
    """
    m = _INCOMING_RE.match(text or "")
    if not m:
        return "", "", 0
    return m.group("prefix").strip(), m.group("year"), int(m.group("serial"))


def year_of(r: dao.Receive) -> str:
    """登记记录所属年度：优先收到日期，其次来文字号里的年份。"""
    if (r.receive_date or "")[:4].isdigit():
        return r.receive_date[:4]
    _prefix, year, _serial = parse_incoming_no(r.incoming_no)
    return year


def is_closed(r: dao.Receive) -> bool:
    """是否已办结（含已归档）——不需要再督办。"""
    return (r.status or "") in CLOSED_STATUSES or bool((r.done_date or "").strip())


def days_left(r: dao.Receive, today: str = "") -> "int | None":
    """距应办结日期的天数：负数表示已逾期；无期限或已办结返回 None。"""
    import datetime as _dt

    if is_closed(r) or not (r.due_date or "").strip():
        return None
    base = today or _dt.date.today().isoformat()
    try:
        return (_dt.date.fromisoformat(r.due_date)
                - _dt.date.fromisoformat(base)).days
    except ValueError:
        return None


def is_overdue(r: dao.Receive, today: str = "") -> bool:
    d = days_left(r, today)
    return d is not None and d < 0


def summarize(rows: list, today: str = "") -> dict:
    """台账概览：总数、总份数、各状态/文种/来文机关件数、年度、超期件数。"""
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    by_org: dict[str, int] = {}
    years: set[str] = set()
    overdue = 0
    pending = 0
    for r in rows:
        by_status[r.status or "未填写"] = by_status.get(r.status or "未填写", 0) + 1
        by_type[r.doc_type or "未填写"] = by_type.get(r.doc_type or "未填写", 0) + 1
        by_org[r.from_org or "未填写"] = by_org.get(r.from_org or "未填写", 0) + 1
        y = year_of(r)
        if y:
            years.add(y)
        if not is_closed(r):
            pending += 1
            if is_overdue(r, today):
                overdue += 1
    return {
        "total": len(rows),
        "copies": sum(int(r.copies or 0) for r in rows),
        "pages": sum(int(r.pages or 0) for r in rows),
        "by_status": dict(sorted(by_status.items(), key=lambda kv: -kv[1])),
        "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
        "by_org": dict(sorted(by_org.items(), key=lambda kv: -kv[1])),
        "years": sorted(years, reverse=True),
        "pending": pending,
        "overdue": overdue,
    }


def validate(r: dao.Receive) -> list[str]:
    """登记前的字段校验，返回问题列表（空列表表示可保存）。"""
    problems: list[str] = []
    if not (r.title or "").strip() and not (r.incoming_no or "").strip():
        problems.append("标题与来文字号至少填写一项")
    if r.secret_level and r.secret_level not in SECRET_LEVELS:
        problems.append(f"密级取值异常：{r.secret_level}")
    if r.urgency and r.urgency not in URGENCY_LEVELS:
        problems.append(f"紧急程度取值异常：{r.urgency}")
    if r.status and r.status not in STATUSES:
        problems.append(f"状态取值异常：{r.status}")
    if r.retention and r.retention not in RETENTION_LEVELS:
        problems.append(f"保管期限取值异常：{r.retention}")
    if r.doc_type and r.doc_type not in doc_types_available():
        # 不阻断保存（历史上可能有非 15 种法定文种的来文），仅提示
        problems.append(f"文种「{r.doc_type}」不在 15 种法定文种内，请核对")
    for field_name, label in (("receive_date", "收到日期"),
                              ("doc_date", "来文成文日期"),
                              ("due_date", "应办结日期"),
                              ("done_date", "办结日期"),
                              ("archive_date", "归档日期")):
        value = (getattr(r, field_name) or "").strip()
        if value and not _DATE_RE.match(value):
            problems.append(f"{label}应为 YYYY-MM-DD 格式：{value}")
    if int(r.pages or 0) < 0 or int(r.copies or 0) < 0:
        problems.append("页数与份数不能为负")
    recv = (r.receive_date or "").strip()
    for field_name, label in (("due_date", "应办结日期"),
                              ("done_date", "办结日期"),
                              ("doc_date", "来文成文日期")):
        value = (getattr(r, field_name) or "").strip()
        if recv and value and label != "来文成文日期" and value < recv:
            problems.append(f"{label}早于收到日期")
    return problems


def export_csv(rows: list, out_path: str) -> int:
    """导出为 CSV（UTF-8-BOM），返回写出的行数。

    用 utf-8-sig 而非 utf-8：Excel 打开无 BOM 的 UTF-8 CSV 会把中文显示成乱码。
    """
    with open(out_path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([label for _key, label in EXPORT_COLUMNS])
        for r in rows:
            data = asdict(r)
            writer.writerow([data.get(key, "") for key, _label in EXPORT_COLUMNS])
    return len(rows)


def export_xlsx(rows: list, out_path: str, with_stats: bool = False) -> int:
    """导出为 xlsx（可选附「统计」工作表），返回写出的数据行数。"""
    from .xlsx import Sheet, write_xlsx

    headers = [label for _key, label in EXPORT_COLUMNS]
    body = []
    for r in rows:
        data = asdict(r)
        body.append([data.get(key, "") for key, _label in EXPORT_COLUMNS])
    # 来文字号与收文登记号的语义是"编码"而非"数值"：强制文本，
    # 否则 Excel 会把长数字串显示成科学计数法
    text_cols = {i for i, (key, _label) in enumerate(EXPORT_COLUMNS)
                 if key in ("incoming_no", "reg_no")}
    sheets = [Sheet(name="收文台账", headers=headers, rows=body,
                    text_columns=text_cols)]
    if with_stats:
        summ = summarize(rows)
        stat_rows = [["按来文机关", k, v] for k, v in summ["by_org"].items()]
        stat_rows += [["按文种", k, v] for k, v in summ["by_type"].items()]
        stat_rows += [["按状态", k, v] for k, v in summ["by_status"].items()]
        stat_rows += [["合计", "总件数", summ["total"]],
                      ["合计", "待办件数", summ["pending"]],
                      ["合计", "已逾期", summ["overdue"]]]
        sheets.append(Sheet(name="统计", headers=["维度", "取值", "数量"],
                            rows=stat_rows))
    return write_xlsx(out_path, sheets)


def format_reg_no(prefix: str, year, serial: int) -> str:
    """组装规范收文登记号，如 format_reg_no("收", 2026, 12)。"""
    return f"{(prefix or '收').strip()}〔{int(year)}〕{int(serial)}号"


def next_reg_no(prefix: str = "收", year=None) -> str:
    """给出下一个收文登记号，形如「收〔2026〕12号」。

    与发文字号取号同源（按年度流水递增、避免撞号），但**独立计数**——
    收文与发文是两本账，共用一套流水会让两边都乱。
    """
    y = str(year or date.today().year)
    serial = dao.max_reg_no_serial((prefix or "收").strip(), y) + 1
    return format_reg_no(prefix, y, serial)
