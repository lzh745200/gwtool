# -*- coding: utf-8 -*-
"""Lead 复核 3：段落对齐假阳性（正确签名）+ 新建公文双冒号。"""
import sys

sys.path.insert(0, ".")

from gwtool.core import paragraph_ref as PR

print("=== A) 段落对齐：草稿与参考逐字相同时是否报『序号重复』===")
text = ("一、总体要求\n（一）提高认识。\n（二）明确目标。\n"
        "二、重点任务\n（一）抓好落实。\n（二）强化保障。\n")
print("  ParagraphRef 字段:", PR.ParagraphRef.__dataclass_fields__.keys()
      if hasattr(PR.ParagraphRef, "__dataclass_fields__") else "非 dataclass")
try:
    ref = PR.ParagraphRef(doc_id=1, ordinal=1, kind="paragraph", level=0,
                          text=text, char_offset=0, text_hash="x",
                          title="样张") if "title" in getattr(
        PR.ParagraphRef, "__dataclass_fields__", {}) else PR.ParagraphRef(
        doc_id=1, ordinal=1, kind="paragraph", level=0, text=text,
        char_offset=0, text_hash="x")
    al = PR.align_to_draft(text, [ref])
    print("  对齐结果:", al)
except Exception as exc:  # noqa: BLE001
    import traceback
    print("  调用失败:", exc)
    traceback.print_exc()

print("\n=== B) 新建公文：主送机关输入带冒号 → 是否出现双冒号 ===")
import inspect

from gwtool.core import skeletons

sk = skeletons.get("通知")
print("  骨架字段: kind=", sk.kind, "| recipient=", repr(sk.recipient))
src = inspect.getsource(skeletons)
for i, line in enumerate(src.splitlines(), 1):
    if "recipient" in line and ("：" in line or ":" in line or "+" in line):
        print(f"  skeletons.py:{i}: {line.strip()[:120]}")
from gwtool.ui import feature_dialogs
fsrc = inspect.getsource(feature_dialogs.SkeletonDialog)
for i, line in enumerate(fsrc.splitlines(), 1):
    if "recipient" in line:
        print(f"  SkeletonDialog:{i}: {line.strip()[:120]}")
