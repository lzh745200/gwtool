# -*- coding: utf-8 -*-
"""一键备份/恢复：打包数据库与配置为 zip（含完整性校验）。"""
from __future__ import annotations

import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from .. import paths
from ..db import connection as dbconn


def create_backup(note: str = "", password: str = "") -> str:
    """备份当前数据库到 backups 目录，返回备份文件路径。

    password 非空时使用 AES 加密（pyzipper）；未安装 pyzipper 时报错提示。
    备份前先做 WAL checkpoint，确保后台线程连接中未落盘的事务进入备份包。
    """
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
    if password:
        try:
            import pyzipper
        except ImportError:
            raise RuntimeError(
                "未安装 pyzipper，无法创建加密备份（pip install pyzipper）") from None
        with pyzipper.AESZipFile(out, "w", compression=pyzipper.ZIP_DEFLATED,
                                 encryption=pyzipper.WZ_AES) as zf:
            zf.setpassword(password.encode("utf-8"))
            zf.writestr("manifest.json", json.dumps(
                {"created": stamp, "version": "1.0.0", "note": note,
                 "encrypted": True}, ensure_ascii=False, indent=1))
            zf.write(src, "gwtool.db")
            _write_templates(zf)
            _write_attachments(zf)
    else:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(src, "gwtool.db")
            meta = {"created": stamp, "version": "1.0.0", "note": note}
            zf.writestr("manifest.json", json.dumps(meta, ensure_ascii=False, indent=1))
            _write_templates(zf)
            _write_attachments(zf)
    rotate_backups()
    return str(out)


def _write_attachments(zf) -> None:
    """把附件文件一并打进备份包（attachments/ 目录下）。

    附件本体存在数据目录里，备份只带 db 的话换机器/恢复后附件全部失联，
    所以必须随包走。个别文件读不到（被占用、已手工删除）只跳过，
    不能因此让整份备份失败。
    """
    from . import attachments as att_core
    from ..db import dao
    try:
        items = dao.list_all_attachments()
    except Exception:
        return
    for att in items:
        p = att_core.resolve(att)
        if p is None or not p.exists():
            continue
        try:
            zf.write(p, f"attachments/{p.name}")
        except (OSError, ValueError):
            continue


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
    """备份轮转：仅保留最近 keep_recent 份。返回删除数。"""
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
                    meta = json.loads(zf.read("manifest.json"))
            out.append({"file": str(f),
                        "created": (meta or {}).get("created", ""),
                        "note": (meta or {}).get("note", "AES 加密备份"),
                        "encrypted": encrypted})
        except Exception:
            continue
    return out


def restore_backup(zip_path: str, password: str = "") -> bool:
    """从备份恢复（自动识别普通/AES 加密包）。恢复前自动做一次“恢复前备份”。"""
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
        # 先备份现状
        try:
            create_backup(note="恢复前自动备份")
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
        try:
            _restore_attachments(zf)
        except Exception:
            pass
    return True


def _restore_attachments(zf) -> int:
    """备份包内 attachments/* -> 数据目录 attachments/，返回还原文件数。

    只取条目的 basename 拼目标路径，并校验结果确实落在附件目录内：
    备份包可能来自别的机器（甚至被人手工改过），不能相信里面的相对路径。
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
