# -*- coding: utf-8 -*-
"""v1.21：Qt6 输入法环境适配（不启动真实 GUI）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from gwtool.ui import ime


def _fake_roots(tmp_path: Path, *names: str):
    d = tmp_path / "plugins" / "platforminputcontexts"
    d.mkdir(parents=True)
    for n in names:
        (d / n).write_bytes(b"fake")
    return [tmp_path / "plugins"]


def test_detect_fcitx5_plugin_and_set_module(tmp_path, monkeypatch):
    roots = _fake_roots(tmp_path, "libfcitx5platforminputcontextplugin.so",
                        "libcomposeplatforminputcontextplugin.so")
    monkeypatch.setattr(ime, "_candidate_plugin_roots", lambda: roots)
    monkeypatch.setenv("XMODIFIERS", "@im=fcitx")
    monkeypatch.delenv("QT_IM_MODULE", raising=False)
    info = ime.configure(force=True)
    assert info["chosen"] == "fcitx"
    assert os.environ["QT_IM_MODULE"] == "fcitx"
    assert not info["warnings"]


def test_detect_ibus_plugin(tmp_path, monkeypatch):
    roots = _fake_roots(tmp_path, "libibusplatforminputcontextplugin.so")
    monkeypatch.setattr(ime, "_candidate_plugin_roots", lambda: roots)
    monkeypatch.setenv("XMODIFIERS", "@im=ibus")
    monkeypatch.delenv("QT_IM_MODULE", raising=False)
    info = ime.configure(force=True)
    assert info["chosen"] == "ibus"


def test_explicit_choice_kept_when_plugin_present(tmp_path, monkeypatch):
    roots = _fake_roots(tmp_path, "libfcitx5platforminputcontextplugin.so",
                        "libibusplatforminputcontextplugin.so")
    monkeypatch.setattr(ime, "_candidate_plugin_roots", lambda: roots)
    monkeypatch.setenv("QT_IM_MODULE", "ibus")
    monkeypatch.setenv("XMODIFIERS", "@im=fcitx")
    info = ime.configure(force=True)
    assert info["chosen"] == "ibus"
    assert os.environ["QT_IM_MODULE"] == "ibus"


def test_explicit_choice_downgraded_when_plugin_missing(tmp_path, monkeypatch):
    """显式设了 QT_IM_MODULE 但插件不存在时必须降级。

    盲信用户设置的后果比"设错了"更糟：Qt 找不到指定插件时会**一个输入法都不加载**，
    连 compose 都没有，中文输入彻底不可用。降级到 XIM 至少能打字。
    """
    roots = _fake_roots(tmp_path, "libcomposeplatforminputcontextplugin.so")
    monkeypatch.setattr(ime, "_candidate_plugin_roots", lambda: roots)
    monkeypatch.setenv("QT_IM_MODULE", "ibus")
    monkeypatch.setenv("XMODIFIERS", "@im=ibus")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    info = ime.configure(force=True)
    assert info["invalid_request"] == "ibus"
    assert info["chosen"] == "xim"
    assert os.environ["QT_IM_MODULE"] == "xim"


def test_xim_fallback_when_hint_but_no_plugin(tmp_path, monkeypatch):
    """有输入法在跑、但没有 Qt6 前端插件时，走 XIM 兜底（不依赖任何插件）。"""
    roots = _fake_roots(tmp_path, "libcomposeplatforminputcontextplugin.so")
    monkeypatch.setattr(ime, "_candidate_plugin_roots", lambda: roots)
    monkeypatch.setenv("XMODIFIERS", "@im=fcitx")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.delenv("QT_IM_MODULE", raising=False)
    info = ime.configure(force=True)
    assert info["chosen"] == "xim"
    assert os.environ["QT_IM_MODULE"] == "xim"
    assert any("XIM" in w for w in info["warnings"])


def test_wayland_never_uses_xim(tmp_path, monkeypatch):
    """Wayland 下 XIM 不成立，只能 compose（并在诊断里点明原因）。"""
    roots = _fake_roots(tmp_path, "libcomposeplatforminputcontextplugin.so")
    monkeypatch.setattr(ime, "_candidate_plugin_roots", lambda: roots)
    monkeypatch.setenv("XMODIFIERS", "@im=fcitx")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.delenv("QT_IM_MODULE", raising=False)
    info = ime.configure(force=True)
    assert info["chosen"] == "compose"
    assert "Wayland" in ime.diagnostics_text(info)


def test_xim_value_itself_is_never_downgraded(tmp_path, monkeypatch):
    """xim 没有插件文件，属于 Qt 内部实现 —— 不能因为"查不到文件"就改掉。"""
    roots = _fake_roots(tmp_path, "libcomposeplatforminputcontextplugin.so")
    monkeypatch.setattr(ime, "_candidate_plugin_roots", lambda: roots)
    monkeypatch.setenv("QT_IM_MODULE", "xim")
    info = ime.configure(force=True)
    assert info["chosen"] == "xim"
    assert not info["invalid_request"]


def test_usable_flag_decides_whether_to_nag(tmp_path, monkeypatch):
    """`usable` 决定是否弹窗：能打中文就不打扰用户。

    compose 能"加载成功"但打不了中文，必须按不可用处理；XIM 兜底能用，属可用。
    """
    roots = _fake_roots(tmp_path, "libcomposeplatforminputcontextplugin.so")
    monkeypatch.setattr(ime, "_candidate_plugin_roots", lambda: roots)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)

    monkeypatch.setenv("XMODIFIERS", "@im=fcitx")      # 触发 XIM 兜底
    monkeypatch.delenv("QT_IM_MODULE", raising=False)
    assert ime.configure(force=True)["usable"] is True

    monkeypatch.delenv("XMODIFIERS", raising=False)    # 无提示 → 只剩 compose
    monkeypatch.delenv("GTK_IM_MODULE", raising=False)
    monkeypatch.delenv("QT_IM_MODULE", raising=False)
    assert ime.configure(force=True)["usable"] is False


def test_non_linux_platform_says_not_applicable():
    """Windows 上输入法由系统原生提供，诊断不能吓唬人说"中文输入不可用"。"""
    text = ime.diagnostics_text({"platform": "win32", "chosen": "", "plugins": {}})
    assert "非 Linux 平台" in text
    assert "中文输入不可用" not in text


def test_missing_plugin_warns_with_action(tmp_path, monkeypatch):
    roots = _fake_roots(tmp_path, "libcomposeplatforminputcontextplugin.so")
    monkeypatch.setattr(ime, "_candidate_plugin_roots", lambda: roots)
    monkeypatch.setenv("XMODIFIERS", "@im=fcitx")
    monkeypatch.delenv("QT_IM_MODULE", raising=False)
    info = ime.configure(force=True)
    assert info["warnings"] and "fcitx" in info["warnings"][0]
    # 安装建议是 Linux 专用文案：非 Linux 上诊断会明确说"本节不适用"（见上一个用例），
    # 所以这条断言只在 Linux（CI）成立。
    if sys.platform == "linux":
        assert "fcitx5-frontend-qt6" in ime.diagnostics_text(info)


def test_system_plugin_dirs_appended_to_plugin_path(tmp_path, monkeypatch):
    roots = _fake_roots(tmp_path, "libibusplatforminputcontextplugin.so")
    monkeypatch.setattr(ime, "_candidate_plugin_roots", lambda: roots)
    monkeypatch.setenv("QT_PLUGIN_PATH", "/already/here")
    monkeypatch.setenv("XMODIFIERS", "@im=ibus")
    ime.configure(force=True)
    parts = os.environ["QT_PLUGIN_PATH"].split(os.pathsep)
    assert "/already/here" in parts and str(roots[0]) in parts
