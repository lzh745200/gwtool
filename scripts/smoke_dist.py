# -*- coding: utf-8 -*-
"""打包产物冒烟校验：确认可执行文件不只是"存在"，而是真能跑起来。

CI 里 PyInstaller 打包成功并不代表安装包可用——缺 Qt 插件、缺 seed.db、
缺字体兜底都会让程序在用户机上启动即崩或输出空白，而这些在"打包无报错"
的日志里完全看不出来。本脚本在打包后、发布前实测：

  1. 关键数据/插件是否真进了产物（seed.db、qsvg 图标插件、opencc、jieba 词典）
  2. 以子进程真实启动可执行文件，确认不会秒退
  3. 首启动种子导入是否在打包环境下可用（用便携模式落在临时 Data/，
     不污染真实用户数据），并校验纠错对条数达标

用法：python scripts/smoke_dist.py [产物目录]   默认 dist/gwtool
退出码：0 全部通过；1 有失败项。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

IS_WIN = sys.platform.startswith("win")
EXE_NAME = "gwtool.exe" if IS_WIN else "gwtool"
ALIVE_SECONDS = 12          # 启动后需存活这么久才算没崩（首启动要导种子库）
MIN_ERROR_PAIRS = 30000     # 与 README/e2e 的验收口径一致

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(name)
    return ok


def find_one(root: Path, *needles: str) -> Path | None:
    """在产物目录里递归找第一个文件名包含任一 needle 的文件。"""
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        low = path.name.lower()
        if any(n.lower() in low for n in needles):
            return path
    return None


def main() -> int:
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist/gwtool").resolve()
    print(f"== 校验产物目录：{dist}")
    if not check("产物目录存在", dist.is_dir()):
        return 1

    exe = dist / EXE_NAME
    if not check(f"可执行文件 {EXE_NAME}", exe.is_file()):
        return 1

    # ---- 1. 关键资源是否真被打包进去 ----
    print("\n-- 打包资源 --")
    seed = find_one(dist, "seed.db")
    check("seed.db（离线词典与纠错库）", seed is not None,
          str(seed.relative_to(dist)) if seed else "缺失将导致首启动无纠错库")

    # icons.py 用 QImage.fromData(..., "SVG") 画图标，依赖 imageformats/qsvg 插件；
    # 缺了不报错，只是工具栏图标全部空白。注意 iconengines/qsvgicon 不能替代它。
    qsvg = next((p for p in dist.rglob("*")
                 if p.is_file() and "qsvg" in p.name.lower()
                 and "imageformats" in str(p.parent).lower()), None)
    check("imageformats/qsvg 图标插件", qsvg is not None,
          str(qsvg.relative_to(dist)) if qsvg else "缺失则工具栏图标全空白")

    check("opencc 简繁转换词典", find_one(dist, "TSCharacters", "STCharacters",
                                          "opencc") is not None)
    check("jieba 分词词典", find_one(dist, "dict.txt") is not None)

    # ---- 2. 真实启动 ----
    print("\n-- 启动实测 --")
    # 便携模式：程序同级存在 Data/ 即把数据写在那里，不碰真实用户数据，
    # 同时正好验证"全新电脑首启动"这条路径。
    data_dir = dist / "Data"
    fresh = not data_dir.exists()
    data_dir.mkdir(exist_ok=True)

    env = dict(os.environ)
    if not IS_WIN:
        env["QT_QPA_PLATFORM"] = "offscreen"   # CI 无显示环境

    started = time.time()
    proc = subprocess.Popen([str(exe), "--portable"], cwd=str(dist), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    alive = True
    try:
        proc.wait(timeout=ALIVE_SECONDS)
        alive = False            # 没到时间就退出了
    except subprocess.TimeoutExpired:
        pass                     # 仍在运行 = 正常（GUI 程序本就该常驻）

    out = b""
    if not alive:
        try:
            out = proc.stdout.read() if proc.stdout else b""
        except Exception:        # noqa: BLE001  仅用于诊断输出
            out = b""
    elapsed = time.time() - started
    check(f"启动后存活 ≥{ALIVE_SECONDS}s", alive,
          f"实际 {elapsed:.1f}s 后退出，返回码 {proc.returncode}")
    if out:
        print("      进程输出：", out.decode("utf-8", "replace")[:800])

    # ---- 3. 首启动种子导入是否可用 ----
    db = data_dir / "gwtool.db"
    seeded = check("首启动生成数据库", db.is_file(),
                   str(db.relative_to(dist)) if db.is_file() else "未生成 gwtool.db")
    if seeded:
        import sqlite3
        try:
            conn = sqlite3.connect(str(db))
            conn.execute("PRAGMA query_only=1")
            n = conn.execute("SELECT count(*) FROM error_pairs").fetchone()[0]
            conn.close()
            check(f"纠错库 ≥{MIN_ERROR_PAIRS} 条", n >= MIN_ERROR_PAIRS, f"{n} 条")
        except Exception as exc:                  # noqa: BLE001  诊断为主
            check("纠错库可读", False, f"{type(exc).__name__}: {exc}")

    if alive:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    # 便携 Data/ 是本次校验造的，清掉以免混进安装包产物
    if fresh:
        shutil.rmtree(data_dir, ignore_errors=True)

    print(f"\n===== 产物冒烟：{len(failures)} 项失败 =====")
    if failures:
        print("失败项：", failures)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
