# -*- coding: utf-8 -*-
"""E3 误报归因（triage）：把"真实语料上到底报了什么"变成**可逐条复核**的结构化报告。

为什么需要它
------------
`eval_corrector.py` 只给一个总数（1 处 / 0.11 处每万字）。这个数字**不能直接用来
改规则**：必须知道每一条是"源文本不规范"（真命中）、"规则误报"（要修），还是
"语料采集/分段缺陷"（不该算引擎的账）。本脚本把这三种判定做成**结构化、可回写**
的流程：

    python scripts/e3_triage.py --corpus doc/corpus/real --out artifacts/e3_real
    # 人工复核后，把判定写进 verdicts.json，再合并出结论：
    python scripts/e3_triage.py --corpus doc/corpus/real --out artifacts/e3_real \
        --verdicts artifacts/e3_real.verdicts.json

口径（与 `eval_corrector.py` 严格一致）
--------------------------------------
* 语料：`--corpus` 指向目录时**只扫第一层** `.txt/.md/.csv`（与评测同口径；
  加 `--recursive` 才递归），`SKIP_CORPUS_NAMES` 里的说明/合规文件跳过；
* 分段：按行切，汉字数 ≥ `--min-cjk`（默认 8）才纳入，与评测同口径；
* 分层：**E3a** = `category ∉ 提示类`（提示类 = `corrector.ADVISORY_CATEGORIES`
  ＋ 机构沿革，与评测的 `E3_ADVISORY_EXTRA` 同口径）；**E3b** = 提示类；
* 数据目录重定向到临时目录并从 `seed.db` 播种 —— 与评测同构，**绝不碰真实用户库**。

产出
----
* `--out` 前缀同时写 `.json`（全量、机器可读）与 `.md`（人读，默认明细上限 500 条）；
* 每条命中带稳定 `id`（文件+段+偏移+错形+建议的短哈希），供人工判定回写；
* `verdict` / `verdict_note` 默认空，由人工填写；`auto_hint` / `auto_reason`
  是脚本的**预判**（不是结论），复核时对着它改即可。

离线、无新依赖、不改 UI。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CJK = re.compile(r"[\u4e00-\u9fff]")

# 与 eval_corrector.py **同内容**的跳过清单（不从评测脚本 import：本脚本要能
# 单独拷到别的机器上用；`tests/test_real_corpus_fixes_v113.py` 断言两边相等，
# 防止两份清单悄悄漂移）。
SKIP_CORPUS_NAMES: frozenset[str] = frozenset({
    "README.md",
    "采集合规检查表.md",
    "AUTHORIZATION.example.json",
    "AUTHORIZATION.json",
})
_SKIP_CORPUS_LOWER = frozenset(n.lower() for n in SKIP_CORPUS_NAMES)

# 与 eval_corrector.E3_ADVISORY_EXTRA 同口径：机构沿革是提示级（不自动替换）
ADVISORY_EXTRA: tuple[str, ...] = ("机构沿革",)

CORPUS_GLOBS = ("*.txt", "*.md", "*.csv")

# 语料缺陷的**确定性**信号（只提示，不下结论）：采集/转存常见残留
_DEFECT_MARKERS = ("责任编辑", "扫一扫在手机打开", "首页 >", "首页>", "打印本页",
                   "关闭窗口", "分享到", "【字号", "字体：", "上一篇", "下一篇")
_PAIRS = (("《", "》"), ("“", "”"), ("‘", "’"), ("「", "」"), ("『", "』"))
_LONG_SEGMENT_CJK = 1500


def cjk_len(s: str) -> int:
    return len(CJK.findall(s))


# ------------------------------------------------------------------ 语料读取
def corpus_files(path: str, recursive: bool = False) -> list[Path]:
    """语料文件列表（与评测同口径：目录只扫第一层；`--recursive` 才递归）。"""
    p = Path(path)
    if p.is_file():
        return [] if p.name.lower() in _SKIP_CORPUS_LOWER else [p]
    if not p.is_dir():
        return []
    files: list[Path] = []
    for suf in CORPUS_GLOBS:
        globber = p.rglob if recursive else p.glob
        files.extend(f for f in sorted(globber(suf))
                     if f.is_file() and f.name.lower() not in _SKIP_CORPUS_LOWER)
    return files


def read_segments(path: str, recursive: bool = False,
                  min_cjk: int = 8) -> list[dict]:
    """读语料 -> [{file, line, text}]（按行切，汉字数 ≥ min_cjk）。"""
    from gwtool.core.parsers.txt_parser import read_text_smart

    out: list[dict] = []
    for f in corpus_files(path, recursive=recursive):
        try:
            text = read_text_smart(str(f))
        except Exception as exc:            # 单文件失败不影响整体
            out.append({"file": str(f), "line": 0, "text": "",
                        "error": f"{type(exc).__name__}: {exc}"})
            continue
        for no, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if cjk_len(line) >= min_cjk:
                out.append({"file": str(f), "line": no, "text": line})
    return out


# ------------------------------------------------------------------ 判定辅助
def _balanced_pairs(text: str) -> list[str]:
    """成对符号是否配对（返回不配对的名称列表）。"""
    bad = []
    for left, right in _PAIRS:
        if text.count(left) != text.count(right):
            bad.append(f"{left}{right}")
    return bad


def _segment_defects(text: str) -> list[str]:
    """单个片段的语料缺陷信号（确定性，不做语义判断）。"""
    out = []
    bad = _balanced_pairs(text)
    if bad:
        out.append("成对符号不配对：" + "、".join(bad))
    for marker in _DEFECT_MARKERS:
        if marker in text:
            out.append(f"页面元素残留：{marker}")
            break
    if cjk_len(text) > _LONG_SEGMENT_CJK:
        out.append(f"单段过长（{cjk_len(text)} 汉字 > {_LONG_SEGMENT_CJK}）"
                   "：段落边界可能丢失")
    if text.startswith(("《", "“")) and len(text) <= 40 and not _balanced_pairs(text):
        # 短标题片段：可能是标题被采集拆行（评测按段调用引擎 → 假误报来源）
        out.append("疑似标题片段（短且以成对符号开头）")
    return out


def _hit_id(file: str, line: int, start: int, wrong: str, suggestion: str) -> str:
    """稳定 id：文件+行+偏移+错形+建议的短 sha1（人工判定可跨次回写）。"""
    raw = f"{file}|{line}|{start}|{wrong}|{suggestion}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


def _context(text: str, start: int, end: int, width: int = 25) -> dict:
    lo, hi = max(0, start - width), min(len(text), end + width)
    return {"left": text[lo:start], "hit": text[start:end],
            "right": text[end:hi], "context": text[lo:hi]}


def _auto_hint(hit: dict, tier: str) -> tuple[str, str]:
    """脚本的**预判**（供人工复核时对照，不是结论）。

    返回 (hint, reason)，hint ∈ {"疑似真命中","疑似误报","疑似语料缺陷"}。
    """
    defects = hit.get("segment_defects") or []
    if defects:
        return "疑似语料缺陷", "；".join(defects)
    cat, conf = hit["category"], hit["confidence"]
    if tier == "E3b":
        return "疑似误报", "提示类（不自动替换）"
    if conf >= 0.85 and cat in ("错别字", "规范用字", "禁用词", "易混词", "用户词库"):
        return "疑似真命中", f"{cat} 且置信度 {conf:.2f} ≥0.85（精标/规则级）"
    if conf < 0.7:
        return "疑似误报", f"低置信生成对（{conf:.2f}）"
    return "疑似真命中", f"{cat}，置信度 {conf:.2f}"


# ------------------------------------------------------------------ 主流程
def triage(corpus: str, recursive: bool = False, min_cjk: int = 8) -> dict:
    """跑一遍语料，返回结构化结果（不做任何写盘）。"""
    from gwtool.core import corrector

    files = corpus_files(corpus, recursive=recursive)
    segments = read_segments(corpus, recursive=recursive, min_cjk=min_cjk)
    advisory = set(corrector.ADVISORY_CATEGORIES) | set(ADVISORY_EXTRA)
    # 被跳过的说明/合规文件（透明化：让人知道"为什么这些文件没进报告"）
    skipped: list[str] = []
    p = Path(corpus)
    if p.is_dir():
        globber = p.rglob if recursive else p.glob
        for suf in CORPUS_GLOBS:
            skipped.extend(str(f) for f in sorted(globber(suf))
                           if f.is_file() and f.name.lower() in _SKIP_CORPUS_LOWER)
    skipped = sorted(set(skipped))

    items: list[dict] = []
    seg_errors = [s for s in segments if s.get("error")]
    seg_rows = [s for s in segments if not s.get("error")]
    defects: list[dict] = []
    per_file = defaultdict(lambda: {"segments": 0, "cjk": 0, "hits": 0})

    t0 = time.perf_counter()
    for seg in seg_rows:
        text = seg["text"]
        fkey = seg["file"]
        per_file[fkey]["segments"] += 1
        per_file[fkey]["cjk"] += cjk_len(text)
        seg_defects = _segment_defects(text)
        if seg_defects:
            defects.append({"file": fkey, "line": seg["line"],
                            "signals": seg_defects, "text": text[:120]})
        for h in corrector.check_text(text):
            ctx = _context(text, h.start, h.end)
            tier = "E3a" if h.category not in advisory else "E3b"
            item = {
                "id": _hit_id(fkey, seg["line"], h.start, h.wrong, h.suggestion),
                "file": fkey,
                "line": seg["line"],
                "segment_start": h.start,
                "segment_end": h.end,
                "tier": tier,
                "category": h.category,
                "confidence": round(float(h.confidence), 3),
                "wrong": h.wrong,
                "suggestion": h.suggestion,
                "reason": h.reason,
                "kind": getattr(h, "kind", "replace"),
                **ctx,
                "segment_defects": seg_defects,
                # 人工判定栏（默认空）：verdict ∈ {真命中, 误报, 语料缺陷} + 备注
                "verdict": "",
                "verdict_note": "",
            }
            item["auto_hint"], item["auto_reason"] = _auto_hint(item, tier)
            items.append(item)
            per_file[fkey]["hits"] += 1
    seconds = time.perf_counter() - t0

    n_chars = sum(cjk_len(s["text"]) for s in seg_rows)
    hits_a = [it for it in items if it["tier"] == "E3a"]
    hits_b = [it for it in items if it["tier"] == "E3b"]
    per_wan = (lambda n: round(n / n_chars * 10000, 3) if n_chars else 0.0)
    return {
        "meta": {
            "tool": "scripts/e3_triage.py",
            "corpus": str(corpus),
            "recursive": recursive,
            "min_cjk": min_cjk,
            "files": len(files),
            "segments": len(seg_rows),
            "chars": n_chars,
            "seconds": round(seconds, 3),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "advisory_categories": sorted(advisory),
            "skipped_files": skipped,
        },
        "summary": {
            "hits": len(items),
            "e3a_hits": len(hits_a),
            "e3a_per_wan": per_wan(len(hits_a)),
            "e3b_hits": len(hits_b),
            "e3b_per_wan": per_wan(len(hits_b)),
            "by_category": dict(Counter(it["category"] for it in items)
                                .most_common()),
            "by_tier": dict(Counter(it["tier"] for it in items).most_common()),
            "by_confidence": {
                ">=0.85": sum(1 for it in items if it["confidence"] >= 0.85),
                "0.70-0.85": sum(1 for it in items if 0.70 <= it["confidence"] < 0.85),
                "<0.70": sum(1 for it in items if it["confidence"] < 0.70),
            },
            "auto_hint": dict(Counter(it["auto_hint"] for it in items).most_common()),
            "verdict": dict(Counter(it["verdict"] for it in items
                                    if it["verdict"]).most_common()),
        },
        "segments": [{"file": s["file"], "line": s["line"]} for s in seg_rows],
        "files": [{"file": f, **v} for f, v in sorted(per_file.items())],
        "errors": seg_errors,
        "defects": defects,
        "items": items,
    }


# ------------------------------------------------------------------ 人工判定回写
def apply_verdicts(data: dict, verdicts_path: str) -> dict:
    """把人工判定合并进结果：{id: {"verdict": "...", "note": "..."}}。"""
    raw = json.loads(Path(verdicts_path).read_text(encoding="utf-8"))
    table = raw.get("verdicts") if isinstance(raw, dict) and "verdicts" in raw else raw
    if not isinstance(table, dict):
        raise ValueError("verdicts 文件格式应为 {id: {verdict, note}} 或 "
                         "{\"verdicts\": {...}}")
    applied = 0
    for item in data["items"]:
        v = table.get(item["id"])
        if not v:
            continue
        if isinstance(v, str):
            item["verdict"], item["verdict_note"] = v, ""
        else:
            item["verdict"] = str(v.get("verdict") or "")
            item["verdict_note"] = str(v.get("note") or "")
        applied += 1
    data["summary"]["verdict"] = dict(Counter(
        it["verdict"] for it in data["items"] if it["verdict"]).most_common())
    data["meta"]["verdicts_file"] = str(verdicts_path)
    data["meta"]["verdicts_applied"] = applied
    return data


# ------------------------------------------------------------------ 报告输出
def _load_whitelist(path: str) -> list[dict]:
    """源文本偏差白名单（与 eval_corrector._load_corpus_whitelist 同格式）。

    命中白名单的条目在报告里直接标成「语料缺陷」并计入 source_deviation，
    不再算进 E3a —— 与评测门禁的口径保持一致（同一份清单两边都能用）。
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items = raw.get("source_deviations") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("白名单格式应为 {\"source_deviations\": [...]} 或数组")
    out = []
    for it in items:
        if isinstance(it, dict) and it.get("wrong"):
            out.append({"wrong": str(it["wrong"]),
                        "suggestion": str(it.get("suggestion") or ""),
                        "contains": str(it.get("contains") or ""),
                        "note": str(it.get("note") or "")})
    return out


def _match_whitelist(whitelist: list[dict], segment: str, item: dict) -> str | None:
    for it in whitelist:
        if it["wrong"] != item["wrong"]:
            continue
        if it["suggestion"] and it["suggestion"] != item["suggestion"]:
            continue
        if it["contains"] and it["contains"] not in segment:
            continue
        return it["note"] or "人工复核：源文本偏差"
    return None


def apply_whitelist(data: dict, whitelist: list[dict]) -> dict:
    """把源文本偏差标记为「语料缺陷」并单列计数（E3a 门禁口径扣除）。"""
    if not whitelist:
        return data
    n = 0
    for item in data["items"]:
        note = _match_whitelist(whitelist, item.get("context") or "", item)
        if note is None:
            continue
        item["verdict"] = "语料缺陷"
        item["verdict_note"] = note
        n += 1
    s = data["summary"]
    s["source_deviation"] = n
    s["e3a_gated_hits"] = s["e3a_hits"] - n
    chars = max(1, data["meta"]["chars"])
    s["e3a_gated_per_wan"] = round(s["e3a_gated_hits"] / chars * 10000, 3)
    data["meta"]["whitelist_entries"] = len(whitelist)
    return data


def _md(data: dict, max_items: int = 500) -> str:
    m, s = data["meta"], data["summary"]
    lines: list[str] = []
    add = lines.append
    add("# E3 归因报告（e3_triage）")
    add("")
    add(f"- 语料：`{m['corpus']}`（{m['files']} 个文件 / {m['segments']} 段 / "
        f"{m['chars']} 汉字，递归={m['recursive']}，min_cjk={m['min_cjk']}）")
    add(f"- 生成时间：{m['generated_at']}　耗时 {m['seconds']} s")
    add(f"- 提示类口径：`{', '.join(m['advisory_categories'])}`")
    if m.get("verdicts_file"):
        add(f"- 人工判定已合并：`{m['verdicts_file']}`（命中 {m['verdicts_applied']} 条）")
    add("")
    add("## 1. 结论摘要")
    add("")
    add("| 口径 | 命中 | 处/万字 | 说明 |")
    add("|---|---:|---:|---|")
    add(f"| **E3a 可自动替换类（门禁）** | {s['e3a_hits']} | **{s['e3a_per_wan']}** | "
        "硬门禁 ≤0.5 |")
    add(f"| E3b 提示类（只报告） | {s['e3b_hits']} | {s['e3b_per_wan']} | 不参与门禁 |")
    add(f"| 合计 | {s['hits']} | — | — |")
    add("")
    add(f"- 自动预判分布：{s['auto_hint'] or '无'}")
    if s.get("source_deviation"):
        add(f"- **源文本偏差（白名单）**：{s['source_deviation']} 处 → "
            f"E3a 门禁口径 **{s['e3a_gated_hits']} 处 = "
            f"{s['e3a_gated_per_wan']} 处/万字**")
    if s.get("verdict"):
        add(f"- **人工判定分布：{s['verdict']}**")
    else:
        add("- 人工判定：**尚未回写**（见 §5 回写方法）")
    add("")
    add("## 2. 按类别聚合")
    add("")
    add("| 类别 | 条数 |")
    add("|---|---:|")
    for cat, n in s["by_category"].items():
        add(f"| {cat} | {n} |")
    add("")
    add("## 3. 置信度分布")
    add("")
    add("| 区间 | 条数 |")
    add("|---|---:|")
    for k, v in s["by_confidence"].items():
        add(f"| {k} | {v} |")
    add("")
    add("## 4. 语料质量提示（采集/分段缺陷，不算引擎误报）")
    add("")
    if data["defects"]:
        add("| 文件:行 | 信号 | 片段 |")
        add("|---|---|---|")
        for d in data["defects"][:200]:
            frag = (d["text"] or "").replace("|", "\\|")[:60]
            add(f"| `{Path(d['file']).name}:{d['line']}` | "
                f"{'；'.join(d['signals'])} | {frag} |")
        if len(data["defects"]) > 200:
            add(f"| … | 另有 {len(data['defects']) - 200} 条 | |")
    else:
        add("（无）")
    add("")
    if data["errors"]:
        add("**读取失败的文件**：")
        for e in data["errors"]:
            add(f"- `{e['file']}`：{e['error']}")
        add("")
    add("## 5. 逐条明细（人工判定栏请填「真命中 / 误报 / 语料缺陷」）")
    add("")
    add(f"明细上限 {max_items} 条（完整清单见 JSON）；"
        f"共 {len(data['items'])} 条。")
    add("")
    add("| id | 文件:行 | 类别 | 置信度 | 建议 | 上下文（前后 25 字） | 自动预判 / 依据 "
        "| 人工判定 | 备注 |")
    add("|---|---|---|---:|---|---|---|---|---|")
    for it in data["items"][:max_items]:
        ctx = it["context"].replace("|", "\\|").replace("\n", " ")
        add(f"| `{it['id']}` | `{Path(it['file']).name}:{it['line']}` | "
            f"{it['category']} | {it['confidence']:.2f} | "
            f"{it['wrong']} → {it['suggestion']} | …{ctx}… | "
            f"{it['auto_hint']}（{it['auto_reason']}） | "
            f"{it['verdict'] or '☐'} | {it['verdict_note'] or ''} |")
    if len(data["items"]) > max_items:
        add(f"| … | | | | | | | | 另有 {len(data['items']) - max_items} 条见 JSON |")
    add("")
    add("## 6. 复跑与人工回写")
    add("")
    add("```bash")
    add(f".venv/Scripts/python.exe scripts/e3_triage.py --corpus {m['corpus']} "
        f"--out artifacts/e3_triage")
    add("# 人工复核：把 id -> {verdict, note} 写进 artifacts/e3_triage.verdicts.json")
    add(f".venv/Scripts/python.exe scripts/e3_triage.py --corpus {m['corpus']} "
        f"--out artifacts/e3_triage --verdicts artifacts/e3_triage.verdicts.json")
    add("```")
    add("")
    add("判定回写文件格式：")
    add("")
    add("```json")
    add('{"verdicts": {"<id>": {"verdict": "误报", "note": "低置信生成对跨词"}}}')
    add("```")
    add("")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="E3 误报归因（逐条结构化 + 人工判定回写）")
    ap.add_argument("--corpus", required=True, help="语料目录/文件（.txt/.md/.csv）")
    ap.add_argument("--out", default="", help="输出前缀（同时写 .json/.md）；缺省不写盘")
    ap.add_argument("--recursive", action="store_true",
                    help="递归子目录（评测口径是只扫第一层，默认关闭保持一致）")
    ap.add_argument("--min-cjk", type=int, default=8, help="片段最少汉字数（默认 8，同评测）")
    ap.add_argument("--max-md-items", type=int, default=500,
                    help="Markdown 明细上限（JSON 始终全量）")
    ap.add_argument("--verdicts", default="", help="人工判定 JSON（{id: {verdict, note}}）")
    ap.add_argument("--whitelist", default="",
                    help="源文本偏差白名单 JSON（与评测 --corpus-whitelist 同格式）："
                         "命中项标为「语料缺陷」，从 E3a 门禁口径扣除")
    args = ap.parse_args(argv)

    if not Path(args.corpus).exists():
        print(f"语料不存在：{args.corpus}", file=sys.stderr)
        return 2

    # ---- 隔离数据目录并播种（与 eval_corrector 同构，绝不碰真实用户数据）
    from gwtool import paths

    tmp = Path(tempfile.mkdtemp(prefix="gwtool_triage_")) / "data"
    tmp.mkdir(parents=True, exist_ok=True)
    paths._override = tmp
    from gwtool.db import connection as dbconn

    dbconn.configure(tmp / "triage.db")
    from gwtool.app import ensure_database_seeded

    ensure_database_seeded()
    from gwtool.core import corrector

    corrector.invalidate_cache()

    data = triage(args.corpus, recursive=args.recursive, min_cjk=args.min_cjk)
    if args.whitelist:
        data = apply_whitelist(data, _load_whitelist(args.whitelist))
    if args.verdicts:
        data = apply_verdicts(data, args.verdicts)

    s = data["summary"]
    print(f"语料：{data['meta']['corpus']}　{data['meta']['files']} 文件 / "
          f"{data['meta']['segments']} 段 / {data['meta']['chars']} 汉字")
    print(f"E3a 可自动替换类：{s['e3a_hits']} 处 = {s['e3a_per_wan']} 处/万字")
    print(f"E3b 提示类：{s['e3b_hits']} 处 = {s['e3b_per_wan']} 处/万字")
    print(f"自动预判：{s['auto_hint']}")
    if s.get("verdict"):
        print(f"人工判定：{s['verdict']}")
    if data["defects"]:
        print(f"语料质量提示：{len(data['defects'])} 条（详见报告 §4）")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out_json = out.with_suffix(".json")
        out_md = out.with_suffix(".md")
        out_json.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        out_md.write_text(_md(data, max_items=args.max_md_items), encoding="utf-8")
        print(f"已写出：{out_json}")
        print(f"已写出：{out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
