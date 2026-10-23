# -*- coding: utf-8 -*-
"""推送 CI 修复并把 v1.11.0 标签移到新提交（触发重新构建）。"""
import base64
import io
import sys

sys.path.insert(0, "scripts")
import api_commit as A

TAG = "v1.11.0"
MESSAGE = io.open(".git/msg_v111fix.txt", encoding="utf-8").read()

token = A.get_token()
print("[api] 凭据有效：", A.api("GET", "//user", token).get("login"))
base = A.api("GET", "/git/ref/heads/main", token)["object"]["sha"]
base_tree = A.api("GET", f"/git/commits/{base}", token)["tree"]["sha"]
print(f"[api] 远端 main={base[:10]}")

payloads = A.detect_changes(token, base)
print(f"[api] 待上传 {len(payloads)} 个文件")
items = []
for rel, data in payloads:
    blob = A.api("POST", "/git/blobs", token,
                 {"content": base64.b64encode(data).decode(), "encoding": "base64"})
    items.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    print(f"  blob {rel}")

tree = A.api("POST", "/git/trees", token, {"base_tree": base_tree, "tree": items})
commit = A.api("POST", "/git/commits", token,
               {"message": MESSAGE, "tree": tree["sha"], "parents": [base]})
print("[api] commit", commit["sha"])
A.api("PATCH", "/git/refs/heads/main", token, {"sha": commit["sha"], "force": False})
print("[api] main 已更新")

tag_obj = A.api("POST", "/git/tags", token,
                {"tag": TAG, "message": MESSAGE.splitlines()[0],
                 "object": commit["sha"], "type": "commit"})
A.api("PATCH", f"/git/refs/tags/{TAG}", token,
      {"sha": tag_obj["sha"], "force": True})
print(f"[api] tag {TAG} 已移动到新提交 -> 触发重新构建")
print("[api] done:", commit["sha"])
