# -*- coding: utf-8 -*-
"""写作参考库：加载「公版/开放授权语料包」，提供检索与模板查询。

语料包结构（由外部构建脚本生成，见 doc/写作参考库_v1.16.md）：

    index.json          篇目索引（id/title/source/work/han_chars）
    templates.json      模板（role/work/pattern/count/examples）
    corpus/…            正文 markdown
    annotations/<id>.json  逐句角色标注

本模块只做**只读加载**：不联网、不写入资料库，缺包/坏包给出中文错误。
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

ROLES = ("论点", "论据", "过渡", "结尾", "背景", "其他")

# 角色优先级：检索结果里论据/过渡这类"可仿写"的句子排前面
_ROLE_RANK = {"论点": 0, "论据": 1, "过渡": 2, "结尾": 3, "背景": 4, "其他": 9}


class MaterialPackError(RuntimeError):
    """语料包不可用（路径错、缺 index.json、不是 zip 等）。"""


@dataclass
class Hit:
    """一条检索结果。"""

    doc_id: str
    title: str
    source: str
    work: str
    role: str
    sentence: str
    rule: str = ""
    conf: float = 0.0
    doc_han: int = 0
    tags: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        role = f"【{self.role}】" if self.role else ""
        return f"{role}{self.sentence}"


@dataclass
class Template:
    role: str
    work: str
    pattern: str
    count: int
    examples: list[str] = field(default_factory=list)


class MaterialPack:
    """一个只读语料包（目录或 zip）。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            raise MaterialPackError(f"语料包不存在：{self.path}")
        self._zip: zipfile.ZipFile | None = None
        if self.path.is_file():
            if not zipfile.is_zipfile(self.path):
                raise MaterialPackError(f"不是有效的 zip 语料包：{self.path}")
            self._zip = zipfile.ZipFile(self.path)
        self._index: list[dict] = self._load_json("index.json", [])
        if not isinstance(self._index, list) or not self._index:
            raise MaterialPackError("语料包缺少 index.json 或篇目为空")
        self._templates: list[dict] = self._load_json("templates.json", [])
        self._ann_cache: dict[str, list[dict]] = {}
        self._doc_by_id = {str(d.get("id")): d for d in self._index}

    # ---------------------------------------------------------- 读取
    def _read(self, name: str) -> bytes | None:
        try:
            if self._zip is not None:
                return self._zip.read(name)
            p = self.path / name
            return p.read_bytes() if p.is_file() else None
        except KeyError:
            return None

    def _load_json(self, name: str, default):
        raw = self._read(name)
        if not raw:
            return default
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MaterialPackError(f"{name} 解析失败：{exc}") from exc

    def _annotations(self, doc_id: str) -> list[dict]:
        if doc_id in self._ann_cache:
            return self._ann_cache[doc_id]
        raw = self._read(f"annotations/{doc_id}.json")
        if not raw:
            self._ann_cache[doc_id] = []
            return []
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            data = {}
        pars = data.get("annotated_paragraphs", []) if isinstance(data, dict) else []
        self._ann_cache[doc_id] = pars
        return pars

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
            self._zip = None

    def __enter__(self) -> "MaterialPack":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------- 统计
    def stats(self) -> dict:
        docs = self._index
        return {
            "docs": len(docs),
            "han_chars": sum(int(d.get("han_chars") or 0) for d in docs),
            "sources": sorted({str(d.get("source") or "") for d in docs}),
            "works": sorted({str(d.get("work") or "") for d in docs}),
            "templates": len(self._templates),
        }

    # ---------------------------------------------------------- 检索
    def search(self, query: str, role: str | None = None,
               limit: int = 30) -> list[Hit]:
        """全文检索：先匹配句子（可按角色过滤），再补标题命中。"""
        q = (query or "").strip().casefold()
        if not q:
            return []
        hits: list[Hit] = []
        seen: set[tuple[str, str]] = set()
        for doc in self._index:
            did = str(doc.get("id"))
            title = str(doc.get("title") or "")
            work = str(doc.get("work") or "")
            src = str(doc.get("source") or "")
            han = int(doc.get("han_chars") or 0)
            if q in title.casefold() or q in work.casefold():
                hits.append(Hit(did, title, src, work, "", f"（篇目命中）{title}",
                                doc_han=han))
            for par in self._annotations(did):
                for sent in par.get("sentences", []):
                    text = str(sent.get("text") or "")
                    if not text or q not in text.casefold():
                        continue
                    r = str(sent.get("role") or "")
                    if role and r != role:
                        continue
                    key = (did, text)
                    if key in seen:
                        continue
                    seen.add(key)
                    hits.append(Hit(
                        did, title, src, work, r, text.strip(),
                        rule=str(sent.get("rule") or ""),
                        conf=float(sent.get("conf") or 0.0), doc_han=han))
        hits.sort(key=lambda h: (_ROLE_RANK.get(h.role, 8), -h.doc_han, len(h.sentence)))
        return hits[:max(1, limit)]

    # ---------------------------------------------------------- 模板
    def templates(self, role: str | None = None, work: str | None = None,
                  limit: int = 100) -> list[Template]:
        out = []
        for t in self._templates:
            if role and str(t.get("role")) != role:
                continue
            if work and str(t.get("work")) != work:
                continue
            out.append(Template(
                role=str(t.get("role") or ""), work=str(t.get("work") or ""),
                pattern=str(t.get("pattern") or ""),
                count=int(t.get("count") or 0),
                examples=[str(x) for x in (t.get("examples") or [])[:2]]))
        out.sort(key=lambda x: (-x.count, x.role, x.work))
        return out[:max(1, limit)]


def find_default_packs(root: str | Path | None = None) -> list[Path]:
    """在 Downloads 里找默认语料包（目录或 zip），供界面直接下拉选择。"""
    base = Path(root) if root else Path.home() / "Downloads"
    if not base.is_dir():
        return []
    packs = []
    for p in sorted(base.glob("写作语料库_公版_*")):
        if p.suffix == ".zip" or (p.is_dir() and (p / "index.json").is_file()):
            packs.append(p)
    return packs
