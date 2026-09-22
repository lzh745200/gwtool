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
# 标准写法是六角括号〔〕，但实际录入中圆括号、全角圆括号、半角/全角方括号
# 都常见，一并认下。
_DOC_NO_RE = re.compile(r"^\s*(?P<prefix>.+?)[〔\[［（(](?P<year>\d{4})[〕\]］）)]"
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


# 封面上的落款日期是给**人看**的，写法五花八门：`2026年8月`、`2026年8月15日`、
# `2026-08-15`、`2026.8.5`。台账要的是 `YYYY-MM-DD`，两边都不该妥协 —— 用户
# 不该为了登记台账去改封面写法，台账也不该存一个日期列里塞不进去的字符串
# （`year_of` 与逐月统计都按 `YYYY-MM-DD` 取前 4/前 2 位切分）。
_DATE_RE = re.compile(r"(?P<y>\d{4})\s*[年\-/.]\s*(?P<m>\d{1,2})"
                      r"(?:\s*[月\-/.]\s*(?P<d>\d{1,2}))?\s*日?")
# 只认年份的情况（"2026年"）：按年初归一，而不是丢弃 —— 丢了会让整条记录
# 落不进任何年度分组，用户会觉得"登记了却统计不到"。
_YEAR_ONLY_RE = re.compile(r"^\s*(?P<y>\d{4})\s*年?\s*$")


def normalize_date(raw: str) -> str:
    """把中文/点号分隔的日期归一为 `YYYY-MM-DD`；识别不了返回空串。"""
    text = (raw or "").strip()
    if not text:
        return ""
    m = _DATE_RE.search(text)
    if m:
        y, mo, d = m.group("y"), int(m.group("m")), m.group("d")
        if not (1 <= mo <= 12):
            return ""
        day = int(d) if d else 1
        if not (1 <= day <= 31):
            return ""
        return f"{y}-{mo:02d}-{day:02d}"
    m2 = _YEAR_ONLY_RE.match(text)
    if m2:
        return f"{m2.group('y')}-01-01"
    return ""


# 从标题尾部推断文种（"关于开展XX的通知" → 通知）。只认**完全匹配后缀**的
# 情形，不做模糊包含判断：标题里出现"通知"二字不代表它是通知（"关于印发
# 《通知管理办法》的通知"只有最后一个才是文种）。
def _guess_doc_type(title: str) -> str:
    text = (title or "").strip().rstrip("。.·")
    for kind in sorted(doc_types(), key=len, reverse=True):
        if text.endswith(kind):
            return kind
    if text.endswith("会议纪要"):
        return "纪要"
    return ""


def from_compile(title: str, cover=None, doc_id: int = 0,
                 status: str = "拟稿") -> dao.Dispatch:
    """把一次汇编的封面信息转成一条待登记的台账记录。

    只做**映射与归一**，不写库 —— 落库时机由调用方决定（用户确认后再写）。
    三条宽容原则，都是为了避免"登记失败反噬生成成功"：
      1. 文种留空时从标题后缀推断，推不出就留空（不硬塞一个错文种）；
      2. 发文字号格式不合规时**丢弃**而不是原样写入 —— 原样写进去会被
         `validate` 判为异常，把可用记录变成不可保存记录；
      3. 日期识别不了时留空（`validate` 允许成文日期为空）。
    最后**必须**过一遍 `validate`，返回值若有问题说明调用方给了异常输入，
    由它决定提示还是放弃；本函数不吞错也不抛错。
    """
    cover = cover if cover is not None else _EMPTY_COVER

    def _get(name: str) -> str:
        return (getattr(cover, name, "") or "").strip()

    text = (title or "").strip() or _get("title") or "汇编成果"
    doc_no = _get("doc_no")
    if doc_no and not parse_doc_no(doc_no)[0]:
        doc_no = ""                     # 见上述原则 2
    secret = _get("secret_level")
    urgency = _get("urgency")
    kind = _get("doc_type") or _guess_doc_type(text)

    return dao.Dispatch(
        doc_no=doc_no,
        title=text,
        doc_type=kind if kind in doc_types() else "",
        org=_get("org"),
        secret_level=secret if secret in SECRET_LEVELS else "公开",
        urgency=urgency if urgency in URGENCY_LEVELS else "",
        sign_date=normalize_date(_get("date")),
        status=status if status in STATUSES else "拟稿",
        doc_id=int(doc_id or 0),
        remark="由「一键汇编」生成后登记",
    )


class _EmptyCover:
    """`from_compile` 的缺省封面：全空，让所有 `.xxx` 取值都回落为 ""。"""

    def __getattr__(self, _name):
        return ""


_EMPTY_COVER = _EmptyCover()


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


# CSV 公式注入防御：Excel/WPS/LibreOffice 会把以这四个字符起首的单元格内容
# 当作**公式**执行。台账里的标题、备注来自用户输入（或来文原样），一旦有人
# 把 `=cmd|'/c calc'!A1` 之类写进备注，导出文件在别人机器上打开就会触发。
# 前置一个单引号是 CSV/Excel 通行的中和写法：Excel 会显示为文本，值本身不变。
# 只在**导出时**加，库里的原值不动，往返导入后仍是原值。
_CSV_FORMULA_PREFIX = ("=", "+", "-", "@")


def csvsafe(value):
    """把单元格值中和为不会被 Excel 当公式执行的文本。

    ⚠ 只对**以危险字符起首**的字符串生效：`-5` 这种负数如果被加引号会变成
    文本，破坏数值列。故纯数字/纯日期等常规值原样返回。
    """
    if not isinstance(value, str):
        return value
    if not value or value[0] not in _CSV_FORMULA_PREFIX:
        return value
    # 纯数值（含负数、小数、科学计数）保持原样：它不是攻击载荷
    try:
        float(value)
    except ValueError:
        return "'" + value
    return value


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
            writer.writerow([csvsafe(data.get(key, "")) for key, _label in EXPORT_COLUMNS])
    return len(rows)


def export_xlsx(rows: list[dao.Dispatch], out_path: str,
                with_stats: bool = False) -> int:
    """导出台账为 xlsx，返回写出的数据行数（不含表头）。

    与 `export_csv` **并存而非替代**：CSV 便于脚本处理、老用户也已在用；
    xlsx 则解决 CSV 的三个硬局限 ——
      1. 无列宽与表头样式（用户每次拿到都要手工重排）；
      2. 无多工作表（统计结果只能挤进同一张表）；
      3. 长数字被 Excel 转成科学计数法。
    with_stats=True 时额外产出一张「统计」工作表，正是多表能力的用武之地。
    """
    from .xlsx import Sheet, write_xlsx

    headers = [label for _key, label in EXPORT_COLUMNS]
    body = []
    for r in rows:
        data = asdict(r)
        body.append([data.get(key, "") for key, _label in EXPORT_COLUMNS])
    # 发文字号的语义是"编码"而非"数值"：强制按文本写入，
    # 否则 Excel 会把它显示成 5.01234E+13 之类
    text_cols = {i for i, (key, _label) in enumerate(EXPORT_COLUMNS)
                 if key == "doc_no"}
    sheets = [Sheet(name="发文台账", headers=headers, rows=body,
                    text_columns=text_cols)]

    if with_stats:
        summ = summarize(rows)
        stat_rows = [["按文种", k, v] for k, v in summ["by_type"].items()]
        stat_rows += [["按状态", k, v] for k, v in summ["by_status"].items()]
        stat_rows += [["合计", "总件数", summ["total"]],
                      ["合计", "总页数", summ["pages"]],
                      ["合计", "总印数", summ["copies"]]]
        sheets.append(Sheet(name="统计", headers=["维度", "取值", "数量"],
                            rows=stat_rows))
    return write_xlsx(out_path, sheets)


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
