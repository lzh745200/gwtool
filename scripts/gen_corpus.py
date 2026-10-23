# -*- coding: utf-8 -*-
"""合成回归语料生成器（**合成数据**；不得用于 E3 精度声明）。

用途
----
* **E5 吞吐**：造大量规范写法公文，压纠错/体检/提示链路的耗时；
* **E2 召回**：``--inject-errors`` 按 ``corrector_data.CURATED_PAIRS`` 注入已知
  错形，并输出 ``labels.json``（正确写法 / 错形 / 偏移），供召回评测逐条核对；
* **跨平台 smoke**：同一 seed 在任何平台产出**逐字节一致**的语料，便于比对。

⚠ 这些文件是**模板拼装的合成语料**，不是真实公文：
  不能拿它声明纠错精度（E3 的负样本必须来自真实语料）；``manifest.json``
  与每份文末都带「合成」标注。

素材只用仓库既有数据
--------------------
* ``skeletons.SKELETONS``（12 文种骨架）；
* ``style_data.LEGAL_SAMPLES``（10 份法定文种示意样张）；
* ``writing_data.PHRASE_BANK``（写作句式库，按文种功能分组填充正文）；
* ``corrector_data.CURATED_PAIRS``（仅用于注入错形）。

占位规范（防 PII，也避免被误认为真实公文）
------------------------------------------
* 单位：``××市`` / ``××局``（如「××市人民政府」「××市××局」）
* 文号：``×政发〔2026〕N号``（命令按体例用令号「第N号」；纪要不写编号）
* 日期：``2026年×月×日``
* 人名：``××`` / ``×××``
不生成身份证号、手机号、邮箱、真实单位名与真实人名（见 tests 的正则扫描）。

确定性
------
只用 ``random.Random(seed)``；文件以 UTF-8 + 固定 ``\n`` 写出，同 seed/同 count
两次生成的每个文件与 ``manifest.json`` / ``labels.json`` 逐字节一致。

用法
----
    python scripts/gen_corpus.py --out DIR --count 60 --seed 20261101
    python scripts/gen_corpus.py --out DIR --count 20 --seed 7 --inject-errors
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:          # 直接 `python scripts/gen_corpus.py` 时
    sys.path.insert(0, str(ROOT))

from gwtool.core import corrector_data
from gwtool.core import skeletons
from gwtool.core import style_data
from gwtool.core import writing_data

GENERATOR_VERSION = "1.0.0"
SYNTHETIC_MARK = "合成语料"
SYNTHETIC_NOTE = ("合成语料：仅供 E5 吞吐 / E2 召回 / 跨平台 smoke 回归使用，"
                  "不得用于 E3 精度声明（非真实公文）")
FOOTER = "（合成语料：仅供回归测试，非真实公文）"

#: 有骨架 + 有示意样张的 10 个法定文种；骨架库另有通令/指示/通告（无样张）。
SAMPLE_KINDS = tuple(style_data.LEGAL_SAMPLES)
SKELETON_ONLY_KINDS = tuple(k for k in skeletons.kinds() if k not in SAMPLE_KINDS)
ALL_KINDS = tuple(skeletons.kinds()) + tuple(
    k for k in SAMPLE_KINDS if k not in skeletons.kinds())

# ---------------------------------------------------------------- 占位素材
ORGS = ("××市人民政府", "××市××局", "××县人民政府")
RECIPIENTS = ("各区县人民政府，市政府各部门", "各有关单位", "市属各企事业单位")
MATTERS = (
    "进一步加强安全生产工作",
    "做好年度重点工作",
    "推进基层治理能力建设",
    "规范公文办理工作",
    "提升政务服务水平",
    "加强和改进调查研究工作",
)
#: 「……」槽位的通用填充词（名词短语；不含任何真实单位/人名）
TOPICS = (
    "安全生产工作", "重点任务落实", "基层基础建设", "政务服务水平",
    "风险隐患整治", "干部队伍建设", "制度机制建设", "工作作风建设",
    "群众反映强烈的突出问题", "高质量发展",
)
#: 「要……」类槽位的动宾短语
ACTIONS = (
    "压实工作责任", "细化工作措施", "加强督促检查", "健全工作机制",
    "完善制度体系", "抓好任务落实", "强化统筹协调", "严格考核问责",
)
#: 按上下文挑词，避免「贯彻落实政务服务水平精神」这类搭配硬伤
ASPECTS = ("上级会议", "有关文件")                    # 贯彻落实……精神
LOCALITIES = ("工作", "本地", "本单位")               # 结合……实际
LEVELS = ("政务服务", "安全生产管理", "基层治理", "公文办理")  # ……水平
WORKS = ("安全生产", "基层治理", "风险隐患整治", "重点任务推进")  # ……工作
TIME_POINTS = ("本月底", "年底", "下月底")            # ……前
REPORT_TO = ("××市人民政府办公室", "××市××局", "上级主管部门")  # 报送……
FILLER_GROUPS = ("背景（形势与问题）", "保障（组织与落实）")
#: 《……」》槽位的文件类名（虚构、泛指）
DOC_TITLES = ("有关制度文件", "有关工作规定", "有关管理办法", "有关实施方案")
#: 人名占位
PERSONS = ("××", "×××")

_DATE_RE = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日")
_YEAR_RE = re.compile(r"(?<!\d)20\d{2}(?=年)")
_DOCNO_RE = re.compile(r"[×A-Za-z\u4e00-\u9fff]{1,12}〔20\d{2}〕\d{1,4}号")
_QUOTED_RE = re.compile(r"《[^》]*》")
_PUNCT_ONLY_RE = re.compile(r"^[\s。！？；：，、（）《》]+$")


# ================================================================ 通用工具
def char_count(text: str) -> int:
    """字数口径：非空白字符数（与"每份 800~2000 字"的验收口径一致）。"""
    return sum(1 for ch in text if not ch.isspace())


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_text(path: Path, text: str) -> None:
    """固定 UTF-8 + ``\\n`` 写出（避免 Windows 下 os.linesep 破坏跨平台字节一致）。"""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _write_json(path: Path, payload) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    _write_text(path, text + "\n")


# ================================================================ 占位符归一
def _normalize_placeholders(text: str, rng: random.Random) -> str:
    """把样张里"像真实数据"的成分统一换成约定的占位形态。"""
    text = _DATE_RE.sub("2026年×月×日", text)
    text = _YEAR_RE.sub("2026", text)
    text = _DOCNO_RE.sub(f"×政发〔2026〕{rng.randint(1, 99)}号", text)
    # 骨架正文里的部队条例全称含真实机构名：换成占位符（合成语料不留真实主体）
    text = text.replace("中国人民解放军", "×××")
    return text


def _slot_value(out: str, at: int, rng: random.Random,
                used_values: set[str]) -> str:
    """按「槽位前后紧邻的词」挑填充词，让拼出来的句子读得通。"""
    after = out[at + 2:at + 4]
    before = out[max(0, at - 3):at]
    if after.startswith("前"):
        pool: tuple[str, ...] = TIME_POINTS
    elif after.startswith(("书面报送", "报送")):
        pool = REPORT_TO
    elif before.endswith("由"):
        pool = ("××市××局", "××市人民政府办公室")
    elif after.startswith("牵头"):
        pool = ("××市××局", "××市人民政府办公室")
    elif after.startswith("配合"):
        pool = ("有关部门", "相关单位")
    elif after.startswith("同志"):
        pool = PERSONS
    elif after.startswith("精神"):
        pool = ASPECTS
    elif after.startswith("实际"):
        pool = LOCALITIES
    elif after.startswith("水平"):
        pool = LEVELS
    elif after.startswith("工作"):
        pool = WORKS
    elif after.startswith("与"):
        pool = ACTIONS
    elif before.endswith(("既", "又", "一是", "二是", "三是", "四是", "与")):
        pool = ACTIONS
    else:
        pool = TOPICS
    choices = [v for v in pool if v not in used_values] or list(pool)
    value = rng.choice(choices)
    used_values.add(value)
    return value


def _expand_phrase(template: str, rng: random.Random) -> str:
    """把句式库模板里的 ``……`` / ``《……》`` 换成本生成器的通用占位词。

    同一模板内的多个槽位尽量取不同词（「一是…二是…三是…」不会三条重复）。
    """
    def _quoted(m: re.Match) -> str:
        return "《" + rng.choice(DOC_TITLES) + "》"

    out = _QUOTED_RE.sub(_quoted, template)
    used_values: set[str] = set()
    while "……" in out:
        at = out.index("……")
        value = _slot_value(out, at, rng, used_values)
        out = out[:at] + value + out[at + 2:]
    return out


_BRACKET_RE = re.compile(r"【[^】]{1,16}】")


def _bracket_phrase(inner: str, rng: random.Random,
                    used: set[str]) -> str:
    """把骨架占位名换成**短语**（不是整句），保证拼接后句子读得通。

    骨架里的 【主持人】【列席范围】 是嵌在一句话中间的，塞整句会拼出
    「……通知如下出席会议并讲话」这类断句错误，所以这里按占位名给短语。
    """
    if "汇报" in inner:
        return "有关工作情况汇报"
    if "要求" in inner and ("时限" in inner or "完成" in inner):
        return "抓紧抓好落实，于本月底前完成"
    if any(k in inner for k in ("主持", "对象")):
        pool: tuple[str, ...] = PERSONS
    elif any(k in inner for k in ("列席", "范围", "人员")):
        return "各有关部门负责同志"
    elif any(k in inner for k in ("审批机关", "机关")):
        return "××市××局"
    elif any(k in inner for k in ("时限", "节点", "时间")):
        return "本月底前"
    elif any(k in inner for k in ("情况", "问题", "背景", "内容")):
        return "工作开展情况"
    elif any(k in inner for k in ("评价", "意见", "建议")):
        return "总体推进有序"
    elif any(k in inner for k in ("奖励", "处分", "表扬", "批评")):
        return "××市××局"
    else:
        pool = ACTIONS
    choices = [v for v in pool if v not in used] or list(pool)
    value = rng.choice(choices)
    used.add(value)
    return value


def _fill_brackets(text: str, rng: random.Random) -> str:
    """把骨架里的 【占位】 换成短语（否则正文会残留方括号）。"""
    used: set[str] = set()
    return _BRACKET_RE.sub(lambda m: _bracket_phrase(m.group(0)[1:-1],
                                                    rng, used), text)


def _usable_phrase(template: str) -> bool:
    """过滤会引入真实日期/文号形态或不可读的模板。"""
    if any(ch in template for ch in "〔〕月日号"):
        return False
    return len(template) >= 10 and not _PUNCT_ONLY_RE.match(template)


def _bank_sentences(kind: str, rng: random.Random) -> list[str]:
    """按文种功能分组取句式库，展开成可读句子（只用仓库既有模板）。

    **不用「结尾（结束语）」组**：结束语只在文末出现一次，混进正文填充会
    造成"文末套语出现在段落中间"的体例错误。
    """
    groups = [g for g in (writing_data.KIND_GROUPS.get(kind)
                          or tuple(writing_data.PHRASE_BANK))
              if g != "结尾（结束语）"]
    groups.extend(FILLER_GROUPS)
    out: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for template in writing_data.PHRASE_BANK.get(group, ()):
            if not _usable_phrase(template):
                continue
            sentence = _expand_phrase(template, rng)
            norm = _norm_closing(sentence)
            # 结束语只在文末出现一次：正文填充里不能出现，也不能"夹带"
            # （如「专此函达，请予复函。」整句不在词表里、但含词表套语）。
            if norm in _CLOSING_POOL or any(
                    len(c) >= 4 and c in sentence for c in _CLOSING_POOL):
                continue
            if sentence not in seen:
                seen.add(sentence)
                out.append(sentence)
    return out


# ================================================================ 文档骨架
def _skeleton_text(kind: str, rng: random.Random) -> str:
    sk = skeletons.get(kind)
    return sk.render(org=rng.choice(ORGS), matter=rng.choice(MATTERS),
                     recipients=rng.choice(RECIPIENTS), date="2026年×月×日")


def _header_index(lines: list[str]) -> int:
    """标题行下标（首个非空行）。"""
    for i, line in enumerate(lines):
        if line.strip():
            return i
    return 0


_DOC_NO_LINE_RE = re.compile(r"^第\d+号$", re.M)


def _insert_doc_number(lines: list[str], doc_no: int, kind: str) -> list[str]:
    """按文种体例补编号行（占位形态，不生成真实文号）。

    * 普通公文：标题下写 ``×政发〔2026〕N号``；
    * 命令：体例用令号 ``第N号``（样张自带时替换为占位序号），不写发文字号；
    * 纪要：按体例**不写**发文字号/编号（会议纪要另行分送印发）。
    """
    if kind == "纪要":
        return lines
    idx = _header_index(lines)
    text = "\n".join(lines)
    if kind == "命令":
        if _DOC_NO_LINE_RE.search(text):
            return [_DOC_NO_LINE_RE.sub(f"第{doc_no}号", ln) for ln in lines]
        return lines[:idx + 1] + [f"第{doc_no}号"] + lines[idx + 1:]
    if "×政发〔2026〕" in text:
        return lines
    return lines[:idx + 1] + [f"×政发〔2026〕{doc_no}号"] + lines[idx + 1:]


def _tail_index(lines: list[str], closing_pool: set[str]) -> int:
    """正文插入点：结束语之前；无结束语时退到落款/署名尾块之前。"""
    for i in range(len(lines) - 1, -1, -1):
        if _norm_closing(lines[i]) in closing_pool:
            return i
    # 样张的结束语可能带语气词/多分句（如「专此函达，请予复函。」），
    # 精确匹配不到时按"含结束语短语"再找一次，保证正文补在结束语之前。
    for i in range(len(lines) - 1, -1, -1):
        norm = _norm_closing(lines[i])
        if any(len(c) >= 4 and c in norm for c in closing_pool):
            return i
    for i in range(len(lines) - 1, -1, -1):
        s = lines[i].strip()
        if not s:
            continue
        if ("2026年×月×日" in s or s.startswith(("××", "市长", "出席", "列席"))
                or s.endswith("同志")):
            continue
        return i + 1
    return len(lines)


def _norm_closing(line: str) -> str:
    """结束语比对口径：去空白、去句末标点（词表里有的带「。」有的不带）。"""
    return line.strip().rstrip("。！？；")


def _closing_pool() -> set[str]:
    pool = {_norm_closing(s) for s in
            writing_data.PHRASE_BANK.get("结尾（结束语）", ())}
    for sk in skeletons.SKELETONS.values():
        if sk.closing:
            pool.add(_norm_closing(sk.closing))
    for closings in style_data.LEGAL_CLOSINGS.values():
        pool.update(_norm_closing(c) for c in closings)
    return pool


_CLOSING_POOL = _closing_pool()


def _paragraph(rng: random.Random, sentences: list[str],
               used: set[str]) -> str:
    """拼一个 2~4 句的规范段落；优先用本文件还没用过的句子，减少机械重复。"""
    pool = [s for s in sentences if s not in used] or list(sentences)
    n = min(len(pool), rng.randint(2, 4))
    picks = rng.sample(pool, n)
    used.update(picks)
    return "".join(picks)


def _base_text(kind: str, rng: random.Random) -> str:
    """选一个基底：一半用法定文种示意样张，一半用骨架渲染（两套素材都覆盖）。"""
    has_skeleton = kind in skeletons.SKELETONS
    if kind in SAMPLE_KINDS and (not has_skeleton or rng.random() < 0.5):
        raw = style_data.LEGAL_SAMPLES[kind]
    else:
        raw = _skeleton_text(kind, rng)
    raw = _normalize_placeholders(raw, rng)
    lines = [ln.rstrip() for ln in raw.split("\n")]
    return "\n".join(_insert_doc_number(lines, rng.randint(1, 99), kind))


def build_document(kind: str, rng: random.Random,
                   max_chars: int = 2000) -> str:
    """生成一份规范写法的合成公文（≤ max_chars 字，文末带合成标注）。"""
    sentences = _bank_sentences(kind, rng)
    base = _fill_brackets(_base_text(kind, rng), rng)
    lines = base.split("\n")
    cut = _tail_index(lines, _CLOSING_POOL)
    head, tail = lines[:cut], lines[cut:]

    target = rng.randint(1000, 1700)
    paras: list[str] = []
    used: set[str] = set()
    while char_count("\n".join(head + paras + tail + [FOOTER])) < target:
        paras.append(_paragraph(rng, sentences, used))
        if len(paras) > 60:       # 防御：素材异常时不至于死循环
            break
    text = "\n".join(head + paras + tail + ["", FOOTER]) + "\n"
    # 篇幅校正：超过上限就丢掉最后补的段落；不足 800 字继续补
    guard = 0
    while char_count(text) > max_chars and paras and guard < 60:
        paras.pop()
        text = "\n".join(head + paras + tail + ["", FOOTER]) + "\n"
        guard += 1
    while char_count(text) < 800 and guard < 120:
        paras.append(_paragraph(rng, sentences, used))
        text = "\n".join(head + paras + tail + ["", FOOTER]) + "\n"
        guard += 1
    return text


# ================================================================ 错形注入
def _injectable_pairs() -> list[tuple[str, str, str, float]]:
    """只挑纯汉字、长度适中的词对：注入后上下文可读，召回核对也稳定。"""
    out = []
    for wrong, correct, category, confidence in corrector_data.CURATED_PAIRS:
        if not wrong or not correct or wrong == correct:
            continue
        if not re.fullmatch(r"[\u4e00-\u9fff]{2,6}", wrong):
            continue
        if not re.fullmatch(r"[\u4e00-\u9fff]{2,6}", correct):
            continue
        out.append((wrong, correct, category, confidence))
    return out


_INJECTABLE = _injectable_pairs()


def inject_errors(text: str, rng: random.Random,
                  per_doc: int = 3):
    """在文档尾部注入已知错形；返回 (新文本, 标签列表)。

    标签 ``offset`` 是**注入后全文**的字符下标（与 ``corrector.Correction.start``
    同一口径），召回评测据此逐条核对。

    注入句固定为「注意事项：<正确写法>到位，确保各项任务落实。」——冒号与
    「到位」把错形夹在分词边界上，避免被纠错引擎的"跨词/词内不报"门控滤掉
    （实测：前缀写「各单位要」时，两字词对被 jieba 并词后漏报，召回仅 91%；
    换成冒号起句后同一批词对召回 100%）。错形只替换正确写法本身，因此偏移
    可以用"句块起点 + 句内相对位置"精确推导。
    """
    if not _INJECTABLE:
        return text, []
    picks = rng.sample(_INJECTABLE, min(per_doc, len(_INJECTABLE)))
    wrong_sentences: list[str] = []
    metas: list[tuple[str, str, str, float, int]] = []
    for wrong, correct, category, confidence in picks:
        sentence = f"注意事项：{correct}到位，确保各项任务落实。"
        rel = sentence.index(correct)
        wrong_sentences.append(
            sentence[:rel] + wrong + sentence[rel + len(correct):])
        metas.append((wrong, correct, category, confidence, rel))
    block = "".join(wrong_sentences)
    # 注入段落在合成标注之前（正文末尾），标注始终保持最后一行
    lines = text.rstrip("\n").split("\n")
    if lines and lines[-1].startswith("（" + SYNTHETIC_MARK):
        new_text = "\n".join(lines[:-1] + [block, ""] + [lines[-1]]) + "\n"
    else:
        new_text = text + block + "\n"
    block_start = new_text.index(block)
    labels: list[dict] = []
    cursor = 0
    for (wrong, correct, category, confidence, rel), sentence in zip(
            metas, wrong_sentences):
        labels.append({
            "wrong": wrong,
            "correct": correct,
            "offset": block_start + cursor + rel,
            "length": len(wrong),
            "category": category,
            "confidence": confidence,
        })
        cursor += len(sentence)
    return new_text, labels


# ================================================================ 生成入口
def _clean_previous(out_dir: Path) -> None:
    """只清理**本生成器自己**产出的文件；目录里别人的文件一律不碰。"""
    manifest = out_dir / "manifest.json"
    if not manifest.exists():
        return
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return
    if not payload.get("synthetic"):
        return
    for item in payload.get("files", []):
        target = out_dir / item.get("name", "")
        if target.is_file() and target.parent == out_dir:
            target.unlink()
    for extra in ("labels.json",):
        candidate = out_dir / extra
        if candidate.is_file():
            candidate.unlink()
    manifest.unlink()


def generate(out_dir: Path | str, count: int = 60, seed: int = 20261101,
             inject_errors_flag: bool = False) -> dict:
    """生成合成语料并落盘，返回 manifest 字典（纯函数式，便于测试）。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _clean_previous(out)
    count = max(1, int(count))
    rng = random.Random(seed)
    files: list[dict] = []
    all_labels: list[dict] = []
    total_chars = 0
    for i in range(count):
        kind = ALL_KINDS[i % len(ALL_KINDS)]
        doc_rng = random.Random(rng.getrandbits(64))
        # 注入模式给错形句块预留篇幅，保证注入后仍在 800~2000 字区间内
        text = build_document(kind, doc_rng,
                              max_chars=1880 if inject_errors_flag else 2000)
        labels: list[dict] = []
        if inject_errors_flag:
            text, labels = inject_errors(text, doc_rng)
        name = f"synth_{i + 1:03d}_{kind}.txt"
        _write_text(out / name, text)
        total_chars += char_count(text)
        files.append({
            "name": name,
            "kind": kind,
            "chars": char_count(text),
            "sha256": _sha256(text),
            "injections": len(labels),
        })
        for label in labels:
            label["file"] = name
            label["kind"] = kind
            all_labels.append(label)
    manifest = {
        "synthetic": True,
        "warning": SYNTHETIC_NOTE,
        "marked_as": SYNTHETIC_MARK,
        "generator": {"name": "scripts/gen_corpus.py",
                      "version": GENERATOR_VERSION},
        "seed": int(seed),
        "count": count,
        "injected": bool(inject_errors_flag),
        "totals": {"docs": count, "chars": total_chars,
                   "injections": len(all_labels)},
        "files": files,
    }
    if inject_errors_flag:
        manifest["labels"] = "labels.json"
        _write_json(out / "labels.json", {
            "synthetic": True,
            "warning": SYNTHETIC_NOTE,
            "seed": int(seed),
            "total": len(all_labels),
            "items": all_labels,
        })
    _write_json(out / "manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="生成合成公文回归语料（合成数据，不得用于精度声明）")
    parser.add_argument("--out", required=True, help="输出目录")
    parser.add_argument("--count", type=int, default=60, help="份数（默认 60）")
    parser.add_argument("--seed", type=int, default=20261101, help="随机种子")
    parser.add_argument("--inject-errors", action="store_true",
                        help="注入 CURATED_PAIRS 错形并输出 labels.json")
    args = parser.parse_args(argv)
    manifest = generate(args.out, args.count, args.seed, args.inject_errors)
    print(json.dumps({
        "out": str(Path(args.out)),
        "synthetic": True,
        "count": manifest["count"],
        "chars": manifest["totals"]["chars"],
        "injections": manifest["totals"]["injections"],
        "seed": manifest["seed"],
        "version": manifest["generator"]["version"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
