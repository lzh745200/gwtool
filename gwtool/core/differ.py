# -*- coding: utf-8 -*-
"""文档对比：段落级 + 字级差异，输出红绿 HTML。"""
from __future__ import annotations

import difflib
import html


def diff_to_html(old_text: str, new_text: str,
                 old_title: str = "文档一", new_title: str = "文档二") -> str:
    """两栏并排视图：删除标红、新增标绿、行内修改黄底。"""
    old_lines = [ln for ln in old_text.replace("\r", "").split("\n")]
    new_lines = [ln for ln in new_text.replace("\r", "").split("\n")]
    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    rows: list[str] = []

    def esc(s: str) -> str:
        return html.escape(s)

    def inline_diff(a: str, b: str) -> tuple[str, str]:
        cm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
        la, lb = [], []
        for op, i1, i2, j1, j2 in cm.get_opcodes():
            if op == "equal":
                la.append(esc(a[i1:i2]))
                lb.append(esc(b[j1:j2]))
            elif op == "delete":
                la.append(f"<span class='del'>{esc(a[i1:i2])}</span>")
            elif op == "insert":
                lb.append(f"<span class='ins'>{esc(b[j1:j2])}</span>")
            elif op == "replace":
                la.append(f"<span class='del'>{esc(a[i1:i2])}</span>")
                lb.append(f"<span class='ins'>{esc(b[j1:j2])}</span>")
        return "".join(la), "".join(lb)

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                a = esc(old_lines[i1 + k])
                b = esc(new_lines[j1 + k])
                rows.append(_row(a, b, "", ""))
        elif tag == "delete":
            for k in range(i1, i2):
                rows.append(_row(f"<span class='del'>{esc(old_lines[k])}</span>", "",
                                 "row-del", ""))
        elif tag == "insert":
            for k in range(j1, j2):
                rows.append(_row("", f"<span class='ins'>{esc(new_lines[k])}</span>",
                                 "", "row-ins"))
        elif tag == "replace":
            n = max(i2 - i1, j2 - j1)
            for k in range(n):
                a = old_lines[i1 + k] if i1 + k < i2 else ""
                b = new_lines[j1 + k] if j1 + k < j2 else ""
                ha, hb = inline_diff(a, b)
                rows.append(_row(ha, hb, "row-mod", "row-mod"))

    stats = _stats(sm, len(old_lines), len(new_lines))
    doc = f"""<!doctype html><meta charset='utf-8'>
<style>
  table.diff {{ width:100%; border-collapse:collapse; font-size:14px;
               font-family:'仿宋_GB2312','仿宋','FangSong',monospace; }}
  td {{ border:1px solid #e0e0e0; padding:3px 8px; vertical-align:top;
       white-space:pre-wrap; word-break:break-all; }}
  td.no {{ width:34px; text-align:right; color:#999; background:#fafafa; }}
  th {{ background:#f3f4f6; padding:6px; border:1px solid #e0e0e0; }}
  .del {{ background:#ffd6d6; color:#b00020; text-decoration:line-through; }}
  .ins {{ background:#d6f5d6; color:#0a6b1e; }}
  .row-del {{ background:#fff0f0; }}
  .row-ins {{ background:#f0fff0; }}
  .row-mod {{ background:#fffbe6; }}
  .stat {{ color:#555; margin:8px 0; }}
</style>
<div class='stat'>{stats}</div>
<table class='diff'>
<tr><th>#</th><th>{esc(old_title)}</th><th>#</th><th>{esc(new_title)}</th></tr>
{"".join(rows)}
</table>"""
    return doc


def _row(a: str, b: str, ca: str, cb: str) -> str:
    return (f"<tr><td class='no'></td><td class='{ca}'>{a}</td>"
            f"<td class='no'></td><td class='{cb}'>{b}</td></tr>")


def _stats(sm, old_n: int, new_n: int) -> str:
    add = dele = mod = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "insert":
            add += j2 - j1
        elif tag == "delete":
            dele += i2 - i1
        elif tag == "replace":
            mod += max(i2 - i1, j2 - j1)
    same = sm.ratio() * 100
    return (f"共 {old_n} 行 → {new_n} 行；新增 <span class='ins'>{add}</span> 行，"
            f"删除 <span class='del'>{dele}</span> 行，修改 {mod} 行；"
            f"相似度 {same:.1f}%")
