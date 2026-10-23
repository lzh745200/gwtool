# -*- coding: utf-8 -*-
"""真实语料契约检查：编码 / 规模 / 脱敏体检，输出 JSON+Markdown 报告。

为什么需要它
------------
`eval_corrector.py --corpus` 只负责"拿语料跑 E3/E5/E6"，它**不判断语料是否合格**：
把一份技术文档、一份 GBK 老文件、或一份带真实身份证号的公文丢进去，评测都会
照跑不误 —— 前者让 E3 变成"技术文档误报率"（口径全错），后两者则可能把个人
信息带进报告/日志/提交历史。本脚本是 `--corpus` 的**前置闸门**：

  - 编码：必须是 UTF-8（允许 BOM）；UTF-16/GBK 老文件一律 FAIL 并给出行号；
  - 规模：单篇汉字数下限、全语料汉字总量下限（小样本的 E3/E5 没有统计意义）；
  - 脱敏：身份证 / 手机号 / 邮箱 / 银行卡 / 座机号命中即 FAIL，并给出
    ``文件:行``（报告里**只输出掩码值**，避免检查工具自己变成泄露渠道）。

**只做正则检测，不猜人名**：人名与"内部单位名"没有可靠的正则判据，靠启发式
猜会把"规则没命中"误当成"已脱敏"，比不查更危险。所以人名/单位名脱敏写进
`doc/corpus/README.md` 的人工规范，本脚本只对**确定形态**的 PII 报错，另对
"内部资料/机密"等敏感标记给 warning（不影响退出码）。

退出码
------
    0 = 语料合格（可能有 warning，不影响退出码）
    1 = 有问题（编码/规模/脱敏任一 FAIL，或目录里没有可检查的语料文件）
    2 = 用法错误（路径不存在、参数非法 —— argparse 自身也返回 2）

用法
----
    # 体检 doc/corpus（递归 .md/.txt），只打印结论
    python scripts/corpus_check.py doc/corpus

    # 落盘报告：--out 以 .json / .md 结尾只写该格式；
    # 其他写法（如 artifacts/corpus_report）同时写 .json 与 .md；
    # 传已存在的目录则写入该目录下的 corpus_report.json / corpus_report.md
    python scripts/corpus_check.py D:/公文语料 --out artifacts/corpus_report
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

# 语料文件后缀：与 eval_corrector.py 的 `--corpus` 扫描口径保持一致（.csv 除外，
# 表格类材料不适合当"正常公文句子"负样本，故本检查只收 .md/.txt）。
CORPUS_SUFFIXES = (".md", ".txt")
#: 与语料同目录、但**不是语料**的说明/合规文件。
#: `doc/corpus/` 下除真实语料外还会放 README、采集合规检查表、AUTHORIZATION*.json
#: （D 批交付物）；不跳过会让体检端把检查表算成 1 篇语料、让评测端的 E3 把它
#: 当成负样本。**评测端 `eval_corrector.py` 有同名同内容常量，两边必须一致**
#: （有测试断言相等；不新增共享模块是刻意的——两个脚本要能单独拷走运行）。
SKIP_CORPUS_NAMES: frozenset[str] = frozenset({
    "README.md",
    "采集合规检查表.md",
    "AUTHORIZATION.example.json",
    "AUTHORIZATION.json",
})
_SKIP_CORPUS_LOWER = frozenset(name.lower() for name in SKIP_CORPUS_NAMES)

# 规模下限（汉字数）。默认值刻意偏低：一条正式公文一般远超此数，而"整包语料"
# 需要足够字数才有统计意义（E3 是"处/万字"，几百字里 1 处命中就 100+ 处/万字）。
DEFAULT_MIN_DOC_CJK = 100
DEFAULT_MIN_TOTAL_CJK = 1000

# PII 正则（**确定形态**才报）。(名称, 正则)
# 顺序即优先级：18 位数字既像身份证也像银行卡，按身份证报（更强的语义）。
PII_RULES: tuple[tuple[str, re.Pattern], ...] = (
    ("身份证", re.compile(r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])")),
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("邮箱", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("银行卡", re.compile(r"(?<!\d)(?:\d{16}|\d{19})(?!\d)")),
    ("座机号", re.compile(r"(?<!\d)0\d{2,3}[-—－ ]?\d{7,8}(?!\d)")),
)
# 只告警的敏感标记：它们说明材料可能不宜外流，但**不是**确定违规，故不判 FAIL。
WARN_RULES: tuple[tuple[str, re.Pattern], ...] = (
    ("敏感标记", re.compile(r"内部资料|内部文件|仅供内部|机密|绝密|秘密")),
)

# ---------------------------------------------------------------- 语料质量提示
# **一律不判 FAIL、不影响退出码**：它们提示"采集/转存可能把语料弄坏了"，
# 由人工决定是否重新采集。
#
# 为什么必须加：真实语料首轮实测 12 处 E3 假阳性**全部**来自"采集把标题拆成
# 两段"—— 标题在书名号中间被换行，评测按段调用引擎时两段各自"书名号不配对"，
# 于是被报成「标点规范」误报。这类缺陷必须在评测前暴露，否则会被当成引擎误报，
# 进而把规则改坏。
QUALITY_TITLE_SPLIT = "标题跨段"
QUALITY_PAIR_MISMATCH = "成对符号不配对"
QUALITY_LONG_PARAGRAPH = "单段过长"
#: 单段汉字数上限：公文自然段一般 100~400 字；超过 1500 基本是"段落边界丢失"
#: （采集把整篇挤成一段，或换行被吃掉）。
LONG_PARAGRAPH_CJK = 1500
#: 标题收尾的文种/文件类词（判断"两段拼起来才是完整标题"）
TITLE_SUFFIXES: tuple[str, ...] = (
    "通知", "通报", "报告", "请示", "批复", "意见", "决定", "命令", "函",
    "纪要", "公告", "通告", "决议", "公报", "议案", "条例", "规定", "办法",
    "细则", "方案", "规划", "计划", "总结", "要点", "讲话", "说明",
)
#: 成对符号：书名号 / 中文引号（含直角引号）。
QUOTE_PAIRS: tuple[tuple[str, str], ...] = (
    ("《", "》"), ("“", "”"), ("‘", "’"), ("「", "」"), ("『", "』"),
)
#: 标题里不该出现的句末标点（出现即说明这行是正文而不是标题前半）
_SENTENCE_END = "。！？；"


def _mask(value: str) -> str:
    """掩码：只保留首尾各 2 字符。报告里绝不出现完整 PII。"""
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def _line_no_at(data: bytes, pos: int) -> int:
    """解码失败位置 -> 1 起的行号（给"文件:行"用）。"""
    return data[:max(0, pos)].count(b"\n") + 1


def _cjk_count(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


# ---------------------------------------------------------- 语料质量提示实现
def _norm_squeeze(line: str) -> str:
    """去空白（含全角空格）：拼接判定不能被换行处的空格骗过。"""
    return re.sub(r"\s+", "", line)


def _title_like(text: str) -> bool:
    return any(text.endswith(suffix) for suffix in TITLE_SUFFIXES)


def _quality_entry(rel: str, line: int, kind: str, message: str) -> dict:
    # 不回显语料正文：报告会被归档/转发，标题里可能有真实单位名。
    return {"file": rel, "line": line, "kind": kind,
            "message": message, "match": ""}


def _quality_title_split(lines: list[str], rel: str) -> list[dict]:
    """连续两段拼接 == 标题：采集把标题拆成两段的典型形态。

    三种判据从严到宽（命中任一即提示，且每篇只提示一次）：
      ① 字面：前两段拼接后与下一行完全相同（标题被重复贴了一次）；
      ② 书名号/引号跨行闭合（第一段有未闭合的开符号、第二段才补上闭符号）；
      ③ 启发式：前两段都是短标题片段，拼起来才以文种词收尾。
    """
    idx = [i for i, ln in enumerate(lines) if ln.strip()][:4]
    if len(idx) < 2:
        return []
    l1 = lines[idx[0]].strip()
    l2 = lines[idx[1]].strip()
    joined = _norm_squeeze(l1) + _norm_squeeze(l2)
    # ① 字面口径（最可靠）
    if len(idx) >= 3 and joined and _norm_squeeze(lines[idx[2]]) == joined:
        return [_quality_entry(
            rel, idx[1] + 1, QUALITY_TITLE_SPLIT,
            "连续两段拼接后与下一行完全相同，疑似采集把标题拆成两段")]
    # ② 成对符号跨行闭合
    for op, cl in QUOTE_PAIRS:
        if l1.count(op) > l1.count(cl) and l2.count(cl) > l2.count(op):
            return [_quality_entry(
                rel, idx[1] + 1, QUALITY_TITLE_SPLIT,
                f"「{op}」跨行闭合（第一段未闭合、第二段才补上），"
                "疑似采集把标题拆成两段")]
    # ③ 启发式：前两段都是短标题片段，拼起来才是完整标题。
    #    刻意排除"元数据行 + 完整标题"（`来源：中国政府网` / `索引号：...` 这类
    #    行常出现在采集文本顶部，它们与标题拼接也会以文种词收尾，会假报）。
    c1, c2 = _cjk_count(l1), _cjk_count(l2)
    if (2 <= c1 <= 60 and 1 <= c2 <= 40
            and l1[-1:] not in _SENTENCE_END
            and not _title_like(_norm_squeeze(l1))
            and not any(ch in l1 for ch in "：:|｜")
            and not l1.startswith(("#", "【", "来源", "发布", "索引", "时间",
                                   "责任", "编辑", "字号"))
            and l2[-1:] not in _SENTENCE_END
            and not l2.startswith(("关于", "在", "对", "为", "致"))
            and _title_like(joined)
            and (joined.startswith(("关于", "在", "对", "为", "致"))
                 or "的" in joined)):
        return [_quality_entry(
            rel, idx[1] + 1, QUALITY_TITLE_SPLIT,
            "前两行拼起来才是完整标题（第一行不以文种词收尾、第二行补齐），"
            "疑似采集把标题拆成两段")]
    return []


def _quality_pair_mismatch(lines: list[str], rel: str) -> list[dict]:
    """书名号/引号全篇不配对（截断、混排、跨段换行都会造成）。"""
    for op, cl in QUOTE_PAIRS:
        total_open = total_close = 0
        balance = 0
        extra_close_line = 0
        for lineno, line in enumerate(lines, 1):
            opened, closed = line.count(op), line.count(cl)
            total_open += opened
            total_close += closed
            if not extra_close_line and balance + opened - closed < 0:
                extra_close_line = lineno
            balance += opened - closed
        if total_open != total_close or extra_close_line:
            line = extra_close_line or (len(lines) if total_open > total_close else 1)
            return [_quality_entry(
                rel, line, QUALITY_PAIR_MISMATCH,
                f"「{op}/{cl}」全篇不配对（{op}×{total_open}、{cl}×{total_close}），"
                "疑似采集截断或段落边界被拆开")]
    return []


def _quality_long_paragraph(lines: list[str], rel: str) -> list[dict]:
    """单段（此处按行）汉字数超过上限：多半是段落边界丢失。"""
    out: list[dict] = []
    for lineno, line in enumerate(lines, 1):
        chars = _cjk_count(line)
        if chars > LONG_PARAGRAPH_CJK:
            out.append(_quality_entry(
                rel, lineno, QUALITY_LONG_PARAGRAPH,
                f"该行 {chars} 汉字，超过 {LONG_PARAGRAPH_CJK}，"
                "疑似段落边界丢失（采集把多段挤成一段）"))
            if len(out) >= 3:
                break
    return out


def _iter_files(target: Path) -> list[Path]:
    """递归收集语料文件（.md/.txt），跳过隐藏目录与常见缓存目录。"""
    if target.is_file():
        return [target] if target.suffix.lower() in CORPUS_SUFFIXES else []
    files: list[Path] = []
    for p in sorted(target.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in CORPUS_SUFFIXES:
            continue
        if p.name.lower() in _SKIP_CORPUS_LOWER:
            continue
        if any(part.startswith(".") or part in ("__pycache__", "node_modules")
               for part in p.relative_to(target).parts):
            continue
        files.append(p)
    return files


def _check_file(path: Path, target: Path, min_doc_cjk: int) -> dict:
    """单篇检查：编码/非空/规模/PII/敏感标记。返回文件级结果（含错误列表）。"""
    try:
        rel = str(path.relative_to(target))
    except ValueError:                      # target 是单个文件时
        rel = path.name
    data = path.read_bytes()
    entry: dict = {"path": rel, "bytes": len(data), "lines": 0, "cjk": 0,
                   "sha256": hashlib.sha256(data).hexdigest(),
                   "encoding": "utf-8", "ok": True}
    errors: list[dict] = []
    warnings: list[dict] = []
    quality: list[dict] = []
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        entry["encoding"] = "非 utf-8"
        entry["ok"] = False
        errors.append({"file": rel, "line": _line_no_at(data, exc.start),
                       "kind": "编码", "message": "不是 UTF-8 编码（请转存为 UTF-8）",
                       "match": ""})
        return {"entry": entry, "errors": errors, "warnings": warnings,
                "quality": quality}

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    entry["lines"] = len(lines)
    entry["cjk"] = _cjk_count(text)
    if not text.strip():
        entry["ok"] = False
        errors.append({"file": rel, "line": 1, "kind": "空文件",
                       "message": "文件内容为空", "match": ""})
    elif entry["cjk"] < min_doc_cjk:
        entry["ok"] = False
        errors.append({"file": rel, "line": 1, "kind": "单篇过短",
                       "message": f"仅 {entry['cjk']} 个汉字，低于单篇下限 "
                                  f"{min_doc_cjk}（小样本没有统计意义）",
                       "match": ""})

    for lineno, line in enumerate(lines, 1):
        if not line:
            continue
        for kind, rx in PII_RULES:
            for m in rx.finditer(line):
                entry["ok"] = False
                errors.append({"file": rel, "line": lineno, "kind": kind,
                               "message": f"疑似{kind}，必须脱敏后再用作语料",
                               "match": _mask(m.group(0))})
        for kind, rx in WARN_RULES:
            for m in rx.finditer(line):
                warnings.append({"file": rel, "line": lineno, "kind": kind,
                                 "message": f"命中敏感标记「{m.group(0)}」，"
                                            "请确认材料是否可外流",
                                 "match": m.group(0)})

    # 语料质量提示（不判 FAIL）：标题跨段 / 成对符号不配对 / 单段过长。
    # 这些是"采集缺陷"，不是"语料违规"——提前看到可以避免把采集问题当成
    # 引擎误报（真实语料首轮 12 处 E3 假阳性全部由此产生）。
    quality.extend(_quality_title_split(lines, rel))
    quality.extend(_quality_pair_mismatch(lines, rel))
    quality.extend(_quality_long_paragraph(lines, rel))
    return {"entry": entry, "errors": errors, "warnings": warnings,
            "quality": quality}


def scan_corpus(target: str | Path, *, min_doc_cjk: int = DEFAULT_MIN_DOC_CJK,
                min_total_cjk: int = DEFAULT_MIN_TOTAL_CJK) -> dict:
    """扫描语料（目录或单文件），返回可直接落盘的报告字典。"""
    root = Path(target)
    report: dict = {
        "tool": "corpus_check",
        "version": 2,
        "corpus": {"path": str(root), "files": 0, "cjk": 0, "lines": 0,
                   "min_doc_cjk": min_doc_cjk, "min_total_cjk": min_total_cjk},
        "ok": True,
        "exit_code": 0,
        "files": [],
        "errors": [],
        "warnings": [],
        # 语料质量提示（不判 FAIL、不影响退出码）：标题跨段 / 成对符号不配对 /
        # 单段过长。它们提示"采集可能把语料弄坏了"。
        "quality": [],
        "quality_total": 0,
    }
    if not root.exists():
        report["ok"] = False
        report["exit_code"] = 2
        report["errors"].append({"file": str(root), "line": 0, "kind": "路径",
                                 "message": "语料路径不存在", "match": ""})
        return report

    files = _iter_files(root)
    if not files:
        report["ok"] = False
        report["exit_code"] = 1
        report["errors"].append({
            "file": str(root), "line": 0, "kind": "无语料",
            "message": f"目录下没有 {'/'.join(CORPUS_SUFFIXES)} 语料文件", "match": ""})
        return report

    for f in files:
        got = _check_file(f, root, min_doc_cjk)
        report["files"].append(got["entry"])
        report["errors"].extend(got["errors"])
        report["warnings"].extend(got["warnings"])
        report["quality"].extend(got["quality"])

    report["corpus"]["files"] = len(_iter_files(root))
    report["corpus"]["cjk"] = sum(e["cjk"] for e in report["files"])
    report["corpus"]["lines"] = sum(e["lines"] for e in report["files"])
    report["quality_total"] = len(report["quality"])
    if report["corpus"]["cjk"] < min_total_cjk:
        report["ok"] = False
        report["errors"].append({
            "file": str(root), "line": 0, "kind": "总量不足",
            "message": f"语料共 {report['corpus']['cjk']} 个汉字，低于总量下限 "
                       f"{min_total_cjk}（E3 是处/万字，样本太小会放大误差）",
            "match": ""})
    if report["errors"]:
        report["ok"] = False
    report["exit_code"] = 0 if report["ok"] else 1
    return report


def render_markdown(report: dict) -> str:
    """把报告渲染成 Markdown（给评审/交付物附件用）。"""
    c = report["corpus"]
    verdict = "PASS（语料合格）" if report["ok"] else "FAIL（语料不合格）"
    out = [
        "# 语料契约检查报告",
        "",
        f"- 语料路径：`{c['path']}`",
        f"- 结论：**{verdict}**　退出码 {report['exit_code']}",
        f"- 规模：{c['files']} 篇 / {c['cjk']} 汉字 / {c['lines']} 行"
        f"（单篇下限 {c['min_doc_cjk']}，总量下限 {c['min_total_cjk']}）",
        "",
        "## 问题（FAIL）",
        "",
    ]
    if report["errors"]:
        out += ["| 文件:行 | 类型 | 说明 | 命中（掩码） |", "|---|---|---|---|"]
        for e in report["errors"]:
            where = f"{e['file']}:{e['line']}" if e["line"] else e["file"]
            out.append(f"| {where} | {e['kind']} | {e['message']} | "
                       f"{e['match'] or '—'} |")
    else:
        out.append("无。")
    out += ["", "## 告警（不影响退出码）", ""]
    if report["warnings"]:
        out += ["| 文件:行 | 类型 | 说明 |", "|---|---|---|"]
        for w in report["warnings"]:
            out.append(f"| {w['file']}:{w['line']} | {w['kind']} | {w['message']} |")
    else:
        out.append("无。")
    quality = report.get("quality") or []
    out += ["", "## 语料质量提示（不影响退出码）", "",
            "> 这些不是「语料违规」，而是「采集/转存可能把语料弄坏了」。"
            "标题跨段与成对符号不配对会让评测把片段当正文，产生假误报；"
            "单段过长通常是段落边界丢失。建议按提示重新采集后再跑 E3a。", ""]
    if quality:
        out += ["| 文件:行 | 类型 | 说明 |", "|---|---|---|"]
        for q in quality:
            out.append(f"| {q['file']}:{q['line']} | {q['kind']} | {q['message']} |")
    else:
        out.append("无。")
    out += ["", "## 文件清单", "",
            "| 文件 | 汉字 | 行数 | 编码 | sha256（前 16） | 结论 |",
            "|---|---|---|---|---|---|"]
    for f in report["files"]:
        out.append(f"| {f['path']} | {f['cjk']} | {f['lines']} | {f['encoding']} | "
                   f"`{f['sha256'][:16]}` | {'OK' if f['ok'] else 'FAIL'} |")
    out.append("")
    return "\n".join(out)


def write_reports(report: dict, out: str) -> list[str]:
    """按 `--out` 的形态落盘，返回已写出的路径列表（供调用方打印/测试）。"""
    p = Path(out)
    written: list[str] = []
    if p.suffix.lower() == ".json":
        targets = [(p, "json")]
    elif p.suffix.lower() == ".md":
        targets = [(p, "md")]
    elif p.is_dir():
        targets = [(p / "corpus_report.json", "json"),
                   (p / "corpus_report.md", "md")]
    else:
        targets = [(Path(str(p) + ".json"), "json"),
                   (Path(str(p) + ".md"), "md")]
    for path, kind in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        if kind == "json":
            path.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        else:
            path.write_text(render_markdown(report), encoding="utf-8")
        written.append(str(path))
    return written


def _print_summary(report: dict, written: list[str]) -> None:
    c = report["corpus"]
    print("=" * 72)
    print("语料契约检查")
    print("=" * 72)
    print(f"语料：{c['path']}")
    print(f"规模：{c['files']} 篇 / {c['cjk']} 汉字 / {c['lines']} 行"
          f"　（单篇下限 {c['min_doc_cjk']}，总量下限 {c['min_total_cjk']}）")
    for e in report["errors"][:20]:
        where = f"{e['file']}:{e['line']}" if e["line"] else e["file"]
        print(f"  [FAIL] {where}　{e['kind']}：{e['message']}"
              + (f"　命中 {e['match']}" if e["match"] else ""))
    if len(report["errors"]) > 20:
        print(f"  ……另有 {len(report['errors']) - 20} 项问题，见报告文件")
    for w in report["warnings"][:5]:
        print(f"  [WARN] {w['file']}:{w['line']}　{w['message']}")
    if len(report["warnings"]) > 5:
        print(f"  ……另有 {len(report['warnings']) - 5} 条告警，见报告文件")
    quality = report.get("quality") or []
    for q in quality[:5]:
        print(f"  [QUALITY] {q['file']}:{q['line']}　{q['kind']}：{q['message']}")
    if len(quality) > 5:
        print(f"  ……另有 {len(quality) - 5} 条质量提示，见报告文件")
    print("-" * 72)
    verdict = "PASS（语料合格）" if report["ok"] else "FAIL（语料不合格）"
    print(f"结论：{verdict}　问题 {len(report['errors'])} 项　"
          f"告警 {len(report['warnings'])} 条　"
          f"质量提示 {len(quality)} 条（不影响退出码）　退出码 {report['exit_code']}")
    for path in written:
        print(f"已写出：{path}")


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="真实语料契约检查（编码 / 规模 / 脱敏）")
    ap.add_argument("corpus", help="语料目录（递归 .md/.txt）或单个语料文件")
    ap.add_argument("--out", default="",
                    help="报告路径：.json/.md 结尾只写一种；其他写法同时写 "
                         ".json 与 .md；给目录则写 corpus_report.{json,md}")
    ap.add_argument("--min-doc-cjk", type=int, default=DEFAULT_MIN_DOC_CJK,
                    help=f"单篇汉字数下限（默认 {DEFAULT_MIN_DOC_CJK}）")
    ap.add_argument("--min-total-cjk", type=int, default=DEFAULT_MIN_TOTAL_CJK,
                    help=f"全语料汉字总量下限（默认 {DEFAULT_MIN_TOTAL_CJK}）")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    target = Path(args.corpus)
    if not target.exists():
        print(f"错误：语料路径不存在：{args.corpus}", file=sys.stderr)
        return 2
    if target.is_file() and target.suffix.lower() not in CORPUS_SUFFIXES:
        print("错误：单文件语料只接受 .md / .txt；整目录请传目录。", file=sys.stderr)
        return 2
    if args.min_doc_cjk < 0 or args.min_total_cjk < 0:
        print("错误：下限不能为负数", file=sys.stderr)
        return 2

    report = scan_corpus(target, min_doc_cjk=args.min_doc_cjk,
                         min_total_cjk=args.min_total_cjk)
    written = write_reports(report, args.out) if args.out else []
    _print_summary(report, written)
    return int(report["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
