# -*- coding: utf-8 -*-
"""体检的文种识别（inspector._guess_kind）与主送机关窗口的测试。

缺陷背景（2026-10-29 实测）：文种原先只从**首行**认，真实材料整篇红头粘进来
时首行是版头（"××市人民政府办公室文件"），文种取不到，结束语/主送机关/
文种混用/篇幅**整组静默跳过** —— 同一份缺主送、缺结束语的通知：
  不带版头 → 2 条；带版头 → 0 条（假清关）。

修复后三个不变量：
  · 缺陷公文无论有没有版头都报出来；
  · **规范**的红头文件仍然 0 条（主送窗口必须随标题行后移，不能变成误报源）；
  · 调用方显式给 kind_hint 时仍优先用它。
"""
from __future__ import annotations

from gwtool.core.inspector import inspect_text

HEAD = "××市人民政府办公室文件\n\n××政办发〔2026〕15号\n\n"

# 缺主送、缺结束语的通知正文
BAD_BODY = ("关于开展安全生产大检查的通知\n"
            "一、检查范围\n全市各类生产经营单位。\n"
            "二、工作要求\n（一）加强组织领导。\n（二）严格执法检查。\n")

# 同一份材料，格式完整
GOOD_FULL = (HEAD + "关于开展安全生产大检查的通知\n\n"
             "各区县人民政府，市政府各部门：\n"
             "为进一步加强安全生产工作，现将有关事项通知如下。\n"
             "一、检查范围\n全市各类生产经营单位。\n"
             "二、工作要求\n（一）加强组织领导。各区县要成立领导小组。\n"
             "（二）严格执法检查。对发现的隐患依法处理。\n"
             "特此通知。\n\n××市人民政府办公室\n2026年10月28日\n")


def _items(text: str) -> list:
    return [f.item for f in inspect_text(text)]


class TestKindDetection:
    def test_defective_body_reports_both(self):
        got = inspect_text(BAD_BODY)
        assert set(_items(BAD_BODY)) >= {"结束语", "主送机关"}
        assert len(got) >= 2

    def test_defective_body_with_letterhead_still_reports(self):
        """核心回归：带版头不再整组静默跳过。"""
        assert {"结束语", "主送机关"} <= set(_items(HEAD + BAD_BODY))

    def test_compliant_full_letterhead_is_clean(self):
        """规范红头必须 0 条 —— 主送窗口随标题后移，不得变成误报源。"""
        assert inspect_text(GOOD_FULL) == []

    def test_report_mixing_pishi_is_flagged(self):
        """「报告不得夹带请示」同样依赖文种识别，带版头时也要能报。"""
        text = (HEAD + "关于2026年第三季度工作情况的报告\n"
                "一、主要工作\n（一）完成检查。\n二、下一步\n请批示。\n")
        assert "文种混用" in _items(text)

    def test_explicit_kind_hint_wins(self):
        """调用方指明文种时按它来：标题写「通知」但 hint 是「函」。"""
        got = [f.detail for f in inspect_text(BAD_BODY, kind_hint="函")]
        assert any("「函」" in d for d in got)
