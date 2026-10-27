# -*- coding: utf-8 -*-
"""P1 一致性校验补全：一级序号跳号 + 日期区间倒序（只提示）。"""
from __future__ import annotations

from gwtool.core import consistency


def test_sequence_gap_detected():
    text = "一、总体要求\n二、主要任务\n四、保障措施"
    kinds = {(f.kind, f.label) for f in consistency.check(text)}
    assert ("序号", "序号不连续：二、之后直接出现四、") in kinds


def test_sequence_normal_and_restart_no_report():
    normal = "一、甲\n二、乙\n三、丙\n四、丁"
    assert not [f for f in consistency.check(normal) if f.kind == "序号"]
    # 分章重新编号（一、二、三 后又从 一、 起）是常态，不得报
    restart = "一、甲\n二、乙\n三、丙\n一、新章\n二、续"
    assert not [f for f in consistency.check(restart) if f.kind == "序号"]


def test_date_range_inverted():
    bad = "2026年10月至2025年3月"
    found = [f for f in consistency.check(bad) if f.kind == "日期区间"]
    assert found and "颠倒" in found[0].label


def test_date_range_normal_no_report():
    for text in ("2025年3月至2026年10月",
                 "2026年10月1日至10月7日",
                 "2026年10月1日—2026年10月7日"):
        assert not [f for f in consistency.check(text)
                    if f.kind == "日期区间"], text


def test_other_checks_still_work():
    """新增两类不能挤掉既有检查（数字写法不一致仍须报）。"""
    text = "本次共3个工作日，另有三个工作日用于整改。"
    kinds = {f.kind for f in consistency.check(text)}
    assert "数字" in kinds
