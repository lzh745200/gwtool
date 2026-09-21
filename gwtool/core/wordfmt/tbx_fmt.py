# -*- coding: utf-8 -*-
"""TBX / XLIFF（XML 术语交换格式）解析，纯标准库。

**支持的形态**

TBX（术语库交换标准，ISO 30042）::

    <termEntry>
      <langSet xml:lang="zh-CN"><tig><term>碳达峰</term></tig></langSet>
      <langSet xml:lang="zh">   <tig><term>碳排放达峰</term></tig></langSet>
    </termEntry>

XLIFF（本地化交换）::

    <trans-unit id="1"><source>碳排放达峰</source><target>碳达峰</target></trans-unit>

**约定**：把**第一个** `term` / `target` 当规范名，其余当异名，展平成
「异名 → 规范名」。这是词表导入能用的最合理映射（本产品不做多语言检索）。

⚠️ **刻意不做**：TBX 的多种方言（Min/Max/Basic/Default 的 DTD 差异）只做
**结构松匹配** —— 按元素局部名找，不校验 DTD、不绑定命名空间。硬套某一版
DTD 会让其它版本的文件全部报"格式不对"，而它们其实都能读出词来。

安全：用标准库 `xml.etree.ElementTree`，它不解析外部实体、遇未定义实体直接
报错，故无 XXE 与实体展开攻击面；另有文件体积上限。
"""
from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from . import Entry, ParseResult

_MAX_BYTES = 32 * 1024 * 1024


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _lang_of(node) -> str:
    """取 `xml:lang`（属性可能带命名空间）。"""
    for k, v in node.attrib.items():
        if _local(k) == "lang":
            return str(v or "")
    return ""


def _texts(node, wanted) -> list:
    """收集 node 下全部局部名在 wanted 里的元素的文本。"""
    out = []
    for sub in node.iter():
        if _local(sub.tag) in wanted:
            t = "".join(sub.itertext()).strip()
            if t:
                out.append(t)
    return out


def _parse_tbx(root, res: ParseResult) -> None:
    entries = [e for e in root.iter() if _local(e.tag) == "termEntry"]
    if not entries:
        res.add_issue(0, "TBX 里没有 termEntry 元素")
        return
    langs = set()
    for i, te in enumerate(entries, start=1):
        langsets = [ls for ls in te if _local(ls.tag) == "langSet"]
        if not langsets:
            res.add_issue(i, "termEntry 下没有 langSet")
            continue
        groups = []
        for ls in langsets:
            terms = _texts(ls, {"term"})
            if terms:
                groups.append((_lang_of(ls), terms))
                if _lang_of(ls):
                    langs.add(_lang_of(ls))
        if not groups:
            res.add_issue(i, "langSet 下没有 term")
            continue
        standard = groups[0][1][0]
        aliases = [t for _, ts in groups for t in ts if t != standard]
        if not aliases:
            res.add_issue(i, "该术语只有一条写法，没有可报的异名")
            continue
        for a in aliases:
            res.entries.append(Entry(role="terms", wrong=a, correct=standard,
                                     src_line=i))
    if len(langs) > 1:
        res.warn("文件含 %d 种语言标注（%s）；本产品不做多语言检索，"
                 "已统一按「首个写法为规范名、其余为异名」处理"
                 % (len(langs), "、".join(sorted(langs)[:5])))


def _parse_xliff(root, res: ParseResult) -> None:
    units = [u for u in root.iter() if _local(u.tag) == "trans-unit"]
    if not units:
        res.add_issue(0, "XLIFF 里没有 trans-unit 元素")
        return
    for i, u in enumerate(units, start=1):
        src = _texts(u, {"source"})
        tgt = _texts(u, {"target"})
        if not src:
            res.add_issue(i, "trans-unit 缺少 source")
            continue
        if not tgt:
            res.add_issue(i, "trans-unit 缺少 target（无法确定规范名）")
            continue
        standard = tgt[0]
        for s in src:
            if s == standard:
                continue
            res.entries.append(Entry(role="terms", wrong=s, correct=standard,
                                     src_line=i))


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text or "")


def _drop_non_cjk(res: ParseResult) -> None:
    """丢掉不含汉字的条目，并告知数量。

    为什么必须过滤：TBX 的 `langSet` 是**按语言**组织的，把英文/日文词条当成
    中文规范名的"异名"会产出「carbon peak → 碳达峰」这类替换建议 —— 中文公文里
    外文词本身合法，报它是纯噪声。本产品**不做多语言**（需求已明确排除），
    所以只保留含汉字的条目。
    """
    kept, dropped = [], 0
    for e in res.entries:
        if _has_cjk(e.wrong) and _has_cjk(e.correct):
            kept.append(e)
        else:
            dropped += 1
    if dropped:
        res.entries[:] = kept
        res.warn("已跳过 %d 条不含汉字的词条（本产品不做多语言检索；"
                 "如需外文↔中文对照，请另行提出）" % dropped)


def parse(path, role: str = "pairs", options=None) -> ParseResult:
    res = ParseResult(fmt=Path(path).suffix.lower().lstrip("."))
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as exc:
        raise ValueError("词表文件读不出来：%s" % exc) from exc
    if size > _MAX_BYTES:
        raise ValueError("文件过大（%.1f MB），疑似异常输入" % (size / 1048576))
    try:
        root = ET.fromstring(p.read_bytes())
    except ET.ParseError as exc:
        raise ValueError("XML 语法有误：%s" % exc) from exc

    names = {_local(e.tag) for e in root.iter()}
    if "termEntry" in names:
        _parse_tbx(root, res)
    elif "trans-unit" in names:
        _parse_xliff(root, res)
    else:
        raise ValueError(
            "认不出这个 XML 词表：既没有 TBX 的 termEntry，"
            "也没有 XLIFF 的 trans-unit。若是自建结构，请改用 CSV/JSON 导入。")
    # 术语表固定产出 terms 角色（规范名↔异名），与用户选的 role 无关
    for e in res.entries:
        e.role = "terms"
    _drop_non_cjk(res)
    return res
