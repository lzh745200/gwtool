# -*- coding: utf-8 -*-
"""批量处理：每份材料单独生成规范公文、按分类批量纠错（单份失败不中断整批）。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
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


# ------------------------------------------------------------------ 批量纠错
# 「数字用法」类命中给出的 suggestion 是提示标签（如"阿拉伯数字年份"）而不是
# 可替换文本，批量写回会把正文改成标签本身，故整类排除 ——
# 与 reference_panel 单篇「全部替换」的既有约定保持一致。
ADVISORY_CATEGORIES = ("数字用法",)


@dataclass
class CorrectHit:
    """一处纠错命中（start/end 基于扫描当时的正文）。"""
    start: int
    end: int
    wrong: str
    suggestion: str
    category: str = ""
    confidence: float = 0.0
    context: str = ""

    @property
    def label(self) -> str:
        return f"{self.wrong} → {self.suggestion}"


@dataclass
class DocCorrection:
    """一篇文档的命中集合；预览与执行共用同一份计划。"""
    doc_id: int
    title: str
    hits: list[CorrectHit] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.hits)


@dataclass
class BatchCorrectResult:
    """批量纠错结果（预览与执行共用一个结构，各取所需字段）。"""
    plans: list[DocCorrection] = field(default_factory=list)   # 预览：每篇命中
    scanned: int = 0                                          # 预览：扫描篇数
    applied: list[int] = field(default_factory=list)           # 执行：已写回的文档
    changes: int = 0                                          # 执行：实际替换处数
    skipped: int = 0                                          # 执行：未能替换的处数
    failures: list[tuple[str, str]] = field(default_factory=list)  # (标题, 原因)

    @property
    def hit_total(self) -> int:
        return sum(p.count for p in self.plans)


def _context(text: str, start: int, end: int, width: int = 14) -> str:
    """命中处上下文（换行压成空格，供预览列表一行显示）。"""
    lo = max(0, start - width)
    hi = min(len(text), end + width)
    return re.sub(r"\s+", " ", text[lo:hi]).strip()


def batch_correct(category_id: int | None = None,
                  doc_ids: list[int] | None = None,
                  apply: bool = False,
                  plans: list[DocCorrection] | None = None,
                  min_confidence: float = 0.0,
                  categories: tuple[str, ...] = (),
                  progress_cb=None) -> BatchCorrectResult:
    """按分类批量纠错：先出命中预览，调用方确认后才真正写回。

    apply=False（预览，默认）：扫描 category_id（None=全部分类）或 doc_ids
        指定的文档，返回 result.plans —— 每篇哪些位置、错→对、上下文与置信度；
        **不写库**。
    apply=True（执行）：必须带上预览得到的 plans，逐篇写回正文并同步 FTS 索引
        与结构化块，返回 applied / changes / skipped / failures。

    单篇失败只记入 failures，不中断整批（与 batch_compile_each 同约定）。
    min_confidence 默认 0.0（不过滤）；批量写回是"一改就改一片"的操作，
    界面侧建议取 0.8 左右 —— 精标词库≥0.85、上下文与标点规则 0.85~0.95、
    程序生成的混淆对 0.55、机构沿革对照 0.7。
    progress_cb(i, total) 每处理完一篇回调一次，供后台线程回报进度；
    全库扫描务必放在工作线程里跑，别在 UI 线程同步调用。
    """
    if apply:
        if not plans:
            raise ValueError("执行纠错需要先预览得到命中计划（plans 为空）")
        return _apply_plans(list(plans), progress_cb=progress_cb)
    return _scan(category_id=category_id, doc_ids=doc_ids,
                 min_confidence=min_confidence, categories=tuple(categories or ()),
                 progress_cb=progress_cb)


def _scan(category_id: int | None, doc_ids: list[int] | None,
          min_confidence: float, categories: tuple[str, ...],
          progress_cb=None) -> BatchCorrectResult:
    """预览阶段：惰性逐篇取正文跑纠错，只收集命中，不动数据库。"""
    from .corrector import check_text

    result = BatchCorrectResult()
    if doc_ids is not None:
        total = len(doc_ids)
    else:
        total = dao.count_documents(category_id)
    wanted = set(categories)
    for i, row in enumerate(
            dao.iter_documents_content(category_id=category_id, doc_ids=doc_ids), 1):
        title = row["title"] or f"文档{row['id']}"
        try:
            text = row["content_text"] or ""
            hits: list[CorrectHit] = []
            for c in check_text(text):
                if c.category in ADVISORY_CATEGORIES:
                    continue        # 提示类（suggestion 不是替换文本），不可批量写回
                if c.confidence < min_confidence:
                    continue
                if wanted and c.category not in wanted:
                    continue
                hits.append(CorrectHit(c.start, c.end, c.wrong, c.suggestion,
                                       c.category, c.confidence,
                                       _context(text, c.start, c.end)))
            if hits:
                result.plans.append(DocCorrection(int(row["id"]), title, hits))
        except Exception as exc:  # noqa: BLE001  单篇失败不中断整批
            result.failures.append((title, str(exc)))
        finally:
            result.scanned += 1
            if progress_cb:
                progress_cb(i, total)
    return result


def _apply_hits(text: str, hits: list[CorrectHit]
                ) -> tuple[str, int, int, set[tuple[str, str]]]:
    """把预览命中写进正文：位置校验 -> 漂移重定位 -> 去重叠 -> 从后往前替换。

    返回 (新正文, 已替换处数, 跳过处数, 已替换的 (错, 对) 集合)。
    预览与执行之间文档可能已被编辑，因此位置对不上时按原词重新定位，
    且只有全文唯一命中才替换（多处同词时宁可跳过，也不改错地方）。
    """
    resolved: list[tuple[int, int, str, str]] = []
    skipped = 0
    for h in sorted(hits, key=lambda x: x.start):
        if not h.wrong or h.wrong == h.suggestion:
            skipped += 1
            continue
        if 0 <= h.start < h.end <= len(text) and text[h.start:h.end] == h.wrong:
            resolved.append((h.start, h.end, h.wrong, h.suggestion))
            continue
        idx = text.find(h.wrong)
        if idx >= 0 and text.find(h.wrong, idx + 1) < 0:
            resolved.append((idx, idx + len(h.wrong), h.wrong, h.suggestion))
        else:
            skipped += 1
    resolved.sort(key=lambda t: t[0])
    kept: list[tuple[int, int, str, str]] = []
    for s, e, wrong, sug in resolved:
        if kept and s < kept[-1][1]:
            skipped += 1          # 重定位后与已保留的命中重叠
            continue
        kept.append((s, e, wrong, sug))
    pairs: set[tuple[str, str]] = set()
    for s, e, wrong, sug in reversed(kept):
        text = text[:s] + sug + text[e:]
        pairs.add((wrong, sug))
    return text, len(kept), skipped, pairs


def _apply_to_blocks(blocks_json: str, pairs: set[tuple[str, str]]) -> str:
    """把已确认的 (错, 对) 同步进结构化块，汇编出的公文才是改后的文本。

    dao.update_document_content(blocks_json=None) 会把块清成 '[]'，汇编随即退回
    按纯文本分段（标题层级全丢），所以必须显式回写。逐块重跑一遍纠错再按 pairs
    过滤：既只改用户预览确认过的词，又继承 corrector 的书名号与词边界保护
    （不会误改《》里引用的原文标题）。直接操作 JSON 字典而不是 Block(**d)，
    以容忍未来新增字段；任何异常都退回原块，宁可不改也不把结构写坏。
    """
    if not pairs or not blocks_json:
        return blocks_json or "[]"
    try:
        import json

        from .corrector import apply_all, check_text

        data = json.loads(blocks_json)
        if not isinstance(data, list) or not data:
            return blocks_json

        def fix(text):
            if not isinstance(text, str) or not text:
                return text
            picked = [c for c in check_text(text)
                      if (c.wrong, c.suggestion) in pairs]
            return apply_all(text, picked) if picked else text

        for blk in data:
            if not isinstance(blk, dict):
                continue
            if blk.get("text"):
                blk["text"] = fix(blk["text"])
            rows = blk.get("rows")
            if isinstance(rows, list):
                blk["rows"] = [[fix(cell) for cell in row] if isinstance(row, list)
                               else row for row in rows]
        return json.dumps(data, ensure_ascii=False)
    except Exception:  # noqa: BLE001  块结构异常不影响正文已改的成果
        return blocks_json


def _apply_plans(plans: list[DocCorrection], progress_cb=None) -> BatchCorrectResult:
    """执行阶段：按预览计划逐篇写回（写库走 DAO，FTS 索引随之同步）。"""
    result = BatchCorrectResult()
    total = len(plans)
    for i, plan in enumerate(plans, 1):
        title = plan.title or f"文档{plan.doc_id}"
        try:
            if not plan.hits:
                continue
            d = dao.get_document(plan.doc_id)
            if d is None:
                raise ValueError("文档不存在（可能已被彻底删除）")
            if d.deleted_time:
                raise ValueError("文档已在回收站，未改动")
            new_text, applied, skipped, pairs = _apply_hits(d.content_text or "",
                                                            plan.hits)
            result.skipped += skipped
            if not applied:
                continue        # 一处都没改成就不写库，避免白刷 updated_time
            # 批量改动没有 Ctrl+Z，写回前留一份快照供「历史版本」逐篇回滚；
            # 快照只是安全网，它自己失败不该拦住纠错
            try:
                dao.add_snapshot(d.id, d.title, d.content_text, reason="批量纠错前")
            except Exception:  # noqa: BLE001
                pass
            blocks = _apply_to_blocks(d.blocks_json, pairs)
            dao.update_document_content(d.id, d.title, new_text, blocks_json=blocks)
            result.applied.append(d.id)
            result.changes += applied
        except Exception as exc:  # noqa: BLE001  单篇失败不中断整批
            result.failures.append((title, str(exc)))
        finally:
            if progress_cb:
                progress_cb(i, total)
    return result
