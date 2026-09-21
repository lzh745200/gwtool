# -*- coding: utf-8 -*-
"""重复字检测（gwtool.core.repeat_rules）回归测试。

覆盖七类验收：
  1. 正样本（虚词重复 / 三连 / 跨词边界，含公文高频）；
  2. 负样本（13 类合法重叠，逐类零命中）；
  3. 强负样本：dictionary 全量词条零命中（最大、最客观的护栏）；
  4. 真实语料回归：仓库中文散文类文本零命中；
  5. 模式 × 位置矩阵 {2连,3连,4连} × {词内,词边界,段首,段尾,标点后,书名号内}；
  6. cXc 间隔重复灰档（恒 0.5、四条件、合法词不报）；
  7. 与 corrector 的集成（category/kind、_dedupe 不重叠、书名号保护）。

本模块使用**独立临时库**并从随包 seed.db 导入词典（CC-CEDICT 全量），
测完恢复会话库，避免污染同会话其它测试模块。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from gwtool.db import connection as dbconn
from gwtool.core import corrector, repeat_rules

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "gwtool" / "resources" / "data" / "seed.db"


@pytest.fixture(scope="module", autouse=True)
def _dict_env(tmp_path_factory):
    """独立临时库 + 全量词典；模块结束恢复会话库与缓存。"""
    prev = dbconn.current_db_file()
    d = tmp_path_factory.mktemp("repeat_rules")
    dbconn.configure(d / "repeat.db")
    conn = dbconn.get_conn()
    assert SEED.exists(), f"缺少随包词典 {SEED}"
    conn.execute("ATTACH DATABASE ? AS seeddb", (str(SEED),))
    conn.execute(
        "INSERT OR IGNORE INTO dictionary(word,pinyin,definition,example,source)"
        " SELECT word,pinyin,definition,example,source FROM seeddb.dictionary")
    conn.commit()
    conn.execute("DETACH DATABASE seeddb")
    corrector.invalidate_cache()
    yield
    corrector.invalidate_cache()
    dbconn.close_current_thread()
    dbconn.configure(prev)
    corrector.invalidate_cache()


def rep(text: str):
    return repeat_rules.check_repeat(text)


def pairs(text: str):
    """(wrong, suggestion, confidence) 三元组列表。"""
    return [(c.wrong, c.suggestion, c.confidence) for c in rep(text)]


# ============================================================ 1) 正样本
# 虚词重复（⑥ → 0.9）：c 是虚词且 c² 不是词典词。
_FUNC_DOUBLES = [
    "在在", "是是", "和和", "与与", "及及", "就就", "也也", "而而", "之之",
    "其其", "这这", "那那", "着着", "过过", "把把", "被被", "为为", "于于",
    "对对", "向向", "因因", "从从", "且且", "则则", "并并", "吧吧", "嘛嘛", "吗吗",
]
# 三连/四连（③ → 0.9）：词典中唯一合法的 3+ 连是「啪啪啪」。
_TRIPLE = ["的的的", "了了了", "在在在", "是是是", "好好好好",
           "学学学", "人人人", "你你你", "做做做", "请请请", "忙忙忙", "快快快"]
# 拟声重复（③ 拟声集 → 0.5 灰档）。
_ONO = ["哈哈哈", "呵呵呵", "哗哗哗", "咚咚咚", "汪汪汪", "轰轰轰"]
# 跨词边界（⑤b：仅左词证据且连尾后是标点/EOS → 0.85）。
_CROSS = ["进行行。", "部门门。", "工作作。", "会议议。", "负责责。",
          "重要要。", "落实实。", "完成成。", "报告告。"]


def test_positive_function_doubles_are_090():
    assert _FUNC_DOUBLES, "样本不能为空"
    for s in _FUNC_DOUBLES:
        r = rep(s)
        assert len(r) == 1, f"{s!r} 应命中 1 处，实际 {r}"
        c = r[0]
        assert c.wrong == s and c.suggestion == s[0]
        assert c.confidence == 0.9, f"{s!r} 虚词重复应 0.9"


def test_positive_triple_and_quad_are_090():
    for s in _TRIPLE:
        r = rep(s)
        assert len(r) == 1 and r[0].confidence == 0.9, f"{s!r} 三/四连应 0.9：{r}"
        assert r[0].wrong == s


def test_positive_onomatopoeia_is_050():
    for s in _ONO:
        r = rep(s)
        assert len(r) == 1 and r[0].confidence == 0.5, f"{s!r} 拟声应降为灰档 0.5：{r}"


def test_positive_cross_boundary_is_085():
    for s in _CROSS:
        r = rep(s)
        assert len(r) == 1, f"{s!r} 应命中 1 处：{r}"
        c = r[0]
        assert c.confidence == 0.85, f"{s!r} 跨边界应 0.85：{r}"
        # 命中即为该重复字（保留一字）
        assert len(c.wrong) == 2 and c.wrong[0] == c.wrong[1]
        assert c.suggestion == c.wrong[0]


def test_positive_public_doc_high_frequency_forms():
    """公文高频重复串：进行行/部门门/工作作/会议议/负责责/重要要。"""
    for s in ["进行行", "部门门", "工作作", "会议议", "负责责", "重要要"]:
        assert pairs(s + "。"), f"{s!r} 应被 ⑤b 命中"


def test_rule1_aabb_requires_han_guard():
    """① 的 AABB/ABAB 必须限定汉字：重复字后紧跟排版符号时不得被误放行（漏检回归）。

    QA 反例：``工作作**`` / ``工作作。。`` / ``重要要，，``——窗内 ``w2==w3``
    （``*==*``、``。==。``、``，==，``）曾令规则 ① 误判 AABB 而**短路放行**，
    使 ⑤b 无从判起。加汉字守卫后应回落 ⑤b（0.85）正常报出。
    """
    for s in ["工作作**", "工作作。。", "重要要，，"]:
        r = rep(s)
        assert len(r) == 1, f"{s!r} 应报出 1 处（曾因 ① 缺汉字守卫漏检）：{r}"
        assert r[0].wrong in ("作作", "要要")
        assert r[0].confidence == 0.85, f"{s!r} 应回落 ⑤b 0.85：{r}"


# ============================================================ 2) 负样本（13 类）
_NEG_CLASSES = {
    "称谓": ["爸爸", "妈妈", "爷爷", "奶奶", "哥哥", "姐姐", "妹妹", "叔叔", "伯伯", "舅舅"],
    "重叠动词": ["看看", "说说", "谈谈", "转转", "谢谢", "拜拜"],
    "重叠形容词": ["好好", "慢慢", "常常", "刚刚", "渐渐", "明明", "偏偏", "统统", "匆匆", "缓缓"],
    "量词重叠": ["个个", "件件", "条条", "天天", "年年", "处处", "人人"],
    "叠音名物": ["猩猩", "娃娃", "姥姥", "婆婆", "饽饽", "蝈蝈", "蛐蛐"],
    "拟声": ["哈哈", "哗哗", "汪汪", "咚咚", "嗡嗡", "滴滴", "呼呼", "隆隆", "沙沙"],
    "AABB": ["高高兴兴", "密密麻麻", "郁郁葱葱", "干干净净", "认认真真", "清清楚楚"],
    "ABB": ["红彤彤", "绿油油", "亮晶晶", "沉甸甸", "喜洋洋"],
    "AAB": ["毛毛雨", "团团转", "面面观", "蒙蒙亮", "跷跷板"],
    "ABAB": ["研究研究", "讨论讨论", "一天一天", "一个一个", "雪白雪白"],
    "人名": ["丽丽", "娜娜", "玲玲", "婷婷"],
    "数字编号": ["一一", "三三", "五五"],
    "跨字结构": ["看一看", "想一想", "一天一天", "越来越", "等一等"],
}


def test_negative_classes_all_zero_hits():
    assert len(_NEG_CLASSES) == 13, "必须覆盖 13 类合法重叠"
    failures = []
    for cls, samples in _NEG_CLASSES.items():
        for s in samples:
            if rep(s):
                failures.append(f"[{cls}] {s!r} -> {pairs(s)}")
    assert not failures, "合法重叠出现误报：\n" + "\n".join(failures)


# ============================================================ 3) 强负样本：词典全量
def test_full_dictionary_zero_false_positive():
    """把检测器在 dictionary 全量词条上跑一遍，断言 0 命中。"""
    words = dbconn.get_conn().execute(
        "SELECT DISTINCT word FROM dictionary").fetchall()
    words = [r[0] for r in words if r[0]]
    assert len(words) >= 119000, f"词典条目过少：{len(words)}"
    bad = [(w, pairs(w)) for w in words if rep(w)]
    assert not bad, (
        f"词典全量误报 {len(bad)}/{len(words)} 条：\n"
        + "\n".join(f"{w} -> {p}" for w, p in bad[:40]))


# ============================================================ 4) 真实语料回归
def _corpus_files():
    """语料文档 = **git 跟踪** 的仓库根 markdown（保证他人 clone 后结果一致）。

    刻意**不用** ``ROOT.glob("*.md")``：仓库长期有并行会话，glob 会扫到未提交的
    临时文档，使语料不可由提交复现、且随并行改动漂移。这里只认 ``git ls-files``
    的受控集合；git 不可用时回退到核心固定文档。
    """
    import subprocess
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "-c", "core.quotepath=false",
             "ls-files", "*.md"],
            capture_output=True, encoding="utf-8", errors="replace",
            check=True).stdout
        names = [ln.strip() for ln in out.splitlines() if ln.strip()]
        files = [ROOT / n for n in names if (ROOT / n).is_file()]
        if files:
            return files, names
    except (OSError, subprocess.SubprocessError):
        pass
    # 回退：核心产品文档（均为 git 跟踪文件）
    fallback = ["README.md", "纠错系统落地方案.md",
                "项目文件结构说明.md", "纠错引擎增强方案.md"]
    files = [ROOT / n for n in fallback if (ROOT / n).is_file()]
    return files, [f.name for f in files]


def _corpus_lines():
    """从 git 跟踪的产品文档中抽取"自然散文"行。

    过滤只为剔除**合成样例**而非天然文本（与架构师的实测口径一致：
    "部署署/甲甲乙丙丙 是测试数据不是自然文本"）：跳过围栏代码块；
    跳过含前后对照箭头 ``→`` 的行（"错→对"示例清单）；行内代码片段
    `` `…` `` 就地剥除后保留其余散文，仅当整行皆为代码时才舍弃。
    """
    import re
    files, names = _corpus_files()
    lines = []
    for f in files:
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        in_fence = False
        for raw in txt.splitlines():
            s = raw.strip()
            if s.startswith("```"):
                in_fence = not in_fence
                continue
            if not s or in_fence:
                continue
            if "\u2192" in s:                 # → 前后对照：合成样例行
                continue
            s = re.sub(r"`[^`]*`", "", s).strip()   # 剥除行内代码，留散文
            if len(s) >= 8:                   # 太短的行（纯符号/编号）无暇接意义
                lines.append(s)
    return lines, names


def test_real_corpus_zero_false_positive():
    """仓库中文散文类文本（git 跟踪的产品 markdown 文档）零命中。"""
    lines, names = _corpus_lines()
    total = sum(len(ln) for ln in lines)
    assert len(lines) > 300 and total > 10000, \
        f"语料过小（{len(lines)} 行 / {total} 字，文件 {names}），验收入口失效"
    bad = [(ln[:60], pairs(ln)) for ln in lines if rep(ln)]
    assert not bad, (
        f"自然语料误报 {len(bad)} 处/{len(lines)} 行（文件 {names}）：\n"
        + "\n".join(f"{ln} -> {p}" for ln, p in bad[:30]))


# ============================================================ 5) 模式 × 位置矩阵
# (模式, 位置, 文本, 期望 [(wrong, conf), ...])
_MATRIX = [
    ("2连", "词内",    "爸爸",           []),
    ("2连", "词边界",  "中国国际",       []),
    ("2连", "段首",    "我我们单位",     [("我我", 0.85)]),
    ("2连", "段尾",    "请进行行",       [("行行", 0.85)]),
    ("2连", "标点后",  "他说，我我们走", [("我我", 0.85)]),
    ("2连", "书名号内", "《立法法》",     []),
    ("3连", "词内",    "啪啪啪",         []),
    ("3连", "词边界",  "进行行行",       [("行行行", 0.9)]),
    ("3连", "段首",    "的的的错",       [("的的的", 0.9)]),
    ("3连", "段尾",    "都的的的",       [("的的的", 0.9)]),
    ("3连", "标点后",  "，的的的",       [("的的的", 0.9)]),
    ("3连", "书名号内", "《哈哈哈》",     []),
    ("4连", "词内",    "好好好好",       [("好好好好", 0.9)]),
    ("4连", "词边界",  "工作作作作",     [("作作作作", 0.9)]),
    ("4连", "段首",    "好好好好很好",   [("好好好好", 0.9)]),
    ("4连", "段尾",    "真的好好好好",   [("好好好好", 0.9)]),
    ("4连", "标点后",  "，好好好好",     [("好好好好", 0.9)]),
    ("4连", "书名号内", "《好好好好》",   []),
]


def test_mode_position_matrix():
    """{2连,3连,4连} × {词内,词边界,段首,段尾,标点后,书名号内} 逐格断言。"""
    modes = {"2连", "3连", "4连"}
    cols = {"词内", "词边界", "段首", "段尾", "标点后", "书名号内"}
    seen = {(m, c) for m, c, _, _ in _MATRIX}
    assert seen == {(m, c) for m in modes for c in cols}, "矩阵不完整"
    for mode, col, text, expected in _MATRIX:
        got = [(c.wrong, c.confidence) for c in rep(text)]
        assert got == expected, f"[{mode}×{col}] {text!r} 期望 {expected} 实际 {got}"


# ============================================================ 6) cXc 间隔重复灰档
def test_interval_repeat_is_gray_050():
    """满足四条件的 cXc 报告，且置信度**恒为 0.5**、kind=delete。"""
    for s in ["是否是文件被占用", "在不在", "了完了"]:
        r = rep(s)
        assert len(r) == 1, f"{s!r} 应命中 1 处：{r}"
        c = r[0]
        assert c.confidence == 0.5, f"{s!r} 必须恒为灰档 0.5"
        assert c.category == "重复字" and c.kind == "delete"
        assert len(c.wrong) == 3 and c.wrong[0] == c.wrong[2]      # cXc


def test_interval_repeat_legal_words_not_reported():
    """词典合法 cXc 词不报。"""
    for s in ["一对一", "不得不", "亚细亚", "克拉克", "看一看", "等一等"]:
        assert not rep(s), f"{s!r} 是合法词，不应报：{pairs(s)}"


def test_interval_repeat_never_exceeds_gray():
    """cXc 通道贯穿一段文本时，任何命中置信度都不得超过 0.5。"""
    text = "是否是；在不在；了完了；一对一；不得不；亚细亚；克拉克；看一看。"
    for c in rep(text):
        assert c.confidence <= 0.5, f"{c.wrong!r} 越档：{c.confidence}"


def test_interval_repeat_requires_real_word_after_deletion():
    """判据必须是「删一字得到**真正的词典词**」，不能只看能否切成词。

    回归用例（2026-09-21 由 CI 的语料零误报断言抓到）：
    正常语句「现在会在…」里含子串「在会在」—— `在` 是虚词、形态又是 cXc，
    原先 `_segmentable` 兜底把 "会在" 切成 会+在（两个单字词）就判为"可删"，
    于是整句被误报。这类"两个常用字都能切成单字词"的情形极其普遍，
    兜底判据等于永远成立。收紧为「必须删出真词」后即不再误报。
    """
    # 误报原文（CI 报出的那一句），必须 0 命中
    for s in ["现在会在结果对话框中逐条点名。",
              "现在会在所有步骤都真正成功时出现。"]:
        assert not rep(s), f"正常语句被误报：{s!r} -> {pairs(s)}"
    # 既有正例必须仍然报（删一字得到的是词典词：是否 / 不在 / 完了）
    for s in ["是否是文件被占用", "在不在", "了完了"]:
        assert len(rep(s)) == 1, f"{s!r} 应仍命中 1 处：{pairs(s)}"


# ============================================================ 7) 集成
def test_corrector_integration_category_kind_style():
    hits = corrector.check_text("工作作。")
    rep_hits = [c for c in hits if c.category == "重复字"]
    assert rep_hits, "重复字未接入 corrector.check_text"
    assert rep_hits[0].kind == "delete"
    assert rep_hits[0].suggestion == "作"
    # 配色已登记
    assert "重复字" in corrector._MARK_STYLE


def test_corrector_hits_do_not_overlap():
    """重复字与其它类别混合时，_dedupe 保证区间不重叠。"""
    text = "工作布署了了，请进行行。"
    hits = corrector.check_text(text)
    spans = sorted((c.start, c.end) for c in hits)
    for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
        assert e1 <= s2, f"命中区间重叠：{(s1, e1)} 与 {(s2, e2)}"


def test_quote_protection_applies_to_repeat():
    """书名号/引号内的重复字不报。"""
    assert not rep("《立法法》")
    assert not rep("“哈哈哈”")
    # 号外部分仍报
    assert rep("《立法法》的的")


def test_empty_dictionary_degrades_to_no_report():
    """词典不可用（空库）时宁少报不误报：直接不报。"""
    prev = dbconn.current_db_file()
    corrector.invalidate_cache()
    # 临时切到空库
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        dbconn.configure(Path(td) / "empty.db")
        corrector.invalidate_cache()
        assert rep("的的") == []            # 无词典 => 不报
        assert rep("进行行。") == []
        dbconn.close_current_thread()
    dbconn.configure(prev)
    corrector.invalidate_cache()
    assert rep("的的")                       # 恢复后仍工作
