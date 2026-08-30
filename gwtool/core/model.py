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
        parts = [self.title] if self.title else []
        for b in self.blocks:
            if b.type == TABLE and b.rows:
                parts.extend(" | ".join(cell for cell in row) for row in b.rows)
            elif b.text:
                parts.append(b.text)
        return "\n".join(parts)

    def effective_blocks(self, insert_titles: bool) -> list["Block"]:
        """汇编视图下的正文块：当材料标题已作为一级标题插入时，
        跳过与标题重复的首个标题块（避免标题渲染两遍）。"""
        blocks = list(self.blocks)
        tree_title = (self.title or "").strip()
        if insert_titles and tree_title:
            if blocks and blocks[0].type == HEADING \
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
        try:
            for d in json.loads(blocks_json or "[]"):
                tree.blocks.append(Block(**d))
        except Exception:
            pass
        return tree
