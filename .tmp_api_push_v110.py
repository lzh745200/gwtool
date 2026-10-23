# -*- coding: utf-8 -*-
"""github.com 被阻断时用 API 推送 v1.10.0（含文件删除）。"""
import base64
import io
import sys

sys.path.insert(0, "scripts")
import api_commit as A

TAG = "v1.10.0"
MESSAGE = io.open(".git/msg_v110.txt", encoding="utf-8").read()
# 相对上一版被删除的路径（API tree 用 sha:null 表达删除）
DELETED = [
    "doc/_audit_v19/probes/lead_deps.py",
    "doc/_audit_v19/probes/lead_verify_correction_claims.py",
    "doc/_audit_v19/probes/lead_verify_docx_spacing.py",
    "doc/_audit_v19/probes/lead_verify_writing.py",
    "doc/_audit_v19/probes/lead_verify_writing2.py",
    "doc/_audit_v19/probes/lead_verify_writing3.py",
]

token = A.get_token()
print("[api] 凭据有效：", A.api("GET", "//user", token).get("login"))
base = A.api("GET", "/git/ref/heads/main", token)["object"]["sha"]
base_tree = A.api("GET", f"/git/commits/{base}", token)["tree"]["sha"]
print(f"[api] 远端 main={base[:10]} tree={base_tree[:10]}")

payloads = A.detect_changes(token, base)
print(f"[api] 待上传 {len(payloads)} 个文件")
items = []
for rel, data in payloads:
    blob = A.api("POST", "/git/blobs", token,
                 {"content": base64.b64encode(data).decode(), "encoding": "base64"})
    items.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    print(f"  blob {rel}")
for rel in DELETED:
    items.append({"path": rel, "mode": "100644", "type": "blob", "sha": None})
    print(f"  delete {rel}")

tree = A.api("POST", "/git/trees", token, {"base_tree": base_tree, "tree": items})
commit = A.api("POST", "/git/commits", token,
               {"message": MESSAGE, "tree": tree["sha"], "parents": [base]})
print("[api] commit", commit["sha"])
A.api("PATCH", "/git/refs/heads/main", token, {"sha": commit["sha"], "force": False})
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
