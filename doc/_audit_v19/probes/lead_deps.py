# -*- coding: utf-8 -*-
"""基线探针 11：依赖健康度（声明 vs 使用 vs 实装）+ 打包内容检查。"""
import io
import os
import re
import subprocess
import sys

sys.path.insert(0, ".")

# 1) requirements 声明
req = io.open("requirements.txt", encoding="utf-8").read()
declared = {}
for line in req.splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    m = re.match(r"^([A-Za-z0-9_.\-]+)", line)
    if m:
        declared[m.group(1).lower()] = line

# 2) 代码里实际 import
third = set()
alias = {"bs4": "beautifulsoup4", "fitz": "pymupdf", "docx": "python-docx",
         "PIL": "pillow", "yaml": "pyyaml", "win32com": "pywin32",
         "pythoncom": "pywin32", "win32api": "pywin32", "win32timezone": "pywin32",
         "opencc": "opencc-python-reimplemented"}
for r, _, fs in os.walk("gwtool"):
    for f in fs:
        if not f.endswith(".py"):
            continue
        p = os.path.join(r, f)
        for line in io.open(p, encoding="utf-8", errors="replace").read().splitlines():
            m = re.match(r"\s*(?:from|import)\s+([A-Za-z_][\w]*)", line)
            if not m:
                continue
            name = m.group(1)
            if name in {"gwtool", "PySide6", "sqlite3", "os", "sys", "re", "io",
                        "json", "time", "datetime", "pathlib", "tempfile",
                        "shutil", "zipfile", "hashlib", "base64", "struct",
                        "subprocess", "threading", "queue", "logging", "typing",
                        "dataclasses", "collections", "contextlib", "functools",
                        "itertools", "random", "math", "copy", "textwrap", "csv",
                        "glob", "unittest", "traceback", "argparse", "enum",
                        "abc", "uuid", "platform", "signal", "socket", "urllib",
                        "http", "email", "html", "xml", "codecs", "string",
                        "operator", "statistics", "unicodedata", "warnings",
                        "weakref", "gc", "inspect", "importlib", "concurrent",
                        "ctypes", "locale", "getpass", "secrets", "binascii",
                        "zlib", "gzip", "tarfile", "sqlite3", "shlex", "stat",
                        "datetime", "calendar", "pickle", "pprint", "types",
                        "typing_extensions", "PySide6.QtCore", "PySide6.QtGui",
                        "PySide6.QtWidgets", "PySide6.QtSvg"}:
                continue
            if name.startswith("_"):
                continue
            third.add(name)

mapped = {alias.get(x, x).lower() for x in third}
missing = sorted(mapped - set(declared))
print("=== 代码使用但 requirements 未声明 ===")
print(missing or "无")

print("\n=== requirements 声明但代码未 import（可能是传递依赖/构建期依赖）===")
unused = sorted(set(declared) - mapped)
print(unused or "无")

# 3) 声明版本 vs 实装版本
print("\n=== 声明版本 vs 当前 venv 实装 ===")
try:
    import importlib.metadata as md
    for name, line in sorted(declared.items()):
        try:
            inst = md.version(name)
        except Exception:  # noqa: BLE001
            inst = "未安装"
        pinned = re.search(r"==\s*([\w.\-]+)", line)
        flag = ""
        if pinned and inst not in ("未安装",) and pinned.group(1) != inst:
            flag = "  ← 与锁定版本不一致"
        if re.search(r"python_version|sys_platform", line) and "win32" not in line:
            flag += "  [条件依赖]"
        print(f"  {name:28} 声明 {pinned.group(1) if pinned else '?':12} "
              f"实装 {inst}{flag}")
except Exception as exc:  # noqa: BLE001
    print("  版本核对失败:", exc)

# 4) 打包配置里排除/包含的目录
print("\n=== gwtool.spec 中 excludes ===")
spec = io.open("gwtool.spec", encoding="utf-8").read()
m = re.search(r"excludes\s*=\s*\[(.*?)\]", spec, re.S)
print(m.group(1).strip()[:600] if m else "未找到")
