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


def suggest_batch(rows: list, prefix: str, year: str = "") -> list:
    """为一批记录预分配档号（不改库，供界面预览）。"""
    y = year or (rows[0].receive_date[:4] if rows and rows[0].receive_date else
                 str(date.today().year))
    used = 0
    for r in rows:
        if (r.archive_no or "").strip():
            continue          # 已有档号的不重编，避免打乱既有卷
        used += 1
        yield r, next_archive_no(prefix, y, used)


def batch_archive(rows: list, prefix: str, retention: str,
                  archive_date: str = "", year: str = "") -> dict:
    """批量归档：写档号、保管期限与归档日期，状态置为「已归档」。

    只处理**尚无档号**的记录：已有档号的重复编目会让档案号错乱，
    而档案号一旦错乱，实物卷与台账就再也对不上了。
    """
    stamp = archive_date or date.today().isoformat()
    done = 0
    skipped = 0
    for r, no in suggest_batch(rows, prefix, year=year):
        r.archive_no = no
        r.retention = retention
        r.archive_date = stamp
        r.status = "已归档"
        dao.update_receive(r)
        done += 1
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
