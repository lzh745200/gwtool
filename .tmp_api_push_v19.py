# -*- coding: utf-8 -*-
"""github.com:443 被阻断时，用 GitHub API 推送 v1.9.0 提交并创建标签。

与 scripts/api_commit.py 同一凭据/通道，但修了两处：
  · `base_tree` 用 **commit 的 tree sha**（GitHub 文档要求 tree，不是 commit）；
  · 标签用 annotated tag 对象 + 强制 PATCH（发布说明保留），而不是轻量标签。
"""
import base64
import io
import sys

sys.path.insert(0, "scripts")
import api_commit as A

TAG = "v1.9.0"
MESSAGE = io.open(".git/msg_v19.txt", encoding="utf-8").read()

token = A.get_token()
me = A.api("GET", "//user", token)
print("[api] 凭据有效：", me.get("login"))

ref = A.api("GET", "/git/ref/heads/main", token)
base = ref["object"]["sha"]
base_commit = A.api("GET", f"/git/commits/{base}", token)
base_tree = base_commit["tree"]["sha"]
print(f"[api] 远端 main={base[:10]} tree={base_tree[:10]}")

payloads = A.detect_changes(token, base)
print(f"[api] 待上传 {len(payloads)} 个文件")
items = []
for rel, data in payloads:
    blob = A.api("POST", "/git/blobs", token,
                 {"content": base64.b64encode(data).decode(), "encoding": "base64"})
    items.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    print(f"  blob {rel} ({len(data)}B)")

tree = A.api("POST", "/git/trees", token, {"base_tree": base_tree, "tree": items})
commit = A.api("POST", "/git/commits", token,
               {"message": MESSAGE, "tree": tree["sha"], "parents": [base]})
print("[api] commit", commit["sha"])
A.api("PATCH", "/git/refs/heads/main", token,
      {"sha": commit["sha"], "force": False})
print("[api] main 已更新")

tag_obj = A.api("POST", "/git/tags", token,
                {"tag": TAG, "message": MESSAGE.splitlines()[0],
                 "object": commit["sha"], "type": "commit"})
try:
    A.api("PATCH", f"/git/refs/tags/{TAG}", token,
          {"sha": tag_obj["sha"], "force": True})
    print(f"[api] tag {TAG} 已更新 -> 触发工作流")
except RuntimeError as exc:
    if not any(s in str(exc) for s in ("404", "422", "does not exist")):
        raise
    A.api("POST", "/git/refs", token,
          {"ref": f"refs/tags/{TAG}", "sha": tag_obj["sha"]})
    print(f"[api] tag {TAG} 已创建 -> 触发工作流")

print("[api] done:", commit["sha"])
