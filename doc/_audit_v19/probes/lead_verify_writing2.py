# -*- coding: utf-8 -*-
"""Lead 复核 2：写作侧逐检查耗时分解 + 段落对齐假阳性 + 『一是』误判跳号。"""
import sys
import time

sys.path.insert(0, ".")

from gwtool.core import paragraph_ref as PR
from gwtool.core import writing_hints as WH

print("=== A) 段落对齐：参考与草稿逐字相同时是否报『序号重复』===")
para = ("一、总体要求\n（一）提高认识。\n（二）明确目标。\n"
        "二、重点任务\n（一）抓好落实。\n（二）强化保障。\n")
try:
    al = PR.align_to_draft([para], para)
    print("  align_to_draft 返回类型:", type(al).__name__)
    print("  ", al)
except Exception as exc:  # noqa: BLE001
    print("  调用失败:", exc)
    import traceback
    traceback.print_exc()

print("\n=== B) 『一是/二是』在经验材料里是否被误判为跳号 ===")
exp = ("一、主要做法\n一是加强组织领导。二是健全工作机制。三是强化督导考核。\n"
       "二、工作成效\n一是安全形势持续好转。二是基层基础不断夯实。\n"
       " 三是群众满意度明显提升。\n")
hs = WH.hints(exp, "")
for h in hs:
    if "序号" in h.title:
        print("  ", h.line[:160])

print("\n=== C) 25k 字文本逐检查耗时 ===")
page = ("一、总体要求\n（一）提高认识。为深入贯彻落实上级部署要求，现就有关"
        "事项通知如下，请各部门结合实际抓好落实，确保各项任务落地见效。\n"
        "（二）明确目标。到本年度末，完成各项目标任务，形成长效机制。\n")
text = page * 260
checks = [
    ("_structure_hints", (text, "通知")), ("_layer_hints", (text,)),
    ("_title_hints", (text,)), ("_element_hints", (text, "通知")),
    ("_cohesion_hints", (text,)), ("_paragraph_hints", (text,)),
    ("_style_hints", (text, "通知")), ("_vague_hints", (text,)),
    ("_sentence_hints", (text,)), ("_fatigue_hints", (text,)),
    ("_punct_hints", (text,)), ("_colloquial_hints", (text,)),
    ("_repeat_hints", (text,)), ("_consistency_hints", (text,)),
    ("_doc_consistency_hints", (text,)),
]
total = 0.0
for name, args in checks:
    fn = getattr(WH, name)
    t0 = time.perf_counter()
    fn(*args)
    d = (time.perf_counter() - t0) * 1000
    total += d
    print(f"  {name:26} {d:8.1f} ms")
print(f"  {'合计':26} {total:8.1f} ms（字数 {len(text)}）")

t0 = time.perf_counter()
WH.hints(text, "通知")
print(f"  hints() 整链 {1000 * (time.perf_counter() - t0):.1f} ms")
