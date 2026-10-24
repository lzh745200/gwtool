# -*- coding: utf-8 -*-
"""任意来源文档的汇编规范化：把"能读到的文本"变成"可排版的公文结构"。

为什么需要它
------------
汇编入口（``compiler.load_trees``）拿到的材料来源五花八门：

* 资料库里**纯文本 / markdown** 导入的文档，``blocks_json`` 常常是空或全是
  ``paragraph``（导入器只切了段）；markdown 的 ``#``/``##`` 字面还留在正文里；
* PDF 提取文本是**逐行硬换行**的，一句话被拆成多行；
* 少数文档连标题、主送机关、落款都没有块级标记，全是正文段。

结果：目录抓不到标题、"一、提高认识""（一）统一思想" 全被当正文，字体/缩进
规则失效——同一份材料从 Word 导入能排版，从 markdown/PDF 导入就散架。

本模块在**汇编组装阶段**做一次确定性规范化（不改解析器、不改入库数据）：
统一成 ``heading``/``paragraph``/``list_item``/``table`` 四类块，标题进
``DocTree.title``，层级、主送、落款、硬换行续接都在这里收口。

规则（精度优先，宁少勿滥）
--------------------------
1. **markdown 标记**：剥离开头 ``#``~``######``（h1→level1 / h2→level2 /
   h3→level3，更深按 3 计）、``>`` 引用符、``*``/``- `` 列表符；围栏代码块
   （``` / ~~~）**内部行原样保留**、不参与任何识别（围栏行本身丢弃）。
2. **标题**：仅看**第一个内容行**——长度 ≤ 40、以文种词收尾、不含句末标点、
   且带「关于/在/为/致/转发/批转/印发」或含「的」→ 文档标题（进 ``title``，
   不再作为正文块）。
3. **层级**：行首 ``一、``→level1、``（一）``→level2、``1./（1）``→level3；
   **行长度 ≤ 60 才判标题**（长行是正文里引用"一、二、"的句子，不能误伤）。
   markdown 显式标题（``#``）不受长度限制。
4. **主送机关**：标题之后前 3 个内容行内、长度 ≤ 30、以冒号结尾的短行
   （「各单位：」「各市人民政府，市政府各部门：」）→ 主送标记（保留为正文段，
   但不会被并进上一段或识别成标题）。
5. **落款**：末尾连续的 1~4 行，**同时含日期行与单位行**（「××市人民政府」/
   「2026年10月3日」）→ 落款（保留为独立正文段，不再参与续接合并）。
6. **硬换行续接**：上一行不以句末标点（。！？；：…"」』））结尾、下一行不是
   标题/主送/落款/代码时，把下一行接上去（PDF/纯文本的断行修复）。
7. **幂等**：``normalize_tree(normalize_tree(t))`` 与单次结果逐块一致。

实现约束：纯函数（不改入参 DocTree）、不读写文件/网络、无第三方依赖；
块类型只用 ``model.py`` 既有的四种，不新增渲染语义。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import Block, DocTree, HEADING, LIST_ITEM, PARAGRAPH, TABLE

#: 文种/文件类尾词：标题必须以其中之一收尾（精度优先，避免把正文句认成标题）
TITLE_WORDS: tuple[str, ...] = (
    "通知", "通报", "报告", "请示", "批复", "意见", "决定", "命令", "通令",
    "指示", "通告", "公告", "决议", "公报", "议案", "纪要", "函", "方案",
    "总结", "讲话", "条例", "规定", "办法", "细则", "规划", "计划", "要点",
)
#: 落款单位名尾词（短行、无句末标点时才认）
SIGNATURE_SUFFIXES: tuple[str, ...] = (
    "人民政府", "政府", "委员会", "办公厅", "办公室", "管理局", "局", "部",
    "厅", "公司", "集团", "中心", "学校", "学院", "医院", "法院", "检察院",
    "党委", "党支部", "支部", "小组", "协会", "学会", "研究院", "研究所",
    "所", "处", "科", "单位",
)
#: 句末/段末标点：续接判据（上一行以这些结尾则视为完整句，不再并入下一行）
_SENT_END = "。！？；：!?;:…”」』）)"
_TITLE_SENT_END = "。！？；!?;"
#: 标题可用的开头（公文标题的常见起手；配合"以文种词收尾"双重判据）
_TITLE_STARTS = ("关于", "在", "为", "致", "转发", "批转", "印发", "××", "XX")

_MD_HEAD_RE = re.compile(r"^(#{1,6})\s*(.*?)\s*#*\s*$")
_MD_QUOTE_RE = re.compile(r"^(?:>\s?)+")
_MD_BULLET_RE = re.compile(r"^(?:[-*+•·])\s+")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_LEVEL1_RE = re.compile(r"^[一二三四五六七八九十百]+、")
_LEVEL2_RE = re.compile(r"^[（(][一二三四五六七八九十百]+[）)]")
_LEVEL3_RE = re.compile(r"^(?:\d+[.．、]|[（(]\d+[）)])")
_DATE_RE = re.compile(
    r"^(?:20\d{2}|[〇零一二三四五六七八九]{4})年"
    r"(?:\d{1,2}|[〇零一二三四五六七八九十]{1,3}|[×Xx]{1,2})月"
    r"(?:\d{1,2}|[〇零一二三四五六七八九十]{1,3}|[×Xx]{1,2})日$")
#: 纯空白行（含全角空格）
_BLANK_RE = re.compile(r"^[\s\u3000]*$")


@dataclass
class NormStats:
    """规范化结果的可观测统计（供测试/日志；不参与排版）。"""
    title: str = ""
    title_removed: bool = False
    recipient: str = ""
    signature: list[str] = field(default_factory=list)
    merged_lines: int = 0
    headings: dict[int, int] = field(default_factory=dict)


@dataclass
class _Entry:
    """扁平化后的中间行（table/heading/list_item/code/role/line）。"""
    kind: str
    text: str = ""
    level: int = 0
    md_level: int = 0
    rows: list[list[str]] | None = None


# ---------------------------------------------------------------- 文本判据
def _strip_ws(text: str) -> str:
    return (text or "").strip().strip("\u3000").strip()


def _strip_md_prefix(line: str) -> tuple[str, int]:
    """剥离 markdown 行首标记，返回 (清理后文本, 标题级别 0/1..3)。

    允许复合前缀（``> # 标题``、``- **一、**`` 这类混排），因此循环剥离到稳定；
    标题级别取见过的最大 ``#`` 层数。
    """
    text = (line or "").rstrip()
    level = 0
    for _ in range(3):
        changed = False
        m = _MD_HEAD_RE.match(text)
        if m:
            level = max(level, min(len(m.group(1)), 3))
            text = m.group(2)
            changed = True
        stripped = _MD_QUOTE_RE.sub("", text)
        if stripped != text:
            text = stripped
            changed = True
        stripped = _MD_BULLET_RE.sub("", text)
        if stripped != text:
            text = stripped
            changed = True
        if not changed:
            break
    return _strip_ws(text), level


def _heading_level(text: str) -> int:
    """行首中文序号 → 1/2/3；不是序号返回 0（不做长度判断）。"""
    if _LEVEL1_RE.match(text):
        return 1
    if _LEVEL2_RE.match(text):
        return 2
    if _LEVEL3_RE.match(text):
        return 3
    return 0


def official_level(text: str) -> int:
    """按公文序号判定**公文层级**（1=一、 2=（一） 3=1./（1）），非序号返回 0。

    字体与目录口径的单一事实源：材料标题占 Heading 1 后块 level 会整体下移
    一级，但「一、」永远该用黑体、「（一）」永远该用楷体。调用方直接按文本
    判定即可，不受解析器给的 level / 重定基影响。
    """
    return _heading_level((text or "").strip())


def _is_title_line(text: str) -> bool:
    """是否公文标题行（长度、文种尾词、非句末标点、非序号标题）。"""
    if not (4 <= len(text) <= 40):
        return False
    if text.endswith(tuple(_TITLE_SENT_END)):
        return False
    if _heading_level(text):
        return False
    if not any(text.endswith(word) for word in TITLE_WORDS):
        return False
    return text.startswith(_TITLE_STARTS) or "的" in text


def _is_recipient_line(text: str) -> bool:
    """主送机关行：短、以冒号结尾、含单位/主体词，且不是"如下："类引导语。"""
    if not (2 <= len(text) <= 30) or not text.endswith(("：", ":")):
        return False
    body = text[:-1].strip()
    if not body or any(ch in body for ch in "。！？；"):
        return False
    if any(word in body for word in ("如下", "通知如下", "报告如下", "请示如下",
                                     "现将", "现就", "特此")):
        return False
    hints = ("各", "单位", "部门", "政府", "局", "委", "办", "公司", "学校",
             "医院", "社区", "乡镇", "街道", "集团", "中心", "处", "科", "室")
    return body.startswith(("××", "XX")) or any(h in body for h in hints)


def _is_date_line(text: str) -> bool:
    return bool(_DATE_RE.match(text))


def _is_org_line(text: str) -> bool:
    if not (2 <= len(text) <= 40):
        return False
    if any(ch in text for ch in _SENT_END):
        return False
    if _heading_level(text):
        return False
    return any(text.endswith(suffix) for suffix in SIGNATURE_SUFFIXES)


def _is_signature_piece(text: str) -> bool:
    return _is_date_line(text) or _is_org_line(text)


# ---------------------------------------------------------------- 扁平化
def _flatten(tree: DocTree) -> list[_Entry]:
    """把所有块拆成"行条目"；table 与既有 heading 保留类型，代码围栏内原样。"""
    entries: list[_Entry] = []
    in_fence = False
    for block in tree.blocks:
        btype = block.type or PARAGRAPH
        if btype == TABLE:
            in_fence = False
            entries.append(_Entry(kind="table", rows=block.rows))
            continue
        if btype == HEADING:
            text, md_level = _strip_md_prefix(block.text)
            # 缺陷 b（v1.15）：解析器给的旧 level 可能不可信（txt 解析器把
            # 首个"一、"记成 level2，docx 却记成 level1）。文本里有**明确公文
            # 序号**时以序号为准（一、→1、（一）→2、1./(1)→3），三来源才一致；
            # 无序号 heading（如「总体要求」）才沿用解析器/markdown 的 level。
            seq_level = _heading_level(text)
            if seq_level:
                level = seq_level
            else:
                level = md_level or int(block.level or 1)
            level = min(max(level, 1), 4)
            if text:
                entries.append(_Entry(kind="heading", text=text, level=level))
            continue
        if btype == LIST_ITEM:
            text = _strip_ws(block.text)
            if text:
                entries.append(_Entry(kind="list_item", text=text))
            continue
        # paragraph：可能是一行，也可能是多行（PDF/纯文本兜底）
        for raw_line in (block.text or "").split("\n"):
            if _FENCE_RE.match(raw_line):
                in_fence = not in_fence        # 围栏行不输出
                continue
            if getattr(block, "_norm_raw", False):
                # 上一轮认定的代码行：原样保留，不再参与识别
                entries.append(_Entry(kind="code", text=raw_line.rstrip()))
                continue
            if in_fence:
                if not _BLANK_RE.match(raw_line):
                    entries.append(_Entry(kind="code", text=raw_line.rstrip()))
                continue
            text, md_level = _strip_md_prefix(raw_line)
            if not text:
                continue
            entries.append(_Entry(kind="line", text=text, md_level=md_level))
    return entries


# ---------------------------------------------------------------- 角色识别
def _take_title(entries: list[_Entry]) -> tuple[list[_Entry], str]:
    """第一个内容行是标题则摘出（返回新列表与标题文本）。"""
    if not entries:
        return entries, ""
    first = entries[0]
    if first.kind == "line" and _is_title_line(first.text):
        return entries[1:], first.text
    return entries, ""


def _mark_recipient(entries: list[_Entry]) -> tuple[list[_Entry], str]:
    """标题之后、前 3 个条目内的第一个正文行 → 主送机关。"""
    for i, entry in enumerate(entries[:3]):
        if entry.kind != "line":
            continue
        if _is_recipient_line(entry.text):
            entries[i] = _Entry(kind="role_recipient", text=entry.text)
            return entries, entry.text
        break               # 第一个正文行不是主送 → 不往后猜
    return entries, ""


def _mark_signature(entries: list[_Entry]) -> tuple[list[_Entry], list[str]]:
    """末尾连续 1~4 行同时含日期与单位 → 落款（独立成段、不参与续接）。"""
    tail: list[tuple[int, _Entry]] = []
    for idx in range(len(entries) - 1, -1, -1):
        entry = entries[idx]
        if entry.kind != "line":
            break
        if not _is_signature_piece(entry.text):
            break
        tail.append((idx, entry))
        if len(tail) >= 4:
            break
    if len(tail) < 2:
        return entries, []
    if not any(_is_date_line(e.text) for _i, e in tail):
        return entries, []
    if not any(_is_org_line(e.text) for _i, e in tail):
        return entries, []
    texts = [entry.text for _i, entry in reversed(tail)]
    for idx, entry in tail:
        entries[idx] = _Entry(kind="role_signature", text=entry.text)
    return entries, texts


def _classify(entries: list[_Entry]) -> None:
    """把 line 条目分成 heading / line；标题长度门槛在这里收口。

    缺陷 b（v1.15）：markdown 的 ``## 一、`` 与 txt/docx 的"一、"必须落到
    同一级——**文本里有明确公文序号时序号优先**，只有无序号标题才用
    markdown 的 ``#`` 层级（长度限制只约束"纯文本行"，markdown 显式标题不受限）。
    """
    for entry in entries:
        if entry.kind != "line":
            continue
        seq_level = _heading_level(entry.text)
        if entry.md_level:
            entry.kind = "heading"
            entry.level = seq_level or min(entry.md_level, 3)
            continue
        if seq_level and len(entry.text) <= 60:
            entry.kind = "heading"
            entry.level = seq_level


# ---------------------------------------------------------------- 组装
def _build_blocks(entries: list[_Entry], stats: NormStats) -> list[Block]:
    blocks: list[Block] = []
    para = ""

    def flush() -> None:
        nonlocal para
        text = _strip_ws(para)
        if text:
            blocks.append(Block(type=PARAGRAPH, text=text))
        para = ""

    for entry in entries:
        if entry.kind == "table":
            flush()
            blocks.append(Block(type=TABLE, rows=entry.rows))
            continue
        if entry.kind == "heading":
            flush()
            blocks.append(Block(type=HEADING, level=entry.level or 1,
                                text=entry.text))
            stats.headings[entry.level or 1] = \
                stats.headings.get(entry.level or 1, 0) + 1
            continue
        if entry.kind == "list_item":
            flush()
            blocks.append(Block(type=LIST_ITEM, text=entry.text))
            continue
        if entry.kind in ("code", "role_recipient", "role_signature"):
            flush()
            block = Block(type=PARAGRAPH, text=entry.text)
            if entry.kind == "code":
                # 动态标记（不参与序列化）：让下一轮规范化仍按"代码行"原样保留，
                # 从而保证幂等；渲染端只认 type/level/text，不受影响。
                block._norm_raw = True       # type: ignore[attr-defined]
            blocks.append(block)
            continue
        # 普通正文行：上一行没有句末标点 → 续接（硬换行修复）
        if para and not para.endswith(tuple(_SENT_END)):
            para += entry.text
            stats.merged_lines += 1
        else:
            flush()
            para = entry.text
    flush()
    return blocks


# ---------------------------------------------------------------- 对外入口
def normalize_tree_ex(tree: DocTree) -> tuple[DocTree, NormStats]:
    """规范化一棵 DocTree，返回 ``(新树, 统计)``；**不修改入参**。"""
    stats = NormStats()
    if tree is None or not tree.blocks:
        return DocTree(title=(tree.title if tree is not None else ""),
                       blocks=[]), stats

    entries = _flatten(tree)

    entries, title = _take_title(entries)
    if title:
        stats.title = title
        stats.title_removed = True
    entries, recipient = _mark_recipient(entries)
    stats.recipient = recipient
    entries, signature = _mark_signature(entries)
    stats.signature = signature

    _classify(entries)
    blocks = _build_blocks(entries, stats)
    new_title = title or _strip_ws(tree.title)
    return DocTree(title=new_title, blocks=blocks), stats


def normalize_tree(tree: DocTree) -> DocTree:
    """规范化一棵 DocTree（幂等、纯函数）；无块时原样返回等价新树。"""
    return normalize_tree_ex(tree)[0]


# ---------------------------------------------------------------- 材料标题重定基
#: 树级动态标记：已做过"材料标题插入后的层级重定基"，防止重复调用继续加层。
#: 动态属性不参与序列化（DocTree 没有该字段），不会污染入库数据。
_REBASE_MARK = "_norm_material_rebased"


def _copy_block(block: Block, level: int | None = None) -> Block:
    """复制一个块（可选改 level），并保留 compile_norm 自己打的动态标记。"""
    copy = Block(type=block.type,
                 level=block.level if level is None else level,
                 text=block.text, align=block.align, rows=block.rows)
    if getattr(block, "_norm_raw", False):     # 代码行标记，保证再次规范化稳定
        copy._norm_raw = True                  # type: ignore[attr-defined]
    return copy


def rebase_material_levels(tree: DocTree) -> DocTree:
    """材料标题以**一级标题**插入时，把材料内部 heading 整体降一级（封顶 4：材料标题 1 + 公文三级 1.）。

    docxgen / pdfrender 会把每份材料的 ``tree.title`` 渲染成 Heading 1；若材料
    内部的"一、"还是 level1，目录里材料标题与它的一级标题就**同为一级**，
    层级扁平（T4 验收矩阵实测）。

    * 只返回**副本**，不修改资料库里的块数据；
    * 重复调用不叠加：返回的树带 ``_REBASE_MARK`` 动态标记，二次调用直接返回；
    * 段落/表格/列表项不移动；无标题的树由调用方跳过（本函数不做条件判断）。
    """
    if tree is None:
        return DocTree()
    if getattr(tree, _REBASE_MARK, False):
        return tree
    blocks: list[Block] = []
    for block in tree.blocks:
        if block.type == HEADING:
            level = min(max(int(block.level or 1), 1) + 1, 4)
            blocks.append(_copy_block(block, level=level))
        else:
            blocks.append(_copy_block(block))
    new_tree = DocTree(title=tree.title, blocks=blocks)
    new_tree._norm_material_rebased = True     # type: ignore[attr-defined]
    return new_tree
