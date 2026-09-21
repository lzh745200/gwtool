# -*- coding: utf-8 -*-
"""词表文件解析层：把各种格式统一成「条目列表」。

设计要点
--------
**为什么要有统一契约**：格式解析的差异只应存在于各 `parse_*` 里，而
"预检 / 冲突判定 / 事务写入"这些**与格式无关**的逻辑只写一遍（在
`core/wordlist.py`）。否则每加一种格式就要重写一遍冲突规则，迟早漂移。

契约：`parse(path, role, opts) -> ParseResult`
  · `entries`：规范化后的条目（已按角色填好字段）；
  · `issues` ：逐行问题 `(行号, 原因)`，**不阻断其余行**；
  · `warnings`：整体性提示（如"TBX 缺 lang 标注，按首个 langSet 处理"）。

**角色（role）**决定条目的落点，由用户导入时选定，文件内声明可覆盖：
  · ``pairs``  纠错对：``wrong`` = 错误写法，``correct`` = 正确写法；
  · ``terms``  行业术语：``wrong`` = 异名/非规范写法，``correct`` = 规范名
               （展平后与 pairs 同样落 ``error_pairs``，故字段复用同一对）；
  · ``protect``保护词：``word`` = 词本身。

**零新增依赖**：只用标准库；XLSX 由本包自带的 OOXML reader 处理（见
`core.xlsx_read`），DOCX 复用项目已有的 python-docx。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Entry", "ParseResult", "ROLES", "sniff", "parse", "SUPPORTED_EXTS"]

# 三类作用。与 db.schema 的 wordlist_sources.role 取值一致。
ROLES = ("pairs", "terms", "protect")

# 角色 → 中文标签（UI 与预览文案共用，避免两处各写一份）
ROLE_LABELS = {
    "pairs": "纠错对",
    "terms": "行业术语",
    "protect": "保护词",
}


@dataclass
class Entry:
    """一条规范化后的词表条目（未与库比对）。"""
    role: str
    wrong: str = ""            # pairs/terms：错误写法 / 异名
    correct: str = ""          # pairs/terms：正确写法 / 规范名
    word: str = ""             # protect：词本身
    category: str = ""
    confidence: float = 0.0    # 0 表示"用角色的默认值"
    pinyin: str = ""
    definition: str = ""
    src_line: int = 0          # 源文件行号，供报错定位

    @property
    def key(self) -> str:
        """去重键：同角色同内容的条目只留一条。"""
        if self.role == "protect":
            return f"protect|{self.word}"
        return f"{self.role}|{self.wrong}|{self.correct}"


@dataclass
class ParseResult:
    fmt: str = ""
    entries: list = field(default_factory=list)
    issues: list = field(default_factory=list)      # [(行号, 原因)]
    warnings: list = field(default_factory=list)    # [整体提示]

    def add_issue(self, line: int, why: str) -> None:
        self.issues.append((int(line), str(why)))

    def warn(self, text: str) -> None:
        self.warnings.append(str(text))


# 扩展名 → 解析器模块名（分派用）。``.txt`` 与 ``.csv`` 共用表格式解析。
_EXTS = {
    ".csv": "csv_tsv", ".tsv": "csv_tsv", ".txt": "csv_tsv",
    ".json": "json_fmt",
    ".xlsx": "xlsx_fmt",
    ".docx": "docx_fmt",
    ".tbx": "tbx_fmt", ".xml": "tbx_fmt", ".xlf": "tbx_fmt", ".xliff": "tbx_fmt",
}

SUPPORTED_EXTS = tuple(sorted(_EXTS))


def sniff(path) -> str:
    """按扩展名判定解析器模块名；不支持的扩展名抛 ValueError。

    ⚠️ **不做内容嗅探**：扩展名与真实内容不符（如 CSV 改名 .xlsx）时应由
    解析器自己报"不是有效的 OOXML 包"这种**可读**错误，而不是在这里猜。
    猜错的代价是把用户的文件按错误格式解释，然后导入一堆脏数据。
    """
    ext = Path(path).suffix.lower()
    mod = _EXTS.get(ext)
    if mod is None:
        raise ValueError(
            "不支持的词表格式：%s（当前支持 %s）"
            % (ext or "无扩展名", "、".join(SUPPORTED_EXTS)))
    return mod


def parse(path, role: str = "pairs", options=None) -> ParseResult:
    """解析词表文件。`role` 是默认角色，文件内声明可覆盖。"""
    if role not in ROLES:
        raise ValueError("未知角色：%r（应为 %s）" % (role, "/".join(ROLES)))
    mod_name = sniff(path)
    import importlib
    mod = importlib.import_module("%s.%s" % (__name__, mod_name))
    return mod.parse(path, role, options or {})
