# -*- coding: utf-8 -*-
"""文内一致性检查（inspector._check_consistency）的测试。

本检查只报"疑似同一单位的简称与全称并存"，**不判对错**。
误报保护是重点：一旦把「安全科」与「安全科研」当成同一个人/单位，
用户就会永久关掉这个功能。
"""
from __future__ import annotations

import gwtool.core.inspector as insp
from gwtool.core.inspector import _is_variant, inspect_text


def _cons(text: str) -> list:
    return [f for f in inspect_text(text) if f.item == "一致性"]


def _details(text: str) -> str:
    return " | ".join(f.detail for f in _cons(text))


class TestIsVariant:
    def test_short_and_full_name(self):
        assert _is_variant("县应急局", "县应急管理局") is True

    def test_prefix_is_not_a_variant(self):
        """「安全科」与「安全科研」是不同的词，不是简写关系。"""
        assert _is_variant("安全科", "安全科研") is False

    def test_办公_室suffix_extension_is_not_variant(self):
        # 短名恰是长名前缀——可能本就是两个主体，不猜
        assert _is_variant("县财政局", "县财政局办公室") is False

    def test_too_short_names_ignored(self):
        assert _is_variant("A局", "A管理局") is False

    def test_moderate_gap_still_detected(self):
        """差 4 字以内的简写仍应被识别——「市安全监管局」确实是简称。"""
        assert _is_variant("市安全监管局", "市安全生产监督管理局") is True

    def test_large_length_gap_ignored(self):
        """长度差过大已不像简写，不去猜。"""
        assert _is_variant("市安全局", "市安全生产监督管理局") is False

    def test_different_suffix_ignored(self):
        assert _is_variant("县应急局", "县应急办") is False

    def test_identical_names_are_not_variants(self):
        assert _is_variant("县应急局", "县应急局") is False

    def test_order_does_not_matter(self):
        assert _is_variant("县应急管理局", "县应急局") is True


class TestDetection:
    def test_flags_coexisting_forms(self):
        text = "县应急局下发了通知。县应急管理局要求各科室抓好落实。"
        assert "并存" in _details(text)

    def test_single_form_is_clean(self):
        text = "县应急管理局下发了通知，县应急管理局要求各科室抓好落实。"
        assert _cons(text) == []

    def test_reports_counts(self):
        text = ("县应急局下发了通知。县应急管理局要求落实。"
                "县应急管理局又补充了要求。")
        got = _details(text)
        assert "县应急局" in got and "县应急管理局" in got
        assert "1 次" in got and "2 次" in got

    def test_does_not_judge_which_is_right(self):
        """只报并列、不判对错——两个写法可能都不规范，或本就是两个主体。"""
        text = "县应急局与县应急管理局先后发文。"
        for f in _cons(text):
            assert "应为" not in f.detail and "建议改为" not in f.detail

    def test_severity_is_warn(self):
        text = "县应急局与县应急管理局先后发文。"
        assert all(f.severity == "warn" for f in _cons(text))


class TestGuards:
    def test_entity_count_cap(self, monkeypatch):
        """实体过多时跳过两两比较——O(n²) 会拖慢体检。"""
        monkeypatch.setattr(insp, "_MAX_ENTITIES", 1)
        text = "县应急局与县应急管理局先后发文。"
        assert _cons(text) == []

    def test_variant_output_capped(self, monkeypatch):
        monkeypatch.setattr(insp, "_MAX_ENTITY_VARIANTS", 1)
        text = "县应急局 县应急管理局 市财政局 市财政管理局"
        assert len(_cons(text)) <= 1

    def test_ordinary_text_has_no_false_positive(self):
        text = (
            "关于做好安全生产工作的通知\n"
            "各科室：\n"
            "为贯彻落实上级部署要求，现就有关事项通知如下。\n"
            "一、提高认识。各部门要充分认识当前形势。\n"
            "二、压实责任。主要负责人要亲自抓。\n"
            "特此通知。\n"
        )
        assert _cons(text) == [], _details(text)

    def test_empty_text(self):
        assert _cons("") == []


class TestWiring:
    def test_check_is_wired_into_inspect_text(self):
        """元测试：负向用例在检查被摘掉后会假绿，需正向锚点。"""
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        code = (root / "gwtool" / "core" / "inspector.py").read_text(
            encoding="utf-8")
        assert "def _check_consistency(" in code
        assert "out.extend(_check_consistency(lines))" in code, (
            "检查函数存在但未接入 inspect_text —— 负向用例会假绿")
