# -*- coding: utf-8 -*-
"""内部文档结构模型：所有解析器统一输出该结构，供汇编/预览/纠错消费。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

# 块类型：heading=标题(level 1..4) paragraph=正文 list_item=列表 quote=引用 table=表格
HEADING = "heading"
PARAGRAPH = "paragraph"
LIST_ITEM = "list_item"
TABLE = "table"


@dataclass
class Block:
    type: str = PARAGRAPH
    level: int = 0          # 标题级别 1-4
    text: str = ""
    align: str = "left"     # left/center/right
    rows: list[list[str]] | None = None   # type=table 时为单元格文本

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DocTree:
    """一篇材料的结构化文本。"""
    title: str = ""
    blocks: list[Block] = field(default_factory=list)

    def plain_text(self) -> str:
        """全文纯文本（标题 + 正文），**首块与标题同文时只保留一份**。

        为什么必须去重：各解析器在文档没有显式标题时会把首个标题块**兼作**
        title（DOCX：`tree.title = blocks[0].text`；TXT/MD/HTML 同理），
        无条件拼接的话同一行会在 content_text 里出现两次。而 content_text 是
        **字数统计（`word_count=len(content_text)`）、查重哈希、FTS 索引与
        "导出纯文本"**的共同来源 —— 短文档的字数会接近翻倍，导出件首行重影。
        （实测 TXT/MD/HTML 三种格式均重复一次。）

        判据只看"文本是否相同"，不像 `effective_blocks` 那样要求 HEADING+L1：
        那里是判断"渲染时会不会重复（只有一级标题会被渲染成材料标题）"，
        这里判断的是"纯文本里是否同一行写了两遍"，与块类型无关。
        """
        parts = [self.title] if self.title else []
        blocks = self.blocks
        head = (self.title or "").strip()
        if head and blocks:
            first = blocks[0]
            if first.type != TABLE and (first.text or "").strip() == head:
                blocks = blocks[1:]
        for b in blocks:
            if b.type == TABLE and b.rows:
                parts.extend(" | ".join(cell for cell in row) for row in b.rows)
            elif b.text:
                parts.append(b.text)
        return "\n".join(parts)

    def effective_blocks(self, insert_titles: bool) -> list["Block"]:
        """汇编视图下的正文块：当材料标题已作为一级标题插入时，
        跳过与标题重复的首个标题块（避免标题渲染两遍）。

        ⚠ 只跳过 **level==1** 的首块：`docxgen` 把材料标题渲染为一级标题，
        真正会被重复的只有同样的一级标题块。若不校验层级，一个 level=2 的
        同名首块也会被当作"已渲染过"而删掉 —— 而它其实**没有被渲染**，
        于是标题连同正文一起消失（`blocks[1:]` 会把整个列表清空）。
        """
        blocks = list(self.blocks)
        tree_title = (self.title or "").strip()
        if insert_titles and tree_title:
            if blocks and blocks[0].type == HEADING \
                    and blocks[0].level == 1 \
                    and blocks[0].text.strip() == tree_title:
                blocks = blocks[1:]
        return blocks

    def to_json(self) -> str:
        import json
        return json.dumps([b.to_dict() for b in self.blocks], ensure_ascii=False)

    @classmethod
    def from_json(cls, title: str, blocks_json: str) -> "DocTree":
        import json

        tree = cls(title=title)
        # try 收窄到单块：坏块只丢自己并留日志，不再连坐其后所有块
        # （原来整个循环被吞异常包住，一个坏块 = 后文全部无声消失）。
        try:
            data = json.loads(blocks_json or "[]")
        except Exception:                        # 整段不可解析：按空结构兜底
            from ..logs import get_logger
            get_logger("model").warning("blocks_json 解析失败，按空结构兜底")
            return tree
        for d in data if isinstance(data, list) else []:
            try:
                tree.blocks.append(Block(**d))
            except Exception as exc:             # 单块损坏不阻断
                from ..logs import get_logger
                get_logger("model").warning("坏块被跳过：%s", exc)
        return tree
