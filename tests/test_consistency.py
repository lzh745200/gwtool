# -*- coding: utf-8 -*-
"""文内一致性校验（core/consistency.py）的测试。

这个模块的全部价值在于**精度**：它只提示、不自动改，误报一次用户就会永久关掉
功能。所以每类检测都同时锁「正例必须命中」和「反例必须零命中」两个方向，
反例覆盖三种典型情形：
  1) 全文只有一种写法；
  2) 数值/实体不同（只是写法相似）；
  3) 只出现一次（没有可比对象）。
"""
from __future__ import annotations

import time

import pytest

from gwtool.core import consistency
from gwtool.core.consistency import Inconsistency, check

ALLOWED_KINDS = {"数字", "日期", "单位", "机构名称", "标点形式"}


def _of(text: str, kind: str) -> list[Inconsistency]:
    return [f for f in check(text) if f.kind == kind]


# ================================================================ 数字 / 数量
class TestNumbersPositive:
    """同一单位 + 归一化后数值相同 + 阿拉伯/汉字两种写法 → 必须命中。"""

    def test_fullwidth_arabic_digits(self):
        """全角阿拉伯数字与汉字数字也是「两种写法」。"""
        text = "共３个工作日完成，另三个工作日复核。"
        hits = _of(text, "数字")
        assert len(hits) == 1
        assert {hits[0].first, hits[0].variant} == {"３个工作日", "三个工作日"}

    def test_workdays(self):
        text = "本通知自发布之日起3个工作日内反馈，逾期三个工作日视为放弃。"
        hits = _of(text, "数字")
        assert len(hits) == 1
        assert hits[0].first == "3个工作日"
        assert hits[0].variant == "三个工作日"
        assert hits[0].positions == [text.index("3个工作日"),
                                     text.index("三个工作日")]

    def test_people_same_value(self):
        text = "会议安排5人参会，另有五人列席。"
        hits = _of(text, "数字")
        assert len(hits) == 1
        assert hits[0].first == "5人"
        assert hits[0].variant == "五人"

    def test_measure_word_ge(self):
        text = "共3个项目，其中三个项目已完成。"
        hits = _of(text, "数字")
        assert len(hits) == 1
        assert {hits[0].first, hits[0].variant} == {"3个", "三个"}

    def test_magnitude_unit_normalized(self):
        """「10万元」与「十万元」数值都是 100000 元，必须归一化后才对得上。"""
        text = "经费共10万元，另需十万元。"
        hits = _of(text, "数字")
        assert len(hits) == 1
        assert {hits[0].first, hits[0].variant} == {"10万元", "十万元"}

    def test_decimal_chinese_numeral(self):
        text = "补贴标准为3.5倍，另有三点五倍奖励。"
        hits = _of(text, "数字")
        assert len(hits) == 1
        assert {hits[0].first, hits[0].variant} == {"3.5倍", "三点五倍"}


class TestNumbersNegative:
    def test_only_arabic(self):
        assert _of("本通知自发布之日起3个工作日内反馈。", "数字") == []

    def test_only_chinese(self):
        assert _of("本通知自发布之日起三个工作日内反馈。", "数字") == []

    def test_different_values_are_not_inconsistent(self):
        """「3人」与「五人」是两个数量，不是同一数量的两种写法。"""
        assert _of("会议安排3人参加，另有五人列席。", "数字") == []

    def test_ordinal_excluded(self):
        """「第3项」与「第三项」是序数，不做同一数量比对。"""
        assert _of("第3项与第三项。", "数字") == []

    def test_ordinal_with_space_excluded(self):
        """「第 8 项」带空格也按序数排除。"""
        assert _of("与 §三 第 8 项（八项清单）对应。", "数字") == []

    def test_determiner_yi_excluded(self):
        """「同一项目」「单一项目」里的「一」是限定词，不是计数。"""
        assert _of("同一项目应按1项标准执行，单一项目不得拆分。", "数字") == []

    def test_range_upper_bound_excluded(self):
        """「3—5个工作日」里的 5 是区间上限，不是独立数量。"""
        assert _of("计划3—5个工作日完成，另需五个工作日复核。", "数字") == []

    def test_hyphen_range_excluded(self):
        assert _of("配额 0-4 个，另设四个专项。", "数字") == []

    def test_approximate_suffix_excluded(self):
        """「一个多小时」是概数，不是 1 个。"""
        assert _of("耗时一个多小时，另需 1 个环节复核。", "数字") == []

    def test_version_or_label_prefix_excluded(self):
        """「P2 期」里的 2 是版本/编号标签，不是数量。"""
        assert _of("P2 期建设与二期扩展分别推进。", "数字") == []

    def test_approximate_numbers_not_guessed(self):
        """「三五天」是概数，不能归一到 35 去和「35天」比。"""
        assert _of("预计三五天完成，避免拖到35天以后。", "数字") == []

    def test_dates_are_not_numbers(self):
        assert _of("定于2026年10月3日召开。", "数字") == []


# ================================================================ 日期
class TestDatesPositive:
    def test_same_date_two_formats(self):
        text = "定于2026年10月3日召开，会议纪要须在2026-10-03前报送。"
        hits = _of(text, "日期")
        assert len(hits) == 1
        assert {hits[0].first, hits[0].variant} == {"2026年10月3日", "2026-10-03"}

    def test_dot_format_with_chinese_numeral(self):
        text = "截止2026.10.3，另见二〇二六年十月三日通知。"
        hits = _of(text, "日期")
        assert len(hits) == 1
        assert {hits[0].first, hits[0].variant} == {"2026.10.3", "二〇二六年十月三日"}

    def test_slash_format(self):
        text = "计划2026年10月1日启动，2026/10/1前完成。"
        hits = _of(text, "日期")
        assert len(hits) == 1

    def test_short_year_with_full_year(self):
        """年份简写与全写混用（26年10月 / 2026年）。"""
        text = "项目于2026年启动，26年10月完成。"
        hits = _of(text, "日期")
        assert len(hits) == 1
        assert "简写" in hits[0].label
        assert {hits[0].first, hits[0].variant} == {"26年10月", "2026年"}


class TestDatesNegative:
    def test_single_date(self):
        assert _of("定于2026年10月3日召开。", "日期") == []

    def test_different_dates(self):
        assert _of("2026年10月3日与2026年10月4日各开一次会。", "日期") == []

    def test_same_format_repeated(self):
        assert _of("2026-10-03报送，2026-10-03反馈。", "日期") == []

    def test_different_granularity(self):
        """「2026年10月」与「2026年10月3日」不是同一个日期，不报。"""
        assert _of("2026年10月开展，2026年10月3日总结。", "日期") == []

    def test_duration_year_is_not_short_year(self):
        """「成立26年」是时长，不是年份简写。"""
        assert _of("公司成立26年，于2026年迁址。", "日期") == []

    def test_standalone_format(self):
        assert _of("报送截止2026-10-03。", "日期") == []


# ================================================================ 计量单位
class TestUnitsPositive:
    def test_kilometer_meter(self):
        text = "全程50公里，返程60千米。"
        hits = _of(text, "单位")
        assert len(hits) == 1
        assert {hits[0].first, hits[0].variant} == {"公里", "千米"}

    def test_square_meter(self):
        text = "面积约30平方米，另有20平米仓库。"
        hits = _of(text, "单位")
        assert len(hits) == 1
        assert {hits[0].first, hits[0].variant} == {"平方米", "平米"}

    def test_kilogram(self):
        text = "货物重50公斤，另箱重60千克。"
        hits = _of(text, "单位")
        assert len(hits) == 1

    def test_liter(self):
        text = "共需3升，另备2公升。"
        hits = _of(text, "单位")
        assert len(hits) == 1
        assert {hits[0].first, hits[0].variant} == {"升", "公升"}

    def test_meter_gongchi(self):
        text = "巷道宽3米，高5公尺。"
        hits = _of(text, "单位")
        assert len(hits) == 1
        assert {hits[0].first, hits[0].variant} == {"米", "公尺"}


class TestUnitsNegative:
    def test_only_legal(self):
        assert _of("全程50公里。", "单位") == []

    def test_same_writing_twice(self):
        assert _of("全程50公里，返程60公里。", "单位") == []

    def test_different_concepts(self):
        """立方米与平方米是两种单位，各自只用一种写法。"""
        assert _of("体积3立方米，面积2平方米。", "单位") == []

    def test_compound_unit_consumed_whole(self):
        """「5毫米」里的「米」不是长度单位「米」，文中虽有「公尺」也不该报。"""
        assert _of("精度5毫米，长度3公尺。", "单位") == []

    def test_unit_char_inside_word(self):
        """「提升/升级」里的「升」不是容量单位。"""
        assert _of("提升效率，升级系统，共5公升。", "单位") == []

    def test_single_concept(self):
        assert _of("大米100公斤。", "单位") == []


# ================================================================ 机构名称
class TestOrgsPositive:
    def test_ministry_rename(self):
        text = "卫生部与国家卫生健康委员会联合发文。"
        hits = _of(text, "机构名称")
        assert len(hits) == 1
        assert {hits[0].first, hits[0].variant} == {"卫生部", "国家卫生健康委员会"}

    def test_bureau_rename(self):
        text = "原市环保局负责，现由市生态环境局承担。"
        hits = _of(text, "机构名称")
        assert len(hits) == 1
        assert {hits[0].first, hits[0].variant} == {"环保局", "生态环境局"}

    def test_ministry_of_agriculture(self):
        text = "由农业部转隶农业农村部。"
        hits = _of(text, "机构名称")
        assert len(hits) == 1

    def test_nested_old_name_reported_once(self):
        """「卫生部」是「国家卫生部」的子串，同一实体只报一次（报更长的旧称）。"""
        text = "原国家卫生部与国家卫生健康委员会均有规定。"
        hits = _of(text, "机构名称")
        assert len(hits) == 1
        assert hits[0].first == "国家卫生部"


class TestOrgsNegative:
    def test_new_name_only(self):
        assert _of("由市生态环境局负责。", "机构名称") == []

    def test_old_name_inside_new_name(self):
        """旧称「建设局」整体落在新称「住房和城乡建设局」内部，不是并存。"""
        assert _of("由住房和城乡建设局负责。", "机构名称") == []

    def test_same_name_twice(self):
        assert _of("市环保局与市环保局联合发文。", "机构名称") == []

    def test_unrelated_entities(self):
        """不同机构的旧称/新称各出现一次，不是「同一机构新旧并存」。"""
        assert _of("农业部与国家卫生健康委员会分别发文。", "机构名称") == []

    def test_single_old_name(self):
        assert _of("原卫生部负责此项工作。", "机构名称") == []


# ================================================================ 标点形式
class TestPunctPositive:
    def test_comma(self):
        text = "会议研究经费问题,并部署下一步工作，确保落实。"
        hits = _of(text, "标点形式")
        assert len(hits) == 1
        assert {hits[0].first, hits[0].variant} == {",", "，"}

    def test_colon(self):
        text = "会议明确:各单位要抓好落实，形成如下意见：确保完成。"
        hits = _of(text, "标点形式")
        assert len(hits) == 1
        assert {hits[0].first, hits[0].variant} == {":", "："}

    def test_semicolon(self):
        text = "一是抓学习;二是抓落实，三是强保障；四是促提升。"
        hits = _of(text, "标点形式")
        assert len(hits) == 1
        assert {hits[0].first, hits[0].variant} == {";", "；"}

    def test_parentheses_with_chinese_inside(self):
        text = "会议研究了(一)经费问题（二）人员安排。"
        hits = _of(text, "标点形式")
        assert len(hits) == 1
        assert {hits[0].first, hits[0].variant} == {"(", "（"}

    def test_parentheses_after_sentence_end(self):
        """句号后的「(一)」仍是中文语境里的半角括号。"""
        text = "会议结束。(一)经费问题另行研究（二）人员安排已定。"
        hits = _of(text, "标点形式")
        assert len(hits) == 1


class TestPunctNegative:
    def test_only_full_width(self):
        assert _of("会议研究经费问题，并部署下一步工作；确保落实。", "标点形式") == []

    def test_english_context(self):
        """英文括号/逗号是正常写法，不算中文标点混用。"""
        text = "使用 Python(3.12) 与 NumPy, SciPy 等库，详见（附件）。"
        assert _of(text, "标点形式") == []

    def test_thousand_separator(self):
        assert _of("共1,000元，另需2,000元。", "标点形式") == []

    def test_ascii_only_no_full_width(self):
        text = "Use Python(3.12), and NumPy, SciPy."
        assert _of(text, "标点形式") == []

    def test_time_and_url(self):
        text = "会议10:30开始，详见http://example.com/a。"
        assert _of(text, "标点形式") == []


# ================================================================ 聚合与契约
class TestAggregation:
    def test_same_entity_reported_once(self):
        text = ("3个工作日内反馈，三个工作日复核；"
                "另有3个工作日用于归档，三个工作日用于公示。")
        hits = _of(text, "数字")
        assert len(hits) == 1

    def test_positions_sorted_and_unique(self):
        text = "3个工作日内反馈，三个工作日复核，3个工作日归档，三个工作日公示。"
        hit = _of(text, "数字")[0]
        assert hit.positions == sorted(hit.positions)
        assert len(hit.positions) == len(set(hit.positions))
        assert len(hit.positions) == 4

    def test_positions_are_offsets_of_writings(self):
        text = "共10万元，另需十万元。"
        hit = _of(text, "数字")[0]
        for pos in hit.positions:
            assert 0 <= pos < len(text)

    def test_result_order_follows_first_position(self):
        text = ("原市环保局负责，现由市生态环境局承担；"
                "全程50公里，返程60千米；3个工作日内反馈，三个工作日复核。")
        hits = check(text)
        assert len(hits) == 3
        assert [h.kind for h in hits] == ["机构名称", "单位", "数字"]
        assert hits == sorted(hits, key=lambda h: h.positions[0])

    def test_every_finding_is_well_formed(self):
        text = ("原市环保局负责，现由市生态环境局承担；全程50公里，返程60千米；"
                "3个工作日内反馈，三个工作日复核；2026年10月3日与2026-10-03；"
                "会议研究经费问题,并部署下一步工作，确保落实。")
        hits = check(text)
        assert len(hits) == 5
        for hit in hits:
            assert hit.kind in ALLOWED_KINDS
            assert hit.label and hit.detail
            assert hit.first and hit.variant
            assert hit.first != hit.variant
            assert hit.first in text and hit.variant in text
            assert hit.positions
            assert hit.positions == sorted(set(hit.positions))
            assert len(hit.positions) <= consistency._MAX_POSITIONS

    def test_short_year_entity_reported_once(self):
        """同一年份的多处简写聚成一条（同一实体只报一次）。"""
        text = "项目于2026年启动，26年10月完成，26年度总结。"
        hits = _of(text, "日期")
        assert len(hits) == 1
        assert hits[0].positions == sorted(set(hits[0].positions))
        assert len(hits[0].positions) == 3  # 1 处全写 + 2 处简写

    def test_each_kind_capped(self):
        """单类最多报 _MAX_PER_KIND 条，避免长文刷屏。"""

        def cn(n: int) -> str:
            digits = "零一二三四五六七八九"
            if n < 10:
                return digits[n]
            if n == 10:
                return "十"
            if n < 20:
                return "十" + digits[n - 10]
            if n == 20:
                return "二十"
            return "二十" + digits[n - 20]

        text = "".join(f"{i}个工作日报送，{cn(i)}个工作日复核；"
                       for i in range(1, 22))
        hits = _of(text, "数字")
        assert 0 < len(hits) <= consistency._MAX_PER_KIND
        # 每个数值是独立实体，不能串成一条
        assert len({(h.first, h.variant) for h in hits}) == len(hits)


class TestContract:
    def test_dataclass_default_positions_are_independent(self):
        """positions 默认工厂必须是每个实例独立列表（共享可变默认值是经典坑）。"""
        first = Inconsistency(kind="数字", label="l", detail="d",
                              first="3个", variant="三个")
        second = Inconsistency(kind="数字", label="l", detail="d",
                               first="3个", variant="三个")
        assert first.positions == [] and second.positions == []
        first.positions.append(0)
        assert second.positions == []

    def test_returns_plain_list_of_dataclass(self):
        hits = check("3个工作日内反馈，三个工作日复核。")
        assert isinstance(hits, list)
        assert all(isinstance(h, Inconsistency) for h in hits)
        assert set(ALLOWED_KINDS) >= {h.kind for h in hits}

    def test_empty_text(self):
        assert check("") == []

    def test_whitespace_only(self):
        assert check("   \n\t\r\n ") == []

    def test_none_is_tolerated(self):
        assert check(None) == []  # type: ignore[arg-type]

    def test_non_str_is_tolerated(self):
        assert check(b"3\xe4\xb8\xaa") == []  # type: ignore[arg-type]

    def test_idempotent(self):
        text = "共3个工作日，另三个工作日；全程50公里，返程60千米。"
        first = check(text)
        second = check(text)
        assert first == second

    def test_input_not_modified(self):
        text = "共3个工作日，另三个工作日。"
        before = text
        check(text)
        assert text == before

    def test_consistent_document_has_no_findings(self):
        """全篇统一写法的规范公文段落必须零命中。"""
        text = (
            "各区县人民政府，市政府各部门：\n"
            "为进一步规范公文办理，现将有关事项通知如下。\n"
            "一、各单位应在3个工作日内反馈意见，逾期视为无意见。\n"
            "二、会议定于2026年10月3日召开，材料于2026年10月1日前报送。\n"
            "三、全市共设5个分会场，安排5名联络员。\n"
            "四、全程约50千米，车辆油耗按每千米8升核定。\n"
            "五、本通知由市生态环境局负责解释。\n"
        )
        assert check(text) == []

    def test_internal_exception_does_not_propagate(self, monkeypatch):
        """某类检测内部炸了，其余类照报，绝不把异常抛给 UI。"""

        def boom(_text):
            raise RuntimeError("boom")

        for name in ("_check_numbers", "_check_dates", "_check_units",
                     "_check_orgs", "_check_punct"):
            monkeypatch.setattr(consistency, name, boom)
        assert check("会议研究经费问题,并部署下一步工作，确保落实。") == []

    def test_one_broken_detector_others_still_work(self, monkeypatch):
        def boom(_text):
            raise RuntimeError("boom")

        monkeypatch.setattr(consistency, "_check_numbers", boom)
        hits = check("会议研究经费问题,并部署下一步工作，确保落实。")
        assert [h.kind for h in hits] == ["标点形式"]


# ================================================================ 性能
class TestPerformance:
    def _long_text(self) -> str:
        paragraph = (
            "各区县人民政府，市政府各部门：为进一步规范公文处理，现将有关事项通知如下。"
            "一、严格执行3个工作日内反馈制度，逾期三个工作日视为放弃。"
            "二、会议定于2026年10月3日召开，材料须在2026-10-03前报送。"
            "三、全程50公里，返程60千米，面积约30平方米，另有20平米仓库。"
            "四、原市环保局负责，现由市生态环境局承担。"
            "五、经费共10万元，另需十万元；标准为3.5倍，另有三点五倍补贴。"
        )
        return paragraph * 32

    def test_long_text_under_200ms(self):
        text = self._long_text()
        assert len(text) >= 5000
        start = time.perf_counter()
        hits = check(text)
        elapsed = time.perf_counter() - start
        assert hits, "长文里确实存在不一致，性能用例同时验证功能"
        # 实测量级约 2~5ms，200ms 是给慢 CI 机器留的余量
        assert elapsed < 0.2, f"check() 耗时 {elapsed * 1000:.1f}ms，超过 200ms"

    def test_long_text_is_deterministic(self):
        text = self._long_text()
        assert check(text) == check(text)


# ================================================================ 精度回归
class TestPrecisionRegression:
    """真实公文/技术文档里踩过的误报形态，逐条锁死。"""

    @pytest.mark.parametrize("text", [
        "公司成立26年，于2026年迁址。",                       # 时长 vs 年份
        "用 Python(3.12) 与 NumPy, SciPy，详见（附件）。",     # 英文语境
        "精度5毫米，长度3公尺。",                               # 复合单位
        "由住房和城乡建设局负责。",                             # 旧称是新称子串
        "第3项与第三项。",                                     # 序数
        "共1,000元，另需2,000元。",                            # 千分位
        "会议10:30开始，详见http://example.com/a。",           # 时间/URL
        "提升效率，升级系统，共5公升。",                        # 构词用字
    ])
    def test_no_false_positive(self, text):
        assert check(text) == []
