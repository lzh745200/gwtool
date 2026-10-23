# -*- coding: utf-8 -*-
"""v1.12.0 D1：真实语料（gov.cn 政策文本 15 篇 / 3.3 万字）首轮 E3 的 4 类规则缺陷。

首轮 E3 = 3.35 处/万字（阈值 0.5），逐条人工复核后确认**不是语料噪声**：
  1. `屏等`→`平等`：低置信生成对横跨 `屏`/`等` 两个词（跨词相接）；
  2. 机构沿革 4 处误报（历史机构引述）+ `法制办→司法局` 映射方向错误；
  3. `做出→作出` 把官方原文的「做出答复」判错；
  4. 书名号内半角括号（`《…规划(2020—2025年)》`）被"规范化"，改的是引用题名。

本文件用**真实句子**做 fixture（只抄句，不提交整篇语料），每类都给正反例：
正例=应报，反例=不得报。同时钉住"不因收紧而误伤既有能力"。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from gwtool.core import corrector, repeat_rules
from gwtool.core.corrector_data import CONTEXT_RULES, ORG_RENAME_PAIRS
from gwtool.db import connection as dbconn

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "gwtool" / "resources" / "data" / "seed.db"


@pytest.fixture(scope="class")
def dict_db(tmp_path_factory):
    """类级"全量词典"库：重复字层依赖词典证据集，空库会退化成"一律不报"。

    与 `tests/test_repeat_rules.py` 同构（那里是模块级）：从随包 seed.db 导入
    CC-CEDICT 全量词条，测完恢复原库。**不能**用 `tmp_db`——空库下
    `check_repeat` 直接返回 []（"宁少报不误报"），负例会"假通过"。
    """
    prev = dbconn.current_db_file()
    d = tmp_path_factory.mktemp("d5_repeat")
    dbconn.configure(d / "d5.db")
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

# ---------------------------------------------------------------- 真实语料原句
S_SCREEN = "室、资料索取点、信息公告栏、电子信息屏等场所、设施，公开政府信息。"
S_ORG_1 = "经研究，并经征求国务院法制办公室、最高人民法院等单位的意见，现答复如下："
S_ORG_2 = "经研究并书面征求国土资源部、国务院法制办的意见，现回复如下："
S_ZUOCHU = "应当在法定期限内对申请人做出答复。"
S_TITLE = ("是贯彻落实《法治中国建设规划(2020—2025年)》《法治政府建设实施纲要"
           "(2021—2025年)》等中央文件的重点工作之一")


def _hits(text: str):
    return corrector.check_text(text)


def _cats(text: str):
    return [c.category for c in _hits(text)]


# ============================================================ ① 跨词相接的生成对
class TestCrossWordGeneratedPair:
    """低置信生成对横跨两个词 → 抑制（真实语料 1 处）。

    用例在空库（`tmp_db`）里**注入**同样的低置信生成对，不依赖种子库抽样，
    保证断言稳定（种子库内容随构建期数据变化）。
    """

    @pytest.fixture(autouse=True)
    def _pairs(self, tmp_db):
        from gwtool.db import dao

        # 真实语料里的那一对，以及 E2 里被误伤风险最大的那类（非虚字收尾）
        dao.add_error_pair("屏等", "平等", "测试", 0.55, source="generated")
        dao.add_error_pair("际础", "基础", "测试", 0.55, source="generated")
        dao.add_error_pair("公开等", "公开凳", "测试", 0.55, source="generated")
        corrector.invalidate_cache()
        yield
        corrector.invalidate_cache()

    def test_screen_deng_not_reported(self):
        """`电子信息屏等场所` 里 `屏等→平等` 是"屏"+"等"两个词的相接，不得报。"""
        assert not [c for c in _hits(S_SCREEN) if c.wrong == "屏等"], _hits(S_SCREEN)
        assert not _hits(S_SCREEN), "整句不应有任何命中（其余部分规范）"

    def test_non_clitic_cross_word_pair_still_recalled(self):
        """反例：**非**词尾虚字的跨词形态仍要召回（不能一刀切压掉召回）。

        `际础→基础` 同样被 jieba 切成两词，但它确实是常见错形；
        E2 的 800 条抽样里 150 条属此类，全部压掉会让 E2 从 80.6% 掉到 61.9%
        （实测），所以判据只收"词尾虚字"这一种结构性形态。
        """
        hits = [c for c in _hits("各单位要高度重视际础工作。") if c.wrong == "际础"]
        assert hits, "常见的两字错形被一并压掉了"

    def test_long_cross_word_pair_suppressed(self):
        """长度 ≥3 且内部有分词边界 → 词被切开，只可能是跨词相接。"""
        # 信息公开|等|工作 —— `公开等` 内部有边界，且长度 3
        assert not [c for c in _hits("全面推进信息公开等工作。")
                    if c.wrong == "公开等"], _hits("全面推进信息公开等工作。")

    @pytest.mark.parametrize("wrong", ["布署", "帐目", "按步就班"])
    def test_curated_pairs_unaffected(self, wrong):
        """精标对（conf≥0.7）不进低置信门控，跨词判定不得误伤。"""
        assert [c for c in _hits(f"这项{wrong}工作要抓紧。") if c.wrong == wrong]

    @pytest.mark.parametrize("tail", ["等", "们", "者", "吗", "呢", "吧", "啊", "呀"])
    def test_clitic_tail_chars_are_the_gate_keys(self, tail):
        """词尾虚字集是判据的显式契约（改集合要连带改本用例）。"""
        assert tail in corrector._CLITIC_TAIL_CHARS

    @pytest.mark.parametrize("tail", ["的", "了", "着", "过", "地", "得", "么"])
    def test_ambiguous_tails_deliberately_excluded(self, tail):
        """「的/了/着/过/地/得/么」**刻意不收**：实测会压掉 E2 的真实召回
        （`士的→似的`、`湿的→似的`、`籍地→基地`），而这些恰是高频笔误。"""
        assert tail not in corrector._CLITIC_TAIL_CHARS

    def test_gate_is_the_reason(self, monkeypatch):
        """机制证明：清空词尾虚字集后 `屏等` 又会报出来
        —— 说明抑制确实来自这条新闸门，而不是别的门控（词边界/词典/重复）。"""
        monkeypatch.setattr(corrector, "_CLITIC_TAIL_CHARS", frozenset())
        hits = [c for c in _hits(S_SCREEN) if c.wrong == "屏等"]
        assert hits, "关掉虚字集后仍未报出，说明判据归因错了"


# ============================================================ ② 机构沿革
class TestOrgRename:
    """机构沿革：历史引述不报 + 整类提示级 + 错误短名映射已删除。"""

    def test_historical_citation_not_reported(self, tmp_db):
        """真实语料：征询旧机构意见的句子不得被改（原 4 处误报的主要来源）。"""
        for text in (S_ORG_1, S_ORG_2):
            assert not [c for c in _hits(text) if c.category == "机构沿革"], \
                (text, _hits(text))

    @pytest.mark.parametrize("text", [
        "1953年，卫生部成立。",
        "原卫生部发布的文件仍然有效。",
        "该机构前身为国家旅游局。",
        "经研究并征求财政部意见后印发。",
        "1998年组建的国家旅游局已撤销。",
    ])
    def test_historical_signals(self, tmp_db, text):
        """历史引述信号（年份/原·前·撤销/沿革词/征求…意见）一律不报。"""
        assert not [c for c in _hits(text) if c.category == "机构沿革"], (text, _hits(text))

    def test_current_org_name_still_hinted(self, tmp_db):
        """反例：新稿里裸用旧名仍要**提示**（只是不再自动替换）。"""
        hits = [c for c in _hits("根据环境保护部的要求执行。")
                if c.category == "机构沿革"]
        assert hits and hits[0].suggestion == "生态环境部"

    def test_org_category_is_advisory(self, tmp_db):
        """整类改为提示级：绝不进整篇应用/批量写回。"""
        assert "机构沿革" in corrector.ADVISORY_CATEGORIES
        fixed, cs = corrector.correct_block("根据环境保护部的要求执行。")
        assert fixed == "根据环境保护部的要求执行。"
        assert not [c for c in cs if c.category == "机构沿革"]

    def test_normal_sentence_not_treated_as_historical(self, tmp_db):
        """反例：含"目前/原则/前提"等常见词不算历史语境。

        裸字「原/前/曾」曾被列为标记 —— 那会让"目前""原则""前提"到处误触，
        等于把这条提示整类关掉，故只用两字标记。
        """
        text = "目前，根据环境保护部的原则要求推进前期工作。"
        assert [c for c in _hits(text) if c.category == "机构沿革"], _hits(text)

    def test_wrong_short_mapping_removed(self, tmp_db):
        """`法制办→司法局` 方向错误（国务院法制办 2018 并入司法部），整条删除。"""
        assert ("法制办", "司法局") not in ORG_RENAME_PAIRS
        # 全称级映射保留且方向正确
        assert ("国务院法制办公室", "司法部") in ORG_RENAME_PAIRS
        # 短名不再产生任何建议
        assert not [c for c in _hits("经研究并征求国务院法制办的意见。")
                    if c.wrong == "法制办"]


# ============================================================ ③ 做出 / 作出
class TestZuochu:
    """`做出` 只对**说法基本唯一**的抽象宾语报；答复/成绩等并存用法不报。"""

    @pytest.mark.parametrize("text", [
        S_ZUOCHU,                       # 官方原文：做出答复
        "各单位要做出成绩。",
        "对群众诉求做出回应。",
        "在关键问题上做出让步。",
        "就此做出解释。",
    ])
    def test_ambiguous_objects_not_reported(self, tmp_db, text):
        assert not [c for c in _hits(text) if c.wrong == "做出"], (text, _hits(text))

    @pytest.mark.parametrize("text", [
        "会议做出了决定。",
        "这是做出的部署。",
        "省政府作出批复。",             # 反例：已经用对的不报
    ])
    def test_canonical_objects_still_reported(self, tmp_db, text):
        hits = [c for c in _hits(text) if c.wrong == "做出"]
        if "作出批复" in text:
            assert not hits
        else:
            assert hits and hits[0].suggestion == "作出", (text, _hits(text))

    def test_rule_keeps_only_unique_objects(self):
        """数据层：宾语表已收窄，答复/回应/表态等并存用法不再驱动自动替换。"""
        rule = [r for r in CONTEXT_RULES if "做出" in r[0]]
        assert rule, "做出/作出 规则应仍然存在"
        pattern = rule[0][0]
        for removed in ("答复", "回应", "表态", "成绩"):
            assert removed not in pattern, f"{removed} 属并存用法，不应自动改"
        for kept in ("决定", "部署", "批复", "贡献"):
            assert kept in pattern, f"{kept} 应保留在宾语表内"


# ============================================================ ④ 书名号内不替换
class TestBookTitleParen:
    """书名号内是**原文引用**，其中的半角符号不得被"规范化"。"""

    def test_title_with_halfwidth_paren_not_converted(self, tmp_db):
        hits = [c for c in _hits(S_TITLE) if c.category == "标点"]
        assert not hits, [(c.wrong, c.suggestion) for c in hits]

    def test_unclosed_book_title_protects_to_end(self, tmp_db):
        """未闭合的《》从《 保护到串尾（渲染/粘贴半截时也不能改）。"""
        text = "见《法治中国建设规划(2020—2025年)"
        assert not [c for c in _hits(text) if c.category == "标点"]

    def test_paren_outside_title_still_converted(self, tmp_db):
        """反例：书名号外的中文括号照旧全角化（能力不能被一起关掉）。"""
        hits = [c for c in _hits("《某某规划》见(附件1)。") if c.category == "标点"]
        assert any(c.suggestion == "（附件1）" for c in hits), hits

    def test_halfwidth_comma_outside_title_is_real_hit(self, tmp_db):
        """反例：`办公厅,国务院` 的半角逗号是真命中，必须保留（真实语料确认）。"""
        hits = [c for c in _hits("国务院办公厅,国务院各部门要高度重视。")
                if c.category == "标点"]
        assert any(c.suggestion.endswith("，") for c in hits), hits

    def test_comma_inside_title_not_touched(self, tmp_db):
        """书名号内的半角逗号属引用原文，同样不改。"""
        text = "印发《关于印发,某某,的通知》。"
        assert not [c for c in _hits(text) if c.category == "标点"]


# ============================================================ ⑤ 全局不回归
class TestNoRegression:
    """收紧不能把既有能力一起收掉：E1 精标 100%、关键规则仍在。"""

    def test_curated_recall_spot_check(self, tmp_db):
        from gwtool.core.corrector_data import CURATED_PAIRS

        miss = []
        for wrong, correct, _cat, _conf in CURATED_PAIRS:
            if not corrector._is_valid_pair(wrong, correct):
                continue
            if not any(h.suggestion == correct for h in _hits(wrong)):
                miss.append(f"{wrong}->{correct}")
        assert not miss, f"精标对漏报：{miss[:8]}"

    @pytest.mark.parametrize("text,cat", [
        ("各单位要加强力度抓落实。", "搭配"),
        ("涉及到多个部门。", "语义冗余"),
        ("经研究决定，同意你单位请示事项。", None),  # 纯规范文本：不该有命中
    ])
    def test_other_layers_intact(self, tmp_db, text, cat):
        hits = _hits(text)
        if cat is None:
            assert not hits, [(c.wrong, c.suggestion) for c in hits]
        else:
            assert any(c.category == cat for c in hits), hits


# ============================================================ ⑥ v1.13.0 D5：重复字
# 真实语料扩到 35 篇 / 9.07 万字（gov 15 + 军报 10 + 求是 10）后，E3a 的 16 处
# 全部复核为误报，其中 12 处是重复字。逐条原句见 task-29：
#   一批批官兵 / 买买提江·吾买尔 / 红34师师长 / 第100团团长 / 韩京京 /
#   红旗猎猎 / 权权交易 / 一场场 / 一簇簇 / 段段接力 / 对标对表
# 修法是四类**结构性**守卫（与语义无关）：分词 token 覆盖 / token 本身成词 /
# 量词叠用表周遍 / 固定搭配白名单。
_REPEAT_FIXTURES = [
    ("一批批官兵重走长征路。", "批批"),
    ("买买提江·吾买尔表示。", "买买"),
    ("红34师师长陈树湘。", "师师"),
    ("红34师第100团团长韩伟之子韩京京。", "团团"),
    ("红34师第100团团长韩伟之子韩京京。", "京京"),
    ("红旗猎猎，长风浩荡。", "猎猎"),
    ("掩盖的是权权交易、权钱交易。", "权权"),
    ("在一场场殊死搏杀中。", "场场"),
    ("播撒下一簇簇革命火种。", "簇簇"),
    ("层层压实责任、段段接力推进。", "段段"),
    ("找遵循、找方法，对标对表、校准偏差。", "对对"),
]


@pytest.mark.usefixtures("dict_db")
class TestMilitaryCorpusRepeatGuard:
    """军报/求是语料上的 12 类重复字误报 → 全部抑制。"""

    @pytest.mark.parametrize("text,wrong", _REPEAT_FIXTURES)
    def test_fixture_not_reported(self, text, wrong):
        hits = [c for c in _hits(text) if c.category == "重复字"]
        assert not hits, f"{text!r} 仍报 {[(c.wrong, c.suggestion) for c in hits]}"
        assert not [c for c in hits if wrong in c.wrong], (text, wrong)

    @pytest.mark.parametrize("text,wrong", _REPEAT_FIXTURES)
    def test_no_other_hits(self, text, wrong):
        """这些句子除此之外也应洁净（避免"压掉重复字却冒出别的建议"）。"""
        assert not _hits(text), [(c.category, c.wrong, c.suggestion) for c in _hits(text)]

    # ---------------- 四类守卫的机制证明（关掉守卫就应恢复误报） ----------------
    def test_token_containment_guard(self, monkeypatch):
        """⑩ 连被更长 token 覆盖 → 放行。清空 spans 后 `师师长` 应恢复报出。"""
        assert not [c for c in _hits("红34师师长陈树湘。") if c.category == "重复字"]
        monkeypatch.setattr(repeat_rules, "_token_spans", lambda _t: [])
        hits = [c for c in _hits("红34师师长陈树湘。") if c.category == "重复字"]
        assert hits, "关掉分词守卫后仍未报出，说明归因错了"

    def test_exact_token_guard(self):
        """⑪ 连本身是 jieba 的词（猎猎/权权/段段）→ 放行。"""
        text = "红旗猎猎，长风浩荡。"
        i = text.index("猎猎")
        spans = repeat_rules._token_spans(text)
        assert repeat_rules._is_exact_token(i, 2, spans)
        assert not [c for c in _hits(text) if c.category == "重复字"]

    def test_quantifier_guard(self):
        """⑫ `一 + X + XX`（一批批/一场场/一簇簇）：量词叠用表周遍。"""
        assert repeat_rules._quantifier_reduplication("一批批", 1, 2, "批")
        assert repeat_rules._quantifier_reduplication("一场场", 1, 2, "场")
        assert not repeat_rules._quantifier_reduplication("一们们", 1, 2, "们")
        assert not repeat_rules._quantifier_reduplication("批次批", 1, 2, "批")

    def test_fixed_phrase_whitelist(self):
        """⑬ 固定搭配白名单：`对标对表` 会被 cXc 判成 `对标对`。"""
        text = "找遵循、找方法，对标对表、校准偏差。"
        i = text.index("对标对")
        assert repeat_rules._in_fixed_phrase(text, i, 3)
        assert not [c for c in _hits(text) if c.category == "重复字"]

    # ---------------- 正例：无意义重复仍要报 ----------------
    @pytest.mark.parametrize("text,want_wrong", [
        ("的的", "的的"),
        ("是是", "是是"),
        ("进行行。", "行行"),
        ("部门门。", "门门"),
        ("我我们出发。", "我我"),
    ])
    def test_meaningless_repeats_still_reported(self, text, want_wrong):
        """守卫只放行"结构性合法重复"，真多打的字必须照报（否则是漏报退化）。"""
        hits = [c for c in _hits(text) if c.category == "重复字"]
        assert any(c.wrong == want_wrong for c in hits), (text, hits)

    def test_dictionary_idioms_not_reported(self):
        """反例守护：词典成语里的叠字不能被"正例"顺手改成报错。

        `卿卿我我`（我我）与 `小时了了`（了了）都是词典词，属既有白名单/④
        的合法放行 —— 这也是"我我/了了 单独出现不报"的原因，不能为了
        凑正例把它们变成误报。
        """
        for text in ("两人卿卿我我。", "小时了了，大未必佳。"):
            assert not [c for c in _hits(text) if c.category == "重复字"], (text, _hits(text))


# ============================================================ ⑦ v1.13.0 D5：生成对
@pytest.mark.usefixtures("dict_db")
class TestAdverbPrefixedGeneratedPair:
    """`还誓→还是`：低置信生成对首字是常用虚词/副词 → 抑制。"""

    @pytest.fixture(autouse=True)
    def _pair(self):
        from gwtool.db import dao

        dao.add_error_pair("还誓", "还是", "测试", 0.55, source="generated")
        corrector.invalidate_cache()
        yield
        corrector.invalidate_cache()

    def test_real_sentence_not_reported(self):
        text = "截掉伤腿还誓要爬到陕北。"
        assert not [c for c in _hits(text) if c.wrong == "还誓"], _hits(text)

    def test_guard_is_the_reason(self, monkeypatch):
        """清空虚词集后应恢复报出 —— 证明抑制来自这条守卫。"""
        monkeypatch.setattr(corrector, "_LEADING_FUNCTION_CHARS", frozenset())
        hits = [c for c in _hits("截掉伤腿还誓要爬到陕北。") if c.wrong == "还誓"]
        assert hits

    def test_measured_expensive_chars_are_excluded(self):
        """E2 实测：首字为 却/可/只/太/就 的命中是**真实错形**，不能收。

        实测（`--profile base`）收进来会把 E2 从 645/800=80.6% 压到 638=79.8%：
        `就湿→就是`、`可揖→可以`、`却嗜→却是`、`只妖→只要`、`太样→太阳`。
        这五个字必须在集合外，否则就是"用召回换精度"。
        """
        for ch in "却可只太就":
            assert ch not in corrector._LEADING_FUNCTION_CHARS, (
                f"{ch} 有 E2 实测代价，不应放进虚词首字守卫")
        assert "还" in corrector._LEADING_FUNCTION_CHARS

    def test_curated_pairs_with_same_leading_char_unaffected(self, tmp_db):
        """高置信精标对（conf≥0.7）不进低置信守卫，首字是虚词也照报。"""
        hits = [c for c in _hits("这项布署工作要抓紧。") if c.wrong == "布署"]
        assert hits
