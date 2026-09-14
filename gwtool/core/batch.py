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


# Windows 保留设备名：以此为主名的文件（含带扩展名的 CON.docx）根本落不到
# 磁盘上——CON/NUL/COM1 会"写入成功"但内容进设备、文件不存在，
# PRN/LPT1 直接抛 FileNotFoundError。批量汇编按标题命名文件，
# 材料标题恰好是这类名字时用户会拿到"成功"却打不开的产物。
_RESERVED_DEVICE_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def safe_filename(name: str) -> str:
    """把材料标题清洗成可安全落盘的单段文件名（跨平台一致）。

    只替换非法字符是不够的：Windows 还有两类"看着合法但写不进去"的名字——
    保留设备名（CON/PRN/AUX/NUL/COM1-9/LPT1-9）和以空格或点结尾的名字。
    两类都要处理，否则批量汇编会静默产出打不开的公文。
    """
    s = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", str(name or ""))
    # Windows 不允许文件名以空格或点结尾（资源管理器会悄悄截掉，导致
    # 调用方记下的路径与实际文件不一致）
    s = s.rstrip(" .").strip()[:80].strip()
    if not s:
        return "未命名"
    if s.split(".")[0].upper() in _RESERVED_DEVICE_NAMES:
        s = "_" + s
    return s


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
    # 改动类型：replace / insert / delete（由 L5 Seq2Seq 层产生变长输出时区分）
    kind: str = "replace"

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
                                       _context(text, c.start, c.end),
                                       getattr(c, "kind", "replace")))
            if hits:
                result.plans.append(DocCorrection(int(row["id"]), title, hits))
        except Exception as exc:  # noqa: BLE001  单篇失败不中断整批
            result.failures.append((title, str(exc)))
        finally:
            result.scanned += 1
            if progress_cb:
                progress_cb(i, total)
    return result


def _relocate(text: str, wrong: str, context: str) -> int:
    """文本变动后为命中词重新定位；只有上下文能对上才返回下标，否则 -1。

    `context` 是预览时抓取的 ±14 字窗口（空白已压平），用它做锚点：
    候选位置两侧的文本若与预览上下文一致，才认为仍是原来那一处。
    同一篇里出现多个同词时，只有上下文唯一的候选才会被采纳；
    无法确认时宁可返回 -1（跳过），也绝不改到别处。
    """
    if not wrong:
        return -1
    anchors = _context_anchors(context, wrong)
    if anchors is None:
        # 预览未带上下文：退化为"全文唯一"这一弱保证（历史行为）
        idx = text.find(wrong)
        return idx if idx >= 0 and text.find(wrong, idx + 1) < 0 else -1
    prefix, suffix = anchors
    found = -1
    pos = text.find(wrong)
    while pos >= 0:
        if _context_matches(text, pos, pos + len(wrong), prefix, suffix):
            if found >= 0:
                return -1          # 多处都对得上 -> 无法确认是哪一处，跳过
            found = pos
        pos = text.find(wrong, pos + 1)
    return found


def _context_anchors(context: str, wrong: str):
    """从压平的上下文里剥出 (前缀, 后缀) 两个锚串；无法解析返回 None。"""
    if not context or wrong not in context:
        return None
    left, _, right = context.partition(wrong)
    if not left and not right:
        return None
    return left, right


def _flat(s: str) -> str:
    return re.sub(r"\s+", " ", s)


def _context_matches(text: str, s: int, e: int, prefix: str, suffix: str) -> bool:
    """候选位置两侧的原文（压平后）是否以预览锚串收/起。"""
    if prefix:
        lo = max(0, s - len(prefix) * 2)      # 留出空白差异的余量
        if not _flat(text[lo:s]).endswith(_flat(prefix)[-max(1, len(prefix) // 2):]):
            return False
    if suffix:
        hi = min(len(text), e + len(suffix) * 2)
        if not _flat(text[e:hi]).startswith(_flat(suffix)[: max(1, len(suffix) // 2)]):
            return False
    return True


def _apply_hits(text: str, hits: list[CorrectHit]
                ) -> tuple[str, int, int, list[tuple[int, int, str, str]]]:
    """把预览命中写进正文：位置校验 -> 漂移重定位 -> 去重叠 -> 从后往前替换。

    返回 (新正文, 已替换处数, 跳过处数, 已替换位置列表)。
    已替换位置列表是 (start, end, 错, 对)，坐标基于**替换前的旧正文**，
    供 _apply_to_blocks 精确同步结构化块 —— 只同步用户确认过的那几处，
    不能只凭「错词」把全文同形词一并改掉。
    预览与执行之间文档可能已被编辑，因此位置对不上时按「上下文锚点」重新
    定位：只有候选位置的上下文与预览时一致才替换；无法确认（多处同形、
    或上下文对不上）时一律跳过 —— 宁可不改，也绝不改错地方。
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
        idx = _relocate(text, h.wrong, h.context)
        if idx >= 0:
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
    for s, e, _wrong, sug in reversed(kept):
        text = text[:s] + sug + text[e:]
    return text, len(kept), skipped, kept


def _apply_to_blocks(blocks_json: str, old_text: str,
                     confirmed: list[tuple[int, int, str, str]],
                     stats: dict | None = None) -> str:
    """把已确认的命中同步进结构化块，汇编出的公文才是改后的文本。

    dao.update_document_content(blocks_json=None) 会把块清成 '[]'，汇编随即退回
    按纯文本分段（标题层级全丢），所以必须显式回写。

    `old_text` 是替换前的正文，`confirmed` 是 _apply_hits 返回的已替换位置
    （坐标基于 old_text）。做法：沿 old_text 顺序定位每块文本，得到块内每个
    命中在 old_text 中的全局坐标；只有坐标与 confirmed 完全一致的才替换 ——
    用户没确认的同形词一律不动。

    此前只按 (错, 对) 词对过滤逐块重跑纠错，位置信息全丢：正文按位置只改了
    确认过的那一处，块却把全文同词全改，同一篇文档的正文与块出现两个版本，
    汇编出的公文与资料库预览对不上。

    ``stats`` 是出参：块没同步（定位不到）或块结构本身异常时计数/记录原因，
    由 _apply_plans 上抛给用户。老实现一律静默退回原块，于是"正文已改、
    汇编出的公文仍是错字"这件事对用户完全不可见 —— 静默失败比报错更坏。
    """
    if not confirmed or not blocks_json or not old_text:
        return blocks_json or "[]"
    if stats is None:
        stats = {}
    try:
        import json

        from .corrector import check_text

        data = json.loads(blocks_json)
        if not isinstance(data, list) or not data:
            return blocks_json

        # 已确认区间 -> 建议词，便于按全局坐标精确命中
        by_span = {(s, e): sug for s, e, _w, sug in confirmed}

        # 沿正文顺序前进的游标：块文本按出现顺序在正文里定位，避免重复词错位。
        # 定位不到（块文本被规范化过、或与正文不一致）就停用坐标同步，
        # 改用「原文出现次数」这一更弱的保证。
        cursor = 0

        def locate(text: str) -> int:
            """返回 text 在正文中的起点（从当前游标起找）；找不到返回 -1。

            先精确匹配，再退一步用"去掉首尾空白"的变体匹配：块文本与正文
            经常只差首尾空白（导入解析/富文本残留的 ``\\u3000``、行尾空格），
            逐字相等就落空的情况下，老实现直接放弃同步 —— 结果正文改了、
            结构化块没改，汇编出的公文里错字原封不动。空白差异不影响
            块内字符偏移（首尾空白不参与替换坐标），所以这种回退是安全的。
            """
            nonlocal cursor
            for cand in (text, text.strip(), text.strip("\u3000 \t\r\n")):
                if not cand:
                    continue
                idx = old_text.find(cand, cursor)
                if idx < 0:
                    idx = old_text.find(cand)
                if idx >= 0:
                    cursor = idx + len(cand)
                    # 返回"块文本"在正文中的起点：上面命中的是去空白变体，
                    # 它相对原块文本的偏移量需要补回来，否则替换坐标会整体偏移
                    return idx - text.find(cand)
            return -1

        def fix(text):
            if not isinstance(text, str) or not text:
                return text
            base = locate(text)
            if base < 0:
                stats["unsynced"] = stats.get("unsynced", 0) + 1
                return text
            picked = [(c, by_span[(base + c.start, base + c.end)])
                      for c in check_text(text)
                      if (base + c.start, base + c.end) in by_span]
            if not picked:
                return text
            # 从后往前替换，前面的位置不受影响
            out = text
            for c, sug in sorted(picked, key=lambda t: t[0].start, reverse=True):
                out = out[:c.start] + sug + out[c.end:]
            return out

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
    except Exception as exc:  # noqa: BLE001  块结构异常不影响正文已改的成果
        # 不能静默：块没同步 -> 汇编出的公文仍是改前的文字，用户必须知道
        stats["error"] = f"{type(exc).__name__}: {exc}"
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
            old_text = d.content_text or ""
            new_text, applied, skipped, confirmed = _apply_hits(old_text, plan.hits)
            result.skipped += skipped
            if not applied:
                continue        # 一处都没改成就不写库，避免白刷 updated_time
            # 批量改动没有 Ctrl+Z，写回前留一份快照供「历史版本」逐篇回滚；
            # 快照只是安全网，它自己失败不该拦住纠错
            try:
                dao.add_snapshot(d.id, d.title, d.content_text, reason="批量纠错前")
            except Exception:  # noqa: BLE001
                pass
            blocks_stats: dict = {}
            blocks = _apply_to_blocks(d.blocks_json, old_text, confirmed,
                                      stats=blocks_stats)
            dao.update_document_content(d.id, d.title, new_text, blocks_json=blocks)
            result.applied.append(d.id)
            result.changes += applied
            # 块未同步 = 汇编出的公文仍是改前的文字。正文已经改了，无法整体回滚
            # （用户确认过的批改不该因为一块对不上就全丢弃），但必须让用户知道：
            # 记进 failures，预览页会按"需人工处理"列出来。
            if blocks_stats.get("unsynced"):
                result.failures.append(
                    (title, f"正文已修正，但有 {blocks_stats['unsynced']} 个结构化段落"
                            "定位失败未能同步，汇编导出可能仍是改前文字；"
                            "请打开该文档核对后再汇编"))
            elif blocks_stats.get("error"):
                result.failures.append(
                    (title, "正文已修正，但结构化块同步失败（"
                            f"{blocks_stats['error']}），汇编导出可能仍是改前文字"))
        except Exception as exc:  # noqa: BLE001  单篇失败不中断整批
            result.failures.append((title, str(exc)))
        finally:
            if progress_cb:
                progress_cb(i, total)
    return result
