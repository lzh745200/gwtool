# -*- coding: utf-8 -*-
"""骨架槽位抽取质量回归（v1.10 / P1-20）：机构名精确率与"防吞句"。

审计背景（doc/_audit_v19/03_writing.md §P1-3）
------------------------------------------------
11 句人工标注集上，槽位**类型**召回 11/12，但**实体级精确率仅 9/14**：
4 个含机构名的句子 org 精确率 **0/4** ——

  1. 36 字符整句被吞成 ``{org}``（跨过了「，」「。」）；
  2. "市生态环境局" 完全抽不到（jieba 切成 ``生态环境`` + ``局``，单字被丢）；
  3. "市住房和城乡建设局" 截成 "城乡建设局"（``_ORG_STOP`` 含「和」）；
  4. "由市教育局…" 截成 "教育局"（介词与地名被分词器粘成 ``由市``）；
  另外「各单位」在 4 例中有 2 例被误抽成单位名（泛化指代）。

修复后本文件锁死以下契约（数字均为本机可复跑值）：

    指标                      修复前(HEAD)   修复后
    实体级精确率               9/14          14/14
    类型召回                   11/12         12/12
    含机构名句子的 org 精确率   0/4            4/4
    多余 org（误抽）            5 处           0 处
    最长 org span              36 字符        ≤20 且不含句读

标注集说明（**重要**）：审计用的那 11 句原始标注未入库，本文件的 11 句是按其
文档列出的 5 条失败形态重建的，且已校准到修复前恰好复现 9/14、11/12、0/4
（逐条见 ``LABELED_SENTENCES`` 的注释），因此前后数字与审计口径可直接对比。

覆盖的正/反例共 40+ 句：11 句标注集 + 23 条机构名定点用例（15 正 / 8 反）
+ 8 条其它槽位定点用例 + 1 条普通句。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from gwtool.core import paragraph_ref as pr

ROOT = Path(__file__).resolve().parents[1]

# 标注集：(编号, 句子, [(槽位类型, 期望实体原文), ...])
# 修复前（HEAD）的失败点用行尾注释标出，便于后人核对"标注集数字从哪来"。
LABELED_SENTENCES: list[tuple[str, str, list[tuple[str, str]]]] = [
    # ① 介词与地名被分词器粘成「由市」→ 旧实现只抽到「教育局」
    ("L1", "本次排查由市教育局牵头，各区县配合。",
     [("org", "市教育局")]),
    # ② 单字后缀 token 被丢弃 → 旧实现完全抽不到（类型也漏）
    ("L2", "市生态环境局牵头开展专项排查。",
     [("org", "市生态环境局")]),
    # ③ 旧 `_ORG_STOP` 含「和」→ 旧实现截成「城乡建设局」
    ("L3", "市住房和城乡建设局负责项目审批。",
     [("org", "市住房和城乡建设局")]),
    # ④ 旧实现跨句读左合并 → 36 字符整句被吞
    ("L4", "排查范围包括校外培训机构，重点检查校舍建筑、消防设施等方面。"
           "各区县教育局要结合实际制定方案。",
     [("org", "各区县教育局")]),
    ("L5", "请各单位于2026年10月20日前完成整改。",
     [("date", "2026年10月20日")]),
    # ⑤ 旧数量规则不支持小数：3.5万元被截成「5万元」（类型召回但实体错）
    ("L6", "本次改造投入3.5万元，收到良好效果。",
     [("count", "3.5万元")]),
    ("L7", "依据《关于进一步加强安全生产工作的意见》（安委〔2026〕3号）抓好落实。",
     [("cited", "《关于进一步加强安全生产工作的意见》（安委〔2026〕3号）")]),
    ("L8", "全区共排查企业120个、场所35项、培训8次。",
     [("count", "120个"), ("count", "35项"), ("count", "8次")]),
    ("L9", "会议决定自2026年11月1日起施行。",
     [("date", "2026年11月1日")]),
    ("L10", "现将《××市安全生产专项整治行动方案》印发给你们。",
     [("cited", "《××市安全生产专项整治行动方案》")]),
    ("L11", "各单位要落实责任，确保3个工作日内反馈，并于2026年12月31日前报送。",
     [("deadline", "3个工作日内"), ("date", "2026年12月31日")]),
]

# 机构名定点用例：(句子, 期望抽到的机构名列表)。期望 [] 表示**不得**抽。
ORG_CASES: list[tuple[str, list[str]]] = [
    # —— 审计点名的三类失败 ——
    ("排查范围包括校外培训机构，重点检查校舍建筑、消防设施等方面。"
     "各区县教育局要结合实际制定方案。", ["各区县教育局"]),
    ("市生态环境局牵头开展专项排查。", ["市生态环境局"]),
    ("市住房和城乡建设局负责项目审批。", ["市住房和城乡建设局"]),
    ("本次排查由市教育局牵头，各区县配合。", ["市教育局"]),
    # —— 名称内部的「和」不能截断，但并列结构必须截断 ——
    ("市文化和旅游局、市卫生健康委员会联合印发方案。",
     ["市文化和旅游局", "市卫生健康委员会"]),
    ("国家发展和改革委员会负责统筹。", ["国家发展和改革委员会"]),
    ("市教育局和各区县教育局要密切配合。", ["市教育局", "各区县教育局"]),
    ("市住房和城乡建设局、市生态环境局联合开展检查。",
     ["市住房和城乡建设局", "市生态环境局"]),
    # —— 占位写法 / 地市前缀 ——
    ("请各单位报××市教育局汇总。", ["××市教育局"]),
    ("××市教育局负责落实。", ["××市教育局"]),
    ("现将有关情况报送长沙市教育局。", ["长沙市教育局"]),
    ("区县教育局要制定方案。", ["区县教育局"]),
    # —— 名称以停用字开头也要能抽（发展/交通是名称词，不是动词） ——
    ("交通局负责道路运输管理。", ["交通局"]),
    ("发展和改革委员会负责统筹。", ["发展和改革委员会"]),
    ("市交通运输局、市发展和改革委员会联合印发。",
     ["市交通运输局", "市发展和改革委员会"]),
    # —— 泛化指代与普通词不得抽（审计实测误抽 2/4 的「各单位」在这里） ——
    ("请各单位于3个工作日内完成整改。", []),
    ("各单位要落实责任，确保3个工作日内反馈。", []),
    ("本次工作全部完成。", []),
    ("到处都要注意安全。", []),
    ("好处很多。", []),
    ("各种干部的培养很重要。", []),
    ("这是一句普通的话。", []),
    ("各有关部门要密切配合。", []),
]

# 审计验收原文：36 字符 → ≤12 字符且不含句读。
_SWALLOW_TEXT = ("排查范围包括校外培训机构，重点检查校舍建筑、消防设施等方面。"
                 "各区县教育局要结合实际制定方案。")
_PUNCT = set("，。、；：（）()《》「」")


def _slots(text: str) -> list[tuple[str, str]]:
    """(类型, 实体原文)；org2/org3 归一成 org，便于按类型比对。"""
    return [(re.sub(r"\d+$", "", s.name), s.example)
            for s in pr.extract_slots(text)]


def _orgs(text: str) -> list[str]:
    return [s.example for s in pr.extract_slots(text) if s.name.startswith("org")]


# ---------------------------------------------------------------- 标注集总账
def test_labeled_set_entity_precision():
    """实体级精确率：14 个标注实体必须**逐个逐字**抽对（修复前 9/14）。"""
    ok, total, bad = 0, 0, []
    for lid, text, labels in LABELED_SENTENCES:
        got = _slots(text)
        for typ, expected in labels:
            total += 1
            if any(t == typ and g == expected for t, g in got):
                ok += 1
            else:
                bad.append((lid, typ, expected, [g for t, g in got if t == typ]))
    assert total == 14, f"标注集实体数应为 14，实为 {total}"
    assert ok == 14, f"实体精确率退化为 {ok}/{total}：{bad}"


def test_labeled_set_type_recall():
    """类型召回：12 个类型标注全部识别（修复前 11/12，漏的是 org）。"""
    got_all, want = 0, 0
    missed = []
    for lid, text, labels in LABELED_SENTENCES:
        got_types = {t for t, _g in _slots(text)}
        for typ in {t for t, _ in labels}:
            want += 1
            if typ in got_types:
                got_all += 1
            else:
                missed.append((lid, typ))
    assert want == 12, f"标注集类型数应为 12，实为 {want}"
    assert got_all == 12, f"类型召回退化为 {got_all}/{want}：{missed}"


def test_labeled_set_org_precision_and_no_false_positive():
    """含机构名的 4 句 org 精确率必须 4/4（修复前 0/4），且不得有多余 org。"""
    orgs = [(lid, o) for lid, text, labels in LABELED_SENTENCES
            for o in _orgs(text)
            if any(t == "org" for t, _ in labels)]
    labeled = {expected for _lid, text, labels in LABELED_SENTENCES
               for t, expected in labels if t == "org"}
    assert len(labeled) == 4
    got_orgs = {o for _lid, o in orgs}
    assert got_orgs == labeled, f"org 实体不匹配：抽到 {got_orgs}，期望 {labeled}"
    extras = [(lid, o) for lid, text, _labels in LABELED_SENTENCES
              for o in _orgs(text) if o not in labeled]
    assert not extras, f"标注集出现多余 org（泛化误抽）：{extras}"


# ---------------------------------------------------------------- 定点用例
@pytest.mark.parametrize(("text", "expected"), ORG_CASES)
def test_org_extraction_cases(text, expected):
    """20 条机构名定点用例（12 正 / 8 反），逐条比对（顺序即出现顺序）。"""
    assert _orgs(text) == expected


def test_audit_swallow_case_is_cut_at_sentence_punctuation():
    """审计原文用例：36 字符整句 → 只抽「各区县教育局」，且长度 ≤12、不含句读。"""
    spans = pr._find_org_spans(_SWALLOW_TEXT)
    got = [_SWALLOW_TEXT[s:e] for s, e, _n, _l in spans]
    assert got == ["各区县教育局"], f"仍然吞并：{got}"
    for s, e, _n, _l in spans:
        piece = _SWALLOW_TEXT[s:e]
        assert len(piece) <= 12, f"org span 过长：{piece!r}"
        assert not (set(piece) & _PUNCT), f"org span 含句读：{piece!r}"


# ---------------------------------------------------------------- 不变量
def _doc_lines() -> list[tuple[str, str]]:
    files = sorted((ROOT / "doc").glob("*.md"))
    assert len(files) >= 15, f"doc/*.md 语料缺失（仅 {len(files)} 份）"
    out: list[tuple[str, str]] = []
    for f in files:
        text = f.read_text(encoding="utf-8", errors="ignore")
        out.extend((f.name, ln.strip()) for ln in text.splitlines() if ln.strip())
    return out


def test_org_span_invariants_on_real_corpus():
    """真实长文语料上：每个 org span 都满足长度/字符约束，且抽取没被整体关掉。

    这是"防吞句"的**全量**兜底：无论分词怎么切，输出都不允许再出现
    "36 字符 + 句读"的 span；同时要求仍有足量正常机构名被抽出（≥30 处），
    防止修法退化成"永不抽 org"。
    """
    total = 0
    offenders = []
    for name, line in _doc_lines():
        for s, e, _n, _l in pr._find_org_spans(line):
            total += 1
            piece = line[s:e]
            if not (2 <= len(piece) <= pr._ORG_MAX_LEN):
                offenders.append((name, piece, "长度"))
            if set(piece) & set("，。、；：（）()《》「」\n"):
                offenders.append((name, piece, "含句读"))
    assert not offenders, f"语料上出现越界 org span：{offenders[:5]}"
    assert total >= 30, f"语料上只抽出 {total} 处 org，疑似抽取被整体削弱"


# ---------------------------------------------------------------- 其它槽位
# 这几条锁死"槽位抽取"的其余五类：本次只收紧机构名，date/deadline/count/
# cited/matter 的抽取口径**不得**回退（数字/日期/引用一旦漂移，骨架回填会
# 切错位）。
OTHER_SLOT_CASES: list[tuple[str, str, str]] = [
    ("本通知自2026年8月15日起执行。", "date", "2026年8月15日"),
    ("请于3个工作日内反馈。", "deadline", "3个工作日内"),
    ("共评选先进个人20名。", "count", "20名"),
    ("共排查企业120个。", "count", "120个"),
    ("本次改造投入3.5万元。", "count", "3.5万元"),
    ("新增设备10万台。", "count", "10万台"),
    ("依据《安全生产管理办法》（安办〔2026〕3号）执行。", "cited",
     "《安全生产管理办法》（安办〔2026〕3号）"),
    ("××局关于安全生产的通知", "matter", "安全生产"),
]


@pytest.mark.parametrize(("text", "slot_type", "expected"), OTHER_SLOT_CASES)
def test_other_slots_not_regressed(text, slot_type, expected):
    hits = [g for t, g in _slots(text) if t == slot_type]
    assert expected in hits, f"{slot_type} 槽位回退：抽到 {hits}，期望 {expected}"


def test_span_matches_example_exactly():
    """槽位区间必须与示例自洽（否则回填会切错位置，正文被改坏）。"""
    for _lid, text, _labels in LABELED_SENTENCES:
        for s in pr.extract_slots(text):
            assert text[s.span[0]:s.span[1]] == s.example


def test_plain_paragraph_has_no_slots():
    assert pr.extract_slots("这是一句普通的话。") == []


# ---------------------------------------------------------------- 骨架不编造
def _ref(text: str, ordinal: int = 0) -> pr.ParagraphRef:
    return pr.ParagraphRef(source="library", para_id=ordinal + 1, doc_id=1,
                           doc_title="参考文献", ordinal=ordinal,
                           kind="paragraph", text=text,
                           char_offset=ordinal * 10, text_hash=str(ordinal))


def test_skeleton_render_equals_source():
    """骨架的核心不变量仍在：用抽到的示例回填后，渲染文本逐字等于参考原文。"""
    refs = [_ref("市教育局关于安全生产的通知"), _ref("请于3个工作日内反馈。", 1)]
    sk = pr.derive_skeleton(refs)
    values = {s.name: s.example for s in sk.slots}
    source = "\n".join(r.text for r in refs)
    assert pr.render_draft(sk, values) == source
    # 机构名必须真的被抽象掉，而不是留在模板里
    assert "市教育局" not in sk.template
    assert any(s.name == "org" and s.example == "市教育局" for s in sk.slots)
