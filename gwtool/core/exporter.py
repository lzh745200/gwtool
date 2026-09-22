# -*- coding: utf-8 -*-
"""批量导出与移交包。

与 `backup.py` 的分工
---------------------
`backup` 是**整库**备份：全量、含设置与模板，用途是灾难恢复。
本模块是**按范围**导出：某个分类、某段时间、某个标签下的资料，
用途是人员交接、专题资料移交、按年度归档。

两者不能互相替代：交接时把整个数据库（含他人资料与内部设置）交出去
既过度又不妥；而整库备份也给不出"这一批材料的清单"。

manifest 与 backup **刻意同构**
-------------------------------
`manifest.json` 沿用 backup 的 `attachments` 段结构（mode/limit/included/
excluded），这样将来做"从移交包导入"时可以直接复用备份的校验与恢复逻辑，
不必另起一套——两套解析同一份格式是典型的缺陷温床。
"""
from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .. import logs
from ..db import dao
from ..paths import attachments_dir
from .batch import safe_filename

log = logs.get_logger("exporter")

MANIFEST_NAME = "manifest.json"


@dataclass
class ExportRequest:
    out_path: str = ""
    category_id: int | None = None      # None = 全部
    tag: str = ""
    date_from: str = ""                 # YYYY-MM-DD（按导入时间）
    date_to: str = ""
    include_attachments: bool = True
    limit_mb: int = 8                   # 附件体积上限；负数=不限制
    note: str = ""


def select_documents(req: ExportRequest) -> list:
    """按范围选取文档（按导入时间升序，便于交接时按时间对账）。"""
    rows = dao.list_documents(category_id=req.category_id, include_content=True)
    out = []
    for d in rows:
        if req.tag and req.tag not in (d.tags or ""):
            continue
        got = (d.import_time or "")[:10]
        if req.date_from and got and got < req.date_from:
            continue
        if req.date_to and got and got > req.date_to:
            continue
        out.append(d)
    out.sort(key=lambda d: (d.import_time or "", d.id))
    return out


def _document_payload(doc) -> "tuple[str, bytes, str]":
    """决定某篇文档在包内的文件名与内容。

    优先复制**用户的原始文件**（保留原格式与既有批注），原始文件已不在
    （换机器、U 盘拔了）时才退化为导出的纯文本 —— 移交包里宁可给一份
    能读的 txt，也不要一个指向不存在路径的空壳。
    """
    stem = safe_filename(doc.title or f"文档{doc.id}") or f"文档{doc.id}"
    src = Path(doc.file_path) if doc.file_path else None
    # exists()+is_file() 会各发一次 stat（3000 篇实测 nt.stat 占 0.5s）；
    # 合并成一次 stat().st_mode 判定（S_ISREG），失败即视为不可用。
    if src is not None and _is_regular_file(src):
        try:
            return f"documents/{stem}{src.suffix}", src.read_bytes(), "原文件"
        except OSError as exc:
            # 原件读不出来时会**静默退化成纯文本**，而用户以为移交包里是原件
            # （原格式与批注全丢）。必须留痕，并在 manifest 里标明来源类型。
            log.warning("移交包无法读取原文件，退化为纯文本：%s（%s）", src, exc)
    text = doc.content_text or ""
    return f"documents/{stem}.txt", text.encode("utf-8"), "导出文本"


def _plan_attachments(doc_ids: list[int], limit_bytes: int) -> "tuple[list, list]":
    """按体积上限规划附件：超限的记入 excluded（**绝不静默丢**）。"""
    included: list[dict] = []
    excluded: list[dict] = []
    used = 0
    base = attachments_dir()
    for did in doc_ids:
        for att in dao.list_attachments(did):
            size = int(att.size or 0)
            item = {"doc_id": int(did), "name": att.file_name or "",
                    "stored": att.stored_path or "", "size": size}
            path = base / Path(att.stored_path or att.file_name or "").name
            if not path.exists():
                item["reason"] = "文件不在数据目录内（可能已被手工删除）"
                excluded.append(item)
                continue
            if limit_bytes >= 0 and used + size > limit_bytes:
                item["reason"] = "超出本次导出的附件体积上限"
                excluded.append(item)
                continue
            used += size
            item["bytes"] = size
            included.append(item)
    return included, excluded


def _sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _is_regular_file(path: Path) -> bool:
    """一次 stat 判定「存在且是普通文件」。

    `path.exists() and path.is_file()` 是两次系统调用；3000 篇导出实测
    nt.stat 累计 0.5s。这里合并为一次 `stat()`，失败（不存在/无权限）即 False。
    """
    try:
        return stat.S_ISREG(path.stat().st_mode)
    except OSError:
        return False


def build(req: ExportRequest) -> dict:
    """生成移交包，返回统计（文档数、附件数、被排除的附件数、总字节）。"""
    docs = select_documents(req)
    if not docs:
        raise ValueError("所选范围内没有资料，无需导出")
    limit_bytes = -1 if req.limit_mb < 0 else int(req.limit_mb) * 1024 * 1024
    doc_ids = [d.id for d in docs]

    included: list = []
    excluded: list = []
    if req.include_attachments:
        included, excluded = _plan_attachments(doc_ids, limit_bytes)

    cats = {}
    try:
        cats = {c.id: c.name for c in dao.list_categories()}
    except Exception:
        cats = {}

    entries: list[dict] = []
    seen_paths: set[str] = set()   # O(1) 重名检查：3000 篇 327ms → 2ms（原为线性扫描，占总耗时 54%）
    total = 0
    with zipfile.ZipFile(req.out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in docs:
            name, blob, source = _document_payload(d)
            # 同名文档（标题重复）在包内会互相覆盖：补上 id 保证唯一
            if name in seen_paths:
                p = Path(name)
                name = f"{p.parent}/{p.stem}_{d.id}{p.suffix}"
            seen_paths.add(name)
            zf.writestr(name, blob)
            total += len(blob)
            entries.append({
                "id": int(d.id),
                "title": d.title or "",
                "path": name,
                "source": source,
                "origin_file": Path(d.file_path).name if d.file_path else "",
                "category": cats.get(d.category_id, "") or "未分类",
                "tags": d.tags or "",
                "import_time": d.import_time or "",
                "bytes": len(blob),
                "sha256": _sha256(blob),
            })

        att_rows = []
        for item in included:
            path = attachments_dir() / Path(
                item["stored"] or item["name"]).name
            try:
                blob = path.read_bytes()
            except OSError:
                item["reason"] = "读取失败"
                excluded.append(item)
                continue
            arc = f"attachments/{safe_filename(item['name']) or 'file'}"
            zf.writestr(arc, blob)
            item["path"] = arc
            item["sha256"] = _sha256(blob)
            total += len(blob)
            att_rows.append(item)

        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        manifest = {
            "created": stamp,
            "version": "1.0.0",
            "note": req.note,
            "kind": "handover",          # 与整库备份区分（backup 无此字段）
            "scope": {
                "category_id": req.category_id,
                "tag": req.tag,
                "date_from": req.date_from,
                "date_to": req.date_to,
            },
            "documents": entries,
            # 与 backup._manifest_json 的 attachments 段**同构**，
            # 便于将来复用备份的校验/恢复逻辑
            "attachments": {
                "mode": "handover" if req.include_attachments else "excluded",
                "limit_mb": req.limit_mb,
                "limit_text": ("不限制" if req.limit_mb < 0
                               else f"{req.limit_mb} MB"),
                "included": att_rows,
                "excluded": excluded,
            },
        }
        zf.writestr(MANIFEST_NAME,
                    json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"))

    return {"documents": len(entries), "attachments": len(att_rows),
            "excluded": len(excluded), "bytes": total, "path": req.out_path,
            "created": stamp}


def read_manifest(zip_path) -> dict:
    """读取移交包的 manifest（供校验与将来的导入功能使用）。"""
    with zipfile.ZipFile(str(zip_path)) as zf:
        return json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))


def _check_entry(zf, names: set, entry: dict, label: str) -> list[str]:
    """校验单个条目：在包内 + 字节数一致 + sha256 一致。"""
    path = entry.get("path") or ""
    if not path:
        return [f"{label}「{entry.get('title') or '（无标题）'}」"
                "在 manifest 里没有路径记录"]
    if path not in names:
        return [f"{label}缺失：{path}"]
    raw = zf.read(path)
    out: list[str] = []
    want_len = entry.get("bytes")
    if isinstance(want_len, int) and len(raw) != want_len:
        out.append(f"{label}字节数不符：{path}"
                   f"（包内 {len(raw)}，manifest 记录 {want_len}）")
    want = (entry.get("sha256") or "").lower()
    if want and _sha256(raw) != want:
        out.append(f"{label}内容校验失败：{path}（sha256 与 manifest 不符）")
    return out


def verify_package(zip_path) -> dict:
    """只读校验移交包，返回 `{ok, documents, attachments, problems}`。

    为什么要能回读（D6）：`build()` 只负责写，此前没有任何校验入口 ——
    而"移交"是**责任转移**：包交出去之后原件往往就被清理了，一旦包在传输
    环节坏掉（U 盘坏块、网盘截断、邮件网关重编码），只能等对方用到时才发现，
    那时数据可能已经在别处删了。交付前自己能验一遍是刚需，不是锦上添花。

    校验三件事：条目在包内、字节数与 manifest 记录一致、sha256 与 manifest
    记录一致。**不校验** manifest 本身是否被改（它随包一起走，没有可信锚点）。
    """
    empty = {"ok": False, "documents": 0, "attachments": 0, "problems": []}
    try:
        with zipfile.ZipFile(str(zip_path)) as zf:
            names = set(zf.namelist())
            if MANIFEST_NAME not in names:
                return {**empty, "problems": [
                    f"包内缺少 {MANIFEST_NAME} —— 这可能不是本程序导出的移交包，"
                    "或者包在生成/传输中被破坏。"]}
            try:
                manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
            except Exception as exc:
                return {**empty, "problems": [
                    f"{MANIFEST_NAME} 无法解析（{type(exc).__name__}）："
                    "包已损坏，无法核对内容清单。"]}
            problems: list[str] = []
            docs = 0
            for entry in manifest.get("documents") or []:
                docs += 1
                problems += _check_entry(zf, names, entry, "文档")
            atts = 0
            included = ((manifest.get("attachments") or {}).get("included")) or []
            for entry in included:
                atts += 1
                problems += _check_entry(zf, names, entry, "附件")
    except zipfile.BadZipFile as exc:
        return {**empty, "problems": [f"不是有效的 ZIP 包：{exc}"]}
    except OSError as exc:
        return {**empty, "problems": [f"无法读取该文件：{exc}"]}
    return {"ok": not problems, "documents": docs, "attachments": atts,
            "problems": problems}


def default_filename() -> str:
    return f"gwtool_资料移交包_{datetime.now():%Y%m%d_%H%M%S}.zip"
