# -*- coding: utf-8 -*-
"""纠错引擎评测基线：把"精度"从口头结论变成可复跑的数值。

为什么需要它
------------
在它之前，"精标集 100% 识别"与"真实公文 0 处/万字"是**两条孤立的断言**，
既没有检测级/纠正级的区分，也没有统一的语料与口径。结果是：

  - 无法回答"这次改动让召回涨了多少、误报涨了多少"；
  - 无法发现"翻译层（L4/L5）一开就崩"这类整体性回归——
    因为没有任何一条门禁会把**开启增强层后的误报**纳入判定。

本脚本把评测收敛为 7 个可判定项（E1–E7）。**默认不触碰真实用户数据**：
数据目录被重定向到临时目录，只有增强包目录是**只读**指向真实位置。

评测档案（--profile）与 SKIP 语义
--------------------------------
    base   只跑基础三层：门禁 E1/E2/E3a/E5/E7（CI 用的就是这一档；
           不需要增强包，任何机器都能通过）
    l4     基础 + 单字精排层：门禁项之外再加 E4/E6（需已装拼写增强包）
    l5     基础 + 序列生成层：同上（需已装语法增强包）
    all    基础 + 两层（默认值，保持旧行为：跑全部 6 项）

E3 口径拆分（v1.12，D2）
------------------------
真实语料上"提示类命中"（数字用法 / 表达提示 / 标点规范 / 机构沿革）与
"可自动替换类误报"混在同一个 0.5 处/万字 门禁里，会把后者掩盖掉。现在拆成：

    E3a  可自动替换类（category ∉ 提示类）→ **硬门禁 ≤0.5 处/万字**
    E3b  提示类（category ∈ 提示类）      → 只报告，不参与门禁（固定 SKIP）
    e3_all  旧总口径（含提示类）           → 仅写 JSON/文本供历史对比

提示类集合 = ``corrector.ADVISORY_CATEGORIES`` ＋ ``E3_ADVISORY_EXTRA``（机构沿革）。

**未参与门禁的项输出 [SKIP]，不计 FAIL、不影响退出码**，并在结果行里写明原因：
  - 档案不含的层（如 base 不含 L4/L5）→ SKIP；
  - 增强包未安装 / onnxruntime 缺失 / 设置未启用 → E4、E6 SKIP；
  - 未给 `--corpus` 时 E3/E5/E6 仍然测量并在结果行标"示意·不参与门禁"，
    但**不据此判 FAIL** —— 224 字内置小样的数值没有统计意义，把它当门禁
    就是"空过"（P0-2 的原始缺陷）。

用法
----
    # CI / 日常自检：基础三层门禁（无需增强包），通过即 exit 0
    ... scripts/eval_corrector.py --profile base

    # 用本机已导入的增强包（只读），跑全部 6 项（旧的默认行为）
    ... scripts/eval_corrector.py

    # 指定公文语料（每个文件一篇，UTF-8/GBK 自动探测）作为负样本；
    # 只有给了 --corpus，E3a/E5/E6 才真正参与门禁。
    # 语料必须先过脱敏/编码/规模体检：scripts/corpus_check.py（见 doc/corpus/README.md）
    ... scripts/eval_corrector.py --corpus D:/公文语料

    # 语料 sha256 清单：两次评测（改前/改后）各写一份，对比时先比清单再比数字，
    # 避免"换了语料还以为指标涨了"
    ... scripts/eval_corrector.py --profile base --corpus D:/公文语料 \
            --corpus-manifest artifacts/corpus_before.json

    # 有增强包机器的验收：E4/E6 必须**真实运行**（未装包/未启用/无真实语料即 FAIL）
    ... scripts/eval_corrector.py --profile all --require-packs --corpus D:/公文语料

    # 落盘 JSON 便于版本对比（含 profile / 语料指纹 / 样本量）
    ... scripts/eval_corrector.py --json eval_base.json

退出码：0 = 门禁项全部通过（允许存在 SKIP）；1 = 有门禁项 FAIL。
"""
from __future__ import annotations

import argparse
import hashlib
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

# --corpus 目录扫描口径（同目录、不递归；与 scripts/corpus_check.py 的
# 递归体检不冲突：检查端会连子目录一起查，评测端只吃目录第一层，
# 避免"我以为评测了全部语料、实际只评测了顶层"这种静默差异）。
CORPUS_GLOBS = ("*.txt", "*.md", "*.csv")
# 与 corpus_check.py 同一份"不是语料"清单：`doc/corpus/` 下除真实语料外还会放
# README、采集合规检查表、AUTHORIZATION*.json（D 批交付物）。不跳过会让 E3 把
# 检查表当成负样本（实测 1213 汉字，足以改变处/万字）。**两个脚本各存一份同内容
# 常量**（不新增共享模块，保证脚本可单独拷走），有测试断言两边相等。
SKIP_CORPUS_NAMES: frozenset[str] = frozenset({
    "README.md",
    "采集合规检查表.md",
    "AUTHORIZATION.example.json",
    "AUTHORIZATION.json",
})
_SKIP_CORPUS_LOWER = frozenset(name.lower() for name in SKIP_CORPUS_NAMES)

# ---------------------------------------------------------------- 判定阈值
# 这些阈值就是"红线"的数值化。改动它们等同于改验收标准，须在提交说明里写明理由。
TH_CURATED_RECALL = 1.00        # E1 精标集检出率（人工精标必须 100% 认出）
TH_GEN_CTX_RECALL = 0.75        # E2 生成对在上下文中的召回（现状 0.82，缓降即预警）
TH_FP_PER_WAN = 0.50            # E3a 可自动替换类误报（现状 0；这是不可退让的红线）
TH_OOV_RECALL_BASE = 0.05       # E4 词表外错字：基础三层的期望（现状 0，属已知天花板）
TH_NEURAL_FP_PER_QIAN = 1.00    # E6 开启神经层后新增误报（处/千字）：现状 57.6，本方案核心待修项
TH_MS_PER_QIAN_BASE = 100.0     # E5 基础三层耗时（毫秒/千字）
TH_E7_F05 = 0.95                # E7 精标配对 F0.5（精确率加权；现状 1.0）
TH_E7_CLEAN_FP = 0              # E7 正确形被自动改的处数（红线：必须 0）

#: E3b「提示类」口径（v1.12，D2）：`corrector.ADVISORY_CATEGORIES`
#: （数字用法 / 表达提示 / 标点规范）是"建议是提示标签、写回会把正文改成标签"的
#: 类别，本就不该与"可自动替换类"共用 0.5/万字 的硬门禁。这里再补一项
#: 「机构沿革」：`corrector_data.ORG_RENAME_PAIRS` 自注"提示级，建议核实后替换"
#: （corrector_data.py:514），真实语料首轮也把 4 处机构沿革算作提示类。
#: **若要让机构沿革回到 E3a 门禁，只删这一行即可**（`by_category` 两份都保留，
#: 可在 JSON 里按任意口径重算）。
E3_ADVISORY_EXTRA: tuple[str, ...] = ("机构沿革",)

# 评测档案 -> 启用的层。E1/E2 两项在所有档案下都算门禁（基础三层）。
PROFILES: dict[str, tuple[str, ...]] = {
    "base": ("base",),
    "l4": ("base", "l4"),
    "l5": ("base", "l5"),
    "all": ("base", "l4", "l5"),
}
# 兼容旧用法 `--layers base,l4,l5`：层组合 -> 档案名。
_LAYERS_TO_PROFILE = {tuple(sorted(v)): k for k, v in PROFILES.items()}

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_SKIP = "SKIP"

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

# 结果行里"未参与门禁"的统一标记（供人工与测试识别，勿改字面量）。
UNGATED_MARK = "示意·不参与门禁"


def cjk_len(s: str) -> int:
    return len(CJK.findall(s))


class Result:
    """一条评测结论。status ∈ {PASS, FAIL, SKIP}，只有 FAIL 影响退出码。"""

    def __init__(self, key: str, name: str, value, text: str,
                 status: str = STATUS_PASS, reason: str = ""):
        self.key, self.name, self.value, self.text = key, name, value, text
        self.status, self.reason = status, reason

    @property
    def passed(self) -> bool:
        return self.status == STATUS_PASS

    @property
    def skipped(self) -> bool:
        return self.status == STATUS_SKIP

    def line(self) -> str:
        return f"[{self.status}] {self.key} {self.name}：{self.text}"

    def to_json(self) -> dict:
        # 保留 `passed` 兼容旧消费方（SKIP 视为 False），并新增 status/reason。
        return {"key": self.key, "name": self.name, "value": self.value,
                "status": self.status, "passed": self.passed,
                "text": self.text, "reason": self.reason}


def _gated_result(key: str, name: str, value, text: str, ok: bool,
                  fail_text: str = "") -> Result:
    """按阈值生成 PASS/FAIL。"""
    if ok:
        return Result(key, name, value, text, STATUS_PASS)
    return Result(key, name, value, f"{text}{fail_text}", STATUS_FAIL)


def _skipped_result(key: str, name: str, value, text: str,
                    reason: str) -> Result:
    return Result(key, name, value, text, STATUS_SKIP, reason)


def _skipped_or_required(key: str, name: str, value, text: str,
                         reason: str, require: bool) -> Result:
    """增强层项（E4/E6）的 SKIP/FAIL 选择。

    `--require-packs`（有增强包机器的验收）下，"未真实运行"必须判 FAIL 而不是
    SKIP —— 否则装了增强包的机器跑出全绿，却可能根本没测到增强层，
    正是 P0-2 那类"假绿"的翻版。普通模式下保持 SKIP（不计 FAIL）。
    """
    if require:
        return Result(key, name, value,
                      f"{text}（--require-packs：{reason}，增强层验收未通过）",
                      STATUS_FAIL, reason)
    return _skipped_result(key, name, value, f"{text}（{reason}，不计入门禁）",
                          reason)


def _load_corpus_whitelist(path: str) -> list[dict]:
    """读入"源文本偏差"白名单（v1.14.0 C4）。

    用途：真实语料里**源文本本身不规范**的地方（如半角逗号 `办公厅,国务院`、
    `6200多平方米,全部`）被引擎正确报出，但按 E3a 口径会被算成"误报"。若为了
    把 E3a 压到 0.5 而收紧规则，就会把**正确能力**改坏。故允许人工复核后把这些
    命中登记为 `source_deviation`，从 E3a 扣除、单独计数（JSON 里逐条留档）。

    文件格式（JSON）：:

        {"source_deviations": [
            {"wrong": "厅,", "suggestion": "厅，",
             "note": "源文本用半角逗号（政府网原文）",
             "contains": "人民政府办公厅,国务院"}   // 可选：限定所在句
        ]}

    匹配键 = (wrong, suggestion) 且（若给了 contains）该句包含 contains。
    **不按文件/行**匹配：语料目录结构会变（扁平/分子目录），按句子内容更稳。
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items = raw.get("source_deviations") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("白名单格式应为 {\"source_deviations\": [...]} 或数组")
    out: list[dict] = []
    for i, it in enumerate(items, 1):
        if not isinstance(it, dict) or not it.get("wrong"):
            raise ValueError(f"白名单第 {i} 条缺少 wrong 字段")
        out.append({"wrong": str(it["wrong"]),
                    "suggestion": str(it.get("suggestion") or ""),
                    "contains": str(it.get("contains") or ""),
                    "note": str(it.get("note") or "")})
    return out


def _match_whitelist(whitelist: list[dict], segment: str, hit) -> str | None:
    """命中是否落在白名单条目上；命中返回该条备注，否则 None。"""
    for it in whitelist:
        if it["wrong"] != hit.wrong:
            continue
        if it["suggestion"] and it["suggestion"] != hit.suggestion:
            continue
        if it["contains"] and it["contains"] not in segment:
            continue
        return it["note"] or "人工复核：源文本偏差"
    return None


def _corpus_files(path: str) -> list[Path]:
    """语料文件列表（目录 -> 同目录 .txt/.md/.csv；单文件原样返回）。

    `SKIP_CORPUS_NAMES` 里的说明/合规文件一律跳过：它们是契约文档与 D 批交付物，
    不是负样本（检查表有 1200+ 汉字，会被算进 E3）。
    """
    p = Path(path)
    if p.is_dir():
        files: list[Path] = []
        for suf in CORPUS_GLOBS:
            files.extend(f for f in sorted(p.glob(suf))
                         if f.name.lower() not in _SKIP_CORPUS_LOWER)
        return files
    if p.exists() and p.name.lower() not in _SKIP_CORPUS_LOWER:
        return [p]
    return []


def _read_corpus(path: str) -> list[str]:
    """读入语料目录/文件，按行切成句子级片段（>=8 个汉字才纳入）。"""
    from gwtool.core.parsers.txt_parser import read_text_smart

    files = _corpus_files(path)
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


def _corpus_manifest(corpus_path: str, segments: list[str]) -> dict:
    """语料 sha256 清单：逐文件指纹 + 聚合指纹 + 样本量。

    为什么需要逐文件哈希：两次评测（改前/改后）只有**语料完全同一**时数值才有
    可比性。聚合指纹相同也可能"文件内容互换了"，逐文件清单能直接看出差异；
    清单本身不含语料正文，可以随报告一起归档。
    """
    root = Path(corpus_path)
    entries: list[dict] = []
    for f in _corpus_files(corpus_path):
        data = f.read_bytes()
        try:
            rel = str(f.relative_to(root))
        except ValueError:                  # corpus 传的是单个文件
            rel = f.name
        try:
            from gwtool.core.parsers.txt_parser import read_text_smart
            chars = cjk_len(read_text_smart(str(f)))
        except Exception:
            chars = 0
        entries.append({"path": rel, "bytes": len(data), "cjk": chars,
                        "sha256": hashlib.sha256(data).hexdigest()})
    digest = hashlib.sha256(
        "\n".join(f"{e['path']}:{e['sha256']}" for e in entries).encode("utf-8")
    ).hexdigest()
    return {"version": 1,
            "corpus": {"path": str(root), "segments": len(segments),
                       "chars": sum(cjk_len(s) for s in segments),
                       "fingerprint": _corpus_fingerprint(segments),
                       "aggregate_sha256": digest, "files": entries}}


def _corpus_fingerprint(segments: list[str]) -> str:
    """语料指纹（sha256 前 16 位）：换语料必然换指纹，版本对比时不会被误读。"""
    h = hashlib.sha256("\n".join(segments).encode("utf-8")).hexdigest()
    return h[:16]


def _resolve_profile(args, ap: argparse.ArgumentParser) -> str:
    """--profile 与旧 --layers 的兼容解析。

    规则：两个都不给 -> all（保持旧默认行为）；只给 --layers -> 由层组合反推
    档案；两个都给且不一致 -> 直接报参数错误（避免"以为在跑 base 门禁，
    实际跑的是 all"这类静默错配）。
    """
    if args.profile is not None and args.layers is not None:
        raw = tuple(sorted({s.strip() for s in args.layers.split(",") if s.strip()}))
        if raw != tuple(sorted(PROFILES[args.profile])):
            ap.error(f"--profile {args.profile} 与 --layers {args.layers} 不一致")
        return args.profile
    if args.profile is not None:
        return args.profile
    if args.layers is not None:
        raw = tuple(sorted({s.strip() for s in args.layers.split(",") if s.strip()}))
        profile = _LAYERS_TO_PROFILE.get(raw)
        if profile is None:
            ap.error("--layers 仅支持 base / base,l4 / base,l5 / base,l4,l5；"
                     "请改用 --profile")
        return profile
    return "all"


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="纠错引擎评测基线（门禁）")
    ap.add_argument("--profile", choices=tuple(PROFILES), default=None,
                    help="评测档案 base|l4|l5|all（默认 all）。档案不包含的层/"
                         "未安装增强包的项输出 SKIP，不计 FAIL")
    ap.add_argument("--corpus", default="",
                    help="负样本语料目录/文件（.md/.txt/.csv，同目录扫描）；"
                         "不给时 E3/E5/E6 仅示意、不参与门禁。"
                         "正式语料需先过 scripts/corpus_check.py 体检")
    ap.add_argument("--corpus-manifest", default="",
                    help="把语料 sha256 清单写到该 JSON（逐文件+聚合指纹，"
                         "供改前/改后两次评测对比；需同时给 --corpus）")
    ap.add_argument("--corpus-whitelist", default="",
                    help="源文本偏差白名单 JSON（人工复核后确认是语料本身不规范、"
                         "不是引擎改错的命中）。这些命中从 E3a 扣除并单独计入 "
                         "E3a.source_deviation；格式见 "
                         "scripts/eval_corrector._load_corpus_whitelist 的 docstring")
    ap.add_argument("--require-packs", action="store_true",
                    help="有增强包机器的验收：E4/E6 必须真实运行；未装包/"
                         "未启用/无真实语料时判 FAIL（默认仍为 SKIP）")
    ap.add_argument("--layers", default=None,
                    help="[兼容旧用法] 层组合；等价于 --profile，两者冲突会报错")
    ap.add_argument("--pack-root", default="",
                    help="增强包根目录（其下应有 current/<kind>/）；缺省用真实数据目录")
    ap.add_argument("--gen-sample", type=int, default=800, help="生成对抽样条数")
    ap.add_argument("--json", default="", help="把结果写到该 JSON 文件")
    ap.add_argument("--seed", type=int, default=20260915)
    return ap


def main() -> int:
    ap = _build_parser()
    args = ap.parse_args()
    profile = _resolve_profile(args, ap)
    if args.corpus_manifest and not args.corpus:
        ap.error("--corpus-manifest 需要同时给 --corpus")
    layers = set(PROFILES[profile])
    enh_requested = bool(layers & {"l4", "l5"})
    external_corpus = bool(args.corpus)

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

    # E3b 提示类类别集合：以 corrector.ADVISORY_CATEGORIES 为唯一来源，另加
    # 数据表自注"提示级"的机构沿革（见文件头 E3_ADVISORY_EXTRA 的说明）。
    e3_advisory = set(corrector.ADVISORY_CATEGORIES) | set(E3_ADVISORY_EXTRA)

    def _force_settings(on_l4: bool, on_l5: bool) -> None:
        csc_neural.set_setting(on_l4)
        csc_gec.set_setting(on_l5)
        csc_neural.reset()
        csc_gec.reset()
        corrector.invalidate_cache()

    _force_settings(False, False)          # 一律从"基础三层"起步

    results: list[Result] = []
    data: dict = {"layers": sorted(layers), "profile": profile}

    pairs = dao.all_error_pairs(only_enabled=False)
    words = set(dao.all_dictionary_words())
    data["pairs_total"] = len(pairs)
    data["dict_words"] = len(words)

    # ---- 增强包就绪度：决定 E4/E6 是"测量"还是"SKIP" ----
    pack_csc = csc_neural.enhance_pack.installed_pack("csc")
    pack_cgec = csc_gec.enhance_pack.installed_pack("cgec")
    runtime_l4 = csc_neural.runtime_available()
    runtime_l5 = csc_gec.runtime_available()
    can_l4 = "l4" in layers and pack_csc is not None and runtime_l4
    can_l5 = "l5" in layers and pack_cgec is not None and runtime_l5
    enh_active = can_l4 or can_l5
    pack_any = pack_csc is not None or pack_cgec is not None
    n_builtin_curated = len(corrector_data.CURATED_PAIRS)
    data["curated_builtin"] = n_builtin_curated
    data["enhance"] = {
        "pack_csc": pack_csc is not None, "pack_cgec": pack_cgec is not None,
        "runtime_l4": runtime_l4, "runtime_l5": runtime_l5,
        "can_l4": can_l4, "can_l5": can_l5,
    }

    print("=" * 72)
    print("纠错引擎评测基线")
    print("=" * 72)
    print(f"评测档案 profile={profile}　启用层={','.join(sorted(layers))}")
    print(f"错别字对 {len(pairs)}（库内生成 "
          f"{sum(1 for p in pairs if p.source == 'generated')} / 库内用户 "
          f"{sum(1 for p in pairs if p.source == 'user')}）"
          f"　内置精标 {n_builtin_curated}　词典 {len(words)} 词")
    print(f"增强包：拼写={'已装' if pack_csc else '未装'}"
          f"　语法={'已装' if pack_cgec else '未装'}"
          f"　onnxruntime={'可用' if runtime_l4 or runtime_l5 else '缺失'}")
    if args.require_packs:
        print("验收模式：--require-packs（E4/E6 未真实运行判 FAIL，不再 SKIP）")
    print()

    # ---------------------------------------------------------- 语料契约
    neg = _read_corpus(args.corpus) if external_corpus else list(DEFAULT_NEG)
    n_chars = sum(cjk_len(s) for s in neg)
    corpus_payload: dict = {
        "path": args.corpus,
        "external": external_corpus,
        "segments": len(neg),
        "chars": n_chars,
        "fingerprint": _corpus_fingerprint(neg),
        "gated": bool(external_corpus and n_chars > 0),
    }
    data["corpus"] = corpus_payload
    # E3/E5/E6 是否参与门禁：只有"外部且非空"的语料才有统计意义。
    corpus_gated = bool(external_corpus and n_chars > 0)
    ungated_reason = "缺 --corpus" if not external_corpus else "外部语料为空或不可读"
    # 无外部语料时统一追加的标记（E3/E5/E6 的结果行都必须带上，见 P0-2 验收口径）
    ungated_tail = f"（{UNGATED_MARK}：{ungated_reason}）"
    if external_corpus:
        neg_src = f"外部语料 {args.corpus}"
    else:
        neg_src = "内置样例（示意，需替换为真实公文）"
    if external_corpus and not n_chars:
        print(f"警告：外部语料不可用（{args.corpus}），E3 判 FAIL、E5/E6 跳过。\n")
    else:
        print(f"负样本语料：{neg_src}　{len(neg)} 段 / {n_chars} 汉字"
              f"　指纹 {corpus_payload['fingerprint']}\n")

    # ---------------------------------------------------------- 语料清单
    manifest: dict | None = None
    if args.corpus_manifest:
        manifest = _corpus_manifest(args.corpus, neg)
        m_path = Path(args.corpus_manifest)
        m_path.parent.mkdir(parents=True, exist_ok=True)
        m_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                          encoding="utf-8")
        meta = manifest["corpus"]
        print(f"语料清单：{args.corpus_manifest}"
              f"（{len(meta['files'])} 个文件，聚合 sha256 "
              f"{meta['aggregate_sha256'][:16]}）")
        data["corpus_manifest"] = manifest
        corpus_payload["manifest_path"] = args.corpus_manifest

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
    results.append(_gated_result(
        "E1", "人工精标集检出率", round(recall, 4),
        f"{ok}/{ok + bad} = {recall:.1%}"
        + (f"；漏报 {miss[:6]}" if miss else ""),
        recall >= TH_CURATED_RECALL))
    data["E1"] = {"ok": ok, "bad": bad, "recall": recall, "miss": miss}

    # ---------------------------------------------------------- E7 配对精确率
    # 规则层配对控制（v1.17 P0）：448 精标对同时构造"错形句/正确形句"——
    # 错形句必须被 **自动层（severity=error）** 改对；正确形句里同一位置
    # 不得有任何自动改动。它补的是 E1/E2 只有召回、没有"正确文本被误改"
    # 这一侧的证据；口径只覆盖精标对所在词位，不宣称整体精度。
    e7_tp = e7_fn = e7_fp = e7_clean_auto = 0
    e7_fp_examples = []
    for wrong, correct, _cat, _conf in corrector_data.CURATED_PAIRS:
        if not corrector._is_valid_pair(wrong, correct):
            continue
        pos = f"请各单位{wrong}有关要求。"
        pos_hits = [h for h in corrector.check_text(pos)
                    if h.severity == "error"]
        if any(h.wrong == wrong and h.suggestion == correct for h in pos_hits):
            e7_tp += 1
        else:
            e7_fn += 1
        neg_sent = f"请各单位{correct}有关要求。"
        idx = neg_sent.find(correct)
        neg_auto = [h for h in corrector.check_text(neg_sent)
                    if h.severity == "error"]
        e7_clean_auto += len(neg_auto)
        bad = [h for h in neg_auto
               if h.start < idx + len(correct) and h.end > idx]
        if bad:
            e7_fp += 1
            if len(e7_fp_examples) < 6:
                e7_fp_examples.append(
                    f"{correct}→{[h.suggestion for h in bad]}")
    e7_recall = e7_tp / max(1, e7_tp + e7_fn)
    e7_precision = e7_tp / max(1, e7_tp + e7_fp)
    e7_f05 = (1.25 * e7_precision * e7_recall
              / (0.25 * e7_precision + e7_recall)
              if (e7_precision + e7_recall) else 0.0)
    e7_ok = (e7_f05 >= TH_E7_F05 and e7_fp <= TH_E7_CLEAN_FP)
    results.append(_gated_result(
        "E7", "精标配对精确率（F0.5）/ 正确形零自动改",
        round(e7_f05, 4),
        f"F0.5={e7_f05:.1%}（P={e7_precision:.1%} R={e7_recall:.1%}，"
        f"正确形被自动改 {e7_fp} 处，同句其它自动命中 {e7_clean_auto}）"
        + (f"；误改示例 {e7_fp_examples}" if e7_fp_examples else ""),
        e7_ok))
    data["E7"] = {"pairs": e7_tp + e7_fn, "tp": e7_tp, "fn": e7_fn,
                  "fp": e7_fp, "clean_auto_hits": e7_clean_auto,
                  "precision": e7_precision, "recall": e7_recall,
                  "f05": e7_f05, "fp_examples": e7_fp_examples}

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
    results.append(_gated_result(
        "E2", "生成对召回（上下文中）", round(ctx_recall, 4),
        f"{ctx}/{len(sample)} = {ctx_recall:.1%}（孤立 {iso / n:.1%}）",
        ctx_recall >= TH_GEN_CTX_RECALL))
    data["E2"] = {"sample": len(sample), "iso": iso, "ctx": ctx,
                  "iso_recall": iso / n, "ctx_recall": ctx_recall}

    # ------------------------------------------- E3a/E3b 负样本误报（口径拆分）
    # E3 口径拆分（v1.12，D2）：真实语料首轮 11 处里近一半是提示类（机构沿革 /
    # 标点规范等）—— 它们的 suggestion 是提示标签或"建议核实后替换"，混进同一个
    # 0.5/万字 门禁会把"可自动替换类"的误报比例掩盖掉。拆成：
    #   E3a 可自动替换类（category ∉ 提示类）→ **硬门禁 ≤0.5 处/万字**；
    #   E3b 提示类（category ∈ 提示类）      → **只报告、不参与门禁**；
    #   e3_all 旧总口径（含提示类）           → 保留在 JSON/文本里便于与历史对比。
    t0 = time.perf_counter()
    fp_pairs: list[tuple[str, object]] = []
    for s in neg:
        for h in corrector.check_text(s):
            fp_pairs.append((s, h))
    t_neg = time.perf_counter() - t0
    # ---- 源文本偏差白名单（v1.14.0 C4）：人工复核确认"是语料本身不规范、
    # 不是引擎改错"的命中（如源文本用半角逗号 `办公厅,国务院`）。它们从 E3a
    # 扣除、单独计入 source_deviation —— 否则门禁会逼着把正确规则改坏。
    # 清单文件格式见 `_load_corpus_whitelist`；不给则完全按原口径。
    whitelist = _load_corpus_whitelist(args.corpus_whitelist) if args.corpus_whitelist else []
    deviations: list[tuple[str, object, str]] = []
    rest_pairs: list[tuple[str, object]] = []
    for seg, h in fp_pairs:
        note = _match_whitelist(whitelist, seg, h)
        if note is not None:
            deviations.append((seg, h, note))
        else:
            rest_pairs.append((seg, h))
    fp_hits = [h for _s, h in rest_pairs]
    hits_a = [h for h in fp_hits if h.category not in e3_advisory]
    hits_b = [h for h in fp_hits if h.category in e3_advisory]

    def _per_wan(count: int) -> float:
        return count / max(1, n_chars) * 10000

    def _by_category(hits) -> dict[str, int]:
        out: dict[str, int] = {}
        for h in hits:
            out[h.category] = out.get(h.category, 0) + 1
        return out

    by_cat = _by_category(fp_hits)
    by_cat_a = _by_category(hits_a)
    by_cat_b = _by_category(hits_b)
    e3a_value: float | None = round(_per_wan(len(hits_a)), 3)
    e3b_value: float | None = round(_per_wan(len(hits_b)), 3)
    # 总口径含提示类 + 源文本偏差（白名单剔除项也仍属"引擎报出的命中"）
    e3_all_value: float | None = round(
        _per_wan(len(fp_hits) + len(deviations)), 3)
    e3a_text = (f"可替换类 {len(hits_a)} 处 / {n_chars} 汉字 = "
                f"{_per_wan(len(hits_a)):.2f} 处/万字"
                f"（总口径含提示类 {len(fp_hits)} 处 = "
                f"{_per_wan(len(fp_hits)):.2f}，仅对比）　来源：{neg_src}"
                f"　分类：{by_cat_a or '无'}"
                + (f"　源文本偏差（白名单剔除）{len(deviations)} 处 = "
                   f"{_per_wan(len(deviations)):.2f} 处/万字"
                   if deviations else ""))
    e3b_text = (f"提示类 {len(hits_b)} 处 / {n_chars} 汉字 = "
                f"{_per_wan(len(hits_b)):.2f} 处/万字"
                f"（只报告、不参与门禁）　分类：{by_cat_b or '无'}")
    if external_corpus and not n_chars:
        results.append(Result(
            "E3a", "可自动替换类误报（基础三层）", None,
            f"外部语料为空或不可读（{args.corpus}）：不得把空样本当门禁通过",
            STATUS_FAIL, ungated_reason))
        results.append(_skipped_result(
            "E3b", "提示类命中（只报告）", None,
            f"外部语料为空或不可读（{args.corpus}）：提示类无法统计",
            ungated_reason))
    else:
        if corpus_gated:
            results.append(_gated_result(
                "E3a", "可自动替换类误报（基础三层）", e3a_value, e3a_text,
                _per_wan(len(hits_a)) <= TH_FP_PER_WAN,
                f"　（阈值 ≤{TH_FP_PER_WAN} 处/万字）"))
        else:
            results.append(_skipped_result(
                "E3a", "可自动替换类误报（基础三层）", e3a_value,
                f"{e3a_text}（{UNGATED_MARK}：{ungated_reason}）", ungated_reason))
        e3b_tail = "" if corpus_gated else f"（{UNGATED_MARK}：{ungated_reason}）"
        results.append(_skipped_result(
            "E3b", "提示类命中（只报告）", e3b_value,
            f"{e3b_text}{e3b_tail}（提示类不计入门禁）", "提示类只报告、不参与门禁"))
    e3a_status = results[-2].status
    e3b_status = results[-1].status
    dev_rows = [{"wrong": h.wrong, "suggestion": h.suggestion,
                 "category": h.category, "note": note,
                 "context": s[max(0, h.start - 15):h.end + 15]}
                for s, h, note in deviations]
    data["E3a"] = {"segments": len(neg), "chars": n_chars, "hits": len(hits_a),
                   "per_wan": e3a_value, "by_category": by_cat_a,
                   "threshold": TH_FP_PER_WAN, "corpus": neg_src,
                   "external": external_corpus, "gated": corpus_gated,
                   "status": e3a_status, "seconds": round(t_neg, 3),
                   "source_deviation": {
                       "count": len(deviations),
                       "per_wan": round(_per_wan(len(deviations)), 3),
                       "whitelist": str(args.corpus_whitelist or ""),
                       "items": dev_rows}}
    data["E3b"] = {"segments": len(neg), "chars": n_chars, "hits": len(hits_b),
                   "per_wan": e3b_value, "by_category": by_cat_b,
                   "corpus": neg_src, "external": external_corpus,
                   "gated": False, "status": e3b_status,
                   "note": "提示类只报告，不参与门禁"}
    data["e3_all"] = {"segments": len(neg), "chars": n_chars,
                      "hits": len(fp_hits) + len(deviations),
                      "per_wan": e3_all_value,
                      "by_category": by_cat, "corpus": neg_src,
                      "external": external_corpus, "gated": False,
                      "advisory_categories": sorted(e3_advisory),
                      "note": "旧 E3 总口径（含提示类与源文本偏差），仅对比、不门禁"}
    # 兼容旧消费方：data["E3"] 仍是"所有命中"的总口径（数值与 e3_all 相同），
    # 但门禁只认 E3a；新代码请读 E3a / E3b / e3_all。
    data["E3"] = data["e3_all"]

    # ---------------------------------------------------------- E4 词表外错字
    oov = [(w, c) for w, c in OOV_CASES
           if w not in words and w != c
           and not any(p.wrong == w for p in pairs)]
    oov_ok = sum(1 for w, _c in oov
                 if corrector.check_text(f"各单位要{w}相关规定。"))
    oov_recall = oov_ok / max(1, len(oov))
    e4_text = (f"{oov_ok}/{len(oov)} = {oov_recall:.1%}"
               f"（词表法天花板，增强层应显著提升）")
    if not enh_requested:
        e4_reason = f"profile={profile}：基础门禁不含 E4"
        results.append(_skipped_or_required(
            "E4", "词表外错字召回", round(oov_recall, 4), e4_text,
            e4_reason, args.require_packs))
    elif not pack_any:
        e4_reason = "未安装增强包（拼写/语法均未装），无补位来源"
        results.append(_skipped_or_required(
            "E4", "词表外错字召回", round(oov_recall, 4), e4_text,
            e4_reason, args.require_packs))
    elif not enh_active:
        e4_reason = "增强包已装但推理栈不可用（onnxruntime 缺失）"
        results.append(_skipped_or_required(
            "E4", "词表外错字召回", round(oov_recall, 4), e4_text,
            e4_reason, args.require_packs))
    else:
        results.append(_gated_result(
            "E4", "词表外错字召回", round(oov_recall, 4), e4_text,
            oov_recall >= TH_OOV_RECALL_BASE))
    data["E4"] = {"n": len(oov), "ok": oov_ok, "recall": oov_recall,
                  "gated": enh_active, "status": results[-1].status}

    # ---------------------------------------------------------- E5 耗时
    probe = neg[:40] or list(DEFAULT_NEG)
    probe_chars = sum(cjk_len(s) for s in probe)
    t0 = time.perf_counter()
    for s in probe:
        corrector.check_text(s)
    dt = time.perf_counter() - t0
    ms_per_qian = dt * 1000 / max(1, probe_chars) * 1000
    e5_text = (f"{dt * 1000:.0f} ms / {probe_chars} 汉字"
               f" = {ms_per_qian:.1f} ms/千字")
    if corpus_gated:
        results.append(_gated_result(
            "E5", "基础三层耗时", round(ms_per_qian, 1), e5_text,
            ms_per_qian <= TH_MS_PER_QIAN_BASE,
            f"　（阈值 ≤{TH_MS_PER_QIAN_BASE} ms/千字）"))
    else:
        results.append(_skipped_result(
            "E5", "基础三层耗时", round(ms_per_qian, 1),
            f"{e5_text}（{UNGATED_MARK}：{ungated_reason}）", ungated_reason))
    data["E5"] = {"chars": probe_chars, "seconds": round(dt, 4),
                  "ms_per_qian": ms_per_qian, "gated": corpus_gated,
                  "status": results[-1].status}

    # ---------------------------------------------------------- E6 神经层误报闸门
    e6_chars = 0
    # 未参与门禁的 E6 若同时缺 --corpus，也要带上统一标记（与 E3/E5 一致）。
    e6_tail = "" if corpus_gated else ungated_tail

    def _e6_not_run(reason: str) -> Result:
        """E6 没有真实运行时的结论：普通模式 SKIP；--require-packs 下 FAIL。"""
        if args.require_packs:
            return Result("E6", "开启增强层后新增误报", None,
                          f"未运行（--require-packs：{reason}，增强层验收未通过）",
                          STATUS_FAIL, reason)
        return _skipped_result(
            "E6", "开启增强层后新增误报", None,
            f"未运行（{reason}，不计入门禁）{e6_tail}", reason)

    if not enh_requested:
        e6_reason = f"profile={profile}：不含增强层"
        results.append(_e6_not_run(e6_reason))
        data["E6"] = {"status": results[-1].status, "reason": e6_reason}
    elif not enh_active:
        if not pack_any:
            e6_reason = "未安装增强包（拼写/语法均未装）"
        else:
            e6_reason = "增强包已装但推理栈不可用（onnxruntime 缺失）"
        results.append(_e6_not_run(e6_reason))
        data["E6"] = {"status": results[-1].status, "reason": e6_reason,
                      "l4_ready": can_l4, "l5_ready": can_l5,
                      "l4_error": csc_neural.last_error(),
                      "l5_error": csc_gec.last_error()}
    else:
        _force_settings(can_l4, can_l5)
        t0 = time.perf_counter()
        neural_hits = []
        for s in probe:
            neural_hits.extend(corrector.check_text(s))
        dt_n = time.perf_counter() - t0
        added = [h for h in neural_hits if h.category in ("神经纠错", "语法纠错")]
        per_qian = len(added) / max(1, probe_chars) * 1000
        samples = [f"{h.wrong}→{h.suggestion}" for h in added[:8]]
        e6_chars = probe_chars
        e6_text = (f"新增 {len(added)} 处 / {probe_chars} 汉字 = {per_qian:.1f}"
                   f" 处/千字　耗时 {dt_n:.1f}s　样例：{samples or '无'}")
        if corpus_gated:
            results.append(_gated_result(
                "E6", "开启增强层后新增误报", round(per_qian, 3), e6_text,
                per_qian <= TH_NEURAL_FP_PER_QIAN,
                f"　（阈值 ≤{TH_NEURAL_FP_PER_QIAN} 处/千字）"))
        elif args.require_packs:
            # 增强包验收必须在真实语料上做：缺语料时误报率没有分母可比，
            # 不能拿 224 字内置小样"跑过就算验收"。
            results.append(Result(
                "E6", "开启增强层后新增误报", round(per_qian, 3),
                f"{e6_text}（--require-packs：缺 --corpus，增强层误报无法验收）",
                STATUS_FAIL, "缺 --corpus（--require-packs）"))
        else:
            results.append(_skipped_result(
                "E6", "开启增强层后新增误报", round(per_qian, 3),
                f"{e6_text}（{UNGATED_MARK}：{ungated_reason}）", ungated_reason))
        data["E6"] = {"added": len(added), "chars": probe_chars,
                      "per_qian": per_qian, "seconds": round(dt_n, 3),
                      "samples": samples, "gated": corpus_gated,
                      "status": results[-1].status,
                      "l4_ready": can_l4, "l5_ready": can_l5,
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
    gated = [r for r in results if not r.skipped]
    failed = [r for r in results if r.status == STATUS_FAIL]
    skipped = [r for r in results if r.skipped]
    print(f"门禁项：通过 {len(gated) - len(failed)}/{len(gated)} 项；"
          f"未达标：{', '.join(r.key for r in failed) or '无'}")
    if skipped:
        detail = "；".join(f"{r.key}（{r.reason}）" for r in skipped)
        print(f"未参与门禁（SKIP，不计 FAIL / 不影响退出码）：{detail}")
    if not external_corpus:
        print(f"说明：E3/E5/E6 缺 --corpus，仅{UNGATED_MARK}；"
              "发布门禁必须用 --corpus 指向本单位真实公文语料"
              "（语料需先过 scripts/corpus_check.py 体检，见 doc/corpus/README.md）。")
    if args.require_packs:
        print("说明：--require-packs 下 E4/E6 必须真实运行（增强包 + 真实语料）；"
              "任一 FAIL 表示增强层验收未完成。")
    print(f"E3 口径：E3a 可自动替换类（硬门禁 ≤{TH_FP_PER_WAN} 处/万字）＋ "
          f"E3b 提示类（{'/'.join(sorted(e3_advisory))}，只报告、不参与门禁）；"
          "JSON 的 e3_all 保留旧总口径便于对比。")
    print(f"退出码：{1 if failed else 0}（{'有门禁项未达标' if failed else '门禁通过'}）")

    # ---------------------------------------------------------- JSON 落盘
    sample_chars = {"E3": n_chars, "E3a": n_chars, "E3b": n_chars,
                    "E5": probe_chars, "E6": e6_chars}
    data["sample_chars"] = sample_chars
    data["require_packs"] = bool(args.require_packs)
    if args.json:
        payload = {
            "profile": profile,
            "layers": sorted(layers),
            "require_packs": bool(args.require_packs),
            "corpus_manifest": args.corpus_manifest,
            "corpus": {**corpus_payload, "manifest_path": args.corpus_manifest},
            "sample_chars": sample_chars,
            "results": [r.to_json() for r in results],
            "data": data,
        }
        Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已写出：{args.json}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
