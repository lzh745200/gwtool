# -*- coding: utf-8 -*-
"""公文归档与保管期限。

依据《机关文件材料归档范围和文书档案保管期限规定》，公文办结后应当归档，
并标注保管期限（**永久 / 30 年 / 10 年**）。

此前 `receive_register` 里的"已归档"只是一个状态字符串 —— 没有档号、没有
保管期限、没有移交清单，实务上等于没归档：档案室不会收一份只有状态标记的
台账。本模块把这一段补齐：批量编号、标注期限、产出可交接的移交清单。

为什么清单用 DOCX 而不是 CSV
----------------------------
移交清单是**要签字盖章随卷走**的正式文书，必须是规范公文体式。
因此复用 `report.py` 的 DocTree → docxgen 通路（继承既定字体、行距、页边距），
而不是另起一套排版——两份格式不一致的文书在归档环节会被退回。
"""
from __future__ import annotations

from datetime import date

from ..db import dao
from . import receive

# 保管期限的三档（与《规定》一致）。空串表示尚未鉴定。
RETENTION_CHOICES = ("永久", "30年", "10年")


def list_archivable(year: str = "", include_archived: bool = False) -> list:
    """列出待归档（或全部）的收文记录。

    只把**已办结**的算作可归档：在办件归档会造成"卷内缺件"，
    比不归档更难收拾。
    """
    rows = dao.list_receive(year=year, limit=100000)
    out = []
    for r in rows:
        archived = (r.status or "") == "已归档" or (r.archive_no or "").strip()
        if archived and not include_archived:
            continue
        if not archived and not receive.is_closed(r):
            continue
        out.append(r)
    out.sort(key=lambda r: ((r.done_date or r.receive_date or ""), r.id))
    return out


def next_archive_no(prefix: str, year: str, seq: int) -> str:
    """生成档号，形如「XX局2026-0007」。

    档号只要求**在本全宗内唯一且有序**，不同单位的具体编法差异很大，
    因此这里给出一个稳妥的默认格式，前缀可自定。
    """
    return f"{(prefix or '').strip()}{year}-{int(seq):04d}"


def _row_year(r, default: str) -> str:
    """记录所属年度：优先收到日期，其次办结日期/来文日期，最后回退默认年。

    跨年批次**不能**统一用首行的年份（旧实现的 `rows[0].receive_date[:4]`）：
    12 月办结、次年 1 月归档的件会被编到错误年度，档号与年度卷对不上。
    """
    for raw in (getattr(r, "receive_date", ""), getattr(r, "done_date", ""),
                getattr(r, "doc_date", "")):
        head = (raw or "")[:4]
        if head.isdigit():
            return head
    return default


def suggest_batch(rows: list, prefix: str, year: str = "") -> list:
    """为一批记录预分配档号（不改库，供界面预览）。

    两条硬约束（v1.9 修 P1-24）：
      1. **起始序号接续本年度已用最大号**（`dao.max_archive_seq`）。旧实现每批
         从 0001 重新开始，同一年度归档第二批必然与第一批重号；
      2. **年度按每条记录自己的日期算**，不取 `rows[0]` 的年份；只有调用方显式
         传 year 时才整批用该年度（导入历史卷等场景）。
    """
    default_year = str(date.today().year)
    if rows:
        default_year = _row_year(rows[0], default_year)
    next_seq: dict[str, int] = {}

    def take(y: str) -> str:
        if y not in next_seq:
            next_seq[y] = dao.max_archive_seq(prefix, y) + 1
        no = next_archive_no(prefix, y, next_seq[y])
        next_seq[y] += 1
        return no

    for r in rows:
        if (r.archive_no or "").strip():
            continue          # 已有档号的不重编，避免打乱既有卷
        y = (year or "").strip() or _row_year(r, default_year)
        yield r, take(y)


def batch_archive(rows: list, prefix: str, retention: str,
                  archive_date: str = "", year: str = "") -> dict:
    """批量归档：写档号、保管期限与归档日期，状态置为「已归档」。

    只处理**尚无档号**的记录：已有档号的重复编目会让档案号错乱，
    而档案号一旦错乱，实物卷与台账就再也对不上了。

    整批**单事务**提交（`dao.update_receive_many`）：归档是"这一批一起成立"
    的语义 —— 移交清单写着 50 件、台账只落 30 件，比整批失败更难收拾。
    逐行提交还会在千件级别退化成千次 fsync。
    """
    stamp = archive_date or date.today().isoformat()
    pending = []
    for r, no in suggest_batch(rows, prefix, year=year):
        r.archive_no = no
        r.retention = retention
        r.archive_date = stamp
        r.status = "已归档"
        pending.append(r)
    done = dao.update_receive_many(pending)
    skipped = len(rows) - done
    return {"archived": done, "skipped": skipped, "date": stamp,
            "retention": retention}


def retention_summary(rows: list) -> dict:
    """按保管期限统计（未鉴定的归入「未标注」）。"""
    out: dict[str, int] = {}
    for r in rows:
        key = (r.retention or "").strip() or "未标注"
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def build_manifest_tree(rows: list, year: str = "", org: str = "") -> object:
    """把归档移交清单组织成 DocTree（复用公文体式渲染）。"""
    from .model import HEADING, TABLE, Block, DocTree

    y = year or (rows[0].receive_date[:4] if rows and rows[0].receive_date
                 else str(date.today().year))
    title = f"{y}年度收文归档移交清单"
    tree = DocTree(title=title)
    tree.blocks.append(Block(type=HEADING, level=1, text=title))
    head = (f"移交单位：{org or '（本单位）'}　　"
            f"移交日期：{date.today().isoformat()}　　"
            f"合计 {len(rows)} 件")
    tree.blocks.append(Block(text=head))

    data = [["序号", "档号", "来文字号", "来文机关", "标题",
             "办结日期", "保管期限"]]
    for i, r in enumerate(rows, start=1):
        data.append([str(i), r.archive_no or "", r.incoming_no or "",
                     r.from_org or "", (r.title or "")[:40],
                     r.done_date or "", r.retention or ""])
    tree.blocks.append(Block(type=TABLE, rows=data))

    summary = retention_summary(rows)
    tail = "　".join(f"{k} {v} 件" for k, v in summary.items())
    tree.blocks.append(Block(text=f"保管期限分布：{tail}"))
    tree.blocks.append(Block(text="移交人（签字）：　　　　　　接收人（签字）："))
    return tree


def export_manifest(rows: list, out_path: str, year: str = "",
                    org: str = "", tpl=None) -> str:
    """导出归档移交清单为 DOCX（复用公文排版通路）。"""
    from .docxgen import generate_docx
    from .template import default_template

    tree = build_manifest_tree(rows, year=year, org=org)
    return generate_docx([tree], tpl or default_template(), out_path)


def export_manifest_xlsx(rows: list, out_path: str) -> int:
    """导出归档清单为 xlsx（便于逐年汇总与筛选）。"""
    from .xlsx import write_table

    data = [[str(i), r.archive_no or "", r.incoming_no or "", r.from_org or "",
             r.title or "", r.done_date or "", r.retention or ""]
            for i, r in enumerate(rows, start=1)]
    return write_table(out_path,
                       ["序号", "档号", "来文字号", "来文机关", "标题",
                        "办结日期", "保管期限"],
                       data, sheet_name="归档移交清单")
