# -*- coding: utf-8 -*-
"""Lead 复核探针：验证 audit-correction 报告中的 3 条关键 P0/P1 结论。

覆盖：
  A. 任意文档纠错「扫描中改输入框 → 应用修正改错字」（数据损坏）
  B. 可自动替换的「标点」类会改动代码/路径文本（print("恢复") / ... / 全角括号）
  C. v1.8 文档内重复闸门按"调用文本"计数 → 整篇 vs 逐块结果不一致
  D. _dedupe 的 O(n²) 开销（4000 命中）
"""
import io
import os
import sys
import tempfile
import time

sys.path.insert(0, ".")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gwtool import paths

tmp = tempfile.mkdtemp(prefix="lead_verify_")
paths._override = __import__("pathlib").Path(tmp)
from gwtool.db import connection as dbconn

dbconn.configure(__import__("pathlib").Path(tmp) / "t.db")
from gwtool import app

app.ensure_database_seeded()

from gwtool.core import corrector
from gwtool.core.corrector import Correction

print("=" * 70)
print("A. 扫描中改输入框 → 应用修正是否改坏正文")
print("=" * 70)
from PySide6.QtWidgets import QApplication

qa = QApplication.instance() or QApplication([])
from gwtool.ui.correct_dialog import AnyDocCorrectDialog

dlg = AnyDocCorrectDialog()
dlg.start_check = lambda: None          # 避免真的起线程，聚焦"快照错位"本身
OLD = "现将工作布署如下。"
NEW = "这是新文件内容。"
dlg._title = ""
dlg._blocks = [{"kind": "para", "text": OLD, "rows": None}]
dlg._loading = True
dlg.input_box.setPlainText(OLD)
dlg._loading = False
# 模拟：worker 扫描的是 OLD 的快照结果（布署@2..4）
old_corr = Correction(2, 4, "布署", "部署", "错别字", "词库匹配", 0.98)
dlg._corrs = [[old_corr]]
dlg._render()
# 用户扫描期间改了输入框（真实路径：textChanged → _on_input_changed 重建 _blocks）
dlg.input_box.setPlainText(NEW)
print("  改输入框后 blocks[0]:", repr(dlg._blocks[0]["text"]))
# worker 收工回调（用旧快照）
dlg._on_checked([[old_corr]])
dlg.listw.setCurrentRow(0)
cur = dlg._current_hit()
print("  列表第一项:", repr(cur["corr"].label) if cur else None)
dlg.apply_selected()
after = dlg._blocks[0]["text"]
print(f"  旧文本: {OLD!r}  新文本: {NEW!r}")
print(f"  点「应用此修正」后: {after!r}")
print("  → 是否改坏正文:", "是（P0 成立）" if after != NEW else "否")

print()
print("=" * 70)
print("B. 「标点」类可自动替换规则对代码/路径文本的影响")
print("=" * 70)
samples = [
    'print("恢复")',
    "详见(path/to/文件.md)",
    "执行命令... 然后重启",
    'status = "成功"',
]
for s in samples:
    hits = [(c.wrong, c.suggestion, c.category) for c in corrector.check_text(s)]
    fixed, cs = corrector.correct_block(s)
    print(f"  原文 {s!r}")
    print(f"    命中 {hits}")
    print(f"    整篇应用后 {fixed!r}")

print()
print("=" * 70)
print("C. 文档内重复闸门：整篇 vs 逐块")
print("=" * 70)
from gwtool.db import dao

dao.add_error_pair("显式", "显示", "错别字(生成)", 0.55, source="generated")
corrector.invalidate_cache()
blocks = ["请显式传入参数。", "显式更清晰。", "这里也要显式说明。"]
whole = "".join(blocks)
w_hits = [c for c in corrector.check_text(whole) if c.wrong == "显式"]
b_hits = []
for b in blocks:
    b_hits += [c for c in corrector.check_text(b) if c.wrong == "显式"]
print(f"  整篇（同一 text 调用）：{len(w_hits)} 处")
print(f"  逐块（三块分别调用）：{len(b_hits)} 处")
print("  → 结果是否一致:", "不一致（P1 成立）" if len(w_hits) != len(b_hits) else "一致")

print()
print("=" * 70)
print("D. _dedupe 的规模代价")
print("=" * 70)
for n in (500, 2000, 4000):
    items = [Correction(i, i + 1, "甲", "乙", "错别字", "词库匹配", 0.9)
             for i in range(n)]
    t0 = time.perf_counter()
    corrector._dedupe(items)
    dt = (time.perf_counter() - t0) * 1000
    print(f"  n={n:5d}  _dedupe={dt:8.1f} ms")
dbconn.close_current_thread()
