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
_override = None  # Path | None（模块级注解不用 PEP 604 语法：本模块需兼容 Python 3.9）


class DataDirError(OSError):
    """数据目录不可用（不可写 / 无法创建）。

    单独定义是为了让启动流程能把它与其他 OSError 区分开，给出**可执行**的
    中文指引（换 --portable、检查目录权限），而不是抛一段栈让用户看。
    """

    def __init__(self, path, reason: str):
        self.path = str(path)
        self.reason = reason
        super().__init__(f"数据目录不可用：{self.path}（{reason}）")


def _ensure_dir(d: Path) -> Path:
    """创建目录，失败时抛带指引的 DataDirError。

    为什么不用 os.access：在 Windows 上对目录的写权限判断**不可靠**
    （ACL、只读属性、继承权限都可能让 access 返回 True 而实际写失败）。
    这里改为「真的写一个临时文件再删掉」——唯一能确证可写性的办法。
    """
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DataDirError(d, f"无法创建目录：{exc}") from exc
    probe = d / ".gwtool_write_test"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise DataDirError(d, f"目录不可写：{exc}") from exc
    return d


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
        return _ensure_dir(_override)
    if _portable:
        return _ensure_dir(_exe_base() / "Data")
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        # XDG_DATA_HOME 优先，兼容麒麟 V10
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return _ensure_dir(Path(base) / APP_DIR_NAME)


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
    return _ensure_dir(app_data_dir() / "backups")


def attachments_dir() -> Path:
    """文档附件存放目录（数据目录内）。

    附件一律复制到这里而不是只记录用户选的原始路径：备份/恢复与便携模式
    （U 盘随带）都只搬数据目录，存原路径的话换机器或恢复备份后附件全部失联。
    """
    return _ensure_dir(app_data_dir() / "attachments")


def _xdg_documents_dir() -> "Path | None":
    """从 ~/.config/user-dirs.dirs 读取本地化的「文档」目录名。

    Linux 桌面会把「文档」本地化（麒麟中文桌面为 ~/文档），此时 ~/Documents
    并不存在。XDG 规范把结果写进 user-dirs.dirs，形如
        XDG_DOCUMENTS_DIR="$HOME/文档"
    读不到或解析失败返回 None（调用方继续退到下一候选）。
    """
    cfg = Path.home() / ".config" / "user-dirs.dirs"
    try:
        for raw_line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line.startswith("XDG_DOCUMENTS_DIR"):
                continue
            _, _, raw = line.partition("=")
            raw = raw.strip().strip('"').strip("'")
            if not raw:
                continue
            # 规范规定用 $HOME 占位，需自行展开
            expanded = raw.replace("$HOME", str(Path.home()))
            p = Path(expanded)
            if p.is_dir():
                return p
    except Exception:
        # 配置文件缺失/权限不足/编码异常都不该拖垮导出，退到下一候选
        pass
    return None


def documents_dir() -> Path:
    """用户的「文档」目录（跨平台 + 中文桌面兼容）。

    探测顺序：~/Documents（英文名，Windows 与多数 Linux 桌面）
            → ~/文档（未配置 XDG 的中文桌面）
            → XDG_DOCUMENTS_DIR（规范位置，能覆盖任意本地化名）
            → Path.home()（最后兜底，宁可放家目录也不抛错）
    """
    candidates = [Path.home() / "Documents", Path.home() / "文档"]
    xdg = _xdg_documents_dir()
    if xdg is not None:
        candidates.append(xdg)
    candidates.append(Path.home())
    for c in candidates:
        try:
            if c.is_dir():
                return c
        except OSError:
            continue
    return Path.home()


def export_dir() -> Path:
    """默认导出目录（用户文档/公文汇编输出）。

    注意：中文桌面下 ~/Documents 不存在（是 ~/文档），旧实现会直接退到
    Path.home()，把导出散落在家目录根部、用户根本找不到。故改用 documents_dir()。
    """
    d = documents_dir() / "公文汇编输出"
    return _ensure_dir(d)


def logs_dir() -> Path:
    return _ensure_dir(app_data_dir() / "logs")


def enhance_dir() -> Path:
    """「精度增强包」目录（可选的神经纠错模型，**不随主包分发**）。

    为什么不打包进安装包：模型动辄上百 MB（KenLM 默认语言模型甚至 2.8 GB），
    而本产品的定位是「小体积、离线、U 盘随带」。因此把重模型做成独立的
    `.zip` 增强包，由用户在有网机器上下载后**离线导入**——主包体积不增长，
    未导入时纠错功能照常（走三级流水线），导入后自动叠加第四级。
    """
    return _ensure_dir(app_data_dir() / "enhance")


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
