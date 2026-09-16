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

# 英文区域设置的 Windows 与 GitHub Windows runner 控制台默认是 charmap 编码，
# print 中文会抛 UnicodeEncodeError 让校验脚本自己先崩掉，故强制 UTF-8 输出。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

IS_WIN = sys.platform.startswith("win")
EXE_NAME = "gwtool.exe" if IS_WIN else "gwtool"
ALIVE_SECONDS = 12          # 启动后需存活这么久才算没崩（首启动要导种子库）
MIN_ERROR_PAIRS = 30000     # 与 README/e2e 的验收口径一致

# L4/L5 可选推理栈的模块集（与 scripts/check_inference_stack.py 同口径）。
# 缺失不算"产物不可用"，但必须显式报告——静默的功能缺失正是过去 ARM64
# 长期"看起来正常、实际没有增强层"的原因。
BUILTIN_STACK = ("onnxruntime", "tokenizers", "numpy")

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


def _runtime_report(exe: Path, dist: Path, env: dict) -> dict | None:
    """让**产物自己**报告能力面，解析 `key: value` 行。

    返回 None 表示命令不可用/输出不可解析（调用方据此判失败）。
    注意 cwd 传 dist：便携模式靠"同级有 Data/"生效，本命令不建 Data/，
    但保持与启动实测同一工作目录，避免路径相关的行为差异。
    """
    try:
        out = subprocess.run(
            [str(exe), "--runtime-report"], cwd=str(dist), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=60, check=False,
        ).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"      --runtime-report 执行失败：{type(exc).__name__}: {exc}")
        return None

    cap: dict[str, str] = {}
    for raw in out.splitlines():
        if ":" not in raw:
            continue
        key, _, val = raw.partition(":")
        key, val = key.strip(), val.strip()
        if key:
            cap[key] = val
    if not cap:
        print("      --runtime-report 无可解析输出：", out[:300])
        return None
    for key, val in cap.items():
        print(f"      {key}: {val}")
    return cap


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
    # OCR 内置（v1.5.0）：Tesseract 二进制与中文包随包分发。
    # 必须按**确切路径**判定，不能用 find_one 的子串匹配：产物里本来就有
    # tesseract\libtesseract-5.dll 与 ocr/lib/libtesseract.so.*，
    # needle 给 "tesseract" 时它们同样命中 —— 于是 tesseract.exe 真的缺失
    # 也会判 PASS，CI 的 OCR 交付门形同虚设（实测已踩中）。
    tess_exe = None
    for cand in (dist / "tesseract" / "tesseract.exe",   # Windows onedir/Inno
                 dist / "ocr" / "bin" / "tesseract"):    # Linux deb/便携
        if cand.is_file():
            tess_exe = cand
            break
    check("内置 Tesseract 可执行文件", tess_exe is not None,
          str(tess_exe.relative_to(dist)) if tess_exe
          else "缺失则 OCR 不可用（注意：libtesseract 动态库不算）")
    chi = find_one(dist, "chi_sim.traineddata")
    check("内置中文 OCR 包 chi_sim", chi is not None,
          str(chi.relative_to(dist)) if chi else "缺失则中文 OCR 不可用")

    # ---- 2. 真实启动 ----
    print("\n-- 启动实测 --")
    # 便携模式：程序同级存在 Data/ 即把数据写在那里，不碰真实用户数据，
    # 同时正好验证"全新电脑首启动"这条路径。
    # 无条件清掉既有 Data/：上一次便携运行残留的库会让"首启动种子导入"
    # 变成读旧数据（假通过），而且残留库会被 Inno 一起打进安装包。
    data_dir = dist / "Data"
    if data_dir.exists():
        shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(exist_ok=True)
    fresh = True

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
        except Exception:
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
        except Exception as exc:
            check("纠错库可读", False, f"{type(exc).__name__}: {exc}")

    if alive:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass

    # ---- 4. 能力面：产物自己报有没有 L4/L5 推理栈与 OCR ----
    # 必须问**产物自己**，不能在构建机上 import 判定：漏一个 hiddenimport，
    # PyInstaller 照样"打包成功"，但用户机上 L4/L5 / OCR 永远不可用。
    # 这一节把"代码在、依赖不在"这类静默功能缺失挡在发布前。
    print("\n-- 能力面（由产物自报）--")
    cap = _runtime_report(exe, dist, env)
    if cap is None:
        check("产物可执行 --runtime-report", False,
              "命令未返回可解析输出（打包或入口有问题）")
    else:
        check("产物可执行 --runtime-report", True, "已取得能力报告")
        missing = [m for m in BUILTIN_STACK if cap.get(f"module.{m}", "MISSING") == "MISSING"]
        # 推理栈是"可选增强"：缺失不算致命（产物仍可用），但必须**显式告知**，
        # 这正是过去 ARM64 长期"静默降级"的根因所在。
        check("L4/L5 推理栈已随产物分发", not missing,
              f"缺失 {missing}；该产物不含 L4/L5 运行时（功能面已削弱，"
              "请确认是有意为之）" if missing else "onnxruntime/tokenizers/numpy 齐备")
        # 逐项核对"L4/L5 是否可启用"。注意区分两层：
        #   runtime = 依赖（onnxruntime/tokenizers）在不在；
        #   available = 依赖 + 增强包是否都就绪。
        # 产物里**不该**预置增强包（模型不随主包分发是硬约束），故 available
        # 期望 False；这里只要求 runtime 为 True，即"闸门修好后随时可用"。
        for layer in ("L4", "L5"):
            check(f"{layer} 运行时可导入", cap.get(f"{layer}.runtime") == "True",
                  f"runtime={cap.get(f'{layer}.runtime', '?')}")

        # OCR 分两个层次核对，只看 available 会被"系统里装了 Tesseract"掩盖：
        # 构建机/开发机常有系统级 Tesseract，于是 available=True，但产物里
        # **没有**自带的引擎 → 到离线用户机上就不可用。故显式核对 bundled 路径。
        check("OCR 引擎可发现", cap.get("ocr.available") == "True",
              f"available={cap.get('ocr.available', '?')}")
        check("OCR 走产物自带引擎", cap.get("ocr.bundled") == "True",
              f"bundled={cap.get('ocr.bundled', '?')}"
              + ("" if cap.get("ocr.bundled") == "True"
                 else "；产物未带 tesseract（离线用户机上 OCR 将不可用）"))
        check("内置中文 OCR 包可用", cap.get("ocr.chi_sim") == "True",
              f"chi_sim={cap.get('ocr.chi_sim', '?')}")

    # 便携 Data/ 是本次校验造的，清掉以免混进安装包产物。
    # Windows 上进程退出后句柄释放有延迟：立即 rmtree 会因 gwtool.db(-wal)
    # 仍被锁定而静默失败（ignore_errors），残留的测试数据库会混进后续的
    # 便携 zip / Inno 安装包。这里带重试的硬删除，全失败则显式报失败。
    if fresh:
        removed = False
        for _ in range(6):
            try:
                shutil.rmtree(data_dir)
                removed = True
                break
            except OSError:
                time.sleep(2)
        if not removed:
            check("便携 Data/ 清理", False,
                  "测试数据库残留，产物不可发布（含约 33MB 冒烟数据）")

    print(f"\n===== 产物冒烟：{len(failures)} 项失败 =====")
    if failures:
        print("失败项：", failures)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
