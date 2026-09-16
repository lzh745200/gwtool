# -*- coding: utf-8 -*-
"""数字用法（全文一致性）检查的测试。

本检查只做纠错层**做不到**的事——跨全文的写法一致性。逐处的数字规范
（汉字年份、相邻概数、百分号）已由纠错层 NUMBER_RULES 覆盖，这里刻意不重复，
以免同一问题在纠错与体检两处各报一次。

重点在**误报保护**：政治术语里的汉字数字（「三个代表」）绝不能被当成
"与 3个 不统一"，否则用户会立刻关掉整个检查。
"""
from __future__ import annotations

from gwtool.core.inspector import _mask_cn_terms, inspect_text


def _nums(text: str) -> list:
    return [f for f in inspect_text(text) if f.item == "数字用法"]


def _details(text: str) -> str:
    return " | ".join(f.detail for f in _nums(text))


class TestConsistency:
    def test_flags_mixed_forms_for_same_classifier(self):
        got = _details("共排查3个隐患。另有一个部门未按期完成。")
        assert "不统一" in got

    def test_accepts_all_arabic(self):
        assert "不统一" not in _details("共排查3个隐患。另有1个部门未完成。")

    def test_accepts_all_chinese(self):
        assert "不统一" not in _details("共排查三个隐患。另有一个部门未完成。")

    def test_flags_mixed_percent_forms(self):
        got = _details("完成率100%，其中新增大户占百分之三十。")
        assert "百分比写法全文不统一" in got

    def test_accepts_single_percent_form(self):
        assert "百分比" not in _details("完成率100%，其中新增大户占30%。")

    def test_reports_line_numbers(self):
        got = _details("第一行没有数字。\n共3个部门。\n另有一个部门。")
        assert "第2行" in got and "第3行" in got


class TestFalsePositiveGuards:
    """误报保护——这决定用户是否会关掉这个检查。"""

    def test_political_terms_do_not_count_as_chinese_numerals(self):
        # 「三个代表」不含计量义，不得与「3个」一起被判为"不统一"
        text = "深入学习三个代表重要思想，本次共排查3个隐患。"
        assert "不统一" not in _details(text)

    def test_political_terms_with_chinese_counter_elsewhere(self):
        # 文中确有一个汉字量词（一个部门）时，才应该报——术语本身仍不参与
        text = "贯彻四个全面战略布局，共3个部门、一个直属单位参加。"
        assert "不统一" in _details(text)

    def test_time_units_are_not_tracked(self):
        # 「一年来」与「1年」并存属正常修辞，纳入统计只会制造噪音
        assert "不统一" not in _details("一年来，共完成1年期的3项任务。")

    def test_single_occurrence_is_not_reported(self):
        assert "不统一" not in _details("共3个部门参加了会议。")


class TestLevelAndWiring:
    def test_all_findings_are_info_level(self):
        """数字用法全部为提示级：误判为"错别字"会让用户对纠错失去信任。"""
        text = "共3个部门，另有一个部门未报。完成率100%，其中百分之三十为新增。"
        fs = _nums(text)
        assert fs, "用例文本应至少触发一条"
        assert all(f.severity == "info" for f in fs)

    def test_mask_preserves_length(self):
        """掩码必须等长替换，否则字符偏移变化会导致行号错位。"""
        src = "贯彻三个代表要求"
        assert len(_mask_cn_terms(src)) == len(src)

    def test_mask_does_not_introduce_newlines(self):
        src = "贯彻四个全面布局，落实五位一体要求"
        masked = _mask_cn_terms(src)
        assert masked.count("\n") == src.count("\n")

    def test_check_is_wired_into_inspect_text(self):
        """元测试：负向用例在检查被摘掉后会假绿，需正向锚点。"""
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        code = (root / "gwtool" / "core" / "inspector.py").read_text(
            encoding="utf-8")
        assert "def _check_number_usage(" in code
        assert "out.extend(_check_number_usage(lines))" in code, (
            "检查函数存在但未接入 inspect_text —— 负向用例会假绿")
