# -*- coding: utf-8 -*-
"""v1.21：Qt6 输入法环境适配（不启动真实 GUI）。"""
from __future__ import annotations

import os
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


def test_explicit_user_choice_is_kept(tmp_path, monkeypatch):
    roots = _fake_roots(tmp_path, "libfcitx5platforminputcontextplugin.so")
    monkeypatch.setattr(ime, "_candidate_plugin_roots", lambda: roots)
    monkeypatch.setenv("QT_IM_MODULE", "ibus")
    monkeypatch.setenv("XMODIFIERS", "@im=fcitx")
    info = ime.configure(force=True)
    assert info["chosen"] == "ibus"
    assert os.environ["QT_IM_MODULE"] == "ibus"


def test_missing_plugin_warns_with_action(tmp_path, monkeypatch):
    roots = _fake_roots(tmp_path, "libcomposeplatforminputcontextplugin.so")
    monkeypatch.setattr(ime, "_candidate_plugin_roots", lambda: roots)
    monkeypatch.setenv("XMODIFIERS", "@im=fcitx")
    monkeypatch.delenv("QT_IM_MODULE", raising=False)
    info = ime.configure(force=True)
    assert info["warnings"] and "fcitx" in info["warnings"][0]
    assert "fcitx5-frontend-qt6" in ime.diagnostics_text(info)


def test_system_plugin_dirs_appended_to_plugin_path(tmp_path, monkeypatch):
    roots = _fake_roots(tmp_path, "libibusplatforminputcontextplugin.so")
    monkeypatch.setattr(ime, "_candidate_plugin_roots", lambda: roots)
    monkeypatch.setenv("QT_PLUGIN_PATH", "/already/here")
    monkeypatch.setenv("XMODIFIERS", "@im=ibus")
    ime.configure(force=True)
    parts = os.environ["QT_PLUGIN_PATH"].split(os.pathsep)
    assert "/already/here" in parts and str(roots[0]) in parts
