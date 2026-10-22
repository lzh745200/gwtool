# -*- coding: utf-8 -*-
"""Lead 复核：写作侧 4 条关键结论（检查项计数 / 段落对齐假阳性 / 大文本耗时 / 文种提示）。"""
import inspect
import sys
import time

sys.path.insert(0, ".")

from gwtool.core import writing_hints as WH

print("=== 1) hints() 实际执行的检查函数数 ===")
src = inspect.getsource(WH.hints)
checks = [ln.strip() for ln in src.splitlines()
          if ln.strip().startswith("(_") and "(" in ln]
for c in checks:
    print("  ", c[:60])
print("  函数数:", len(checks))
# 模块里以 _*_hints 命名的检查函数总数
all_checks = [n for n, f in vars(WH).items()
              if n.endswith("_hints") and callable(f)]
print("  模块内 *_hints 函数:", len(all_checks), sorted(all_checks))

print("\n=== 2) 段落对齐：把参考段落原样贴回，是否仍报『序号重复』 ===")
from gwtool.core import paragraph_ref as PR

ref = ("一、总体要求\n（一）提高认识。\n（二）明确目标。\n"
       "二、重点任务\n（一）抓好落实。\n（二）强化保障。\n")
try:
    issues = PR.align_texts([ref], [ref]) if hasattr(PR, "align_texts") else None
    print("  align_texts 直接返回:", issues)
except Exception as exc:  # noqa: BLE001
    print("  align_texts 调用失败:", exc)
    issues = None
if issues is None:
    # 按报告口径：用 align 相关函数名探测
    names = [n for n in dir(PR) if "align" in n.lower()]
    print("  可用对齐接口:", names)

print("\n=== 3) 100 页写作提示耗时（6 万字）===")
page = ("一、总体要求\n（一）提高认识。为深入贯彻落实上级部署要求，现就有关"
        "事项通知如下，请各部门结合实际抓好落实，确保各项任务落地见效。\n"
        "（二）明确目标。到本年度末，完成各项目标任务，形成长效机制。\n")
text = page * 260  # ≈ 100 页
print("  文本字数:", len(text))
t0 = time.perf_counter()
hs = WH.hints(text, "通知")
dt = (time.perf_counter() - t0) * 1000
print(f"  hints() 总耗时 {dt:.1f} ms，提示 {len(hs)} 条")
for name in ("_element_hints", "_structure_hints", "_layer_hints", "_punct_hints",
             "_repeat_hints", "_doc_consistency_hints"):
    fn = getattr(WH, name, None)
    if fn is None:
        continue
    t0 = time.perf_counter()
    try:
        args = (text, "通知") if name in ("_structure_hints", "_element_hints",
                                          "_style_hints") else (text,)
        fn(*args)
        d = (time.perf_counter() - t0) * 1000
        print(f"    {name:24} {d:8.1f} ms")
    except Exception as exc:  # noqa: BLE001
        print(f"    {name:24} ERR {exc}")

print("\n=== 4) 法定文种样张的『更像哪个文种』===")
notice = ("关于进一步加强安全生产工作的通知\n各市人民政府：\n"
          "为深入贯彻上级部署，现就有关事项通知如下。\n"
          "一、提高认识\n各部门要充分认识做好安全生产工作的极端重要性。\n"
          "二、明确任务\n（一）开展排查整治。\n（二）强化督导考核。\n"
          "三、工作要求\n请各单位抓好落实。\n特此通知。\n")
print("  detect_kind(通知样张):", WH.detect_kind(notice, limit=3))
