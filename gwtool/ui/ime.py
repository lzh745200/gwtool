# -*- coding: utf-8 -*-
"""Linux/麒麟 Qt6 输入法（IME）适配。

问题背景：麒麟 V10 上双击安装后，窗口能开但**切不了中文输入法**。Qt6 不再走
XIM，必须由 `platforminputcontexts` 插件对接 fcitx5 / ibus；而 PyInstaller
打包后 `QT_PLUGIN_PATH` 只指向包内插件目录，系统安装的 fcitx5-qt6 插件默认
找不到；同时 `QT_IM_MODULE` 未设置时 Qt 只加载 compose（英文）。

本模块在 QApplication 构造**之前**：
  1. 探测系统/包内可用的 IME 插件（compose / ibus / fcitx5）；
  2. 尊重用户已有的 QT_IM_MODULE；未设置时按 XMODIFIERS/GTK_IM_MODULE
     与会话里实际运行的输入法自动选择；
  3. 把系统 Qt6 插件目录追加进 QT_PLUGIN_PATH（不覆盖包内路径）；
  4. 返回诊断信息，供启动日志/`--startup-report`/故障弹窗使用。

只做环境准备，不改任何业务逻辑；任何异常都返回诊断而不是阻断启动。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_PLUGIN_SUBDIR = Path("platforminputcontexts")
_PLUGIN_NAMES = {
    "compose": ("libcomposeplatforminputcontextplugin.so",),
    "ibus": ("libibusplatforminputcontextplugin.so",),
    "fcitx": ("libfcitx5platforminputcontextplugin.so",
              "libfcitxplatforminputcontextplugin.so"),
}


def _candidate_plugin_roots() -> list[Path]:
    roots: list[Path] = []
    # 包内（PyInstaller onedir / onefile / 源码运行）
    try:
        import PySide6
        roots.append(Path(PySide6.__file__).resolve().parent / "Qt" / "plugins")
    except Exception:
        pass
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        roots.append(Path(meipass) / "PySide6" / "Qt" / "plugins")
        roots.append(Path(meipass) / "PySide6" / "plugins")
    # 系统 Qt6 插件目录（不同发行版路径不同，宁多勿漏）
    roots += [
        Path("/usr/lib/aarch64-linux-gnu/qt6/plugins"),
        Path("/usr/lib/x86_64-linux-gnu/qt6/plugins"),
        Path("/usr/lib/qt6/plugins"),
        Path("/usr/lib64/qt6/plugins"),
        Path("/usr/local/lib/qt6/plugins"),
    ]
    return roots


def _plugins_present() -> dict[str, Path | None]:
    found: dict[str, Path | None] = {k: None for k in _PLUGIN_NAMES}
    for root in _candidate_plugin_roots():
        d = root / _PLUGIN_SUBDIR
        if not d.is_dir():
            continue
        names = {p.name for p in d.iterdir() if p.is_file()}
        for kind, candidates in _PLUGIN_NAMES.items():
            if found[kind] is None:
                for name in candidates:
                    if name in names:
                        found[kind] = d / name
                        break
    return found


def _session_hint() -> str:
    """从环境判断会话期望的输入法：fcitx / ibus / ''。"""
    xmod = (os.environ.get("XMODIFIERS") or "").lower()
    gtk = (os.environ.get("GTK_IM_MODULE") or "").lower()
    qt = (os.environ.get("QT_IM_MODULE") or "").lower()
    blob = " ".join((xmod, gtk, qt))
    if "fcitx" in blob:
        return "fcitx"
    if "ibus" in blob:
        return "ibus"
    return ""


def configure(force: bool = False) -> dict:
    """在 QApplication 前调用；返回诊断字典（可安全写入日志）。

    force=True 仅供单元测试在非 Linux 主机上验证选择逻辑。
    """
    info: dict = {
        "platform": sys.platform,
        "session_type": os.environ.get("XDG_SESSION_TYPE", ""),
        "xmodifiers": os.environ.get("XMODIFIERS", ""),
        "gtk_im_module": os.environ.get("GTK_IM_MODULE", ""),
        "qt_im_module_before": os.environ.get("QT_IM_MODULE", ""),
        "chosen": "",
        "plugins": {},
        "warnings": [],
    }
    if sys.platform != "linux" and not force:
        return info
    try:
        plugins = _plugins_present()
        info["plugins"] = {k: (str(v) if v else "") for k, v in plugins.items()}

        # 系统插件目录追加进 QT_PLUGIN_PATH（保留原有顺序与包内路径）
        sys_dirs = [str(p) for p in _candidate_plugin_roots()
                    if p.is_dir() and "site-packages" not in str(p)
                    and "PySide6" not in str(p)]
        if sys_dirs:
            cur = os.environ.get("QT_PLUGIN_PATH", "")
            parts = [p for p in cur.split(os.pathsep) if p]
            for d in sys_dirs:
                if d not in parts:
                    parts.append(d)
            if parts:
                os.environ["QT_PLUGIN_PATH"] = os.pathsep.join(parts)

        hint = _session_hint()
        current = (os.environ.get("QT_IM_MODULE") or "").strip().lower()
        if current:
            info["chosen"] = current
        elif hint and plugins.get(hint):
            os.environ["QT_IM_MODULE"] = hint
            info["chosen"] = hint
        elif hint:
            info["warnings"].append(
                f"检测到系统使用 {hint}，但包内/系统中没有对应的 Qt6 插件")
        else:
            info["chosen"] = current or "compose"
            if not plugins.get("compose"):
                info["warnings"].append("未找到 Qt6 输入法插件（compose/ibus/fcitx5）")
        return info
    except Exception as exc:            # 输入法适配绝不能阻断启动
        info["warnings"].append(f"输入法适配失败：{exc}")
        return info


def diagnostics_text(info: dict | None = None) -> str:
    """一行行可读诊断（写日志/--startup-report 用）。"""
    info = info or {}
    lines = [
        f"session: {info.get('session_type', '')}  XMODIFIERS={info.get('xmodifiers', '')}",
        f"GTK_IM_MODULE={info.get('gtk_im_module', '')}  "
        f"QT_IM_MODULE={info.get('qt_im_module_before', '')} -> {info.get('chosen', '')}",
    ]
    for k, v in (info.get("plugins") or {}).items():
        lines.append(f"plugin[{k}]: {v or 'MISSING'}")
    for w in info.get("warnings") or []:
        lines.append(f"WARN: {w}")
    lines.append(
        "若 fcitx5 插件缺失：sudo apt install fcitx5-frontend-qt6；"
        "ibus：sudo apt install ibus")
    return "\n".join(lines)
