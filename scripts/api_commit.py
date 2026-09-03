# -*- coding: utf-8 -*-
"""GitHub API 提交工具：当 github.com:443 被阻断时，改用 api.github.com
创建 blob/tree/commit 并更新分支引用（等效 git push）。

用法：python scripts/api_commit.py <message> <file1> [file2 ...] [--tag=vX.Y.Z]
      python scripts/api_commit.py <message> --auto [--tag=vX.Y.Z]
      --auto：自动比对远端 main，只上传新增/变更的文件（推荐）。
      给出 --tag 时在推送后把该标签移到新提交，用于触发 Release 构建。
凭据：复用 Git Credential Manager 已缓存令牌（git credential fill）。
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = "lzh745200/gwtool"
API_HOST = "api.github.com"

# 本地产物与虚拟环境，绝不能上传（与 .gitignore 保持一致）
SKIP_DIRS = {".git", "__pycache__", ".venv", "build", "dist", ".pytest_cache",
             ".ruff_cache", "node_modules", ".qoder", "wheels_aarch64",
             "dist_samples"}


def git_blob_sha(raw: bytes) -> str:
    """git 的对象哈希：blob <len>\\0<content> 的 sha1，与 trees 接口返回值可直接比对。"""
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(raw))
    h.update(raw)
    return h.hexdigest()


def scan_local(root: Path = Path(".")) -> dict[str, bytes]:
    """收集本地待比对文件的相对路径与内容。"""
    out: dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            if name.endswith((".pyc", ".tmp")):
                continue
            out[rel] = full.read_bytes()
    return out


def detect_changes(token: str, base_sha: str) -> list[tuple[str, bytes]]:
    """与远端 main 树逐文件比对，返回需要上传的 (路径, 内容) 列表。"""
    tree = api("GET", f"/git/trees/{base_sha}?recursive=1", token)
    if tree.get("truncated"):
        raise RuntimeError("远端树被截断，无法安全比对，请改用显式文件列表")
    remote = {e["path"]: e["sha"] for e in tree.get("tree", []) if e["type"] == "blob"}
    local = scan_local()
    changed = [(p, data) for p, data in sorted(local.items())
               if remote.get(p) != git_blob_sha(data)]
    missing = sorted(set(remote) - set(local))
    if missing:
        # 本工具只能新增/修改，无法删除远端文件；出现这种情况必须人工处理
        print("[api] 警告：以下文件仅存在于远端，本地缺失（本工具不会删除它们）：",
              file=sys.stderr)
        for p in missing:
            print(f"       {p}", file=sys.stderr)
    return changed


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
            time.sleep(3)


def main() -> int:
    tag = ""
    auto = False
    positional = []
    for a in sys.argv[1:]:
        if a.startswith("--tag="):
            tag = a.split("=", 1)[1].strip()
        elif a == "--auto":
            auto = True
        else:
            positional.append(a)
    if not positional or (not auto and len(positional) < 2):
        print("用法：python scripts/api_commit.py <提交说明> <文件1> [文件2 ...] "
              "[--tag=vX.Y.Z]", file=sys.stderr)
        print("      python scripts/api_commit.py <提交说明> --auto [--tag=vX.Y.Z]",
              file=sys.stderr)
        print("      --auto 自动比对远端 main，只上传新增/变更的文件。", file=sys.stderr)
        print("      给出 --tag 时，推送后把该标签移到新提交以触发 Release 构建。",
              file=sys.stderr)
        return 2
    message = positional[0]
    token = get_token()

    me = api("GET", "//user", token)
    print(f"[api] 凭据有效：{me.get('login')}")

    ref = api("GET", "/git/ref/heads/main", token)
    base_sha = ref["object"]["sha"]
    print(f"[api] 远端 main = {base_sha[:10]}")

    if auto:
        payloads = detect_changes(token, base_sha)
        print(f"[api] 自动比对：{len(payloads)} 个文件需要上传")
        if not payloads:
            print("[api] 本地与远端一致，无需提交")
            return 0
    else:
        payloads = []
        for p in positional[1:]:
            path = Path(p)
            if not path.is_file():
                print(f"[api] 错误：文件不存在 {p}", file=sys.stderr)
                return 2
            payloads.append((path.as_posix(), path.read_bytes()))

    tree_items = []
    for rel_path, data in payloads:
        blob = api("POST", "/git/blobs", token,
                   {"content": base64.b64encode(data).decode(),
                    "encoding": "base64"})
        tree_items.append({"path": rel_path, "mode": "100644",
                           "type": "blob", "sha": blob["sha"]})
        print(f"[api] blob {rel_path} ({len(data)} 字节) -> {blob['sha'][:10]}")

    tree = api("POST", "/git/trees", token,
               {"base_tree": base_sha, "tree": tree_items})
    commit = api("POST", "/git/commits", token,
                 {"message": message, "tree": tree["sha"], "parents": [base_sha]})
    print(f"[api] commit = {commit['sha'][:10]}")

    api("PATCH", "/git/refs/heads/main", token,
        {"sha": commit["sha"], "force": False})
    print("[api] main 已更新")

    if tag:
        try:
            api("PATCH", f"/git/refs/tags/{tag}", token,
                {"sha": commit["sha"], "force": True})
            print(f"[api] tag {tag} 已移动 -> 触发工作流")
        except RuntimeError as exc:
            if "404" not in str(exc):
                raise
            # 标签尚不存在：PATCH 会 404，改用 POST 创建
            api("POST", "/git/refs", token,
                {"ref": f"refs/tags/{tag}", "sha": commit["sha"], "force": True})
            print(f"[api] tag {tag} 已创建 -> 触发工作流")
    return 0


if __name__ == "__main__":
    sys.exit(main())
