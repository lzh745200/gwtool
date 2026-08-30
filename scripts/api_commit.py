# -*- coding: utf-8 -*-
"""GitHub API 提交工具：当 github.com:443 被阻断时，改用 api.github.com
创建 blob/tree/commit 并更新分支引用（等效 git push）。

用法：python scripts/api_commit.py <message> <file1> [file2 ...]
凭据：复用 Git Credential Manager 已缓存令牌（git credential fill）。
"""
from __future__ import annotations

import base64
import http.client
import json
import subprocess
import sys
from pathlib import Path

REPO = "lzh745200/gwtool"
API_HOST = "api.github.com"


def get_token() -> str:
    out = subprocess.run(
        ["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, timeout=20, check=True).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("未取到缓存凭据（请先成功 git push 一次）")


def api(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    """直连 api.github.com。path 以 "//" 开头表示 API 根路径，否则相对仓库。"""
    if path.startswith("//"):
        request_path = path[1:]
    else:
        request_path = f"/repos/{REPO}{path}"
    body = json.dumps(payload).encode() if payload is not None else None
    for attempt in (1, 2, 3):
        try:
            conn = http.client.HTTPSConnection(API_HOST, timeout=180)
            conn.request(method, request_path, body=body, headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "gwtool-api-commit",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)) if body else "0",
            })
            resp = conn.getresponse()
            data = resp.read()
            conn.close()
            if resp.status >= 400:
                raise RuntimeError(
                    f"{method} {path} -> HTTP {resp.status}: "
                    f"{data.decode('utf-8', errors='replace')[:300]}")
            return json.loads(data) if data else {}
        except (http.client.HTTPException, ConnectionError, TimeoutError) as exc:
            if attempt == 3:
                raise RuntimeError(f"{method} {path} 网络失败：{exc}") from exc
            import time
            time.sleep(3)


def main() -> int:
    message = sys.argv[1]
    files = [Path(p) for p in sys.argv[2:]]
    token = get_token()

    me = api("GET", "//user", token)
    print(f"[api] 凭据有效：{me.get('login')}")

    ref = api("GET", "/git/ref/heads/main", token)
    base_sha = ref["object"]["sha"]
    print(f"[api] 远端 main = {base_sha[:10]}")

    tree_items = []
    for f in files:
        data = f.read_bytes()
        blob = api("POST", "/git/blobs", token,
                   {"content": base64.b64encode(data).decode(),
                    "encoding": "base64"})
        tree_items.append({"path": f.as_posix(), "mode": "100644",
                           "type": "blob", "sha": blob["sha"]})
        print(f"[api] blob {f} ({len(data)} 字节) -> {blob['sha'][:10]}")

    tree = api("POST", "/git/trees", token,
               {"base_tree": base_sha, "tree": tree_items})
    commit = api("POST", "/git/commits", token,
                 {"message": message, "tree": tree["sha"], "parents": [base_sha]})
    print(f"[api] commit = {commit['sha'][:10]}")

    api("PATCH", "/git/refs/heads/main", token,
        {"sha": commit["sha"], "force": False})
    print("[api] main 已更新")

    api("PATCH", "/git/refs/tags/v1.0.0", token,
        {"sha": commit["sha"], "force": True})
    print("[api] tag v1.0.0 已移动 -> 触发工作流")
    return 0


if __name__ == "__main__":
    sys.exit(main())
