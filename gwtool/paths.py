# -*- coding: utf-8 -*-
"""应用数据目录与路径管理。

数据目录规则（与程序分离，便于升级与备份）：
  - Windows : %APPDATA%/gwtool
  - Linux   : ~/.local/share/gwtool（麒麟V10 属此情况）
不依赖 Qt，便于纯逻辑单测。
"""
import os
import sys
from pathlib import Path

APP_DIR_NAME = "gwtool"
_portable = False
_override: Path | None = None


def set_portable(flag: bool) -> None:
    """便携模式：数据目录取程序同级 Data/（U 盘随带随走）。"""
    global _portable
    _portable = bool(flag)


def is_portable() -> bool:
    return _portable


def set_app_data_dir(path: "str | Path | None") -> None:
    """显式指定数据根目录（优先级最高）。

    供测试/探测使用：dbconn.configure 只重定向数据库文件，而附件、备份、
    日志目录仍由 app_data_dir() 推导，会写进真实用户目录造成泄漏。
    传 None 清除覆盖，恢复按平台规则推导。
    """
    global _override
    _override = Path(path) if path else None


def app_data_dir() -> Path:
    """返回应用数据根目录，不存在则创建。"""
    if _override is not None:
        _override.mkdir(parents=True, exist_ok=True)
        return _override
    if _portable:
        base = _exe_base() / "Data"
        base.mkdir(parents=True, exist_ok=True)
        return base
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        # XDG_DATA_HOME 优先，兼容麒麟 V10
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    d = Path(base) / APP_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _exe_base() -> Path:
    """程序所在目录（脚本目录或 PyInstaller onedir 内的根）。"""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        # onedir: dist/gwtool/gwtool.exe -> Data 建在 gwtool/ 下
        return exe.parent
    return Path(__file__).resolve().parent.parent


def db_path() -> Path:
    return app_data_dir() / "gwtool.db"


def backup_dir() -> Path:
    d = app_data_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def attachments_dir() -> Path:
    """文档附件存放目录（数据目录内）。

    附件一律复制到这里而不是只记录用户选的原始路径：备份/恢复与便携模式
    （U 盘随带）都只搬数据目录，存原路径的话换机器或恢复备份后附件全部失联。
    """
    d = app_data_dir() / "attachments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def export_dir() -> Path:
    """默认导出目录（用户文档/公文汇编输出）。"""
    home = Path.home() / "Documents" if Path.home().joinpath("Documents").exists() else Path.home()
    d = home / "公文汇编输出"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    d = app_data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resource_dir() -> Path:
    """随程序分发的只读资源目录（种子数据等）。

    开发态位于源码 gwtool/resources；PyInstaller 打包后位于
    sys._MEIPASS/gwtool/resources（onedir 模式同样生效）。
    """
    if hasattr(sys, "_MEIPASS"):  # PyInstaller 解包目录
        p = Path(sys._MEIPASS) / "gwtool" / "resources"
        if p.exists():
            return p
    return Path(__file__).resolve().parent / "resources"


def bundled_db_seed_path() -> Path:
    """随包分发的种子数据库（含词典、错别字对、规则），不存在则返回空。"""
    return resource_dir() / "data" / "seed.db"
