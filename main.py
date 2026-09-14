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
"""
import argparse
import sys
from pathlib import Path

# 确保源码目录可导入（打包后不需要）
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gwtool import paths  # noqa: E402


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

from gwtool.app import run  # noqa: E402

if __name__ == "__main__":
    sys.exit(run(import_path=_args.import_path))
