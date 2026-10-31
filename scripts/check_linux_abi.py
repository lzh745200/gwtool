# -*- coding: utf-8 -*-
"""检查 Linux 产物对 GLIBC / GLIBCXX 的最低版本要求（麒麟兼容门禁）。

背景：用较新发行版构建的 Qt/Python 二进制，在麒麟 V10 上会报
`version 'GLIBC_2.xx' not found` / `GLIBCXX_3.4.xx not found`，表现为双击闪退、
终端没有任何业务日志。此脚本在 CI 里对**最终产物**逐个二进制取最高版本要求，
超过目标上限就让构建失败，避免把不兼容的包发出去。

用法：
  python scripts/check_linux_abi.py pkgsrc/gwtool \
      --max-glibc 2.31 --max-glibcxx 3.4.29
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

_RE_GLIBC = re.compile(r"GLIBC_(\d+\.\d+(?:\.\d+)?)")
_RE_GLIBCXX = re.compile(r"GLIBCXX_(\d+\.\d+(?:\.\d+)?)")


def _ver(s: str) -> tuple:
    return tuple(int(x) for x in s.split("."))


def _scan(path: Path) -> tuple[set[str], set[str]]:
    """返回该文件里出现的 GLIBC_/GLIBCXX_ 版本集合。"""
    blobs = []
    try:
        out = subprocess.run(["readelf", "--version-info", str(path)],
                             capture_output=True, text=True, timeout=30)
        blobs.append(out.stdout + out.stderr)
    except Exception:
        pass
    try:
        out = subprocess.run(["strings", "-a", str(path)],
                             capture_output=True, text=True, timeout=30,
                             errors="ignore")
        blobs.append(out.stdout)
    except Exception:
        pass
    text = "\n".join(blobs)
    return set(_RE_GLIBC.findall(text)), set(_RE_GLIBCXX.findall(text))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="产物目录（pkgsrc/gwtool）")
    ap.add_argument("--max-glibc", default="2.31")
    ap.add_argument("--max-glibcxx", default="3.4.29")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"错误：目录不存在 {root}", file=sys.stderr)
        return 2
    targets = [p for p in root.rglob("*")
               if p.is_file() and (p.suffix in ("", ".so")
                                   or ".so." in p.name)]
    if not targets:
        print("错误：未找到可扫描的二进制", file=sys.stderr)
        return 2

    max_g = _ver(args.max_glibc)
    max_gxx = _ver(args.max_glibcxx)
    worst_g = worst_gxx = None
    worst_g_file = worst_gxx_file = ""
    bad = []
    for p in targets:
        try:
            with p.open("rb") as fh:
                if b"\x7fELF" != fh.read(4):
                    continue
        except OSError:
            continue
        g, gxx = _scan(p)
        if g:
            top = max(g, key=_ver)
            if worst_g is None or _ver(top) > _ver(worst_g):
                worst_g, worst_g_file = top, str(p.relative_to(root))
            over = [v for v in g if _ver(v) > max_g]
            if over:
                bad.append((str(p.relative_to(root)), "GLIBC", sorted(over, key=_ver)))
        if gxx:
            top = max(gxx, key=_ver)
            if worst_gxx is None or _ver(top) > _ver(worst_gxx):
                worst_gxx, worst_gxx_file = top, str(p.relative_to(root))
            over = [v for v in gxx if _ver(v) > max_gxx]
            if over:
                bad.append((str(p.relative_to(root)), "GLIBCXX", sorted(over, key=_ver)))

    print(f"扫描二进制 {len(targets)} 个")
    print(f"最高 GLIBC   ：{worst_g or '—'}  （{worst_g_file}）上限 {args.max_glibc}")
    print(f"最高 GLIBCXX ：{worst_gxx or '—'}  （{worst_gxx_file}）上限 {args.max_glibcxx}")
    if bad:
        print("\n以下文件要求高于目标机，麒麟上会闪退：")
        for f, kind, vers in bad[:20]:
            print(f"  {f}: {kind} {', '.join(vers)}")
        return 1
    print("ABI 门禁通过：产物可在 glibc >= "
          f"{args.max_glibc} 的目标机运行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
