#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公文汇编助手 —— 程序入口。

单机离线运行：本程序不发起任何网络请求，全部数据存于本地 SQLite。

用法：
  python main.py                     # 正常启动
  python main.py --portable          # 便携模式（数据存于程序同级 Data/）
  python main.py --import 路径       # 启动并导入指定文件（配合右键菜单）
  python main.py --install-context-menu [exe路径]   # 安装资源管理器右键菜单
  python main.py --uninstall-context-menu           # 卸载右键菜单
  python main.py --runtime-report    # 打印运行环境能力（不启动 GUI，供打包冒烟校验）
"""
import argparse
import sys
from pathlib import Path

# 确保源码目录可导入（打包后不需要）
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gwtool import paths


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="gwtool", add_help=False)
    ap.add_argument("--portable", action="store_true", help="便携模式")
    ap.add_argument("--import", dest="import_path", default="", help="启动时导入文件")
    # 右键菜单的登记/注销走这里而不是批处理里的 `reg add`：后者要过控制台
    # 代码页，CP936 下中文会写成乱码；winreg 直接用 Unicode，
    # 且写入后能立刻读回校验（见 core/win_integration 的说明）。
    ap.add_argument("--install-context-menu", nargs="?", const="", default=None,
                    help="安装资源管理器右键菜单（可给 exe 路径）")
    ap.add_argument("--uninstall-context-menu", action="store_true",
                    help="卸载资源管理器右键菜单")
    # 供 scripts/smoke_dist.py 在**打包产物**上核对能力面：产物里到底有没有
    # L4/L5 推理栈、OCR 引擎，只有让产物自己报出来才作数（在构建机上 import
    # 成功不代表冻结后的 exe 也带进去了）。不启动 GUI，直接打印后退出。
    ap.add_argument("--runtime-report", action="store_true",
                    help="打印运行环境能力后退出（打包冒烟校验用）")
    args, _rest = ap.parse_known_args()
    return args


_args = _parse_args()
if _args.portable or (getattr(sys, "frozen", False)
                      and (Path(sys.executable).parent / "Data").exists()):
    paths.set_portable(True)


def _handle_context_menu() -> "int | None":
    """处理右键菜单相关命令；未涉及时返回 None，由调用方继续启动 GUI。"""
    if _args.install_context_menu is None and not _args.uninstall_context_menu:
        return None
    from gwtool.core import win_integration

    if not win_integration.supported():
        print("右键菜单集成仅支持 Windows。", file=sys.stderr)
        return 2

    if _args.uninstall_context_menu:
        print("已卸载右键菜单。" if win_integration.uninstall()
              else "未发现已安装的右键菜单。")
        return 0

    # 空字符串表示"用当前程序自己的路径"（便携版与安装版都适用）
    exe = _args.install_context_menu or sys.executable
    try:
        ok = win_integration.install(exe)
    except OSError as exc:
        print(f"安装右键菜单失败：{exc}", file=sys.stderr)
        return 1
    if not ok:
        print("安装右键菜单失败：写入注册表后读回校验未通过。", file=sys.stderr)
        return 1
    print(f"已安装右键菜单：任意文件右键 ->「{win_integration.MENU_LABEL}」")
    print(f"  指向程序：{exe}")
    return 0


_cm_rc = _handle_context_menu()
if _cm_rc is not None:
    sys.exit(_cm_rc)


def _runtime_report() -> int:
    """打印本产物的能力面（不启动 GUI），供打包冒烟校验断言。

    为什么必须由**产物自己**回答：在构建机上 `import onnxruntime` 成功，
    并不等于 PyInstaller 把它打进了 exe —— 漏一个 hiddenimport，产物照样
    "打包成功"，但到用户机上 L4/L5 永远开不起来。只有让冻结后的可执行文件
    自报，才真正回答了"这份安装包有没有这个功能"。
    """
    import platform

    print(f"platform: {platform.machine()} / {sys.platform}")
    print(f"python: {platform.python_version()}")
    print(f"frozen: {bool(getattr(sys, 'frozen', False))}")

    # 可选推理栈。任何异常都咽掉——本命令只用于探报告，不该因某模块初始化
    # 失败而整体崩掉（那会让冒烟校验得到"启动即退"的误导性结论）。
    import importlib.util

    for mod in ("onnxruntime", "tokenizers", "numpy"):
        try:
            if importlib.util.find_spec(mod) is None:
                print(f"module.{mod}: MISSING")
                continue
            print(f"module.{mod}: {getattr(__import__(mod), '__version__', '?')}")
        except Exception as exc:
            print(f"module.{mod}: ERROR {type(exc).__name__}")

    # L4/L5 的"可启用性"：runtime_available 看依赖，available 还要看增强包是否导入。
    for layer, modname in (("L4", "csc_neural"), ("L5", "csc_gec")):
        try:
            mod = __import__(f"gwtool.core.{modname}", fromlist=["x"])
            print(f"{layer}.runtime: {mod.runtime_available()}")
            print(f"{layer}.available: {mod.available()}")
        except Exception as exc:
            print(f"{layer}.runtime: ERROR {type(exc).__name__}")

    # OCR 引擎（内置 Tesseract + 中文包）——与 L4/L5 同理，是"功能面"的一部分。
    try:
        from gwtool.core import ocr

        print(f"ocr.available: {ocr.available()}")
        # 单独报告"是否走产物自带引擎"：available 只看"能不能找到一个 tesseract"，
        # 构建机/开发机常装有系统级引擎，于是 available 为真、产物里却**没有**自带
        # 的那一份 —— 到离线用户机上就不成立了。using_bundled 才是交付口径。
        print(f"ocr.path: {ocr.tesseract_path() or 'NONE'}")
        print(f"ocr.bundled: {ocr.using_bundled()}")
        print(f"ocr.chi_sim: {ocr.has_chi_sim()}")
    except Exception as exc:
        print(f"ocr.available: ERROR {type(exc).__name__}")
    return 0


if _args.runtime_report:
    sys.exit(_runtime_report())

from gwtool.app import run

if __name__ == "__main__":
    sys.exit(run(import_path=_args.import_path))
