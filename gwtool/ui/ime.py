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
# 用户/发行版可能写成 fcitx5 或 fcitx4，插件键统一叫 fcitx
_MODULE_ALIASES = {"fcitx5": "fcitx", "fcitx4": "fcitx", "fcitx5-qt": "fcitx"}
# 不依赖任何 Qt 插件、由输入法框架自己提供服务的兜底（仅 X11 可用）
_XIM = "xim"


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


def _is_wayland() -> bool:
    return (os.environ.get("XDG_SESSION_TYPE") or "").strip().lower() == "wayland"


def _module_available(module: str, plugins: dict[str, Path | None]) -> bool:
    """`QT_IM_MODULE` 的取值是否有对应插件可用。

    只有 compose/ibus/fcitx 三种是靠**插件文件**加载的，能查；xim 由 Qt 的 xcb
    平台插件内部实现（没有独立 .so），其余自定义值同样无法用文件清单判定 ——
    对它们一律放行，交给 Qt 自己处理，避免把用户本来就对的设置改坏。
    """
    key = _MODULE_ALIASES.get(module, module)
    if key not in _PLUGIN_NAMES:
        return True
    return plugins.get(key) is not None


def configure(force: bool = False) -> dict:
    """在 QApplication 前调用；返回诊断字典（可安全写入日志）。

    force=True 仅供单元测试在非 Linux 主机上验证选择逻辑。

    决策顺序（**任何一步都不能盲信上层设置**）：
      1. QT_IM_MODULE 已设置且对应插件存在 → 尊重用户；
      2. 已设置但插件不存在 → 记 invalid 并降级（Qt 找不到插件时连英文替换都进不去，
         比 compose 更糟，所以"保留用户设置"在这里是错误的做法）；
      3. 未设置：按会话提示装对应插件（最佳）；
      4. 有提示但插件缺失 + X11 → 用 XIM 兜底（fcitx/ibus 自己提供 XIM 服务，
         **不依赖任何 Qt 插件**，能打中文）；
      5. 都没有 → compose（能加载但只能输拉丁字符，仅保证"输入框不瘫"）。
    """
    info: dict = {
        "platform": sys.platform,
        "session_type": os.environ.get("XDG_SESSION_TYPE", ""),
        "wayland": _is_wayland(),
        "display": os.environ.get("DISPLAY", ""),
        "xmodifiers": os.environ.get("XMODIFIERS", ""),
        "gtk_im_module": os.environ.get("GTK_IM_MODULE", ""),
        "qt_im_module_before": os.environ.get("QT_IM_MODULE", ""),
        "chosen": "",
        "usable": False,
        "invalid_request": "",
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
        if current and not _module_available(current, plugins):
            info["invalid_request"] = current
            info["warnings"].append(
                f"QT_IM_MODULE={current} 指向的 Qt6 插件在本机找不到，已自动降级"
                f"（否则 Qt 会一个输入法都不加载）")
            current = ""

        if current:
            info["chosen"] = current
        elif hint and plugins.get(hint):
            os.environ["QT_IM_MODULE"] = hint
            info["chosen"] = hint
        elif hint and not info["wayland"] and info["display"]:
            os.environ["QT_IM_MODULE"] = _XIM
            info["chosen"] = _XIM
            info["warnings"].append(
                f"系统使用 {hint}，但缺少 Qt6 前端插件；已改用 XIM 兜底"
                f"（可正常输入中文，候选框位置可能不理想）")
        else:
            info["chosen"] = "compose" if plugins.get("compose") else ""
            msg = "未找到 Qt6 输入法插件（compose/ibus/fcitx5）"
            if info["wayland"]:
                msg += "；当前是 Wayland 会话，XIM 兜底不可用"
            info["warnings"].append(msg)
        # compose 只能输拉丁字符，"加载成功"不等于"能打中文"，一律按不可用处理；
        # 这条同时决定启动时要不要弹窗 —— 能用就不该打扰用户。
        info["usable"] = info["chosen"] in ("fcitx", "ibus", _XIM)
        return info
    except Exception as exc:            # 输入法适配绝不能阻断启动
        info["warnings"].append(f"输入法适配失败：{exc}")
        return info


def diagnostics_text(info: dict | None = None) -> str:
    """一行行可读诊断（写日志/--startup-report 用）。"""
    info = info or {}
    lines = [
        f"session: {info.get('session_type', '')}"
        f"  wayland={info.get('wayland', False)}  DISPLAY={info.get('display', '')}"
        f"  XMODIFIERS={info.get('xmodifiers', '')}",
        f"GTK_IM_MODULE={info.get('gtk_im_module', '')}  "
        f"QT_IM_MODULE={info.get('qt_im_module_before', '')} -> {info.get('chosen', '')}",
    ]
    if info.get("invalid_request"):
        lines.append(f"invalid_request: {info['invalid_request']}（插件缺失，已降级）")
    for k, v in (info.get("plugins") or {}).items():
        lines.append(f"plugin[{k}]: {v or 'MISSING'}")
    for w in info.get("warnings") or []:
        lines.append(f"WARN: {w}")
    chosen = info.get("chosen", "")
    if info.get("platform", sys.platform) != "linux":
        # Windows/macOS 的输入法由系统原生提供（走 IMM/TSF），不存在"插件"这回事。
        # 不区分平台就会在 Windows 上打印"中文输入不可用"，把自己人吓一跳。
        lines.append("非 Linux 平台：输入法由系统原生提供，本节不适用。")
        return "\n".join(lines)
    if chosen == _XIM:
        lines.append("当前走 XIM 兜底：能输入中文；装前端插件后候选框体验更好。")
    elif chosen in ("fcitx", "ibus"):
        lines.append(f"当前使用 {chosen} 的 Qt6 前端插件。")
    elif chosen == "compose":
        lines.append("当前只有 compose 插件：只能输拉丁字符，打不了中文。")
    else:
        lines.append("没有加载任何输入法插件：中文输入不可用。")
    lines.append(
        "若 fcitx5 插件缺失：sudo apt install fcitx5-frontend-qt6；"
        "ibus：sudo apt install ibus")
    lines.append(
        "Wayland 会话且无插件时，可临时 `QT_QPA_PLATFORM=xcb ./gwtool`"
        " 走 X11（XWayland）后用 XIM 兜底。")
    return "\n".join(lines)
