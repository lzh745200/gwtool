# -*- coding: utf-8 -*-
"""写作能力量化校准脚本（**开发期离线工具**，不参与产品运行、不改产品行为）。

做什么
------
在一批真实公文上逐篇跑 ``writing_hints.hints(text, kind=detect_kind 首选)``，
把"提示能力"量成可比较的数字，并把**每条提示的触发片段**（前后各 20 字）落盘，
供人工复核（真该提示 / 误报 / 表述可改进）与阈值校准。

输出（默认写到 ``doc/corpus/writing_calib/``，该目录在 .gitignore 内）：
  * ``writing_calib_v1.14.json`` —— 全量结构化结果（含语料指纹与逐条 trigger）
  * ``writing_calib_v1.14.md``   —— 人读报表（warn 误报名单、top 提示、密度分布）

用法
----
  .\\.venv\\Scripts\\python.exe scripts\\writing_calibrate.py
  .\\.venv\\Scripts\\python.exe scripts\\writing_calibrate.py --sample 10
  .\\.venv\\Scripts\\python.exe scripts\\writing_calibrate.py --kind-mode empty
  .\\.venv\\Scripts\\python.exe scripts\\writing_calibrate.py --corpus <目录> --out <目录>

退出码：0 = 正常产出；2 = 用法/语料问题（目录不存在、语料为空）。

为什么要"触发片段"而不是只给条数
--------------------------------
提示条数只能看出"吵不吵"，看不出"对不对"。校准必须回到原文：一句真实句子
才能判断「用语偏空泛：有关」是真该提示（全篇几十处"有关"）还是误报（只出现
一次的正常公文用语）。故每条提示都带 needle 与 ±20 字上下文。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gwtool.core import writing_hints as WH

CORPUS_SUFFIXES = (".txt", ".md")
DEFAULT_CORPUS = ROOT / "doc" / "corpus" / "all_texts"
DEFAULT_OUT = ROOT / "doc" / "corpus" / "writing_calib"
REPORT_STEM = "writing_calib_v1.14"
CONTEXT = 20          # 触发片段前后各 20 字

#: 从提示标题里直接取 needle 的模板（顺序即优先级）。
_NEEDLE_TEMPLATES = (
    re.compile(r"用语偏空泛：(.+)$"),
    re.compile(r"口语化表达：(.+)$"),
    re.compile(r"重复用语：(.+)$"),
    re.compile(r"句以「(.+?)」开头"),
    re.compile(r"自称不统一：(.+)$"),
    re.compile(r"同一数量写作不一致：(.+?)\s*/\s*.+$"),
    re.compile(r"同一日期写法不一致：(.+?)\s*/\s*.+$"),
    re.compile(r"计量单位混用：(.+?)\s*/\s*.+$"),
    re.compile(r"机构新旧名称并存：(.+?)\s*/\s*.+$"),
    re.compile(r"标点全角/半角混用：(.+?)\s*/\s*.+$"),
)
#: 标题形如「层次序号不规范：跳号」，真正的标记在 detail 的「」里。
_RE_QUOTED = re.compile(r"「([^」]+)」")
#: 指代"缺了什么"的提示：原文里没有可摘的触发片段。
_ABSENCE_MARKERS = ("未识别到", "缺少", "未识别", "本文更像", "平均句长偏长",
                    "正文分层不明显", "标题未包含文种", "未检出")


# ---------------------------------------------------------------- 语料读写
def iter_corpus(target: Path) -> list[Path]:
    """目录第一层收集语料文件（与 eval_corrector --corpus 口径一致）。"""
    if target.is_file():
        return [target] if target.suffix.lower() in CORPUS_SUFFIXES else []
    if not target.is_dir():
        return []
    return [p for p in sorted(target.iterdir())
            if p.is_file() and p.suffix.lower() in CORPUS_SUFFIXES]


def read_text(path: Path) -> tuple[str, str]:
    """读语料，返回 (正文, 实际编码)。中文语料常见 UTF-8-BOM / GBK 两种。"""
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return data.decode(enc).replace("\r\n", "\n").replace("\r", "\n"), enc
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8(replace)"


def fingerprint(entries: list[dict]) -> dict:
    """语料指纹：篇数/字符数/汉字数 + 逐篇 sha256 的聚合 sha256。

    聚合哈希保证"换了任意一篇"指纹就变——复跑对比必须能判断语料是否同一批。
    """
    agg = hashlib.sha256()
    for e in sorted(entries, key=lambda x: x["path"]):
        agg.update(f"{e['path']}:{e['sha256']}\n".encode("utf-8"))
    return {
        "files": len(entries),
        "chars": sum(e["chars"] for e in entries),
        "cjk": sum(e["cjk"] for e in entries),
        "sha256": agg.hexdigest(),
    }


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")


# ---------------------------------------------------------------- 触发片段
def _needle_for(text: str, hint) -> str:
    """从提示本身反推"是哪段文字触发的"。取不到就返回空串（结构类缺失提示）。"""
    title, detail = hint.title, hint.detail or ""
    if any(mark in title for mark in _ABSENCE_MARKERS):
        return ""
    if title.startswith(("感叹号使用偏多", "问号使用偏多", "省略号使用偏多")):
        ch = {"感": "！", "问": "？", "省": "…"}[title[0]]
        return ch if ch in text else ""
    if "标题偏长" in title:
        for line in text.split("\n"):
            if line.strip():
                return line.strip()[:40]
        return ""
    if "单句偏长" in title:
        # 「第 N 句偏长（L 字）」：按模块同一口径取第 N 个长句
        m = re.search(r"第\s*(\d+)\s*句偏长", title)
        if m:
            return _nth_long_sentence(text, int(m.group(1)))
        return _longest_sentence(text)
    if "段过长" in title:
        m = re.search(r"第\s*(\d+)\s*段", title)
        if m:
            return _nth_long_paragraph(text, int(m.group(1)))
        return _longest_paragraph(text)
    for rx in _NEEDLE_TEMPLATES:
        m = rx.search(title)
        if m:
            return m.group(1).strip().rstrip("…")[:24]
    m = _RE_QUOTED.search(detail)
    if m:
        return m.group(1).strip()[:24]
    return ""


def _body_paras(text: str) -> list[str]:
    try:
        return WH._body_lines(text)
    except Exception:
        return [ln.strip() for ln in text.split("\n") if ln.strip()]


def _split_sents(text: str) -> list[str]:
    try:
        return WH._split_sentences(text)
    except Exception:
        return re.split(r"[。！？；]+", text)


def _nth_long_sentence(text: str, n: int) -> str:
    idx = 0
    for line in _body_paras(text):
        for sent in _split_sents(line):
            idx += 1
            if idx == n:
                return sent
    return ""


def _longest_sentence(text: str) -> str:
    sents = [s for line in _body_paras(text) for s in _split_sents(line)]
    return max(sents, key=len) if sents else ""


def _nth_long_paragraph(text: str, n: int) -> str:
    paras = _body_paras(text)
    return paras[n - 1] if 0 < n <= len(paras) else ""


def _longest_paragraph(text: str) -> str:
    paras = _body_paras(text)
    return max(paras, key=len) if paras else ""


def excerpt(text: str, needle: str) -> dict:
    """needle 在原文中的位置与前后各 CONTEXT 字。

    找不到就退一步用前 12 字找（提示里的短语可能被截断成"…"）。
    """
    if not needle:
        return {"pos": -1, "hit": "", "before": "", "after": ""}
    pos = text.find(needle)
    if pos < 0 and len(needle) > 12:
        pos = text.find(needle[:12])
    if pos < 0:
        return {"pos": -1, "hit": needle, "before": "", "after": ""}
    end = pos + len(needle)
    return {
        "pos": pos,
        "hit": text[pos:end],
        "before": text[max(0, pos - CONTEXT):pos].replace("\n", " "),
        "after": text[end:end + CONTEXT].replace("\n", " "),
    }


# ---------------------------------------------------------------- 单篇/整批
def analyze_doc(path: Path, corpus_dir: Path, kind_mode: str) -> dict:
    text, encoding = read_text(path)
    try:
        rel = str(path.relative_to(corpus_dir))
    except ValueError:
        rel = path.name
    ranking = WH.detect_kind(text, limit=3) if kind_mode == "auto" else []
    kind = ranking[0][0] if ranking else ""
    hints = WH.hints(text, kind)
    chars = len(text)
    per_k = 1000.0 / chars if chars else 0.0
    items = []
    for h in hints:
        needle = _needle_for(text, h)
        items.append({
            "scope": h.scope,
            "title": h.title,
            "level": h.level,
            "detail": h.detail,
            "trigger": excerpt(text, needle),
        })
    return {
        "path": rel,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "encoding": encoding,
        "chars": chars,
        "cjk": _cjk_count(text),
        "kind": kind,
        "kind_top3": [[g, round(d, 3)] for g, d in ranking],
        "hints": len(hints),
        "warn": sum(1 for h in hints if h.level == "warn"),
        "density": round(len(hints) * per_k, 2),
        "items": items,
    }


def build_report(corpus_dir: Path, kind_mode: str, sample: int) -> dict:
    files = iter_corpus(corpus_dir)
    if sample > 0:
        files = files[:sample]
    docs = [analyze_doc(p, corpus_dir, kind_mode) for p in files]
    title_counter: Counter = Counter()
    title_level: dict[str, str] = {}
    title_scope: dict[str, str] = {}
    title_docs: dict[str, set] = defaultdict(set)
    title_sample: dict[str, dict] = {}
    level_counter: Counter = Counter()
    scope_counter: Counter = Counter()
    for d in docs:
        for it in d["items"]:
            t = it["title"]
            title_counter[t] += 1
            title_level[t] = it["level"]
            title_scope[t] = it["scope"]
            title_docs[t].add(d["path"])
            level_counter[it["level"]] += 1
            scope_counter[it["scope"]] += 1
            if t not in title_sample and it["trigger"]["hit"]:
                title_sample[t] = {"doc": d["path"], **it["trigger"]}
    chars = sum(d["chars"] for d in docs)
    total = sum(d["hints"] for d in docs)
    densities = [d["density"] for d in docs if d["chars"]]
    return {
        "meta": {
            "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "corpus_dir": str(corpus_dir),
            "kind_mode": kind_mode,
            "context_chars": CONTEXT,
        },
        "fingerprint": fingerprint(docs),
        "totals": {
            "docs": len(docs),
            "chars": chars,
            "hints": total,
            "warn": level_counter.get("warn", 0),
            "info": level_counter.get("info", 0),
            "density_per_kchar": round(total * 1000 / chars, 2) if chars else 0.0,
            "density_median": round(statistics.median(densities), 2) if densities else 0.0,
            "density_p90": round(sorted(densities)[int(len(densities) * 0.9)], 2)
                           if densities else 0.0,
        },
        "levels": dict(level_counter),
        "scopes": dict(scope_counter),
        "by_title": [
            {"title": t, "count": c, "level": title_level[t],
             "scope": title_scope[t], "docs": len(title_docs[t]),
             "sample": title_sample.get(t, {})}
            for t, c in title_counter.most_common()
        ],
        "warn_items": [
            {"doc": d["path"], "kind": d["kind"], "title": it["title"],
             "detail": it["detail"], "trigger": it["trigger"]}
            for d in docs for it in d["items"] if it["level"] == "warn"
        ],
        "per_doc": [{k: v for k, v in d.items() if k != "items"} for d in docs],
        "docs": docs,
    }


# ---------------------------------------------------------------- 渲染
def render_md(report: dict, top: int = 30) -> str:
    meta, fp, tot = report["meta"], report["fingerprint"], report["totals"]
    lines: list[str] = []
    lines.append("# 写作提示 · 真实语料量化校准报表")
    lines.append("")
    lines.append("> 由 `scripts/writing_calibrate.py` 自动生成（开发期离线工具，"
                 "不参与产品运行）。**不要手工编辑**，复跑即可刷新。")
    lines.append("")
    lines.append("## 0. 语料指纹与运行参数")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 语料目录 | `{meta['corpus_dir']}` |")
    lines.append(f"| 篇数 | {fp['files']} |")
    lines.append(f"| 字符数 / 汉字数 | {fp['chars']} / {fp['cjk']} |")
    lines.append(f"| 语料指纹（聚合 sha256） | `{fp['sha256'][:32]}…` |")
    lines.append(f"| kind 口径 | `{meta['kind_mode']}`"
                 "（auto = detect_kind 首选文种） |")
    lines.append(f"| 生成时间 | {meta['generated_at']} |")
    lines.append("")
    lines.append("## 1. 总览")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 提示总数 | {tot['hints']} |")
    lines.append(f"| warn / info | **{tot['warn']}** / {tot['info']} |")
    lines.append(f"| 提示密度（条/千字，全量） | **{tot['density_per_kchar']}** |")
    lines.append(f"| 每篇密度中位 / p90 | {tot['density_median']} / {tot['density_p90']} |")
    lines.append("")
    scope_bits = "、".join(f"{k} {v}" for k, v in
                          sorted(report["scopes"].items(), key=lambda x: -x[1]))
    lines.append(f"按层面分布：{scope_bits}")
    lines.append("")
    if report["warn_items"]:
        lines.append("## 2. warn 明细（必须逐条复核：规范公文上应为 0 条误报）")
        lines.append("")
        lines.append("| # | 文种 | 提示 | 篇目 |")
        lines.append("|---|---|---|---|")
        for i, w in enumerate(report["warn_items"], 1):
            lines.append(f"| {i} | {w['kind']} | {w['title']} | `{w['doc']}` |")
        lines.append("")
    else:
        lines.append("## 2. warn 明细")
        lines.append("")
        lines.append("**0 条** —— 规范公文上没有 warn 级误报。")
        lines.append("")
    lines.append(f"## 3. Top {top} 提示类型（按出现次数）")
    lines.append("")
    lines.append("| # | 次数 | 篇数 | 级别 | 层面 | 提示 | 触发片段（±20 字） |")
    lines.append("|---|---|---|---|---|---|---|")
    for i, row in enumerate(report["by_title"][:top], 1):
        s = row.get("sample") or {}
        trig = ""
        if s.get("hit"):
            trig = f"…{s.get('before', '')}**【{s['hit'][:20]}】**{s.get('after', '')}…"
        lines.append(f"| {i} | {row['count']} | {row['docs']} | {row['level']} | "
                     f"{row['scope']} | {row['title']} | {trig} |")
    lines.append("")
    lines.append("## 4. 逐篇密度（降序）")
    lines.append("")
    lines.append("| 篇目 | 文种 | 字数 | 提示 | warn | 密度(条/千字) |")
    lines.append("|---|---|---|---|---|---|")
    for d in sorted(report["per_doc"], key=lambda x: -x["density"]):
        lines.append(f"| `{d['path'][:48]}` | {d['kind']} | {d['chars']} | "
                     f"{d['hints']} | {d['warn']} | {d['density']} |")
    lines.append("")
    lines.append("## 5. 复跑")
    lines.append("")
    lines.append("```powershell")
    lines.append("$env:PYTHONIOENCODING='utf-8'")
    lines.append(r".\.venv\Scripts\python.exe scripts\writing_calibrate.py")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 入口
def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="在真实公文语料上量化写作提示（开发期离线工具）")
    ap.add_argument("--corpus", default=str(DEFAULT_CORPUS),
                    help="语料目录（默认 doc/corpus/all_texts，只扫第一层）")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录")
    ap.add_argument("--sample", type=int, default=0,
                    help="只跑前 N 篇（0=全部；用于快速抽查）")
    ap.add_argument("--kind-mode", choices=("auto", "empty"), default="auto",
                    help="auto=按 detect_kind 首选文种跑；empty=不传文种")
    ap.add_argument("--top", type=int, default=30, help="报表里列多少条高频提示")
    ap.add_argument("--quiet", action="store_true", help="只写文件，不打印摘要")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    corpus = Path(args.corpus)
    files = iter_corpus(corpus)
    if not files:
        print(f"语料目录为空或不存在：{corpus}", file=sys.stderr)
        return 2
    report = build_report(corpus, args.kind_mode, args.sample)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"{REPORT_STEM}.json"
    md_path = out / f"{REPORT_STEM}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    md_path.write_text(render_md(report, args.top), encoding="utf-8")
    if not args.quiet:
        tot, fp = report["totals"], report["fingerprint"]
        print(f"语料 {fp['files']} 篇 / {fp['chars']} 字符 / 汉字 {fp['cjk']}"
              f"（sha256 {fp['sha256'][:12]}…）")
        print(f"提示 {tot['hints']} 条（warn {tot['warn']} / info {tot['info']}）"
              f"，密度 {tot['density_per_kchar']} 条/千字，"
              f"中位 {tot['density_median']}，p90 {tot['density_p90']}")
        print(f"JSON → {json_path}")
        print(f"MD   → {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
