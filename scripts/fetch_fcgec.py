# -*- coding: utf-8 -*-
"""P3：拉取独立病句基准 FCGEC valid（不随仓库分发）。

来源：https://github.com/xlxwalex/FCGEC （EMNLP 2022，仓库 Apache-2.0）
数据文件：data/FCGEC_valid.json
固定 sha256：6afb961cae85c0fce8aa97dd7e0c68907a704ff7b08e0b9aa337128e24fdf9f2
落盘：doc/bench/fcgec_valid.json（.gitignore 已排除；仅本地评测用，不再分发）

用法：python scripts/fetch_fcgec.py [--force]
网络：raw.githubusercontent 常被阻断，这里走 api.github.com 的 contents 接口
（base64），稳定且只读。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "doc" / "bench" / "fcgec_valid.json"
API = "https://api.github.com/repos/xlxwalex/FCGEC/contents/data/FCGEC_valid.json"
SHA256 = "6afb961cae85c0fce8aa97dd7e0c68907a704ff7b08e0b9aa337128e24fdf9f2"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if OUT.is_file() and not args.force:
        data = OUT.read_bytes()
        if hashlib.sha256(data).hexdigest() == SHA256:
            print(f"已存在且校验通过：{OUT}（{len(data)} bytes）")
            return 0
    req = urllib.request.Request(API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "gwtool-bench-fetch",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    raw = base64.b64decode(payload["content"])
    got = hashlib.sha256(raw).hexdigest()
    if got != SHA256:
        print(f"哈希不匹配：期望 {SHA256}，实际 {got}", file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(raw)
    rows = json.loads(raw.decode("utf-8"))
    bad = sum(1 for v in rows.values() if v.get("error_flag") == 1)
    print(f"已下载：{OUT}（{len(raw)} bytes，{len(rows)} 句 / 病句 {bad}）")
    print("来源：https://github.com/xlxwalex/FCGEC（Apache-2.0，仅本地评测，不再分发）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
