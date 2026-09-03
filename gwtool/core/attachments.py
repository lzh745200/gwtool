# -*- coding: utf-8 -*-
"""文档附件管理：把用户选的文件**复制进数据目录**并登记，随库一起走。

为什么复制而不只记原始绝对路径：本工具备份/恢复与便携模式（U 盘随带）搬运的
都是数据目录，只记原路径的话，换台电脑或恢复备份后附件全部失联。因此附件本体
一律落在 paths.attachments_dir()（数据目录下的 attachments/ 子目录），库里存
相对数据目录的路径，读取时再拼回绝对路径。

分工：本模块负责文件 IO 与命名去重，数据库行由 db.dao 负责（DAO 不做文件 IO）。
完全离线，不引入任何新依赖。
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from .. import paths
from ..db import dao

# Windows 与麒麟都要能落盘的文件名字符集；顺带挡掉路径分隔符与 ".." 穿越
_INVALID_CHARS = r'[\\/:*?"<>|\r\n\t]'
_MAX_NAME_LEN = 120


# ------------------------------------------------------------------ 路径与命名
def storage_dir() -> Path:
    """附件存放目录（数据目录内，不存在则创建）。"""
    return paths.attachments_dir()


def safe_stored_name(file_name: str) -> str:
    """把用户给的文件名清洗成可安全落盘的单段名字（跨平台结果一致）。

    不用 os.path.basename：Windows 的 ntpath 会把 "报:告.pdf" 里的冒号当盘符
    切掉，麒麟（posixpath）上却不会，同一份数据在两个平台会落出不同名字。
    这里手工归一分隔符取末段，再去掉残留盘符、替换非法字符、剥掉前导点
    （防 ".." 与隐藏文件），最后截断长度 —— 名字来自外部选择，
    绝不能让它决定落盘位置。
    """
    raw = str(file_name or "").replace("\\", "/")
    name = raw.rsplit("/", 1)[-1]
    name = re.sub(r"^[A-Za-z]:", "", name)          # 残留的盘符前缀
    name = re.sub(_INVALID_CHARS, "_", name).strip()
    name = name.lstrip(".").strip()
    return (name or "附件")[:_MAX_NAME_LEN]


def unique_path(directory: Path, file_name: str) -> Path:
    """目录内不重名的目标路径：重名依次加 _2/_3…，序号用尽补短哈希。

    绝不静默覆盖同名文件 —— 用户可能给两篇材料挂了两份同名不同内容的附件。
    """
    base = safe_stored_name(file_name)
    target = directory / base
    if not target.exists():
        return target
    stem, suffix = os.path.splitext(base)
    for i in range(2, 1000):
        cand = directory / f"{stem}_{i}{suffix}"
        if not cand.exists():
            return cand
    import hashlib
    import time
    digest = hashlib.sha1(f"{base}{time.time()}".encode("utf-8")).hexdigest()[:8]
    return directory / f"{stem}_{digest}{suffix}"


def _inside_storage(p: Path | None) -> bool:
    """路径是否落在数据目录内。

    本工具只碰自己数据目录里的文件：既不能删用户目录里的原件，
    也不能被库里的脏路径（"../.."、手工改过的绝对路径）牵到外面去。
    """
    if p is None:
        return False
    try:
        base = paths.app_data_dir().resolve()
        resolved = p.resolve()
    except OSError:
        return False
    return base == resolved or base in resolved.parents


def _ensure_inside_storage(target: Path) -> Path:
    """最后一道防线：目标必须真的落在数据目录内。"""
    if not _inside_storage(target):
        raise ValueError(f"附件路径越界，已拒绝：{target}")
    return target.resolve()


def resolve(att: dao.Attachment | None) -> Path | None:
    """附件在磁盘上的绝对路径；记录无效（无路径/相对路径越界）时返回 None。

    兼容历史与手工改库留下的绝对路径。返回的 Path 不保证存在，
    调用方用 exists() 判断（文件被用户手工挪走时应提示"已丢失"而不是崩）。
    """
    if att is None:
        return None
    stored = (att.stored_path or "").strip()
    if not stored:
        return None
    p = Path(stored)
    if not p.is_absolute():
        p = paths.app_data_dir() / stored
        if not _inside_storage(p):
            return None         # 相对路径跑出数据目录：脏数据，拒绝定位
    return p


def exists(att: dao.Attachment | None) -> bool:
    p = resolve(att)
    return bool(p and p.exists())


def human_size(size: int) -> str:
    """字节数转易读文本（界面显示用）。"""
    try:
        n = float(size or 0)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# ------------------------------------------------------------------ 增删
def add(doc_id: int, src_path: str) -> dao.Attachment:
    """把 src_path 复制进附件目录并登记，返回附件记录。

    失败抛 OSError（磁盘满、无权限、源文件被占用）或 ValueError（越界/无文档），
    由 UI 层捕获后 warn 提示 —— 绝不让异常裸奔到 Qt 事件循环。
    """
    if not doc_id:
        raise ValueError("附件必须挂在某篇文档下（请先选中文档）")
    src = Path(str(src_path))
    if not src.is_file():
        raise FileNotFoundError(f"文件不存在或不是普通文件：{src_path}")
    directory = storage_dir()
    target = unique_path(directory, src.name)
    _ensure_inside_storage(target)          # 只做越界校验，落盘仍用原路径
    shutil.copy2(str(src), str(target))
    # 用 relpath 而不是 resolve().relative_to()：数据目录若在符号链接/junction 下
    # （macOS 的 /var、Windows 的目录联接），resolve 后的前缀对不上会抛 ValueError
    rel = Path(os.path.relpath(str(target), str(paths.app_data_dir()))).as_posix()
    att_id = dao.add_attachment(int(doc_id), safe_stored_name(src.name), rel,
                                target.stat().st_size)
    att = dao.get_attachment(att_id)
    if att is None:                       # 理论上不会发生，兜底防 None 解引用
        raise OSError("附件登记失败，请重试")
    return att


def add_many(doc_id: int, src_paths) -> tuple[list[dao.Attachment],
                                              list[tuple[str, str]]]:
    """批量添加：单个文件失败不中断其余（沿用批量汇编的错误处理约定）。

    返回 (成功记录列表, 失败清单 [(文件名, 原因)])。
    """
    added: list[dao.Attachment] = []
    failures: list[tuple[str, str]] = []
    for p in src_paths or []:
        name = Path(str(p)).name or str(p)
        try:
            added.append(add(doc_id, p))
        except (OSError, ValueError) as exc:
            failures.append((name, str(exc)))
    return added, failures


def remove(att: dao.Attachment | None) -> bool:
    """删除单个附件：先删磁盘文件，再删数据库记录。

    只对数据目录内的文件动手 —— 记录里若存着指向用户目录的绝对路径
    （手工改库留下的脏数据），只清记录、不删那个文件。
    文件已丢失时仍删记录（清残留）；文件删不掉（被占用/无权限）时保留记录
    并返回 False，让用户能重试，而不是留下指向不存在文件的记录。
    """
    if att is None:
        return False
    p = resolve(att)
    if p is not None and p.exists() and _inside_storage(p):
        try:
            p.unlink()
        except OSError:
            return False
    dao.delete_attachment(att.id)
    return True


def remove_for_document(doc_id: int) -> tuple[int, list[str]]:
    """删除某文档的全部附件（不动文档本身）。返回 (删除数, 删不掉的文件名)。"""
    ok = 0
    stuck: list[str] = []
    for att in dao.list_attachments(doc_id):
        if remove(att):
            ok += 1
        else:
            stuck.append(att.file_name or Path(att.stored_path or "").name)
    return ok, stuck


def purge_document(doc_id: int) -> list[str]:
    """彻底删除一篇文档：附件文件 + 文档行 + FTS 行 + 快照 + 附件记录。

    返回删不掉的附件文件名（被占用等）。即使个别文件删不掉也会继续删库记录：
    用户要的是"这篇材料彻底没了"，最坏只在数据目录留下无人引用的孤儿文件，
    可用 sweep_orphans() 清掉。软删除（回收站）请用 dao.delete_document。
    """
    stuck: list[str] = []
    for att in dao.list_attachments(doc_id):
        p = resolve(att)
        if p is not None and p.exists() and _inside_storage(p):
            try:
                p.unlink()
            except OSError:
                stuck.append(att.file_name or p.name)
    dao.purge_document(doc_id)
    return stuck


def purge_documents(doc_ids) -> list[str]:
    """批量彻底删除，返回全部删不掉的附件文件名。"""
    stuck: list[str] = []
    for did in doc_ids or []:
        stuck.extend(purge_document(did))
    return stuck


def sweep_orphans() -> int:
    """清掉附件目录里没有任何记录引用的文件（彻底删除时被占用的残留）。

    只在数据目录的 attachments/ 内动手，删除失败的文件跳过；返回删除数。
    """
    directory = storage_dir()
    referenced: set[str] = set()
    for att in dao.list_all_attachments():
        p = resolve(att)
        if p is not None:
            referenced.add(p.name)
    removed = 0
    try:
        entries = list(directory.iterdir())
    except OSError:
        return 0
    for entry in entries:
        if not entry.is_file() or entry.name in referenced:
            continue
        try:
            entry.unlink()
            removed += 1
        except OSError:
            continue
    return removed
