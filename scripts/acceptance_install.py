# -*- coding: utf-8 -*-
"""安装验收测试：对 Inno Setup 安装后的目录做与 smoke_dist 同口径的校验。

用法：python scripts/acceptance_install.py <安装目录>
校验：关键资源齐全 -> 真实启动存活 >=12s -> 首启动种子导入（纠错库条数）。
退出码：0 全部通过；1 有失败项。
"""
from __future__ import annotations

import shutil
import subprocess
import sqlite3
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

ALIVE_SECONDS = 12
MIN_ERROR_PAIRS = 30000
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(name)
    return ok


def main() -> int:
    if len(sys.argv) < 2:
        print("用法：python scripts/acceptance_install.py <安装目录>\n"
              "      例：python scripts/acceptance_install.py "
              "\"%LOCALAPPDATA%\\Programs\\gwtool\"", file=sys.stderr)
        return 2
    root = Path(sys.argv[1]).resolve()
    if not root.is_dir():
        print(f"错误：安装目录不存在：{root}", file=sys.stderr)
        return 2
    print(f"== 安装验收：{root}")
    exe = root / "gwtool.exe"
    if not check("gwtool.exe 已安装", exe.is_file()):
        return 1
    if not check("_internal 运行库已安装", (root / "_internal").is_dir()):
        return 1
    check("seed.db 已安装",
          any("seed.db" == p.name for p in root.rglob("seed.db")))
    check("内置 Tesseract 已安装", (root / "tesseract" / "tesseract.exe").is_file())
    check("内置 chi_sim 中文包已安装",
          (root / "tesseract" / "tessdata" / "chi_sim.traineddata").is_file())
    check("卸载器已生成", any(p.name.startswith("unins") for p in root.glob("unins*")))

    print("\n-- 启动实测 --")
    data_dir = root / "Data"
    # 无条件清理：残留的便携数据库会让"首启动"变成读旧数据（假通过）
    if data_dir.exists():
        shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(exist_ok=True)
    fresh = True
    started = time.time()
    proc = subprocess.Popen([str(exe), "--portable"], cwd=str(root),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    alive = True
    try:
        proc.wait(timeout=ALIVE_SECONDS)
        alive = False
    except subprocess.TimeoutExpired:
        pass
    if not alive and proc.stdout:
        out = proc.stdout.read() or b""
        print("      退出输出：", out.decode("utf-8", "replace")[:600])
    check(f"启动后存活 ≥{ALIVE_SECONDS}s", alive,
          f"实际 {time.time() - started:.1f}s")

    db = data_dir / "gwtool.db"
    if check("首启动生成数据库", db.is_file()):
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA query_only=1")
        n = conn.execute("SELECT count(*) FROM error_pairs").fetchone()[0]
        conn.close()
        check(f"纠错库 ≥{MIN_ERROR_PAIRS} 条", n >= MIN_ERROR_PAIRS, f"{n} 条")

    if alive:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    if fresh:
        import shutil
        shutil.rmtree(data_dir, ignore_errors=True)

    print(f"\n===== 安装验收：{len(failures)} 项失败 =====")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
