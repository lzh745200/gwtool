# -*- coding: utf-8 -*-
"""段落参考引擎：段落级检索、引用定位、内容对齐与骨架抽取。

**定位**：纯逻辑层（不含 UI），与 `core/reference.py` 同层同风格。

**与索引的关系（本模块最重要的设计前提）**：本模块建立在 schema v7 的
`paragraphs` 表上，但**不假设该表一定可用**。表缺失、索引未回填、外部改库
导致索引过期时，一律回落到"现场派生"路径 —— 索引只影响**速度**，不影响
**正确性**。这条设计让"段落表被删掉程序依然完全可用"成为事实而非承诺。

**三条不可变原则**（对应《段落参考写作功能规格说明书》§1.3）：

1. **人写、机辅** —— 只提供信息与结构复用，不替用户决定"写什么"。
   槽位抽取只把参考原文里的具体值抽象成占位符，**不产出任何新语义内容**。
2. **可解释** —— 任一输出都能回答"凭什么"。全部是确定性规则（正则、
   位置、相等比较），没有黑箱，没有"模型觉得"。
3. **不静默** —— 索引不可用要能被发现，且功能要降级可用而不是崩掉。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..db import dao

# ---------------------------------------------------------------- 数据类
@dataclass
class ParagraphRef:
    """一条段落级引用。source=library 来自资料库，draft 来自当前草稿。"""
    source: str = "library"
    para_id: int = 0
    doc_id: int = 0
    doc_title: str = ""
    ordinal: int = 0
    kind: str = "paragraph"
    level: int = 0
    text: str = ""
    char_offset: int = -1
    text_hash: str = ""
    score: float = 0.0

    @property
    def source_label(self) -> str:
        return {"library": "资料", "draft": "草稿"}.get(self.source, self.source)

    @property
    def kind_label(self) -> str:
        return {"heading": "标题", "paragraph": "正文",
                "list_item": "列表", "table": "表格"}.get(self.kind, self.kind)

    @property
    def locator(self) -> str:
        """人类可读的定位串，用于溯源展示。"""
        base = f"{self.doc_title or '（无标题）'} 第 {self.ordinal + 1} 段"
        if self.kind == "heading" and self.level:
            base += f"（{self.level} 级标题）"
        return base


@dataclass
class Alignment:
    """参考段落与草稿段落的对齐结果。"""
    ref_para_id: int = 0
    ref_text: str = ""
    draft_index: int = -1
    draft_text: str = ""
    role: str = ""
    relation: str = "matched"     # matched/missing/extra/level_mismatch/order_swap
    score: float = 0.0
    hint: str = ""


@dataclass
class Slot:
    """参考段落中的一个可变槽位（只记录"抽象掉了什么"，不生成新内容）。"""
    name: str = ""
    example: str = ""
    span: tuple[int, int] = (0, 0)
    label: str = ""


@dataclass
class SkeletonDraft:
    """从参考段落派生的骨架。"""
    kind: str = ""
    template: str = ""
    slots: list[Slot] = field(default_factory=list)
    source_paras: list[int] = field(default_factory=list)

    def slot_names(self) -> list[str]:
        return [s.name for s in self.slots]


# ---------------------------------------------------------------- 块 → 段落
def _blocks_from_json(blocks_json: str) -> list[tuple[str, int, str]]:
    """解析 blocks_json 为 (kind, level, text)。

    复用 `DocTree.from_json`：它已内建"JSON 损坏就返回部分结果/空树"的容错，
    不必在此重复一套 try/except。空列表是**正常输入**（文档被
    `update_document_content(blocks_json=None)` 清过块），由调用方回落纯文本切分。
    """
    from .model import TABLE, DocTree
    tree = DocTree.from_json("", blocks_json or "[]")
    out: list[tuple[str, int, str]] = []
    for b in tree.blocks:
        if b.type == TABLE and b.rows:
            text = "\n".join(" | ".join(str(c) for c in row) for row in b.rows)
            if text.strip():
                out.append((TABLE, 0, text))
            continue
        text = (b.text or "").strip()
        if text:
            out.append((b.type or "paragraph", int(b.level or 0), text))
    return out


def _blocks_from_text(content_text: str) -> list[tuple[str, int, str]]:
    """纯文本兜底切分：与既有导入路径**同一口径**。

    规则刻意与 `core/parsers/txt_parser` / `core/importer._text_to_tree` 对齐
    （按行切、去空行、`_HEADING_RE` 且长度 ≤40 判二级标题）：两处口径若不一致，
    同一篇文档"按块解析"与"按文本兜底"会得出不同段落，而这两条路径会在
    blocks_json 为空时互相切换 —— 口径漂移会让段落序号在用户眼皮底下跳变。
    """
    try:
        from .parsers.txt_parser import _HEADING_RE
    except Exception:                      # 解析器不可用也不能拖垮段落功能
        _HEADING_RE = None
    out: list[tuple[str, int, str]] = []
    # 首行判据是"**首个非空行**"，不是行下标 0：正文以空行开头时（导入来的
    # 文本很常见），用下标 0 会让第一个非空行永远拿不到 heading，与
    # importer._text_to_tree 的口径分叉，同一篇文档两条路径给出不同段落类型。
    first = True
    for raw in (content_text or "").split("\n"):
        para = raw.strip()
        if not para:
            continue
        is_first = first
        first = False
        if is_first and len(para) <= 50:
            out.append(("heading", 1, para))
        elif _HEADING_RE is not None and _HEADING_RE.match(para) and len(para) <= 40:
            out.append(("heading", 2, para))
        else:
            out.append(("paragraph", 0, para))
    return out


def _locate(text: str, content_text: str, start: int = 0) -> int:
    """在正文里顺序定位一段文本，返回起始偏移；定位不到返回 -1。

    **落点不变量**由本函数保证：调用方拿到的偏移处切片必须恰好等于该段文本。
    因此先按顺序找（`find(t, start)`）；顺序找不着再全局找一次（应对"块顺序
    与正文顺序不一致"的历史数据）；仍找不到就返回 -1 —— **不猜**。

    宁可返回 -1（UI 降级为"打开所属文档"）也不能给一个错位置：
    错位置的后果是"点跳转跳到别处"，比"不能跳转"更坏。
    """
    if not text:
        return -1
    idx = content_text.find(text, start)
    if idx < 0:
        idx = content_text.find(text)
    return idx


def derive_blocks(blocks_json: str, content_text: str) -> list[dao.ParagraphRow]:
    """把文档的块结构派生为段落列表（含字符偏移）。

    这是段落表的**唯一数据来源**：`dao.rebuild_paragraphs` 与"首次使用兜底"
    都走它，因此"索引里的段落"与"现场派生的段落"天然一致（不变量 1）。

    **块与正文不一致时的处置**：优先信块结构（它带标题层级，信息更丰富），
    但若"块里一条文本都在正文中定位不到"，则判定块已陈旧、改用正文切分。
    这个判据是确定性的、不是阈值猜测：正常同步的文档不可能一条都对不上
    （正文与块由同一个函数一起写入），对不上说明块被外部改成了与正文
    无关的旧内容 —— 此时继续信块，用户会看到一段自己文档里根本没有的
    "参考段落"，并可能把它当成自己的内容。
    """
    pairs = _blocks_from_json(blocks_json)
    content = content_text or ""
    if pairs and content:
        probe = [t for _k, _l, t in pairs if t]
        if probe and all(_locate(t, content, 0) < 0 for t in probe):
            pairs = []
    if not pairs:
        pairs = _blocks_from_text(content)
    rows: list[dao.ParagraphRow] = []
    pos = 0
    for ordinal, (kind, level, text) in enumerate(pairs):
        off = _locate(text, content, pos)
        if off >= 0:
            pos = off + len(text)
        rows.append(dao.ParagraphRow(
            doc_id=0, ordinal=ordinal, kind=kind, level=level, text=text,
            char_offset=off, text_hash=dao.text_hash(text)))
    return rows


# ---------------------------------------------------------------- 索引兜底
def _try_index(doc_id: int, rows: list[dao.ParagraphRow], doc_hash: str) -> None:
    """尝试把派生结果补进索引。失败**只记日志**，绝不影响返回值。

    索引是加速手段，不是正确性来源：补不上就每次现场派生，功能照常可用。
    把补索引的异常放出去会让"段落检索在某个坏文档上整个不可用"——那才是
    真的引入问题。
    """
    try:
        from ..db.connection import get_conn
        dao.replace_paragraphs(get_conn(), doc_id, rows, doc_hash=doc_hash)
    except Exception as exc:
        from .. import logs
        logs.get_logger("paragraph").warning(
            "段落索引补建失败（doc_id=%s），本次改为现场派生：%s", doc_id, exc)


def index_is_fresh(doc_id: int) -> bool:
    """该文档的段落索引是否与当前正文内容一致（哈希判据）。"""
    try:
        d = dao.get_document(doc_id)
        if d is None:
            return True
        return dao.paragraph_index_hash(doc_id) == (d.text_hash or "")
    except Exception:
        return False


def paragraphs_of(doc_id: int, doc_title: str = "") -> list[ParagraphRef]:
    """取某文档的段落（`ParagraphRef` 列表，按序号升序）。

    索引新鲜就直接读索引；否则现场派生并**顺带尝试**补索引。两条路径都走
    `derive_blocks`，所以结果必然一致 —— 这正是"段落表可随时重建"的依据。
    """
    d = dao.get_document(doc_id)
    title = doc_title or (d.title if d else "")

    if d is not None and index_is_fresh(doc_id):
        rows = dao.list_paragraphs(doc_id)
        if rows:
            return [_to_ref(r, title, "library") for r in rows]

    if d is None:
        return []
    rows = derive_blocks(d.blocks_json, d.content_text)
    _try_index(doc_id, rows, d.text_hash or "")
    return [_to_ref(r, title, "library") for r in rows]


def _to_ref(row, doc_title: str, source: str, score: float = 0.0) -> ParagraphRef:
    return ParagraphRef(
        source=source, para_id=int(row.id or 0), doc_id=int(row.doc_id or 0),
        doc_title=doc_title, ordinal=int(row.ordinal or 0), kind=row.kind or "",
        level=int(row.level or 0), text=row.text or "",
        char_offset=int(row.char_offset), text_hash=row.text_hash or "",
        score=score)


# ---------------------------------------------------------------- 草稿侧
def split_draft(text: str) -> list[ParagraphRef]:
    """把编辑区文本切段，返回 source="draft" 的段落列表。

    与资料库侧共用 `_blocks_from_text`，保证"草稿的段落切分规则"与
    "资料的段落切分规则"一致 —— 对齐功能比较的必须是同一套切分口径，
    否则差异列表里会混进大量"其实是切分差异"的假差异。
    """
    out: list[ParagraphRef] = []
    for ordinal, (kind, level, para) in enumerate(_blocks_from_text(text)):
        out.append(ParagraphRef(
            source="draft", para_id=ordinal, doc_id=0, doc_title="当前草稿",
            ordinal=ordinal, kind=kind, level=level, text=para,
            char_offset=-1, text_hash=dao.text_hash(para), score=0.0))
    return out


# ---------------------------------------------------------------- 检索
# 与 core/reference.py 同一套加权思路（组内归一 + 结构化加权），但段落侧
# 额外加一项**段长惩罚**：FTS5 的 BM25 偏向长文本，不惩罚就会让"整章正文"
# 永远压过"精准的那一句"，而这恰恰与"找参考段"的意图相反。
_LONG_PARA = 400
_LONG_PENALTY_MAX = 0.10
# 精确（AND）命中少于此数时才启用 OR 放宽兜底。取 5 是因为面板一屏通常能显示
# 5 条以上：够 5 条就没有"搜不到"的观感，不需要牺牲精确性去换召回。
_RELAX_MIN = 5
# 精确命中与放宽命中各自的分值区间，**刻意不相交**：精确项恒在
# [_STRICT_FLOOR, 1.0]，放宽项恒在 [0, _STRICT_FLOOR]。
# 这样"精确命中永远排在放宽命中之前"是**结构性保证**，不依赖两组 bm25
# 量纲碰巧可比 —— 后者在实测中真的翻过车（放宽项排到了精确项前面）。
_STRICT_FLOOR = 0.5
# 表格作参考价值低、噪声大：仍然建索引（可搜到），但默认不展示。
DEFAULT_HIDDEN_KINDS = ("table",)


def search_paragraphs(query: str, *, limit: int = 30,
                      doc_ids: list[int] | None = None,
                      exclude_kinds: tuple[str, ...] = DEFAULT_HIDDEN_KINDS
                      ) -> list[ParagraphRef]:
    """段落级检索（便捷入口，丢弃"是否放宽"标记）。"""
    return search_paragraphs_ex(query, limit=limit, doc_ids=doc_ids,
                               exclude_kinds=exclude_kinds)[0]


def search_paragraphs_ex(query: str, *, limit: int = 30,
                         doc_ids: list[int] | None = None,
                         exclude_kinds: tuple[str, ...] = DEFAULT_HIDDEN_KINDS,
                         ) -> tuple[list[ParagraphRef], bool]:
    """段落级检索，返回 `(结果, 是否放宽为任一词命中)`。

    排序口径（全部确定性）：FTS5 bm25 → 组内 min-max 归一到 [0,1] →
    标题段 +0.10 → 段长超 400 字按长度线性惩罚（最多 -0.10）。

    **两段式召回**（AND 优先、OR 兜底）：
      1. 先用 AND（精确）跑一次；
      2. 命中不足 `_RELAX_MIN` 且查询是多词时，再用 OR 跑一次并把新命中的
         段落按 0.6 折算分补进来。

    为什么需要第 2 步：段落很短，AND 极易全落空 —— 实测「安全生产 责任」
    被切成 安全/生产/责任 三词，而索引侧「安全生产责任制」是一个整词，
    同段里不存在独立的「责任」，AND 必然不命中。整篇检索因文本长而不易
    暴露这个问题，段落检索则首当其冲。

    为什么折算 0.6 而不是与 AND 结果平权：放宽是**召回**手段，若与精确命中
    同分，用户会以为"这些同样相关"。折算后精确命中稳定排在前面，同时
    `relaxed=True` 让 UI 如实说明"已放宽为任一词命中"，用户对排序保持信任。

    `doc_ids` 非空时只在指定文档内检索；`exclude_kinds` 默认滤掉表格段。
    """
    q = (query or "").strip()
    if not q:
        return ([], False)
    want = max(limit * 3, limit)

    strict = dao.search_paragraphs_fts(q, want)
    relaxed_used = False
    # 单词查询不需要放宽：放宽了也没有更多可命中，只是白跑一次 FTS。
    from ..db.tokenize import query_terms
    multi_term = len(query_terms(q)) > 1
    groups: list[tuple[list, float, float]] = [(list(strict), _STRICT_FLOOR, 1.0)]
    if len(strict) < min(limit, _RELAX_MIN) and multi_term:
        seen_ids = {int(r.ref_id) for r in strict}
        extra = [r for r in dao.search_paragraphs_fts(q, want, any_terms=True)
                 if int(r.ref_id) not in seen_ids]
        if extra:
            relaxed_used = True
            groups.append((extra, 0.0, _STRICT_FLOOR))

    if not any(rows for rows, _f, _c in groups):
        return ([], False)

    wanted = set(int(d) for d in doc_ids) if doc_ids else None
    all_ids = [r.ref_id for rows, _f, _c in groups for r in rows]
    info = dao.paragraph_doc_info(all_ids)

    out: list[ParagraphRef] = []
    for rows, floor, ceil in groups:
        if not rows:
            continue
        # **组内**归一：两组各自算 min-max，再映射到互不相交的分值区间。
        # 若把两组混在一起归一，放宽项可能因 bm25 量纲不同而排到精确项前面
        # （实测发生过：放宽项 0.60 压在精确项 0.22 之上）——那就等于
        # "放宽"把"精确"挤下去了，与设计意图相反。
        sc = [abs(r.rank) for r in rows]
        lo, hi = min(sc), max(sc)
        span = (hi - lo) or 1.0
        for r in rows:
            pid = int(r.ref_id)
            meta = info.get(pid)
            if meta is None:
                continue
            if wanted is not None and int(meta.get("doc_id") or 0) not in wanted:
                continue
            kind = meta.get("kind") or "paragraph"
            if exclude_kinds and kind in exclude_kinds:
                continue
            # FTS5 的 bm25 为**负值且越负越相关**，故 abs(rank) 越大越相关；
            # 归一化必须让最相关者拿到 1.0（此前写成 1.0-(abs-lo)/span，
            # 把最相关的段落压成最低分、最不相关的顶到首位）。
            base = (abs(r.rank) - lo) / span          # 组内相对相关度 0..1
            text = meta.get("text") or r.snippet or ""
            boost = 0.10 if (kind == "heading" and q in text) else 0.0
            if len(text) > _LONG_PARA:
                over = min(len(text) - _LONG_PARA, 2000) / 2000.0
                boost -= _LONG_PENALTY_MAX * over
            ratio = min(max(base + boost, 0.0), 1.0)
            out.append(ParagraphRef(
                source="library", para_id=pid,
                doc_id=int(meta.get("doc_id") or 0),
                doc_title=meta.get("doc_title") or r.title or "",
                ordinal=int(meta.get("ordinal") or 0), kind=kind,
                level=int(meta.get("level") or 0), text=text,
                char_offset=int(meta.get("char_offset", -1)),
                text_hash=meta.get("text_hash") or "",
                score=floor + (ceil - floor) * ratio))

    out.sort(key=lambda x: x.score, reverse=True)
    # 去重：同一段落可能因 FTS 多词命中被返回多次（ref_id 已唯一，此处兜底）
    seen: set[int] = set()
    uniq: list[ParagraphRef] = []
    for it in out:
        if it.para_id in seen:
            continue
        seen.add(it.para_id)
        uniq.append(it)
        if len(uniq) >= limit:
            break
    return (uniq, relaxed_used)


# ---------------------------------------------------------------- 内容对齐
# 结束语特征（公文实操中的固定收束句）。用于识别"末段是不是结束语"，
# 识别不到就按位置与形态退化为 body/signature —— 不做模糊推断。
_CLOSING_HINTS = (
    "特此通知", "特此报告", "特此通报", "特此函告", "特此批复", "特此公告",
    "妥否，请批示", "请遵照执行", "此令", "请予支持配合为盼", "本通告自",
    "专此报告", "请批复", "以上请示如无不妥", "现予公布",
)
# 落款形态：短行、以机关名常见后缀结尾，或纯日期。
# `×` 必须允许：公文书里「××市教育局」这种占位写法极常见（`skeletons.py`
# 自己就用「××单位」「××××」），不认它会把最常见的落款形态漏掉。
_SIG_CHARS = r"[\u4e00-\u9fff（）()·×]{0,24}"
_SIGNATURE_RE = re.compile(
    r"^\s*" + _SIG_CHARS +
    r"(局|部|厅|委|办|处|科|公司|单位|机关|政府|党委|大学|学院|支队|大队)"
    r"[\u4e00-\u9fff（）()·×]{0,12}\s*$")
_DATE_ONLY_RE = re.compile(r"^\s*\d{4}\s*[年\-/.]\s*\d{1,2}\s*[月\-/.]?\s*\d{0,2}\s*日?\s*$")
# 序号体系：一、 / （一） / 1. / 1、  —— 层级由序号形态决定
_ORD_CN_RE = re.compile(r"^\s*[一二三四五六七八九十]+[、．.]")
_ORD_CN_SUB_RE = re.compile(r"^\s*[（(][一二三四五六七八九十]+[）)]")
_ORD_NUM_RE = re.compile(r"^\s*\d+[、．.]")


def _heuristic_heading_level(text: str) -> int:
    """按**导入侧同一套规则**从文本推断标题级别（0 = 不是标题）。

    为什么对齐必须自己按文本判定、而不能只看 `kind`：参考段落来自
    `blocks_json`（kind 是解析器给的），草稿段落来自 `_blocks_from_text`
    （kind 是启发式给的）。同一段文字在两侧可能拿到不同的 kind —— 实测
    「第一部分内容。」在正文切分里被 `_HEADING_RE` 判为二级标题，而直接
    构造的参考段是 paragraph。两侧角色一旦不同，差异列表里就会出现成对的
    "假缺失 + 假多余"，把真正要看的结构差异淹没掉。
    """
    t = (text or "").strip()
    if not t:
        return 0
    try:
        from .parsers.txt_parser import _HEADING_RE
    except Exception:
        return 0
    if _HEADING_RE is not None and _HEADING_RE.match(t) and len(t) <= 40:
        return 2
    return 0


def _role_of(index: int, total: int, kind: str, level: int, text: str) -> str:
    """判定一段的**结构角色**。只按结构与固定收束句判断，不猜语义。

    判定次序刻意是"文本优先、kind 兜底"：见 `_heuristic_heading_level`。
    """
    lvl = _ordinal_level(text)
    if lvl:
        return f"heading_{lvl}"
    if index == 0:
        return "opening"
    heur = _heuristic_heading_level(text)
    if heur:
        return f"heading_{heur}"
    if kind == "heading":
        return f"heading_{level or 1}"
    if total > 1 and index == total - 1:
        if any(h in text for h in _CLOSING_HINTS):
            return "closing"
        if _DATE_ONLY_RE.match(text) or _SIGNATURE_RE.match(text):
            return "signature"
    return "body"


def _level_of_role(role: str) -> int:
    if role.startswith("heading_"):
        try:
            return int(role.rsplit("_", 1)[1])
        except (IndexError, ValueError):
            return 0
    return 0


def _roles(paras: list[ParagraphRef]) -> list[str]:
    total = len(paras)
    return [_role_of(i, total, p.kind, p.level, p.text) for i, p in enumerate(paras)]


def _ordinal_level(text: str) -> int:
    """从序号形态推断层级：`一、`→2，`（一）`→3，`1.`→4，无序号→0。"""
    if _ORD_CN_RE.match(text):
        return 2
    if _ORD_CN_SUB_RE.match(text):
        return 3
    if _ORD_NUM_RE.match(text):
        return 4
    return 0


_ROLE_LABEL = {
    "opening": "起首语", "body": "正文", "closing": "结束语", "signature": "落款",
}


def _role_label(role: str) -> str:
    if role.startswith("heading_"):
        return f"{role.rsplit('_', 1)[1]} 级标题"
    return _ROLE_LABEL.get(role, role)


def align_to_draft(draft_text: str, refs: list[ParagraphRef]) -> list[Alignment]:
    """把参考段落与当前草稿做**结构对齐**，返回差异列表。

    六个维度：层级 / 序号连续性 / 章节角色 / 顺序 / 覆盖度 / 多余度。
    **只比较结构与角色，不比较语义** —— 语义比较需要词向量，本项目不引入。

    输出按"参考段落顺序"稳定排列（每条都带一句可直接展示的中文提示）。
    """
    if not refs:
        return []
    draft = split_draft(draft_text)
    ref_roles = _roles(refs)
    draft_roles = _roles(draft)

    # 同角色段的出现次序索引：role -> [草稿段下标, ...]
    buckets: dict[str, list[int]] = {}
    for i, r in enumerate(draft_roles):
        buckets.setdefault(r, []).append(i)
    # 按文本找同段落（用于识别"换了层级"与"换了位置"）
    text_index: dict[str, list[int]] = {}
    for i, p in enumerate(draft):
        text_index.setdefault(p.text.strip(), []).append(i)

    used: dict[str, int] = {}
    out: list[Alignment] = []

    for ref_i, ref in enumerate(refs):
        role = ref_roles[ref_i]
        nth = used.get(role, 0)
        used[role] = nth + 1
        cand = buckets.get(role, [])
        draft_i = cand[nth] if nth < len(cand) else -1

        if draft_i < 0:
            # 参考有、草稿没有：标题缺与角色缺的提示语不同
            if role.startswith("heading_"):
                hint = (f"参考文献有{_role_label(role)}「{_short(ref.text)}」，"
                        f"你的草稿缺对应章节")
            else:
                hint = (f"参考文献有{_role_label(role)}「{_short(ref.text)}」，"
                        f"你的草稿缺对应内容")
            out.append(Alignment(
                ref_para_id=ref.para_id, ref_text=ref.text, draft_index=-1,
                draft_text="", role=role, relation="missing", score=0.0,
                hint=hint))
            continue

        d = draft[draft_i]
        same = text_index.get(ref.text.strip(), [])
        if not same or draft_i in same:
            # 文本对得上（或草稿里本就没有同一段文字可比）→ 视为已对齐。
            # 注意 `not same` 也算对齐：角色相同、内容不同是**正常**的 ——
            # 参考段本来就是拿来当模板改写的，不要求逐字相同。
            out.append(Alignment(
                ref_para_id=ref.para_id, ref_text=ref.text,
                draft_index=draft_i, draft_text=d.text, role=role,
                relation="matched", score=1.0,
                hint=f"「{_role_label(role)}」已对齐（第 {draft_i + 1} 段）"))
            continue

        # 同一段文字在草稿里的实际位置与"按角色配对"的位置不一致。
        # 是"换了层级"还是"换了先后"，取决于两处角色的层级是否不同。
        ref_lvl = _level_of_role(role)
        d_lvl = _level_of_role(draft_roles[same[0]])
        if ref_lvl and d_lvl and ref_lvl != d_lvl:
            out.append(Alignment(
                ref_para_id=ref.para_id, ref_text=ref.text,
                draft_index=same[0], draft_text=draft[same[0]].text, role=role,
                relation="level_mismatch", score=0.6,
                hint=(f"「{_short(ref.text)}」在参考文献里是{ref_lvl} 级标题，"
                      f"你的草稿里是{d_lvl} 级")))
        else:
            out.append(Alignment(
                ref_para_id=ref.para_id, ref_text=ref.text,
                draft_index=same[0], draft_text=draft[same[0]].text, role=role,
                relation="order_swap", score=0.5,
                hint=(f"「{_short(ref.text)}」在参考文献里是第 {ref_i + 1} 段，"
                      f"你的草稿里排在第 {same[0] + 1} 段")))

    # 多余度：草稿里存在、参考里完全没有的角色
    ref_role_set = set(ref_roles)
    for role, idxs in buckets.items():
        if role in ref_role_set:
            continue
        for i in idxs:
            out.append(Alignment(
                ref_para_id=0, ref_text="", draft_index=i,
                draft_text=draft[i].text, role=role,
                relation="extra", score=0.0,
                hint=(f"你的草稿有{_role_label(role)}"
                      f"「{_short(draft[i].text)}」，参考文献里没有对应段落")))

    # 序号连续性（只看草稿，指出跳号/重复）
    out.extend(_ordinal_issues(draft))
    return out


_CN_NUM = "一二三四五六七八九十"


def _cn_to_int(s: str) -> int:
    """中文数字 1..99 转 int；转不了返回 0（不猜）。"""
    if not s:
        return 0
    if len(s) == 1:
        return _CN_NUM.index(s) + 1 if s in _CN_NUM else 0
    if s.startswith("十") and len(s) == 2 and s[1] in _CN_NUM:
        return 10 + _CN_NUM.index(s[1]) + 1
    if s.endswith("十") and s[0] in _CN_NUM:
        return (_CN_NUM.index(s[0]) + 1) * 10
    if len(s) == 3 and s[1] == "十" and s[0] in _CN_NUM and s[2] in _CN_NUM:
        return (_CN_NUM.index(s[0]) + 1) * 10 + _CN_NUM.index(s[2]) + 1
    return 0


def _ordinal_issues(draft: list[ParagraphRef]) -> list[Alignment]:
    """检查草稿里各级序号的连续性，指出跳号与重复。"""
    found: dict[int, list[tuple[int, int]]] = {}
    for i, p in enumerate(draft):
        text = p.text
        m = _ORD_CN_SUB_RE.match(text)
        if m:
            v = _cn_to_int(m.group(0).strip("（()）"))
            if v:
                found.setdefault(3, []).append((i, v))
            continue
        m = _ORD_CN_RE.match(text)
        if m:
            v = _cn_to_int(m.group(0).strip("、．."))
            if v:
                found.setdefault(2, []).append((i, v))
            continue
        m = _ORD_NUM_RE.match(text)
        if m:
            try:
                v = int(m.group(0).strip("、．."))
            except ValueError:
                continue
            found.setdefault(4, []).append((i, v))

    out: list[Alignment] = []
    for level in sorted(found):
        items = found[level]
        seen: set[int] = set()
        for idx, val in items:
            if val in seen:
                out.append(Alignment(
                    draft_index=idx, draft_text=draft[idx].text,
                    role=f"heading_{level}", relation="level_mismatch",
                    score=0.4,
                    hint=f"第 {idx + 1} 段序号「{val}」重复（同一层级内）"))
                continue
            seen.add(val)
            # 期望序号取**去重后**的计数：用 enumerate 的 pos 会把前面被跳过
            # 的重复项也算进去，于是「一、二、二、三」里的「三」被判成跳号，
            # 用户照提示改成「四」就把本来正确的序号改坏了。
            expect = len(seen)
            if val != expect:
                out.append(Alignment(
                    draft_index=idx, draft_text=draft[idx].text,
                    role=f"heading_{level}", relation="level_mismatch",
                    score=0.4,
                    hint=(f"第 {idx + 1} 段序号跳到「{val}」，"
                          f"按顺序应为「{expect}」")))
    return out


def _short(text: str, n: int = 18) -> str:
    t = (text or "").replace("\n", " ").strip()
    return t if len(t) <= n else t[:n] + "…"


# ---------------------------------------------------------------- 骨架与槽位
# 6 类槽位规则。**只做确定性抽取**：规则命中即抽象，不命中即留原文。
# 刻意不做"开放式槽位识别"（如"这句话的重点词是什么"）——那需要语义理解，
# 必然不可解释且会误抽，而误抽的后果是把参考原文里的具体事项悄悄换成一个
# 空槽，用户填完才发现内容对不上。
_RE_MATTER = re.compile(r"关于([^，。；、\n]{2,30}?)的")
_RE_DATE = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?")
_RE_DEADLINE = re.compile(r"[0-9一二三四五六七八九十两]+\s*个工作日内")
_RE_COUNT = re.compile(
    r"[0-9一二三四五六七八九十两]+\s*(?:名|项|个|件|次|人|份|条|台|套|万元|元)")
_RE_CITED = re.compile(r"《[^》\n]{2,40}》(?:（[^）\n]{0,30}〔\d{4}〕\s*\d+\s*号）)?")

# 单位名**不用一条正则整体匹配**，改成"后缀锚定 + 按 jieba 词界向左扩展"。
#
# 为什么：`[\u4e00-\u9fff]{2,12}(局|部|…)` 这种写法会让前缀吞噬前面的动词——
# 实测「请各单位报××市教育局汇总。」里 `请各` + `单位` 直接拼成「请各单位」，
# 槽位示例成了一句带谓语的短语。用错位值做骨架，用户填出来的稿子是错的。
#
# 为什么用 jieba 词界、而不是"遇到停用字就停"：停用字是个闭集，动词是开放集
# ——「请各单位报…」挡住了"请"，但「现将有关情况报送长沙市教育局…」照样把
# "送"吞了进来。分词器本来就给出了词界，按词界切才不会随动词变化而漏。
_ORG_SUFFIXES = ("委员会", "办公室", "管理局", "支队", "大队", "中心", "集团",
                 "公司", "学院", "大学", "政府", "党委", "机关", "单位",
                 "局", "部", "厅", "委", "办", "处", "科")
# 合并左邻词时，含这些"动词/虚词"字的词立即停下（它们只可能出现在名称之外）
_ORG_STOP = set("请在由向对给经据要并与和为报将把及或以从到被让使令望拟兹是的了着过"
                "送交呈抄转发批致于至按依照遵根依")
# 以单字后缀（局/部/厅/委/办/处/科）结尾的**两字词**，只有本身是机关/组织
# 简称才成立：「全部/好处/干部/到处/内部」同样以后缀字结尾，不加这道判别
# 会把整句吞成 {org}（实测「本次工作全部完成。」→「本次工作全部」）。
# 只做确定性白名单，不做词性/语义推断（规格 C 系列约束）。
_ORG_SHORT_OK = frozenset({
    "省委", "市委", "县委", "区委", "地委", "党委", "纪委", "监委", "工委",
    "党组", "工会", "团委", "妇联", "残联", "人大", "政协", "政府", "法院",
    "检察", "公安", "财政", "教育", "卫健", "应急", "审计", "统计", "税务",
    "海关", "总局", "部委", "分局", "支队", "大队", "学校", "医院", "公司",
    "银行", "集团", "中心", "机关", "单位", "学院", "大学",
})
# 三字及以上、但确定不是单位的常见词（同为启发式，宁缺勿滥地只列高频例）
_ORG_NOT_ORG = frozenset({
    "大部分", "一部分", "小部分", "好处", "坏处", "全部", "干部",
    "内部", "外部", "局部", "到处", "部分",
})
_ORG_MAX_PREFIX = 10


def _find_org_spans(text: str) -> list[tuple[int, int, str, str]]:
    """找出单位名区间（后缀锚定 + 按词界向左合并，见 `_ORG_SUFFIXES` 处说明）。"""
    from ..db.tokenize import token_spans
    try:
        toks = token_spans(text)
    except Exception:
        return []
    out: list[tuple[int, int, str, str]] = []
    for i, (word, start, end) in enumerate(toks):
        if len(word) < 2 or not any(word.endswith(s) for s in _ORG_SUFFIXES):
            continue
        if word in _ORG_NOT_ORG:
            continue                      # 「大部分/好处」这类普通词
        if len(word) == 2 and word not in _ORG_SHORT_OK:
            continue                      # 两字词须是机关简称才成立
        beg = start                   # 左合并的游标（不改写循环变量）
        j = i - 1
        while j >= 0:
            pw, ps, pe = toks[j]
            if pe != beg:                         # 中间有空白/换行，不跨过去
                break
            if any(ch in _ORG_STOP for ch in pw):
                break
            if beg - ps > _ORG_MAX_PREFIX:
                break
            beg = ps
            j -= 1
        out.append((beg, end, "org", "单位名"))
    return out


# 除单位名外的 5 类规则；单位名由 _find_org_spans 单独处理
_SLOT_RULES: tuple[tuple[re.Pattern, str, str], ...] = (
    (_RE_CITED, "cited", "引用公文"),
    (_RE_DATE, "date", "日期"),
    (_RE_DEADLINE, "deadline", "时限"),
    (_RE_COUNT, "count", "数量"),
    (_RE_MATTER, "matter", "事项名"),
)


def extract_slots(text: str) -> list[Slot]:
    """从一段文本抽取槽位。**后出现的同名槽位加数字后缀**（org/org2）。

    重叠处理：先按起点、再按"长匹配优先"排序后依次取用，已覆盖的区间跳过。
    这一步是必需的：`《XX办法》（XX〔2026〕3号）` 会同时被"引用公文"与
    "日期"命中，不去重就会把同一段文字切成两个互相嵌套的槽位。
    """
    hits: list[tuple[int, int, str, str]] = []
    for pat, name, label in _SLOT_RULES:
        for m in pat.finditer(text):
            s, e = m.span()
            if name == "matter":
                # 事项名只取"关于…的"中间那段，不能把"关于""的"也抽象掉
                s, e = m.span(1)
            hits.append((s, e, name, label))
    # 单位名走独立的"后缀锚定 + 向左扩展"扫描（理由见 _ORG_SUFFIXES 处的注释）
    hits.extend(_find_org_spans(text))
    if not hits:
        return []
    # 长匹配优先 + 起点靠前优先：先占住大块，避免被小块切碎
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    taken: list[tuple[int, int]] = []
    out: list[Slot] = []
    counter: dict[str, int] = {}
    for s, e, name, label in hits:
        if s >= e:
            continue
        if any(not (e <= ts or s >= te) for ts, te in taken):
            continue
        taken.append((s, e))
        counter[name] = counter.get(name, 0) + 1
        slot_name = name if counter[name] == 1 else f"{name}{counter[name]}"
        out.append(Slot(name=slot_name, example=text[s:e],
                        span=(s, e), label=label))
    out.sort(key=lambda x: x.span[0])
    return out


def derive_skeleton(refs: list[ParagraphRef], kind: str = "") -> SkeletonDraft:
    """从参考段落派生骨架：把具体值抽象成槽位，**不生成任何新文本**。

    模板 = 参考段落按序拼接；每个槽位区间替换为 `{name}`。
    替换从后往前做，避免前面的替换改变后面区间的偏移。
    """
    ordered = sorted([r for r in refs if (r.text or "").strip()],
                     key=lambda x: (x.doc_id, x.ordinal))
    if not ordered:
        return SkeletonDraft(kind=kind, template="", slots=[],
                             source_paras=[])
    template = "\n".join(r.text.strip() for r in ordered)
    slots = extract_slots(template)
    for slot in sorted(slots, key=lambda s: s.span[0], reverse=True):
        s, e = slot.span
        template = template[:s] + "{" + slot.name + "}" + template[e:]
    return SkeletonDraft(kind=kind, template=template, slots=slots,
                         source_paras=[r.para_id for r in ordered])


def render_draft(skeleton: SkeletonDraft | str, values: dict) -> str:
    """填槽生成草稿文本。未填的槽位**保留 `{name}` 占位**，不静默清空。

    保留占位而不是替换为空串：清空会让"还没填的事项"看起来像"这段本来
    就没有内容"，用户很可能直接采用；保留占位则一眼能看出还差什么。
    """
    template = skeleton.template if isinstance(skeleton, SkeletonDraft) else str(skeleton)
    out = template or ""
    for k, v in (values or {}).items():
        out = out.replace("{" + str(k) + "}", "" if v is None else str(v))
    return out


def skeleton_from_template(template: str, kind: str = "",
                           source_paras: list[int] | None = None,
                           examples: dict[str, str] | None = None) -> SkeletonDraft:
    """从已存模板文本重建骨架（含槽位），供"载入用户骨架"用。

    `examples` 是「槽位名 → 参考原值」的映射（派生阶段由 `extract_slots` 抽到）。
    模板被用户编辑后必须重建槽位表单，而重建只能靠模板文本里的 `{name}`
    —— 不把原值带回来的话，界面上就只剩"请填写"占位，用户看不到参考段落
    里本来的单位名/日期/事项名，只能凭空填，"可解释、不编造"的支撑随之消失。
    """
    ex = examples or {}
    slots: list[Slot] = []
    for m in re.finditer(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template or ""):
        name = m.group(1)
        if any(s.name == name for s in slots):
            continue
        slots.append(Slot(name=name, example=ex.get(name, ""),
                          span=m.span(), label=""))
    return SkeletonDraft(kind=kind, template=template or "", slots=slots,
                         source_paras=list(source_paras or []))


# ---------------------------------------------------------------- 引用溯源
def reference_changed(ref: ParagraphRef) -> bool:
    """引用是否已失效（源段落被改动）。

    判据是段落**自身**的哈希，不用文档哈希：用户引用的是那一段，同文档
    别处的修改不该让这条引用被报为"已变更"（那会产生大量无意义告警，
    用户很快就会无视它，告警随之失效）。
    """
    if ref.source != "library" or not ref.para_id:
        return False
    try:
        row = dao.get_paragraph(ref.para_id)
    except Exception:
        return False
    if row is None:
        return True                      # 段落已不存在（文档被删/重建后换了 id）
    return (row.text_hash or "") != (ref.text_hash or "")


def resolve_anchor(ref: ParagraphRef) -> tuple[int, int]:
    """把引用解析为源文档里的 (起始偏移, 长度)，供跳转高亮。

    返回 (-1, 0) 表示"定位不到"，调用方应降级为"打开所属文档"。
    定位不到时**不猜**：宁可少一个高亮，也不能高亮到错误位置。
    """
    if ref.source != "library" or not ref.doc_id:
        return (-1, 0)
    text = ref.text or ""
    if not text:
        return (-1, 0)
    d = dao.get_document(ref.doc_id)
    if d is None:
        return (-1, 0)
    content = d.content_text or ""
    # 先用索引里的偏移验一次（落点不变量）；不成立再现场重新定位
    off = int(ref.char_offset)
    if off >= 0 and content[off:off + len(text)] == text:
        return (off, len(text))
    fresh = _locate(text, content, 0)
    if fresh >= 0:
        return (fresh, len(text))
    return (-1, 0)


def to_json_slots(slots: list[Slot]) -> str:
    return json.dumps(
        [{"name": s.name, "example": s.example, "label": s.label,
          "span": list(s.span)} for s in slots], ensure_ascii=False)


def from_json_slots(raw: str) -> list[Slot]:
    try:
        data = json.loads(raw or "[]")
    except Exception:
        return []
    out: list[Slot] = []
    for d in data:
        try:
            span = d.get("span") or (0, 0)
            out.append(Slot(name=d.get("name", ""), example=d.get("example", ""),
                            label=d.get("label", ""),
                            span=(int(span[0]), int(span[1]))))
        except Exception:
            continue
    return out
