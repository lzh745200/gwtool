# -*- coding: utf-8 -*-
"""v1.8 规则数据扩充回归：GB/T 15835 数字用法 + 精标错别字 + 公文上下文规则。

为什么需要本文件
----------------
``corrector_data.py`` 里的数据是"裸数据"，没有类型系统兜底。一条错形若本身
就是合法常用词（同音异形真词），就会在全量公文里稳定误报；一条数字规则若
写得太宽（例如把"8:00—12:00"这种**规范写法**也命中），就会把正确文稿标成
错误。本文件把"收录口径"变成可执行断言，而不是靠注释与人工记忆。

jieba 词频核验方法（本批 80 条精标对逐条执行）
------------------------------------------------
    wrong 侧：``jieba.dt.FREQ.get(wrong, 0) <= 20`` —— 错形必须不是词。
      阈值取 20 而非 0：jieba 词典里混入了少量错形统计噪声（如 `永保青春`
      freq=3），这类噪声不代表规范写法，不能因为它把真错判成"合法的词"。
      反过来 freq>20 说明该字形在正常文本里被当作词使用，收录即稳定误报。
    correct 侧：``jieba.dt.FREQ.get(correct, 0) > 0`` —— 正形必须是真词，
      否则"纠正建议"本身可能是生造词。
另外两条无法用词频自动判定的口径，由用例设计保证：
    1) 错形不得是"两段正常文本跨词相接"的产物 —— 本批全部为四字成语；
       两字错形（如 据绝←依据绝密、效律←生效律所）已因此被剔除。
    2) 反例必须不命中 —— 每条规则给了正确写法作反例，每条精标对给了
       含正形的整句作反例（正形所在的正常句子不得报出该错形）。
"""
from __future__ import annotations

import re
from pathlib import Path

from gwtool.core import corrector
from gwtool.core.corrector_data import (CONTEXT_RULES, CURATED_PAIRS,
                                        NUMBER_RULES)

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# 本批新增的 80 条精标对（显式清单）。
# 不按"文件末尾 N 条"取数：数据被误删、改向或被追加时，这里会立刻报错。
# ---------------------------------------------------------------------------
NEW_PAIRS: list[tuple[str, str]] = [
    ("无可后非", "无可厚非"), ("草管人命", "草菅人命"),
    ("对薄公堂", "对簿公堂"), ("纷至踏来", "纷至沓来"),
    ("锋芒必露", "锋芒毕露"), ("凤毛鳞角", "凤毛麟角"),
    ("功亏一匮", "功亏一篑"), ("刚腹自用", "刚愎自用"),
    ("汗流夹背", "汗流浃背"), ("晃然大悟", "恍然大悟"),
    ("脍灸人口", "脍炙人口"), ("良秀不齐", "良莠不齐"),
    ("廖廖无几", "寥寥无几"), ("目不瑕接", "目不暇接"),
    ("目不转晴", "目不转睛"), ("破斧沉舟", "破釜沉舟"),
    ("千均一发", "千钧一发"), ("潜移墨化", "潜移默化"),
    ("契而不舍", "锲而不舍"), ("如愿以尝", "如愿以偿"),
    ("三翻五次", "三番五次"), ("同仇敌慨", "同仇敌忾"),
    ("无计于事", "无济于事"), ("喜出忘外", "喜出望外"),
    ("兴灾乐祸", "幸灾乐祸"), ("雪中送碳", "雪中送炭"),
    ("一触既发", "一触即发"), ("一撅不振", "一蹶不振"),
    ("义愤填鹰", "义愤填膺"), ("意想天开", "异想天开"),
    ("应接不瑕", "应接不暇"), ("与日具增", "与日俱增"),
    ("原形必露", "原形毕露"), ("运筹帷握", "运筹帷幄"),
    ("自爆自弃", "自暴自弃"), ("自顾不瑕", "自顾不暇"),
    ("罪魁祸手", "罪魁祸首"), ("坐想其成", "坐享其成"),
    ("言简意该", "言简意赅"), ("别出心栽", "别出心裁"),
    ("不寒而粟", "不寒而栗"), ("步履维坚", "步履维艰"),
    ("得不尝失", "得不偿失"), ("独辟溪径", "独辟蹊径"),
    ("发人深醒", "发人深省"), ("分道扬镖", "分道扬镳"),
    ("斧底抽薪", "釜底抽薪"), ("过尤不及", "过犹不及"),
    ("记忆尤新", "记忆犹新"), ("兼收并畜", "兼收并蓄"),
    ("娇生贯养", "娇生惯养"), ("戒骄戒燥", "戒骄戒躁"),
    ("精亦求精", "精益求精"), ("聚精汇神", "聚精会神"),
    ("扑塑迷离", "扑朔迷离"), ("情有独衷", "情有独钟"),
    ("司空见贯", "司空见惯"), ("玩乎职守", "玩忽职守"),
    ("歪风斜气", "歪风邪气"), ("无与轮比", "无与伦比"),
    ("习已为常", "习以为常"), ("相得益张", "相得益彰"),
    ("笑逐言开", "笑逐颜开"), ("言不由忠", "言不由衷"),
    ("眼花瞭乱", "眼花缭乱"), ("摇摇欲堕", "摇摇欲坠"),
    ("因咽废食", "因噎废食"), ("怨天由人", "怨天尤人"),
    ("越俎代疱", "越俎代庖"), ("真知酌见", "真知灼见"),
    ("争分夺妙", "争分夺秒"), ("震奋人心", "振奋人心"),
    ("忠心梗梗", "忠心耿耿"), ("专心至志", "专心致志"),
    ("转弯磨角", "转弯抹角"), ("装模做样", "装模作样"),
    ("字斟句啄", "字斟句酌"), ("自吹自雷", "自吹自擂"),
    ("提纲契领", "提纲挈领"), ("沾轻怕重", "拈轻怕重"),
]

# 本批新增数字规则的提示标签（suggestion）。按标签取规则，而不是按"末尾 N 条"，
# 这样后续追加规则不会让本文件静默失配。`日期分隔符` 对应两条规则
# （半角斜杠 / 全角分隔符），故规则条数比标签多 1。
V18_NUMBER_LABELS: tuple[str, ...] = (
    "百分数区间", "百分数写法", "金额写法", "千分位", "日期分隔符", "日期虚位",
    "范围号", "年份范围", "时间范围", "数字与单位", "小数写法", "编号虚位",
    "字母O误作0", "字母l误作1",
)

# 每条数字规则的 (标签, 正例, 反例)。反例是**规范写法**，必须不命中。
NUMBER_CASES: list[tuple[str, str, str]] = [
    ("百分数区间", "今年产量增长10-20%，超出预期。", "覆盖面达10%～20%。"),
    ("百分数写法", "完成率提高到百分之85以上。", "完成率达到百分之八十五。"),
    ("金额写法", "本次采购预算为￥5000元。", "预算为￥5000，另有5000元自筹。"),
    ("千分位", "全年共接待游客1，200人次。", "全年共接待游客1,200人次。"),
    ("日期分隔符", "会议定于2026/10/3召开。", "会议定于2026年10月3日召开。"),
    ("日期分隔符", "印发日期为2026．10．3。", "印发日期为2026-10-03。"),
    ("日期虚位", "成文日期为2026年03月05日。", "成文日期为2026年3月5日。"),
    ("范围号", "覆盖范围约10~20个乡镇。", "覆盖率约10%～20%。"),
    ("年份范围", "规划期为2021-2025年。", "会议于2026年10月召开。"),
    ("时间范围", "值班时间为8:00-17:00。", "值班时间为8:00—12:00。"),
    ("数字与单位", "共计投入资金 500 元。", "会议定于2026 年10月召开。"),
    ("小数写法", "增长率为.5个百分点。", "增长率为0.5个百分点。"),
    ("编号虚位", "请对照第01条执行。", "请对照第1条、第2条执行。"),
    ("字母O误作0", "会议于2O26年召开。", "采用O2O模式运营。"),
    ("字母l误作1", "预算为l00万元。", "采用l10n本地化方案。"),
]

# 公文文体正样本：数字、日期、区间全部按 GB/T 15835 / GB/T 9704 规范书写，
# 本批数字规则必须**一处都不命中**（等价于 E3 的"公文负样本零误报"口径）。
GONGWEN_CLEAN: list[str] = [
    "会议于2026年10月21日召开，会期1天。",
    "全年完成投资12.5亿元，同比增长10%。",
    "报名截止到12月31日，过期不予受理。",
    "计划招收人员10%～20%，具体名额另行通知。",
    "项目总投资500万元，其中设备购置费300万元。",
    "2021—2025年累计完成改造面积1.2万平方米。",
    "值班时间为8:00—12:00、14:00—18:00。",
    "请对照第1条、第2条规定执行。",
    "全区共3万余人参加，覆盖面达到85%以上。",
    "成文日期为2026年3月5日。",
]


def _new_pair_index() -> dict[str, tuple[str, str, float]]:
    """NEW_PAIRS 在 CURATED_PAIRS 中的登记信息：wrong -> (correct, cat, conf)。"""
    index = {}
    for wrong, correct, cat, conf in CURATED_PAIRS:
        index[wrong] = (correct, cat, conf)
    return index


def _v18_number_rules() -> list[tuple[str, str, str, float]]:
    rules = [r for r in NUMBER_RULES if r[1] in V18_NUMBER_LABELS]
    assert len(rules) == 15, f"v1.8 数字规则应为 15 条，实为 {len(rules)}"
    return rules


def _doc_corpus() -> list[tuple[str, str]]:
    files = sorted((ROOT / "doc").glob("*.md"))
    assert len(files) >= 15, f"仓库 doc/*.md 语料缺失（仅 {len(files)} 份）"
    return [(f.name, f.read_text(encoding="utf-8", errors="ignore")) for f in files]


# ---------------------------------------------------------------- 精标对结构
def test_new_pairs_are_registered_with_curated_confidence():
    """新增对必须进 CURATED_PAIRS，且置信度落在人工精标区间 0.85~0.98。

    置信度 ≥0.7 还有一层运行时含义：corrector v1.8 的生成层误报闸门
    （词典词豁免 / 同文档重复豁免）只作用于 conf<0.7，精标对必须永不被它吃掉。
    """
    index = _new_pair_index()
    missing = [(w, c) for w, c in NEW_PAIRS if w not in index]
    assert not missing, f"未登记进 CURATED_PAIRS：{missing}"
    for wrong, correct in NEW_PAIRS:
        got_correct, cat, conf = index[wrong]
        assert got_correct == correct, f"{wrong} 的纠正方向被改动：{got_correct}"
        assert cat == "错别字", f"{wrong} 的类别应为“错别字”，实为 {cat}"
        assert 0.85 <= conf <= 0.98, f"{wrong} 置信度 {conf} 不在 0.85~0.98"
        assert conf >= 0.7, f"{wrong} 会被 v1.8 生成层闸门误伤"


def test_new_pairs_wrong_form_is_not_a_word():
    """错形必须不是合法词：``jieba.dt.FREQ[错形] <= 20``（逐条核验）。"""
    import jieba

    from gwtool.db.tokenize import _ensure_jieba

    _ensure_jieba()
    bad = []
    for wrong, _correct in NEW_PAIRS:
        freq = int(jieba.dt.FREQ.get(wrong, 0))
        if freq > 20:
            bad.append((wrong, freq))
    assert not bad, f"错形本身是词典词（收录即稳定误报）：{bad}"


def test_new_pairs_correct_form_is_a_real_word():
    """正形必须是真词：``jieba.dt.FREQ[正形] > 0``，否则"建议"是生造词。"""
    import jieba

    from gwtool.db.tokenize import _ensure_jieba

    _ensure_jieba()
    bad = [(c, int(jieba.dt.FREQ.get(c, 0))) for _w, c in NEW_PAIRS
           if int(jieba.dt.FREQ.get(c, 0)) <= 0]
    assert not bad, f"正形不是词：{bad}"


def test_new_pairs_hit_in_full_sentence(tmp_db):
    """每一条都必须在**整句上下文**里命中（不是孤立串偶然命中）。"""
    failed = []
    for wrong, correct in NEW_PAIRS:
        text = f"材料中出现了{wrong}的表述，需要核实。"
        hits = [c for c in corrector.check_text(text)
                if c.wrong == wrong and c.suggestion == correct]
        if not hits:
            failed.append((wrong, correct))
    assert not failed, f"整句上下文未命中：{failed}"


def test_new_pairs_correct_form_is_not_flagged(tmp_db):
    """反例：正形所在的正常句子不得报出该错形（防止"正形本身也是错形"）。"""
    failed = []
    for wrong, correct in NEW_PAIRS:
        text = f"本次会议强调要{correct}，抓好落实。"
        if any(c.wrong == wrong for c in corrector.check_text(text)):
            failed.append((wrong, correct))
    assert not failed, f"正确写法被误报：{failed}"


# ---------------------------------------------------------------- 数字规则
def test_number_rules_scale():
    """GB/T 15835 类规则从 6 条扩到 20+ 条。"""
    assert len(NUMBER_RULES) >= 20, f"NUMBER_RULES 仅 {len(NUMBER_RULES)} 条"
    _v18_number_rules()


def test_number_rules_positive_and_negative(tmp_db):
    """每条新规则：正例命中该提示标签，反例（规范写法）不得命中。"""
    failures = []
    for label, positive, negative in NUMBER_CASES:
        pos = [c for c in corrector.check_text(positive)
               if c.category == "数字用法" and c.suggestion == label]
        neg = [c for c in corrector.check_text(negative)
               if c.category == "数字用法" and c.suggestion == label]
        if not pos:
            failures.append(f"{label}: 正例未命中 {positive!r}")
        if neg:
            failures.append(f"{label}: 反例被误报 {negative!r} -> "
                            f"{[(c.wrong, c.suggestion) for c in neg]}")
    assert not failures, "\n".join(failures)


def test_number_rules_clean_on_official_prose(tmp_db):
    """公文规范写法零误报：这是本类规则的红线（E3=0 的同口径自检）。"""
    failures = []
    for text in GONGWEN_CLEAN:
        hits = [c for c in corrector.check_text(text) if c.category == "数字用法"]
        if hits:
            failures.append((text, [(c.wrong, c.suggestion) for c in hits]))
    assert not failures, f"规范写法被误报：{failures}"


def test_number_usage_never_written_back(tmp_db):
    """「数字用法」的 suggestion 是提示标签，correct_block 必须整类排除。

    这类命中的 suggestion 是“百分数区间”等标签，一旦被 write-back 路径当成
    替换文本，正文就会被改成标签本身——比漏报严重得多。
    """
    text = "会议于2O26年召开，共1，200人参会，完成率百分之85，经费￥5000元。"
    hits = [c for c in corrector.check_text(text) if c.category == "数字用法"]
    assert len(hits) >= 3, "本用例依赖新规则产生数字用法提示"
    fixed, cs = corrector.correct_block(text)
    assert all(c.category not in corrector.ADVISORY_CATEGORIES for c in cs)
    assert fixed == text, "提示类命中被写回正文"
    assert "数字用法" in corrector.ADVISORY_CATEGORIES


# ---------------------------------------------------------------- 上下文规则
def test_context_rule_quanli_jigou(tmp_db):
    """权利/权力：只有“机构/机关”搭配改法唯一（机关掌握的是权力）。"""
    hits = {(c.wrong, c.suggestion) for c in corrector.check_text(
        "各级权利机关要依法履职。")}
    assert ("权利", "权力") in hits
    # 反例：公民权利 / 权利保障 / 维权机构 都是规范表述
    assert not [c for c in corrector.check_text(
        "依法保障公民权利，健全权利保障机制。")
        if c.wrong == "权利" and c.suggestion == "权力"]


def test_context_rule_scale_and_compile():
    assert len(CONTEXT_RULES) >= 36
    for rx, _repl, _reason, conf in CONTEXT_RULES:
        re.compile(rx)
        assert 0 < conf <= 1


# ---------------------------------------------------------------- 真实语料回归
# 「元文档」：本身就是**讨论纠错规则/汇报实测数据**的变更记录、审计报告与方案书。
# 它们会引用错形做示例（如 `戒骄戒燥→戒骄戒躁`），也会大量书写 `1.5~1.6ms`
# 这类区间**作为证据数字**——把它们算进"行文语料"等于要求文档不许举例、不许
# 写区间，那会让这两道门禁变成"文档禁令"，与它想防的"规则在真实文本上误报"无关。
#
# 用**文件名特征**而不是逐个列举：每版都会新增"修复记录/审计报告"，列举法必然
# 每版都要改测试（v1.10 就因此红过一次），特征法一劳永逸且边界清晰 ——
# 真正的行文类文档（设计说明、UI 方案、规格书…）不含这些字样，仍在语料里。
CORPUS_META_MARKERS = ("修复记录", "深审报告", "优化建议", "全功能检测")


def _is_meta_doc(name: str) -> bool:
    return any(marker in name for marker in CORPUS_META_MARKERS)


def test_new_pairs_never_occur_in_real_corpus():
    """仓库自身 doc/*.md 真实中文长文零新增误报。

    精标对只在**字面完全一致**时才可能命中（词边界门控只会抑制、不会创造
    命中），所以用 ``wrong in text`` 做等价且更快的回归——比逐段跑 check_text
    快两个数量级，判据却不变。
    """
    corpus = [(name, text) for name, text in _doc_corpus()
              if not _is_meta_doc(name)]
    appeared = [(wrong, name) for wrong, _c in NEW_PAIRS
                for name, text in corpus if wrong in text]
    assert not appeared, f"真实语料出现了新增错形（需复核）：{appeared}"


# 真实语料里允许出现的数字规则命中（逐个白名单 + 判定）。
# doc/ 是技术文档，其中 `6-8%`（应写 `6%～8%`）、`3~4`（应写 `3～4`）这类
# 区间写法**本身就不合 GB/T 15835**，属真命中而非误报；把它们逐个列出而不是
# 直接放行，是为了"新增同类命中必须人工复核"——新增命中会先在这里失败。
CORPUS_ALLOWED: dict[str, set[str]] = {
    "百分数区间": {"6-8%", "75-87%", "47-59%", "27-56%", "44-53%", "24-50%"},
    "范围号": {"~"},
}
# 上限按"人工复核过的命中数"设，不是随手放的余量：
#   百分数区间 9 处（6 个不同串，全部是区间首端漏百分号）
#   范围号   15 处 = 旧语料 9 处 + 深审报告_v1.8.md 6 处（`3.10~5.96` 等性能区间）
# 语料里新增一份**行文类**文档就可能顶到上限，届时**必须人工复核新命中再调数**；
# 但变更记录/审计报告属元文档（见 `_is_meta_doc`），不参与本口径 —— 它们书写的
# 区间是"实测证据数字"，不是行文缺陷。
CORPUS_ALLOWED_MAX = {"百分数区间": 12, "范围号": 20}


def test_number_rules_corpus_hits_are_known_style_deviations():
    """真实语料回归：新数字规则的命中必须全部是白名单内的**真不规范写法**。"""
    corpus = [(name, text) for name, text in _doc_corpus()
              if not _is_meta_doc(name)]
    unexpected = []
    counts: dict[str, int] = {}
    for rx, label, _reason, _conf in _v18_number_rules():
        pattern = re.compile(rx)
        allowed = CORPUS_ALLOWED.get(label, set())
        for name, text in corpus:
            for m in pattern.finditer(text):
                counts[label] = counts.get(label, 0) + 1
                if m.group(0) not in allowed:
                    unexpected.append((label, m.group(0), name))
    assert not unexpected, f"语料新增未白名单命中（需人工复核）：{unexpected}"
    over = {k: v for k, v in counts.items()
            if v > CORPUS_ALLOWED_MAX.get(k, 0)}
    assert not over, f"命中数超出已复核上限：{over}"
    # 白名单外的标签必须零命中（13 个标签）
    zero_labels = set(V18_NUMBER_LABELS) - set(CORPUS_ALLOWED)
    noisy = {k: counts.get(k, 0) for k in zero_labels if counts.get(k, 0)}
    assert not noisy, f"本应零命中的规则在语料上有命中：{noisy}"
