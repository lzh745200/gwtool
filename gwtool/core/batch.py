# -*- coding: utf-8 -*-
"""批量汇编：每份材料单独生成一份规范公文（单份失败不中断整批）。"""
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
                       cover: dict | None = None, progress_cb=None,
                       ) -> tuple[list[str], list[tuple[str, str]]]:
    """对每份材料独立生成 docx（同一模板）。

    返回 (成功路径列表, 失败清单 [(材料标题, 原因)])。
    cover: 可选 {title,org,date} —— 每份输出使用材料自身标题作封面标题。
    progress_cb(i, n) 在第 i 份完成或失败后回调，与实际进度同步。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    failures: list[tuple[str, str]] = []
    total = len(doc_ids)
    for i, did in enumerate(doc_ids, 1):
        d = dao.get_document(did)
        title = d.title if d else f"文档{did}"
        try:
            if not d:
                raise ValueError("文档不存在")
            trees = load_trees([did], [])
            if not trees:
                raise ValueError("没有可汇编的内容")
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
        except Exception as exc:  # noqa: BLE001
            failures.append((title, str(exc)))
        finally:
            if progress_cb:
                progress_cb(i, total)
    return paths, failures
