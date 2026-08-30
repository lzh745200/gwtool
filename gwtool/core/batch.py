# -*- coding: utf-8 -*-
"""批量汇编：每份材料单独生成一份规范公文。"""
from __future__ import annotations

import re
from pathlib import Path

from . import docxgen
from .compiler import load_trees
from .template import DocTemplate
from ..db import dao


def safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\n\r]', "_", name)[:80] or "未命名"


def batch_compile_each(doc_ids: list[int], template: DocTemplate, out_dir: str,
                       cover: dict | None = None,
                       progress_cb=None) -> list[str]:
    """对每份材料独立生成 docx（同一模板），返回输出路径列表。

    cover: 可选 {title,org,date} —— 每份输出使用材料自身标题作封面标题。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for i, did in enumerate(doc_ids, 1):
        if progress_cb:
            progress_cb(i, len(doc_ids))
        d = dao.get_document(did)
        if not d:
            continue
        trees = load_trees([did], [])
        if not trees:
            continue
        tpl = template.clone(template.name)
        if cover:
            tpl.cover.enabled = True
            tpl.cover.title = trees[0].title or d.title
            tpl.cover.org = cover.get("org", "")
            tpl.cover.date = cover.get("date", "")
        target = out / f"{safe_filename(trees[0].title or d.title)}.docx"
        # 防重名
        k = 1
        while target.exists():
            target = out / f"{safe_filename(trees[0].title or d.title)}_{k}.docx"
            k += 1
        docxgen.generate_docx(trees, tpl, str(target))
        paths.append(str(target))
    return paths
