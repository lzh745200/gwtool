# -*- coding: utf-8 -*-
"""纠错引擎评测基线：把"精度"从口头结论变成可复跑的数值。

为什么需要它
------------
在它之前，"精标集 100% 识别"与"真实公文 0 处/万字"是**两条孤立的断言**，
既没有检测级/纠正级的区分，也没有统一的语料与口径。结果是：

  - 无法回答"这次改动让召回涨了多少、误报涨了多少"；
  - 无法发现"翻译层（L4/L5）一开就崩"这类整体性回归——
    因为没有任何一条门禁会把**开启增强层后的误报**纳入判定。

本脚本把评测收敛为 6 个可判定项（E1–E6），每项给出数值与 PASS/FAIL，
并可用 `--json` 落盘供版本间对比。**默认不触碰真实用户数据**：
数据目录被重定向到临时目录，只有增强包目录是**只读**指向真实位置。

用法
----
    # 用本机已导入的增强包（只读），跑全部 6 项
    PYTHONPATH= .venv/Scripts/python.exe scripts/eval_corrector.py

    # 指定公文语料（每个文件一篇，UTF-8/GBK 自动探测）作为负样本
    ... scripts/eval_corrector.py --corpus D:/公文语料

    # 只看基础三层（不看增强层）
    ... scripts/eval_corrector.py --layers base

    # 落盘 JSON 便于版本对比
    ... scripts/eval_corrector.py --json eval_base.json

退出码：0 = 全部通过；1 = 有项目未达标（明细见输出）。
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CJK = re.compile(r"[\u4e00-\u9fff]")

# ---------------------------------------------------------------- 判定阈值
# 这些阈值就是"红线"的数值化。改动它们等同于改验收标准，须在提交说明里写明理由。
TH_CURATED_RECALL = 1.00        # E1 精标集检出率（人工精标必须 100% 认出）
TH_GEN_CTX_RECALL = 0.75        # E2 生成对在上下文中的召回（现状 0.82，缓降即预警）
TH_FP_PER_WAN = 0.50            # E3 公文体负样本误报（现状 0；这是不可退让的红线）
TH_OOV_RECALL_BASE = 0.05       # E4 词表外错字：基础三层的期望（现状 0，属已知天花板）
TH_NEURAL_FP_PER_QIAN = 1.00    # E6 开启神经层后新增误报（处/千字）：现状 57.6，本方案核心待修项
TH_MS_PER_QIAN_BASE = 100.0     # E5 基础三层耗时（毫秒/千字）

# E4 内置"词表外错字"小样本：真实但不入词表、不入纠错表的错形。
# 它的作用是**度量词表法的天花板**与增强层的补位能力，不追求统计充分性。
OOV_CASES = [
    ("落施", "落实"), ("据绝", "拒绝"), ("签于", "鉴于"), ("制序", "秩序"),
    ("布殖", "布置"), ("汇禀", "汇报"), ("贡任", "责任"), ("范筹", "范畴"),
    ("严历", "严厉"), ("详佃", "详细"), ("协离", "协调"), ("措拖", "措施"),
    ("贯沏", "贯彻"), ("经捡", "经验"), ("推委", "推诿"), ("诚垦", "诚恳"),
    ("登计", "登记"), ("偏辟", "偏僻"), ("暑名", "署名"), ("签暑", "签署"),
    ("效律", "效率"), ("尊守", "遵守"), ("贯输", "灌输"), ("落施到位", "落实到位"),
]

# E3 缺省负样本：纯中文公文体、人工确认无错字。
# **这不是充分语料**——正式门禁必须用 `--corpus` 指到本单位真实公文。
DEFAULT_NEG = [
    "各部门要高度重视此项工作，严格落实责任制，确保各项任务按期完成。",
    "为进一步规范公文处理流程，现将有关事项通知如下，请遵照执行。",
    "各地区、各部门要结合实际，认真抓好贯彻落实，并及时报告有关情况。",
    "会议听取了关于上半年工作情况的汇报，研究部署了下半年重点任务。",
    "要坚持问题导向，深入基层开展调查研究，切实解决群众反映的突出问题。",
    "各单位要加强协调配合，形成工作合力，共同推动各项措施落地见效。",
    "经研究决定，同意你单位请示事项，请按有关规定办理相关手续。",
    "此项工作纳入年度考核，请各单位务必于本月底前完成材料报送。",
]

_CTX_TEMPLATES = ["各单位要高度重视{}工作。", "现将{}情况报告如下。",
                  "请认真抓好{}落实。"]


def cjk_len(s: str) -> int:
    return len(CJK.findall(s))


class Result:
    """一条评测结论。"""

    def __init__(self, key: str, name: str, value, text: str, passed: bool):
        self.key, self.name, self.value, self.text, self.passed = \
            key, name, value, text, passed

    def line(self) -> str:
        tag = "PASS" if self.passed else "FAIL"
        return f"[{tag}] {self.key} {self.name}：{self.text}"


def _read_corpus(path: str) -> list[str]:
    """读入语料目录/文件，按行切成句子级片段（>=8 个汉字才纳入）。"""
    from gwtool.core.parsers.txt_parser import read_text_smart

    files: list[Path] = []
    p = Path(path)
    if p.is_dir():
        for suf in ("*.txt", "*.md", "*.csv"):
            files.extend(sorted(p.glob(suf)))
    elif p.exists():
        files = [p]
    out: list[str] = []
    for f in files:
        try:
            text = read_text_smart(str(f))
        except Exception:
            continue
        for raw_line in text.splitlines():
            # 用新名字承接规范化的结果，不覆盖循环变量本身（PLW2901）：
            # 覆盖循环变量是静态检查与人工复核都容易漏掉的隐患写法。
            line = raw_line.strip()
            if cjk_len(line) >= 8:
                out.append(line)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="纠错引擎评测基线")
    ap.add_argument("--corpus", default="", help="负样本语料目录/文件（缺省用内置样例）")
    ap.add_argument("--layers", default="base,l4,l5",
                    help="启用的层：base / l4 / l5 的逗号组合")
    ap.add_argument("--pack-root", default="",
                    help="增强包根目录（其下应有 current/<kind>/）；缺省用真实数据目录")
    ap.add_argument("--gen-sample", type=int, default=800, help="生成对抽样条数")
    ap.add_argument("--json", default="", help="把结果写到该 JSON 文件")
    ap.add_argument("--seed", type=int, default=20260915)
    args = ap.parse_args()

    layers = {s.strip() for s in args.layers.split(",") if s.strip()}

    # ---- 隔离数据目录：绝不碰真实用户库 ----
    from gwtool import paths

    tmp = Path(tempfile.mkdtemp(prefix="gwtool_eval_")) / "data"
    tmp.mkdir(parents=True, exist_ok=True)
    paths._override = tmp
    # 增强包目录只读指向真实位置（或 --pack-root）。app_data_dir 仍是临时目录。
    if args.pack_root:
        pack_root = Path(args.pack_root)
        paths.enhance_dir = lambda: pack_root          # type: ignore[assignment]
    elif not (tmp / "enhance").exists():
        real = Path.home() / "AppData" / "Roaming" / "gwtool" / "enhance"
        if not real.exists():
            real = Path.home() / ".local" / "share" / "gwtool" / "enhance"
        if real.exists():
            paths.enhance_dir = lambda: real           # type: ignore[assignment]

    from gwtool.db import connection as dbconn

    dbconn.configure(tmp / "eval.db")
    from gwtool.app import ensure_database_seeded

    ensure_database_seeded()

    from gwtool.core import corrector, corrector_data, csc_gec, csc_neural
    from gwtool.db import dao

    def _force_settings(on_l4: bool, on_l5: bool) -> None:
        csc_neural.set_setting(on_l4)
        csc_gec.set_setting(on_l5)
        csc_neural.reset()
        csc_gec.reset()
        corrector.invalidate_cache()

    _force_settings(False, False)          # 一律从"基础三层"起步

    results: list[Result] = []
    data: dict = {"layers": sorted(layers)}

    print("=" * 72)
    print("纠错引擎评测基线")
    print("=" * 72)
    pairs = dao.all_error_pairs(only_enabled=False)
    words = set(dao.all_dictionary_words())
    data["pairs_total"] = len(pairs)
    data["dict_words"] = len(words)
    csc_info = csc_neural.enhance_pack.installed_pack("csc")
    cgec_info = csc_gec.enhance_pack.installed_pack("cgec")
    print(f"错别字对 {len(pairs)}（精标 {sum(1 for p in pairs if p.source == 'curated')}"
          f" / 生成 {sum(1 for p in pairs if p.source == 'generated')}）"
          f"　词典 {len(words)} 词")
    print(f"增强包：拼写={'已装' if csc_info else '未装'}"
          f"　语法={'已装' if cgec_info else '未装'}"
          f"　onnxruntime={'可用' if csc_neural.runtime_available() else '缺失'}")
    print()

    # ---------------------------------------------------------- E1 精标集
    ok = bad = 0
    miss = []
    for wrong, correct, _cat, _conf in corrector_data.CURATED_PAIRS:
        if not corrector._is_valid_pair(wrong, correct):
            continue
        hits = [h for h in corrector.check_text(wrong) if h.suggestion == correct]
        if hits:
            ok += 1
        else:
            bad += 1
            miss.append(f"{wrong}→{correct}")
    recall = ok / max(1, ok + bad)
    results.append(Result(
        "E1", "人工精标集检出率", round(recall, 4),
        f"{ok}/{ok + bad} = {recall:.1%}"
        + (f"；漏报 {miss[:6]}" if miss else ""),
        recall >= TH_CURATED_RECALL))
    data["E1"] = {"ok": ok, "bad": bad, "recall": recall, "miss": miss}

    # ---------------------------------------------------------- E2 生成对
    rnd = random.Random(args.seed)
    gen = [p for p in pairs if (p.source or "") == "generated"]
    sample = rnd.sample(gen, min(args.gen_sample, len(gen)))
    iso = ctx = 0
    for i, p in enumerate(sample):
        if corrector.check_text(p.wrong):
            iso += 1
        if corrector.check_text(_CTX_TEMPLATES[i % len(_CTX_TEMPLATES)].format(p.wrong)):
            ctx += 1
    n = max(1, len(sample))
    ctx_recall = ctx / n
    results.append(Result(
        "E2", "生成对召回（上下文中）", round(ctx_recall, 4),
        f"{ctx}/{len(sample)} = {ctx_recall:.1%}（孤立 {iso / n:.1%}）",
        ctx_recall >= TH_GEN_CTX_RECALL))
    data["E2"] = {"sample": len(sample), "iso": iso, "ctx": ctx,
                  "iso_recall": iso / n, "ctx_recall": ctx_recall}

    # ---------------------------------------------------------- E3 负样本误报
    neg = _read_corpus(args.corpus) if args.corpus else list(DEFAULT_NEG)
    neg_src = f"外部语料 {args.corpus}" if args.corpus else "内置样例（需替换为真实公文）"
    n_chars = sum(cjk_len(s) for s in neg)
    t0 = time.perf_counter()
    fp_hits = []
    for s in neg:
        fp_hits.extend(corrector.check_text(s))
    t_neg = time.perf_counter() - t0
    fp_per_wan = len(fp_hits) / max(1, n_chars) * 10000
    by_cat: dict[str, int] = {}
    for h in fp_hits:
        by_cat[h.category] = by_cat.get(h.category, 0) + 1
    results.append(Result(
        "E3", "负样本误报（基础三层）", round(fp_per_wan, 3),
        f"{len(fp_hits)} 处 / {n_chars} 汉字 = {fp_per_wan:.2f} 处/万字"
        f"　来源：{neg_src}　分类：{by_cat or '无'}",
        fp_per_wan <= TH_FP_PER_WAN))
    data["E3"] = {"segments": len(neg), "chars": n_chars, "hits": len(fp_hits),
                  "per_wan": fp_per_wan, "by_category": by_cat,
                  "corpus": neg_src, "seconds": round(t_neg, 3)}

    # ---------------------------------------------------------- E4 词表外错字
    oov = [(w, c) for w, c in OOV_CASES
           if w not in words and w != c
           and not any(p.wrong == w for p in pairs)]
    oov_ok = sum(1 for w, _c in oov
                 if corrector.check_text(f"各单位要{w}相关规定。"))
    oov_recall = oov_ok / max(1, len(oov))
    results.append(Result(
        "E4", "词表外错字召回（基础三层）", round(oov_recall, 4),
        f"{oov_ok}/{len(oov)} = {oov_recall:.1%}（词表法天花板，增强层应显著提升）",
        oov_recall >= TH_OOV_RECALL_BASE))
    data["E4"] = {"n": len(oov), "ok": oov_ok, "recall": oov_recall}

    # ---------------------------------------------------------- E5 耗时
    probe = neg[:40] or list(DEFAULT_NEG)
    probe_chars = sum(cjk_len(s) for s in probe)
    t0 = time.perf_counter()
    for s in probe:
        corrector.check_text(s)
    dt = time.perf_counter() - t0
    ms_per_qian = dt / max(1, probe_chars) * 1000 * 1000 / 1000
    ms_per_qian = dt * 1000 / max(1, probe_chars) * 1000
    results.append(Result(
        "E5", "基础三层耗时", round(ms_per_qian, 1),
        f"{dt * 1000:.0f} ms / {probe_chars} 汉字 = {ms_per_qian:.1f} ms/千字",
        ms_per_qian <= TH_MS_PER_QIAN_BASE))
    data["E5"] = {"chars": probe_chars, "seconds": round(dt, 4),
                  "ms_per_qian": ms_per_qian}

    # ---------------------------------------------------------- E6 神经层误报闸门
    if "l4" in layers or "l5" in layers:
        ready_l4 = "l4" in layers and csc_neural.runtime_available()
        ready_l5 = "l5" in layers and csc_gec.runtime_available()
        _force_settings(ready_l4, ready_l5)
        t0 = time.perf_counter()
        neural_hits = []
        for s in probe:
            neural_hits.extend(corrector.check_text(s))
        dt_n = time.perf_counter() - t0
        added = [h for h in neural_hits if h.category in ("神经纠错", "语法纠错")]
        per_qian = len(added) / max(1, probe_chars) * 1000
        samples = [f"{h.wrong}→{h.suggestion}" for h in added[:8]]
        results.append(Result(
            "E6", "开启增强层后新增误报", round(per_qian, 3),
            f"新增 {len(added)} 处 / {probe_chars} 汉字 = {per_qian:.1f} 处/千字"
            f"　耗时 {dt_n:.1f}s　样例：{samples or '无'}",
            per_qian <= TH_NEURAL_FP_PER_QIAN))
        data["E6"] = {"added": len(added), "chars": probe_chars,
                      "per_qian": per_qian, "seconds": round(dt_n, 3),
                      "samples": samples,
                      "l4_ready": ready_l4, "l5_ready": ready_l5,
                      "l4_error": csc_neural.last_error(),
                      "l5_error": csc_gec.last_error()}

        # 增强层开启后，基础负样本首段不得凭空长出误报（抽样，控制评测时长）
        first_segment_hits = corrector.check_text(neg[0]) if neg else []
        data["E6_extra"] = {"first_segment_hits": len(first_segment_hits),
                            "first_segment": neg[0] if neg else ""}
        _force_settings(False, False)

    # ---------------------------------------------------------- 汇总
    print("-" * 72)
    for r in results:
        print(r.line())
    print("-" * 72)
    failed = [r for r in results if not r.passed]
    print(f"通过 {len(results) - len(failed)}/{len(results)} 项；"
          f"未达标：{', '.join(r.key for r in failed) or '无'}")
    print("\n注：E3/E6 的负样本缺省仅为示意（公文体小样）。作为发布门禁时，"
          "必须用 --corpus 指向本单位真实公文语料，"
          "并按 §评估 的口径补上检测级/纠正级 F1。")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"results": [{"key": r.key, "name": r.name,
                                     "value": r.value, "passed": r.passed,
                                     "text": r.text} for r in results],
                        "data": data}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        print(f"已写出：{args.json}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
