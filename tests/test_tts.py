# -*- coding: utf-8 -*-
"""TTS 链路回归（第 21 轮逐模块探测产出）：分句语义、设置钳制、引擎探测。"""
from __future__ import annotations

from gwtool.core import tts


def test_split_sentences_chinese():
    assert tts.split_sentences("第一句。第二句！第三句；最后") == [
        "第一句。", "第二句！", "第三句；", "最后"]


def test_split_sentences_halfwidth_dot_is_not_a_boundary():
    """半角句点刻意不在切分集：保护 '1.5' 与编号 '1.' 不被朗读切碎。"""
    assert len(tts.split_sentences("甲乙1.5丙")) == 1
    assert len(tts.split_sentences("第1.条内容")) == 1


def test_split_sentences_halfwidth_bang_is_a_boundary():
    assert tts.split_sentences("A. B! C?") == ["A. B!", "C?"]


def test_split_sentences_newline_and_empty():
    assert len(tts.split_sentences("甲\n乙")) == 2
    assert tts.split_sentences("") == []


def test_settings_clamped(tmp_db):
    from gwtool.db import dao as _dao
    _dao.set_setting("tts_rate", "999")
    assert tts._settings()[0] == 10
    _dao.set_setting("tts_rate", "-999")
    assert tts._settings()[0] == -10
    _dao.set_setting("tts_rate", "not-a-number")
    assert tts._settings()[0] == 0      # 容错回退默认
    _dao.set_setting("tts_rate", "0")


def test_available_returns_tuple():
    ok, desc = tts.available()
    assert isinstance(ok, bool) and isinstance(desc, str) and desc
