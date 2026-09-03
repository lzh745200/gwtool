# -*- coding: utf-8 -*-
"""一键备份/恢复：打包数据库、模板与附件为 zip（含完整性校验与附件体积上限）。

为什么要有附件体积上限
----------------------
附件功能让备份包从「几百 KB 的数据库」变成「数据库 + 全部附件」，而退出程序时的
自动备份是无条件触发的：附件攒到几百 MB 后，每次关程序都要把全量附件重新压缩一遍，
且备份轮转保留 20 份 → 备份目录变成 20 × 全量附件。这与「退出要快、别吃磁盘」直接冲突。

于是按**备份意图**分两档（都可在「设置 → 系统与安全」里改，绝不硬编码死）：
  MODE_MANUAL 手动备份：用户要的是「一个能换台电脑继续用的完整包」，附件预算默认
      30 MB（依据见 DEFAULT_ATTACHMENT_LIMIT_MB 注释），可一路调到「不限制」。
  MODE_AUTO   自动备份（退出时 + 定时器 + 恢复前兜底）：只是防丢失的安全网，
      附件预算默认 8 MB，保证退出路径耗时与备份目录体积都不随附件增长而失控。

超限时**绝不静默丢数据**（静默丢弃比备份变大严重得多）：
  1. 包内 manifest.json 记录 excluded 清单（文件名/体积/所属文档/原因）；
  2. 另写一份纯文本 excluded_attachments.txt，用资源管理器打开备份包就能看到；
  3. 恢复时 restore_backup_detailed() 核对磁盘，把仍然缺失的附件连同原因返回给 UI，
     由 UI 明确告知「这些附件不在本包里、去哪儿补」；
  4. 手动备份当场弹提示，自动备份写 logs/backup.log。

旧格式备份包（没有 attachments 清单，甚至没有 manifest.json）一律按「全量」处理：
恢复流程不报错，缺失情况改为按恢复后的磁盘实况核对，照样能报出来。
"""
from __future__ import annotations

import json
import os
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .. import paths
from ..db import connection as dbconn

# ------------------------------------------------------------------ 策略常量
MODE_MANUAL = "manual"      # 用户主动点「备份…」：目标是可换机迁移的完整包
MODE_AUTO = "auto"          # 退出自动备份 / 定时备份 / 恢复前兜底：安全网，必须快

# 附件体积上限默认值（MB）的定量依据（实测：Windows，16 MB 的库 + 40 MB 附件）：
#   备份轮转保留 20 份（rotate_backups），故备份目录最坏占用 ≈ 20 ×（上限 + 库文件）。
#   手动 30 MB → 单包 36.5 MB、耗时约 3.4 s、20 份约 0.7 GB：用户主动点一次可接受；
#   自动  8 MB → 单包 12 MB、耗时约 0.8 s（其中库本身约 0.6 s）、20 份约 0.24 GB。
#   对照组：不设上限把 40 MB 附件全带 → 3.1~4.1 s，且附件只增不减，退出会越来越久。
# -1 表示不限制（换机迁移时用），0 表示完全不打包附件。
DEFAULT_ATTACHMENT_LIMIT_MB = 30
DEFAULT_AUTO_ATTACHMENT_LIMIT_MB = 8
NO_LIMIT = -1

SETTING_LIMIT_MB = "backup_attachment_limit_mb"
SETTING_AUTO_LIMIT_MB = "backup_auto_attachment_limit_mb"

MANIFEST_NAME = "manifest.json"
# 纯 ASCII 文件名：第三方解压器/老系统都能正常显示，内容仍是 UTF-8 中文
EXCLUDED_LIST_NAME = "excluded_attachments.txt"
PART_SUFFIX = ".part"
LOG_NAME = "backup.log"
LOG_MAX_BYTES = 512 * 1024
LOG_KEEP_LINES = 200
_LOG_DETAIL_LIMIT = 20        # 日志/提示里最多逐条列出的附件数


# ------------------------------------------------------------------ 明细结构
@dataclass
class BackupReport:
    """一次备份的明细：路径 + 附件随包情况（供 UI 提示与日志使用）。"""
    path: str = ""
    mode: str = MODE_MANUAL
    limit_bytes: int = 0                    # -1 = 不限制
    included: list[dict] = field(default_factory=list)   # [{"name","size","doc_id"}]
    excluded: list[dict] = field(default_factory=list)   # [... + "reason"]

    @property
    def included_bytes(self) -> int:
        return sum(int(i.get("size") or 0) for i in self.included)

    @property
    def excluded_bytes(self) -> int:
        return sum(int(i.get("size") or 0) for i in self.excluded)

    @property
    def truncated(self) -> bool:
        """是否有附件因超限/读不到而没进包（用户必须被告知的那一档）。"""
        return bool(self.excluded)


@dataclass
class RestoreReport:
    """一次恢复的明细：重点是「哪些附件没跟着回来」。"""
    ok: bool = True
    legacy: bool = False                    # 包内没有附件清单（旧格式/全量）
    restored_files: int = 0                 # 从包内还原到附件目录的文件数
    missing: list[dict] = field(default_factory=list)
    attachments_dir: str = ""               # 提示用户去哪儿补附件


@dataclass
class _AttItem:
    """规划阶段的一条附件（path 只在内存里用，不进 JSON）。"""
    name: str = ""
    size: int = 0
    doc_id: int = 0
    reason: str = ""
    path: Path | None = None

    def to_json(self) -> dict:
        return {"name": self.name, "size": self.size,
                "doc_id": self.doc_id, "reason": self.reason}


# ------------------------------------------------------------------ 上限读取
def attachment_limit_mb(mode: str = MODE_MANUAL) -> int:
    """当前生效的附件体积上限（MB）；-1 = 不限制。设置缺失/写坏时回落到默认值。"""
    from ..db import dao
    if mode == MODE_AUTO:
        key, default = SETTING_AUTO_LIMIT_MB, DEFAULT_AUTO_ATTACHMENT_LIMIT_MB
    else:
        key, default = SETTING_LIMIT_MB, DEFAULT_ATTACHMENT_LIMIT_MB
    try:
        raw = str(dao.get_setting(key, "") or "").strip()
        mb = int(round(float(raw))) if raw else int(default)
    except Exception:                       # 设置项被手工写成乱七八糟的值
        mb = int(default)
    return NO_LIMIT if mb < 0 else mb


def attachment_limit_bytes(mode: str = MODE_MANUAL) -> int:
    """同 attachment_limit_mb，单位换成字节；-1 = 不限制。"""
    mb = attachment_limit_mb(mode)
    return NO_LIMIT if mb == NO_LIMIT else mb * 1024 * 1024


def _human(size: int) -> str:
    from .attachments import human_size
    return human_size(size)


# ------------------------------------------------------------------ 创建备份
def create_backup(note: str = "", password: str = "", mode: str = MODE_MANUAL) -> str:
    """备份当前数据库到 backups 目录，返回备份文件路径（兼容旧调用）。

    password 非空时使用 AES 加密（pyzipper）；未安装 pyzipper 时报错提示。
    备份前先做 WAL checkpoint，确保后台线程连接中未落盘的事务进入备份包。
    mode=MODE_AUTO 时按自动备份的（更小的）附件预算打包，见模块文档。
    """
    return create_backup_detailed(note=note, password=password, mode=mode).path


def create_backup_detailed(note: str = "", password: str = "",
                           mode: str = MODE_MANUAL) -> BackupReport:
    """创建备份并返回明细（含被排除的附件清单），供 UI 提示与日志使用。"""
    try:
        dbconn.get_conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass
    dbconn.close_current_thread()  # 确保落盘（WAL checkpoint 在连接关闭时完成）
    src = dbconn.current_db_file()
    if not src.exists():
        raise FileNotFoundError("数据库不存在，无可备份内容")
    # 文件名含微秒，避免同一秒内多次备份互相覆盖
    now = datetime.now()
    stamp = now.strftime("%Y%m%d_%H%M%S") + f"_{now.microsecond // 1000:03d}"
    suffix = "_加密" if password else ""
    out = paths.backup_dir() / f"gwtool_backup_{stamp}{suffix}.zip"

    limit = attachment_limit_bytes(mode)
    included, excluded = _plan_attachments(limit)

    # 先写 .part 再原子改名：进程被杀/断电/线程被终止时，轮转与恢复永远看不到
    # 「看着完整其实截断」的备份包（zip 的中央目录在最后，半截包必然打不开）。
    tmp = out.with_name(out.name + PART_SUFFIX)
    _clean_stale_parts(out.parent)
    # manifest 必须在附件之后写：写包失败的附件会被挪进 excluded，清单要如实反映
    manifest_kwargs = dict(stamp=stamp, note=note, mode=mode, limit_bytes=limit,
                           included=included, excluded=excluded)
    renamed = False
    try:
        if password:
            try:
                import pyzipper
            except ImportError:
                raise RuntimeError(
                    "未安装 pyzipper，无法创建加密备份（pip install pyzipper）") from None
            with pyzipper.AESZipFile(tmp, "w", compression=pyzipper.ZIP_DEFLATED,
                                     encryption=pyzipper.WZ_AES) as zf:
                zf.setpassword(password.encode("utf-8"))
                zf.write(src, "gwtool.db")
                _write_templates(zf)
                _write_attachments(zf, included, excluded)
                zf.writestr(MANIFEST_NAME, _manifest_json(encrypted=True, **manifest_kwargs))
                _write_excluded_list(zf, included, excluded, mode, limit)
        else:
            # 自动备份只求快：deflate level 1 比默认 level 6 快 2~3 倍，包只大约一成
            # （实测 16 MB 的库：1.19s/6.5 MB → 0.49s/7.2 MB）。退出路径上要的是
            # "立刻能关程序"，手动备份仍用默认级别求紧凑（用户主动点一次，愿意等）。
            level = 1 if mode == MODE_AUTO else None
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED,
                                 compresslevel=level) as zf:
                zf.write(src, "gwtool.db")
                _write_templates(zf)
                _write_attachments(zf, included, excluded)
                zf.writestr(MANIFEST_NAME, _manifest_json(encrypted=False,
                                                          **manifest_kwargs))
                _write_excluded_list(zf, included, excluded, mode, limit)
        os.replace(tmp, out)                # 落名：这一步之后包才算存在
        renamed = True
    finally:
        if not renamed:                     # 任何异常（磁盘满、口令错、被中断）
            try:                            # 都不该把半成品留在备份目录里
                tmp.unlink()
            except OSError:
                pass

    rotate_backups()
    report = BackupReport(path=str(out), mode=mode, limit_bytes=limit,
                          included=[it.to_json() for it in included],
                          excluded=[it.to_json() for it in excluded])
    if report.truncated:
        # 自动备份不弹窗（退出路径不能拦着用户），一律留痕到 logs/backup.log；
        # 手动备份由 UI 层再弹提示。
        _log_exclusions(report)
    return report


def _plan_attachments(limit_bytes: int) -> tuple[list[_AttItem], list[_AttItem]]:
    """按预算决定哪些附件随包走，返回 (included, excluded)。

    超预算时按体积从小到大装入：同样的上限下带走的附件**数量**最多，
    恢复后「缺附件」的文档也最少。装不下的一律记进 excluded（带体积与原因），
    绝不静默丢弃 —— 用户会以为备份是全的，灾难恢复时才发现附件没了。
    """
    from . import attachments as att_core
    from ..db import dao
    try:
        items = dao.list_all_attachments()
    except Exception:
        return [], []
    pending: list[_AttItem] = []
    excluded: list[_AttItem] = []
    for att in items:
        p = att_core.resolve(att)
        if p is None:
            # 记录里是越界相对路径等脏数据：定位不到文件，也要记下来
            excluded.append(_AttItem(name=att.file_name or att.stored_path or "附件",
                                     size=int(att.size or 0), doc_id=att.doc_id,
                                     reason="附件记录路径无效，定位不到文件"))
            continue
        if not p.exists():
            excluded.append(_AttItem(name=att.file_name or p.name,
                                     size=int(att.size or 0), doc_id=att.doc_id,
                                     reason="备份时文件已不在磁盘上（可能被手工删除/移动）"))
            continue
        try:
            size = p.stat().st_size
        except OSError:
            size = int(att.size or 0)
        pending.append(_AttItem(name=p.name, size=size, doc_id=att.doc_id, path=p))
    pending.sort(key=lambda it: (it.size, it.name))
    included: list[_AttItem] = []
    used = 0
    for it in pending:
        if limit_bytes >= 0 and used + it.size > limit_bytes:
            it.reason = "超出备份附件体积上限"
            excluded.append(it)
            continue
        included.append(it)
        used += it.size
    return included, excluded


def _write_attachments(zf, included: list[_AttItem], excluded: list[_AttItem]) -> None:
    """把已规划的附件写进备份包（attachments/ 目录下）。

    附件本体存在数据目录里，备份只带 db 的话换机器/恢复后附件全部失联，
    所以必须随包走。个别文件读不到（被占用、刚被删）不能让整份备份失败：
    挪进 excluded 并写明原因，同样会被清单与恢复提示带出去。
    """
    ok: list[_AttItem] = []
    seen: set[str] = set()
    for it in included:
        arc = f"attachments/{it.name}"
        if arc in seen:
            # 历史脏数据里两条记录指向同名文件：只打一份，也不重复占预算
            it.reason = "包内已有同名附件"
            excluded.append(it)
            continue
        try:
            zf.write(it.path, arc)
        except (OSError, ValueError) as exc:
            it.reason = f"写入备份包失败：{exc}"
            excluded.append(it)
            continue
        seen.add(arc)
        ok.append(it)
    included[:] = ok


def _manifest_json(stamp: str, note: str, mode: str, limit_bytes: int,
                   encrypted: bool, included: list[_AttItem],
                   excluded: list[_AttItem]) -> str:
    """manifest.json：元信息 + 附件清单（included/excluded）。

    附件清单是「没静默丢数据」的凭据：恢复时靠它解释每个缺失附件的原因。
    老备份包里没有 attachments 段，恢复端按「旧格式/全量」处理。
    """
    from .attachments import human_size
    meta = {"created": stamp, "version": "1.0.0", "note": note}
    if encrypted:
        meta["encrypted"] = True
    meta["attachments"] = {
        "mode": mode,
        "limit_mb": limit_bytes if limit_bytes < 0 else limit_bytes // (1024 * 1024),
        "limit_text": "不限制" if limit_bytes < 0 else human_size(limit_bytes),
        "included": [it.to_json() for it in included],
        "excluded": [it.to_json() for it in excluded],
    }
    return json.dumps(meta, ensure_ascii=False, indent=1)


def _write_excluded_list(zf, included: list[_AttItem], excluded: list[_AttItem],
                         mode: str, limit_bytes: int) -> None:
    """有附件被排除时，额外写一份人眼可读的清单进包里。

    manifest.json 是给程序读的；用户拿资源管理器打开备份包时，
    第一眼就该看到「哪些附件没在里面、去哪儿补」。
    """
    if not excluded:
        return
    zf.writestr(EXCLUDED_LIST_NAME, excluded_list_text(included, excluded, mode,
                                                       limit_bytes))


def excluded_list_text(included: list[_AttItem], excluded: list[_AttItem],
                       mode: str, limit_bytes: int) -> str:
    """附件缺失清单的正文（UTF-8 中文，写进包内 excluded_attachments.txt）。"""
    total = len(included) + len(excluded)
    lines = [
        "本备份包未包含以下附件（恢复此备份时程序会再次提醒）",
        "=" * 46,
        f"备份方式：{'手动备份' if mode == MODE_MANUAL else '自动备份'}",
        f"附件体积上限：{'不限制' if limit_bytes < 0 else _human(limit_bytes)}",
        f"附件总数：{total}    已随包：{len(included)}    未随包：{len(excluded)}",
        "",
    ]
    for it in excluded:
        lines.append(f"- {it.name}（{_human(it.size)}，文档ID {it.doc_id}）：{it.reason}")
    lines += [
        "",
        "怎么补回来：",
        "  1) 这些附件的原件仍在「备份来源电脑」数据目录下的 attachments 子目录里，",
        "     把同名文件复制回本机数据目录的 attachments 子目录即可（恢复不删本机已有附件）；",
        "  2) 若来源电脑已不可用，只能从原始出处重新添加这些附件；",
        "  3) 需要完整迁移包时，请在「设置 → 系统与安全」调高附件体积上限后重新备份。",
    ]
    return "\n".join(lines) + "\n"


def _clean_stale_parts(directory: Path) -> None:
    """清掉以往备份中途失败留下的 .part 半成品（1 小时内还在写的不动）。"""
    try:
        parts = list(directory.glob(f"gwtool_backup_*.zip{PART_SUFFIX}"))
    except OSError:
        return
    cutoff = time.time() - 3600
    for p in parts:
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            continue


def _log_exclusions(report: BackupReport) -> None:
    """附件被排除时留痕到 logs/backup.log（自动备份不弹窗，事后可查）。"""
    limit = "不限制" if report.limit_bytes < 0 else _human(report.limit_bytes)
    kind = "手动备份" if report.mode == MODE_MANUAL else "自动备份"
    lines = [f"{kind}：{len(report.excluded)} 个附件未随包（上限 {limit}，"
             f"未随包合计 {_human(report.excluded_bytes)}）→ {report.path}"]
    for it in report.excluded[:_LOG_DETAIL_LIMIT]:
        lines.append(f"    - {it.get('name')}（{_human(int(it.get('size') or 0))}）"
                     f"：{it.get('reason')}")
    rest = len(report.excluded) - _LOG_DETAIL_LIMIT
    if rest > 0:
        lines.append(f"    …其余 {rest} 个见备份包内 {EXCLUDED_LIST_NAME}")
    _append_log(lines)


def _append_log(lines: list[str]) -> None:
    """追加写日志；超过 LOG_MAX_BYTES 只留末尾若干行（日志本身也不能吃磁盘）。"""
    if not lines:
        return
    try:
        log = paths.logs_dir() / LOG_NAME
        # 格式串全 ASCII：Windows 的 strftime 按 C 运行时 locale 编码处理格式串，
        # 英文区域设置的机器上带中文会直接 UnicodeEncodeError
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            if log.exists() and log.stat().st_size > LOG_MAX_BYTES:
                old = log.read_text(encoding="utf-8", errors="replace").splitlines()
                log.write_text("\n".join(old[-LOG_KEEP_LINES:]) + "\n",
                               encoding="utf-8")
        except OSError:
            pass
        with open(log, "a", encoding="utf-8") as f:
            for ln in lines:
                f.write(f"[{stamp}] {ln}\n")
    except OSError:
        pass


def _write_templates(zf) -> None:
    """附带模板导出（便于跨机迁移）。"""
    from ..db import dao
    tpls = dao.list_templates()
    configs = []
    for t in tpls:
        cfg = dao.get_template_config(t["name"])
        if cfg:
            configs.append({"name": t["name"], "is_default": t["is_default"],
                            "config_json": cfg})
    zf.writestr("templates.json", json.dumps(configs, ensure_ascii=False, indent=1))


def rotate_backups(keep_recent: int = 20) -> int:
    """备份轮转：仅保留最近 keep_recent 份。返回删除数。

    只认 .zip：写一半的 .part 半成品不在轮转范围内（也永不会被当成备份恢复）。
    """
    backups = sorted(paths.backup_dir().glob("gwtool_backup_*.zip"), reverse=True)
    removed = 0
    for old in backups[keep_recent:]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def list_backups() -> list[dict]:
    out = []
    for f in sorted(paths.backup_dir().glob("gwtool_backup_*.zip"), reverse=True):
        try:
            encrypted = "_加密" in f.name
            if encrypted:
                meta = None  # 加密包跳过元信息读取
            else:
                with zipfile.ZipFile(f) as zf:
                    meta = _read_manifest(zf) or None
            att = (meta or {}).get("attachments") or {}
            out.append({"file": str(f),
                        "created": (meta or {}).get("created", ""),
                        "note": (meta or {}).get("note", "AES 加密备份"),
                        "encrypted": encrypted,
                        "attachments_included": len(att.get("included") or []),
                        "attachments_excluded": len(att.get("excluded") or [])})
        except Exception:
            continue
    return out


def _read_manifest(zf) -> dict:
    """读包内 manifest.json；缺失/损坏/口令错误一律返回 {}（按旧格式处理）。"""
    try:
        data = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# ------------------------------------------------------------------ 恢复
def restore_backup(zip_path: str, password: str = "") -> bool:
    """从备份恢复（自动识别普通/AES 加密包）。恢复前自动做一次“恢复前备份”。"""
    return restore_backup_detailed(zip_path, password=password).ok


def restore_backup_detailed(zip_path: str, password: str = "") -> RestoreReport:
    """恢复并返回明细，重点是 missing：哪些附件没跟着回来、为什么、去哪儿补。

    旧格式备份包（无 manifest.json 或 manifest 里没有 attachments 段）完全兼容：
    按「全量」处理，legacy=True，缺失情况改为按恢复后的磁盘实况核对。
    """
    if password:
        try:
            import pyzipper
        except ImportError:
            raise RuntimeError("未安装 pyzipper，无法读取加密备份") from None
        zf_obj = pyzipper.AESZipFile(zip_path)
        zf_obj.setpassword(password.encode("utf-8"))
    else:
        zf_obj = zipfile.ZipFile(zip_path)
    with zf_obj as zf:
        names = zf.namelist()
        if "gwtool.db" not in names:
            raise ValueError("备份包不完整（缺少 gwtool.db）")
        manifest = _read_manifest(zf)
        att_meta = manifest.get("attachments")
        legacy = not isinstance(att_meta, dict)
        recorded = [] if legacy else (att_meta.get("excluded") or [])
        # 先备份现状。用自动备份档：恢复只往附件目录里写文件、从不删除，
        # 这个兜底包的价值在数据库，没必要再压一遍全量附件把恢复拖慢。
        try:
            create_backup(note="恢复前自动备份", mode=MODE_AUTO)
        except Exception:
            pass
        dbconn.close_current_thread()
        target = dbconn.current_db_file()
        tmp = target.with_suffix(".restore.tmp")
        with zf.open("gwtool.db") as fsrc, open(tmp, "wb") as fdst:
            shutil.copyfileobj(fsrc, fdst)
        # 校验可打开
        import sqlite3
        conn = sqlite3.connect(str(tmp))
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        conn.close()
        tmp.replace(target)
        # 模板迁移包回写（旧版只写不读）
        if "templates.json" in names:
            try:
                _restore_templates(zf)
            except Exception:
                pass
        # 附件回数据目录（备份包里没有该目录时静默跳过，兼容旧备份）
        restored = 0
        try:
            restored = _restore_attachments(zf)
        except Exception:
            pass
    return RestoreReport(ok=True, legacy=legacy, restored_files=restored,
                         missing=_missing_attachments(recorded),
                         attachments_dir=str(paths.attachments_dir()))


def _missing_attachments(recorded: list[dict]) -> list[dict]:
    """恢复后核对：库里引用的附件在磁盘上是否真的回来了。

    不只信备份包的清单 —— 旧格式包（没有清单）同样要能报出缺哪些；
    清单里带了原因的（超上限/备份时就不在）把原因一并给用户。
    """
    from . import attachments as att_core
    from ..db import dao
    by_name: dict[str, dict] = {}
    for r in recorded or []:
        if isinstance(r, dict) and r.get("name"):
            by_name[str(r["name"])] = r
    try:
        items = dao.list_all_attachments()
    except Exception:
        return []
    missing: list[dict] = []
    for att in items:
        if att_core.exists(att):
            continue
        p = att_core.resolve(att)
        stored = p.name if p is not None else ""
        rec = by_name.get(stored) or by_name.get(att.file_name or "")
        missing.append({
            "name": att.file_name or stored or att.stored_path or "附件",
            "stored_name": stored,
            "size": int((rec or {}).get("size") or att.size or 0),
            "doc_id": att.doc_id,
            "reason": (rec or {}).get("reason")
                      or "备份包内没有该附件（旧格式备份或未随包）",
        })
    return missing


def _restore_attachments(zf) -> int:
    """备份包内 attachments/* -> 数据目录 attachments/，返回还原文件数。

    只取条目的 basename 拼目标路径，并校验结果确实落在附件目录内：
    备份包可能来自别的机器（甚至被人手工改过），不能相信里面的相对路径。
    同名文件以包内版本为准（恢复语义）；包里**没有**的文件一律不删 ——
    所以超限被排除的附件在同机恢复时其实一点没丢，文件还原样躺在附件目录里。
    """
    from .. import paths
    dest_dir = paths.attachments_dir().resolve()
    n = 0
    for name in zf.namelist():
        if not name.startswith("attachments/") or name.endswith("/"):
            continue
        file_name = Path(name).name
        if not file_name:
            continue
        target = (dest_dir / file_name).resolve()
        if dest_dir != target and dest_dir not in target.parents:
            continue                      # 路径穿越，拒绝
        try:
            with zf.open(name) as fsrc, open(target, "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst)
            n += 1
        except (OSError, KeyError, RuntimeError):
            continue
    return n


def _restore_templates(zf) -> int:
    """备份包内 templates.json -> 模板表（upsert，便于跨机迁移后补齐模板）。"""
    from ..db import dao
    cfgs = json.loads(zf.read("templates.json").decode("utf-8"))
    n = 0
    for c in cfgs:
        if isinstance(c, dict) and c.get("name") and c.get("config_json"):
            dao.save_template(c["name"], c["config_json"],
                              is_default=bool(c.get("is_default")))
            n += 1
    return n
