# -*- coding: utf-8 -*-
"""B 批 T6：法定文种指纹与写作提示误报（P1-18）。

背景（v1.9.0 审计实测）：规范「通知」样张

    detect_kind -> [('经验总结', 0.751), ('经验材料', 0.870), ('工作方案', 0.919)]

因为 ``style_data`` 当时只有 7 种**无固定模板**文体指纹，法定文种没有可比
基线，任何通知/请示/报告都会被判成经验类，写作提示因此恒发
"本文更像「经验总结」"——每一条规范公文都吃一条误导性软提示。

本文件守四件事：

1. **法定文种指纹数据完整**：10 个文种各有自撰示意样张与
   (p25, p75, 中位数) 参数；且**不进入** ``REF``（"法定公文不设写作技法参数行"
   的既有契约不回退）；
2. **法定文种样张识别回自身**：标题写明「…的通知」即确定性证据，优先于统计
   相似度（P1-18 的直接症状消失）；
3. **写作提示不再误报**：通知/请示/报告/意见/函 5 类样张的任何提示里都不再出现
   "更像经验总结"（法定文种不出跨族"更像"提示，仍全部 info 级、只提示不写回）；
4. **7 份无固定模板文体样张逐字不回退**：``detect_kind`` 结果与
   ``style_profile.match_genre`` 完全一致（连距离数值都相等），且提示口径不变。
"""
from __future__ import annotations

import pytest

from gwtool.core import style_data as D
from gwtool.core import style_profile as SP
from gwtool.core import writing_hints as WH

#: 验收要求的 5 类法定文种
ACCEPT_KINDS = ("通知", "请示", "报告", "意见", "函")

#: P1-18 主控实测的原样张（规范通知，579 字符；标题与结束语齐全）
NOTICE_SAMPLE = (
    "关于进一步加强安全生产工作的通知\n各市人民政府：\n"
    "为深入贯彻上级部署，现就有关事项通知如下。\n"
    "一、提高认识\n各部门要充分认识做好安全生产工作的极端重要性。\n"
    "二、明确任务\n（一）开展排查整治。\n（二）强化督导考核。\n"
    "三、工作要求\n请各单位抓好落实。\n特此通知。\n"
)

_FILLER = "各单位要按照职责分工抓好落实，确保各项任务按期完成。"
_REQ = "要压实责任、细化措施、严格督导，确保工作取得实效。"


def _pad(text: str, target: int = 1250) -> str:
    """把样张补到语料中位篇幅（约 1250 字）；保留最后两行（结束语/落款）在末尾。"""
    lines = text.split("\n")
    tail = lines[-2:]
    body = "\n".join(lines[:-2])
    while len(body) + sum(len(ln) + 1 for ln in tail) < target:
        body += "\n" + _FILLER * 2
    return body + "\n" + "\n".join(tail)


def _style_samples() -> dict[str, str]:
    """7 份无固定模板文体样张（结构按 ``style_data.ID_PARAM`` 的身份证特征搭）。"""
    samples = {}
    samples["调研报告"] = _pad(
        "关于全市安全生产工作情况的调研报告\n"
        "一、调研缘起\n为深入了解全市安全生产工作情况，我们组织专题调研。" + _FILLER * 8 + "\n"
        "二、做法与成效\n（一）健全责任体系。" + _FILLER * 6 + "\n"
        "（二）强化隐患整治。" + _FILLER * 6 + "\n（三）夯实基层基础。" + _FILLER * 6 + "\n"
        "三、存在问题及原因\n一是责任落实还不到位。二是监管存在盲区。三是基层力量薄弱。"
        + _FILLER * 6 + "\n"
        "四、几点建议\n一是完善责任体系。二是加强监管执法。三是强化基础保障。"
        + _FILLER * 6 + "\n")
    samples["领导讲话"] = _pad(
        "在全市安全生产工作会议上的讲话\n同志们：\n"
        "一、深刻认识安全生产工作的极端重要性\n"
        + (_REQ + "要始终把人民生命安全放在首位。") * 12 + "\n"
        "二、突出重点领域隐患排查整治\n" + (_REQ + "要聚焦重点行业领域。") * 10 + "\n"
        "三、切实强化安全生产责任落实\n" + (_REQ + "要严格考核问责。") * 10 + "\n")
    samples["工作意见"] = _pad(
        "关于加强安全生产工作的意见\n"
        "一、总体要求\n（一）指导思想。坚持安全第一、预防为主。" + _FILLER * 5 + "\n"
        "（二）基本原则。坚持党政同责、一岗双责。" + _FILLER * 5 + "\n"
        "二、重点任务\n"
        + "".join(f"（{c}）加强重点领域整治。" + _FILLER * 3 + "\n"
                  for c in "一二三四五六七八")
        + "三、保障措施\n（一）加强组织领导。" + _FILLER * 4 + "\n"
        "（二）强化资金保障。" + _FILLER * 4 + "\n"
        "（三）严格督导考核。" + _FILLER * 4 + "\n"
        "以上意见，请结合实际认真贯彻落实。")
    samples["经验材料"] = _pad(
        "坚持党建引领提升基层治理效能\n"
        "一、健全组织体系，把党的领导贯穿基层治理全过程\n" + _FILLER * 8 + "\n"
        "一是建强组织。二是下沉力量。三是完善机制。" + _FILLER * 8 + "\n"
        "1．优化网格设置。" + _FILLER * 3 + "\n"
        "二、聚焦群众需求，提升服务群众能力\n" + _FILLER * 8 + "\n"
        "一是优化服务。二是办好实事。三是回应诉求。" + _FILLER * 8 + "\n"
        "三、深化数字赋能，提高治理精细水平\n" + _FILLER * 8 + "\n"
        "一是建设平台。二是数据共享。三是闭环处置。" + _FILLER * 8 + "\n")
    samples["工作方案"] = _pad(
        "关于全市安全生产专项整治工作方案\n"
        "一、工作目标\n到今年年底，全市安全生产形势持续稳定好转。" + _FILLER * 5 + "\n"
        "二、重点任务\n（一）摸清底数。" + "1．开展拉网式排查。" + _FILLER * 2
        + "2．建立问题台账。" + _FILLER * 2 + "3．实行动态更新。" + _FILLER * 2 + "\n"
        "（二）补齐短板。" + "1．加快设施建设。" + _FILLER * 2
        + "2．加装监测设备。" + _FILLER * 2 + "3．整治违规行为。" + _FILLER * 2 + "\n"
        "三、实施步骤\n（一）动员部署阶段。" + _FILLER * 3 + "\n"
        "（二）集中整治阶段。" + _FILLER * 3 + "\n"
        "四、保障措施\n（一）加强组织领导。" + _FILLER * 3 + "\n"
        "（二）强化资金保障。" + _FILLER * 3 + "\n")
    samples["经验总结"] = _pad(
        "以党建引领推动基层治理提质增效\n"
        "一、总体成效\n今年以来，我们坚持党建引领，推动基层治理取得新成效。"
        + _FILLER * 6 + "\n"
        "二、主要做法\n一是建强“红色堡垒”。" + _FILLER * 6 + "\n"
        "二是织密“服务网络”。" + _FILLER * 6 + "\n"
        "三是搭建“协商平台”。" + _FILLER * 6 + "\n"
        "三、工作体会\n一是必须坚持党的领导。" + _FILLER * 5 + "\n"
        "二是必须坚持群众路线。" + _FILLER * 5 + "\n")
    samples["短经验材料"] = _pad(
        "党建引领基层治理的生动实践\n"
        "近年来，我们坚持党建引领，把党的组织优势转化为治理效能。" + _FILLER * 4 + "\n"
        "建强组织体系，筑牢治理根基。" + _FILLER * 4 + "\n"
        "党支部书记李某某说：“过去群众办事跑多次，现在一次就办好。”" + _FILLER * 4 + "\n"
        "优化服务供给，回应群众关切。" + _FILLER * 4 + "\n"
        "居民王某某说：“小区环境越来越好，大家心里更踏实了。”" + _FILLER * 3 + "\n")
    return samples


STYLE_SAMPLES = _style_samples()


def _titles(text: str, kind: str = "") -> str:
    return " ".join(h.title for h in WH.hints(text, kind))


def _legacy_rank(text: str) -> list[tuple[str, float]]:
    """v1.9.0 的口径：只与 7 种无固定模板文体比。"""
    return list(SP.match_genre(SP.analyze(text)))


# ================================================================ 数据层
class TestLegalFingerprintData:
    def test_every_legal_style_has_sample_direction_and_row(self):
        for genre in D.LEGAL_STYLES:
            assert D.LEGAL_SAMPLES[genre].strip(), f"{genre} 缺少示意样张"
            assert D.LEGAL_REF[genre], f"{genre} 缺少识别指纹"
            assert D.LEGAL_DIRECTION[genre] in ("上行", "下行", "平行")
            assert D.LEGAL_ID_PARAM[genre]

    def test_sample_keys_match_style_keys(self):
        assert set(D.LEGAL_SAMPLES) == set(D.LEGAL_STYLES)
        assert set(D.LEGAL_REF) == set(D.LEGAL_STYLES)

    def test_legal_rows_are_lo_hi_mid_triples(self):
        for genre, row in D.LEGAL_REF.items():
            for key in WH._LEGAL_MATCH_KEYS:
                lo, hi, mid = row[key]
                assert lo <= mid <= hi, f"{genre}.{key} 中位数不在区间内"
            assert row["n"] == 0, "法定文种是示意样本，n=0 表示非语料统计"

    def test_each_sample_lies_in_its_own_band(self):
        """参数必须由样张量化而来：样张落在自己的区间内，参数才有可复核来源。"""
        for genre in D.LEGAL_STYLES:
            a = SP.analyze(D.LEGAL_SAMPLES[genre])
            for key in WH._LEGAL_MATCH_KEYS:
                lo, hi, _mid = D.LEGAL_REF[genre][key]
                assert lo <= a[key] <= hi, (
                    f"{genre}.{key}={a[key]} 不在自身区间 {lo}-{hi}")

    def test_legal_styles_not_entering_ref(self):
        """既有契约：法定公文不设写作技法参数行（REF/骨架/硬冲突都不掺）。"""
        for genre in D.LEGAL_STYLES:
            assert genre not in D.REF
            assert genre in D.NO_PARAM
        # 无固定模板文体集合保持不变（7 种，识别契约据此判定）
        assert len(D.STYLES) == 7

    def test_title_marks_and_closings_cover_all_styles(self):
        marked = {genre for genre, _suffixes in D.LEGAL_TITLE_MARKS}
        assert marked == set(D.LEGAL_STYLES)
        assert set(D.LEGAL_CLOSINGS) == set(D.LEGAL_STYLES)

    def test_match_keys_mirror_style_profile(self):
        """距离口径必须与 style_profile 一致（quote_long 只在短经验材料定义）。"""
        assert set(SP._MATCH_KEYS) - set(WH._LEGAL_MATCH_KEYS) == {"quote_long"}
        assert tuple(k for k in SP._MATCH_KEYS
                     if k in WH._LEGAL_MATCH_KEYS) == WH._LEGAL_MATCH_KEYS


# ================================================================ 识别
class TestLegalDetection:
    @pytest.mark.parametrize("genre", D.LEGAL_STYLES)
    def test_sample_identifies_itself(self, genre):
        res = WH.detect_kind(D.LEGAL_SAMPLES[genre], limit=3)
        assert res and res[0][0] == genre, f"{genre} 样张被识别成 {res}"

    @pytest.mark.parametrize("genre", D.LEGAL_STYLES)
    def test_title_is_authoritative(self, genre):
        """标题写明「…的通知」就是确定性证据：该文种距离记 0 排在首位。"""
        genre_hint, source = WH._legal_marker(D.LEGAL_SAMPLES[genre])
        assert (genre_hint, source) == (genre, "title")
        res = WH.detect_kind(D.LEGAL_SAMPLES[genre], limit=3)
        assert res[0] == (genre, 0.0)

    def test_lead_notice_sample_no_longer_looks_like_experience(self):
        """P1-18 原始复现：0.751 的『经验总结』top1 必须消失。"""
        legacy = _legacy_rank(NOTICE_SAMPLE)
        assert legacy[0][0] == "经验总结", "样本前提变了：审计实测 top1 是经验总结"
        res = WH.detect_kind(NOTICE_SAMPLE, limit=3)
        assert res[0][0] == "通知", res
        assert "经验总结" not in [g for g, _d in res]

    def test_closing_only_marker_is_just_a_clue(self):
        """只有结束语、没有标题时标记来源必须是 closing（要配合统计距离用）。"""
        body = "现将有关事项通知如下。\n一、提高认识。\n二、明确任务。\n特此通知。\n"
        assert WH._legal_marker(body) == ("通知", "closing")

    def test_title_marker_tolerates_document_header_lines(self):
        """真实公文粘贴后前几行是发文机关标志/发文字号，标题在第 3 行也要认。"""
        text = ("××市人民政府文件\n"
                "×政发〔2026〕15号\n"
                "关于进一步加强安全生产工作的通知\n"
                "各区县人民政府：\n现将有关事项通知如下。\n特此通知。\n")
        assert WH._legal_marker(text) == ("通知", "title")
        assert WH.detect_kind(text, limit=1)[0][0] == "通知"

    def test_no_marker_means_legacy_ranking(self):
        """没有任何法定文种迹象的文本，识别结果与既有 7 文体口径完全一致。"""
        text = STYLE_SAMPLES["经验总结"]
        assert WH._legal_marker(text) == ("", "")
        assert WH.detect_kind(text, limit=5) == _legacy_rank(text)[:5]

    def test_unknown_text_safe(self):
        assert WH.detect_kind("") == []
        assert WH.detect_kind("   \n") == []
        assert WH.detect_kind(None) == []  # type: ignore[arg-type]


# ================================================================ 提示
class TestNoMisleadingHint:
    @pytest.mark.parametrize("genre", ACCEPT_KINDS)
    def test_legal_samples_get_no_more_like_hint(self, genre):
        """验收核心：5 类法定文种样张不再出现"更像"（尤其"更像经验总结"）。"""
        titles = _titles(D.LEGAL_SAMPLES[genre], genre)
        assert "更像" not in titles, titles
        assert "经验总结" not in titles

    def test_lead_notice_sample_no_misleading_hint(self):
        titles = _titles(NOTICE_SAMPLE, "通知")
        assert "更像" not in titles, titles
        assert "更像经验总结" not in titles

    @pytest.mark.parametrize("legal_kind", ["通知", "报告", "意见", "条例", "通告"])
    def test_all_legal_kinds_suppress_cross_family_hint(self, legal_kind):
        """即便正文其实是无模板文体，法定文种也不出跨族"更像"提示。"""
        titles = _titles(STYLE_SAMPLES["经验总结"], legal_kind)
        assert "更像" not in titles, titles

    def test_legal_kind_still_gets_other_hints(self):
        """不能"修误报"修成一片空白：法定文种其它提示照旧。"""
        hs = WH.hints(D.LEGAL_SAMPLES["通知"], "通知")
        assert hs, "通知样张不应零提示"
        assert all(h.scope != "文风" or "更像" not in h.title for h in hs)

    def test_style_kind_keeps_legacy_hint(self):
        """7 种无固定模板文体的"更像"提示口径不变（只提示、info 级）。"""
        text = STYLE_SAMPLES["经验总结"]
        legacy_top = _legacy_rank(text)[0][0]
        kind = "工作意见" if legacy_top != "工作意见" else "工作方案"
        hs = [h for h in WH.hints(text, kind) if "更像" in h.title]
        assert hs, "既有文种的『更像』提示不应被顺手删掉"
        assert f"更像「{legacy_top}」" in hs[0].title
        assert hs[0].level == "info"


# ================================================================ 不回退
class TestStyleSamplesNoRegression:
    @pytest.mark.parametrize("genre", D.STYLES)
    def test_detect_kind_equals_legacy_for_style_samples(self, genre):
        text = STYLE_SAMPLES[genre]
        assert WH.detect_kind(text, limit=5) == _legacy_rank(text)[:5]
        assert WH.detect_kind(text) == _legacy_rank(text)[:3]

    @pytest.mark.parametrize("genre", D.STYLES)
    def test_style_ranking_only_contains_style_names(self, genre):
        res = WH.detect_kind(STYLE_SAMPLES[genre], limit=10)
        assert all(name in D.STYLES for name, _d in res), res

    def test_quoted_legal_closing_does_not_hijack(self):
        """正文引用别家公文的"以上意见"只是线索：统计距离明显更优才允许并入。

        工作意见样张结尾就是「以上意见…」（合法公文写法），但它仍应被识别为
        工作意见，而不是被法定"意见"抢位。
        """
        text = STYLE_SAMPLES["工作意见"]
        assert WH._legal_marker(text) == ("意见", "closing"), "样本应命中结束语线索"
        assert WH.detect_kind(text, limit=3) == _legacy_rank(text)[:3]

    @pytest.mark.parametrize("genre", D.STYLES)
    def test_no_hard_conflict_on_style_samples(self, genre):
        """既有契约不回退：7 份规范样张的硬冲突必须为 0（只有硬冲突判错）。"""
        v = SP.verdict(STYLE_SAMPLES[genre], genre)
        assert v.ok, [f.message for f in v.hard]

    def test_hint_set_unchanged_for_style_kinds(self):
        """比对口径：既有 7 样张的完整提示行里不得掺入法定文种名。"""
        for genre in D.STYLES:
            text = STYLE_SAMPLES[genre]
            lines = "\n".join(h.line for h in WH.hints(text, genre))
            for legal in D.LEGAL_STYLES:
                assert f"更像「{legal}」" not in lines, (genre, legal)


# ================================================================ 健壮性
class TestRobustness:
    def test_detect_kind_swallows_analyze_failure(self, monkeypatch):
        def boom(_text):
            raise RuntimeError("量化失败")

        monkeypatch.setattr(SP, "analyze", boom)
        assert WH.detect_kind(D.LEGAL_SAMPLES["通知"]) == []

    def test_detect_kind_survives_empty_legal_data(self, monkeypatch):
        monkeypatch.setattr(D, "LEGAL_REF", {})
        text = D.LEGAL_SAMPLES["通知"]
        # 没有法定基线时退回既有口径，不得抛异常
        assert WH.detect_kind(text, limit=3) == _legacy_rank(text)[:3]

    def test_hints_survive_empty_legal_data(self, monkeypatch):
        monkeypatch.setattr(D, "LEGAL_REF", {})
        monkeypatch.setattr(D, "LEGAL_TITLE_MARKS", ())
        assert isinstance(WH.hints(D.LEGAL_SAMPLES["通知"], "通知"), list)

    def test_detect_kind_returns_ascending_distances(self):
        for genre in ACCEPT_KINDS:
            res = WH.detect_kind(D.LEGAL_SAMPLES[genre], limit=5)
            assert [d for _g, d in res] == sorted(d for _g, d in res)
