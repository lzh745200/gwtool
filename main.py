#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公文汇编助手 —— 程序入口。

单机离线运行：本程序不发起任何网络请求，全部数据存于本地 SQLite。

用法：
  python main.py                     # 正常启动
  python main.py --portable          # 便携模式（数据存于程序同级 Data/）
  python main.py --import 路径       # 启动并导入指定文件（配合右键菜单）
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
    args, _rest = ap.parse_known_args()
    return args


_args = _parse_args()
if _args.portable or (getattr(sys, "frozen", False)
                      and (Path(sys.executable).parent / "Data").exists()):
    paths.set_portable(True)

from gwtool.app import run  # noqa: E402

if __name__ == "__main__":
    sys.exit(run(import_path=_args.import_path))
