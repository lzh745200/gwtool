# -*- coding: utf-8 -*-
"""TTS 模块（gwtool/core/tts.py）真实链路深探。

真实调用 Windows SAPI（pywin32 COM）零 mock：真实 Dispatch SAPI.SpVoice、
真实同步朗读短句、真实语音名匹配；设置项走真实 SQLite（tmp_db）。
CI Linux 容器无语音引擎时，available()=False 的路径由真实环境覆盖，
本机不可达的分支以能力门控跳过。
"""
import pytest

from gwtool.core import tts
from gwtool.db import dao


@pytest.fixture()
def tts_settings(tmp_db):
    """真实 SQLite 设置项：进出都清空 tts_rate/tts_voice。"""
    dao.set_setting("tts_rate", "")
    dao.set_setting("tts_voice", "")
    yield
    dao.set_setting("tts_rate", "")
    dao.set_setting("tts_voice", "")


# ---------------------------------------------------------- 引擎探测
def test_available_reports_windows_sapi():
    pytest.importorskip("win32com.client")
    ok, desc = tts.available()
    assert ok is True
    assert desc == "Windows SAPI"


def test_list_voices_real():
    """真实 Dispatch SAPI.SpVoice 枚举语音描述列表。"""
    pytest.importorskip("win32com.client")
    voices = tts.list_voices()
    assert isinstance(voices, list)
    for v in voices:
        assert isinstance(v, str) and v


# ---------------------------------------------------------- 设置读取
def test_settings_clamp_and_defaults(tts_settings):
    """语速夹取 -10..10；默认 0/空。"""
    assert tts._settings() == (0, "")
    dao.set_setting("tts_rate", "15")
    dao.set_setting("tts_voice", "Huihui")
    assert tts._settings() == (10, "Huihui")
    dao.set_setting("tts_rate", "-15")
    assert tts._settings()[0] == -10
    dao.set_setting("tts_rate", "3")
    assert tts._settings() == (3, "Huihui")


def test_settings_non_numeric_rate_falls_back(tts_settings):
    """设置被污染成非数字：整条设置读取兜底 (0, "")，不抛异常。"""
    dao.set_setting("tts_rate", "not-a-number")
    assert tts._settings() == (0, "")


# ---------------------------------------------------------- 引擎实例
def test_engine_init_real(tts_settings):
    pytest.importorskip("win32com.client")
    eng = tts.TTSEngine()
    assert eng._voice is None and eng._proc is None and eng._stopped is False
    assert isinstance(eng._rate, int) and -10 <= eng._rate <= 10


def test_engine_init_raises_without_engine(tmp_db, monkeypatch):
    """无任何引擎的真实环境：构造即抛 RuntimeError。

    Windows 本机必装 SAPI 无法真实构造无引擎态，此分支由无语音引擎的
    真实环境（CI Linux 容器）覆盖；这里仅在有引擎的机器上跳过。
    """
    ok, _ = tts.available()
    if ok:
        pytest.skip("本机存在真实语音引擎，无引擎分支由 CI Linux 覆盖")
    with pytest.raises(RuntimeError, match="没有可用的语音引擎"):
        tts.TTSEngine()


# ---------------------------------------------------------- 朗读链路
def test_speak_short_sentence_real(tts_settings):
    """真实同步朗读一句：Rate 设置 + Speak 全链路（约 1-2 秒）。"""
    pytest.importorskip("win32com.client")
    eng = tts.TTSEngine()
    eng.speak("公文汇编助手朗读测试。")
    assert eng._voice is not None          # 真实 Dispatch 已发生


def test_speak_with_unknown_voice_keyword(tts_settings):
    """语音名关键字不命中任何已装语音：循环真实遍历后仍正常朗读。"""
    pytest.importorskip("win32com.client")
    dao.set_setting("tts_voice", "绝不存在的语音引擎名字")
    eng = tts.TTSEngine()
    eng.speak("语音名未命中时照常朗读。")
    assert eng._voice is not None


def test_speak_with_real_voice_keyword(tts_settings):
    """语音名关键字取真实已装语音描述子串：命中并切换 Voice 后朗读。"""
    pytest.importorskip("win32com.client")
    voices = tts.list_voices()
    if not voices:
        pytest.skip("本机 SAPI 无已装语音")
    dao.set_setting("tts_voice", voices[0][:4])
    eng = tts.TTSEngine()
    eng.speak("语音命中切换后朗读。")
    assert eng._voice is not None


def test_stop_then_speak_short_circuits(tts_settings):
    """先 stop 再 speak：stopped 标志短路，不再进入朗读分支。"""
    pytest.importorskip("win32com.client")
    eng = tts.TTSEngine()
    eng.stop()                              # voice/proc 均为 None 的安全路径
    assert eng._stopped is True
    eng.speak("这句话不该被读出来")          # 89-90 行短路 return
    assert eng._voice is None               # 未发生真实 Dispatch


def test_stop_after_speak_skips_sentence(tts_settings):
    """真实朗读后 stop：voice 已 Dispatch → SAPI Skip("Sentence") 真实执行。"""
    pytest.importorskip("win32com.client")
    eng = tts.TTSEngine()
    eng.speak("先朗读一句再停止。")          # 同步完成后 _voice 已就绪
    assert eng._voice is not None
    eng.stop()                              # 走 Skip 分支（124-127）
    assert eng._stopped is True
