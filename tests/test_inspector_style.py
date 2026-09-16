# -*- coding: utf-8 -*-
"""公文用语与文风检查（inspector._check_style）的测试。

要点：全部 `info` 级、条目克制。文风建议一旦变成 `warn` 或频繁误报，
用户会觉得软件在说教，进而连整个体检一起忽略。
"""
from __future__ import annotations

from gwtool.core.inspector import inspect_text


def _style(text: str) -> list:
    return [f for f in inspect_text(text) if f.item == "文风"]


def _details(text: str) -> str:
    return " | ".join(f.detail for f in _style(text))


class TestSpokenWords:
    def test_flags_obvious_colloquialism(self):
        assert "口语词" in _details("咱们要把这项工作落实好。")

    def test_flags_several_kinds(self):
        assert "口语词" in _details("这个方案啥时候能搞定？")
        assert "口语词" in _details("情况差不多就是这样。")

    def test_reports_line_number(self):
        got = _details("第一行正常。\n第二行有咱这个词。")
        assert "第2行" in got

    def test_only_first_occurrence_per_word(self):
        """同一口语词只报一次，避免长文刷屏。"""
        text = "咱们要做。咱们要抓紧。咱们要落实。"
        hits = [f for f in _style(text) if "口语词" in f.detail]
        assert len(hits) == 1


class TestFalsePositiveGuards:
    def test_common_formal_words_not_flagged(self):
        """「搞」「特别」在公文里有大量规范用法，不得误报。"""
        text = "搞好安全生产，特别重要。各部门要抓紧抓实，切实抓好落实。"
        assert "口语词" not in _details(text)

    def test_formal_document_is_clean(self):
        text = (
            "关于做好安全生产工作的通知\n"
            "各科室：\n"
            "为贯彻落实上级部署要求，现就有关事项通知如下。\n"
            "一、提高认识。各部门要充分认识当前安全生产形势的严峻性。\n"
            "二、压实责任。主要负责人要亲自抓、负总责。\n"
            "特此通知。\n"
        )
        assert _style(text) == [], _details(text)

    def test_empty_text(self):
        assert _style("") == []


class TestLongSentence:
    def test_flags_overlong_sentence(self):
        body = "为深入贯彻落实上级关于进一步加强和改进新形势下安全生产工作的各项部署要求" * 3
        assert "建议拆分" in _details(body + "。")

    def test_short_sentence_is_clean(self):
        assert "建议拆分" not in _details("各部门要抓紧落实。")

    def test_caps_long_sentence_findings(self):
        body = "为深入贯彻落实上级关于进一步加强和改进新形势下安全生产工作的各项部署要求" * 3
        text = (body + "。") * 5
        hits = [f for f in _style(text) if "建议拆分" in f.detail]
        assert len(hits) <= 3, "长句提示应设上限，否则长文会被刷屏"

    def test_semicolon_breaks_sentence(self):
        """分号也算句子边界：小标题式的长段落不该整段算一句。"""
        part = "为深入贯彻落实上级关于进一步加强和改进新形势下安全生产工作的各项部署要求"
        text = part + "；" + part + "；"
        assert "建议拆分" not in _details(text)


class TestLevelAndCaps:
    def test_all_findings_are_info_level(self):
        body = "为深入贯彻落实上级关于进一步加强和改进新形势下安全生产工作的各项部署要求" * 3
        text = body + "。咱们要搞定这件事。"
        fs = _style(text)
        assert fs
        assert all(f.severity == "info" for f in fs), (
            "文风建议必须是提示级，否则用户会觉得被说教")

    def test_total_findings_capped(self):
        body = "为深入贯彻落实上级关于进一步加强和改进新形势下安全生产工作的各项部署要求" * 3
        text = (body + "。") * 8 + "咱们 啥 咋 搞定 弄好 差不多"
        assert len(_style(text)) <= 6


class TestWiring:
    def test_check_is_wired_into_inspect_text(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        code = (root / "gwtool" / "core" / "inspector.py").read_text(
            encoding="utf-8")
        assert "def _check_style(" in code
        assert "out.extend(_check_style(lines))" in code, (
            "检查函数存在但未接入 inspect_text —— 负向用例会假绿")
