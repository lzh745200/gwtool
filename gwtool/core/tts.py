# -*- coding: utf-8 -*-
"""离线朗读校对引擎。

  - Windows：系统自带 SAPI（pywin32，零额外安装）
  - Linux/麒麟：espeak-ng 或 speech-dispatcher（spd-say），系统包
提供同步朗读接口，句子切分由 UI 层驱动以实现高亮。
"""
from __future__ import annotations

import re
import shutil
import subprocess

_SENT_SPLIT = re.compile(r"(?<=[。！？；!?;])|\n")


def split_sentences(text: str) -> list[str]:
    out = []
    for s in _SENT_SPLIT.split(text or ""):
        s = s.strip()
        if s:
            out.append(s)
    return out


def available() -> tuple[bool, str]:
    """返回 (可用性, 引擎描述)。"""
    import sys
    if sys.platform.startswith("win"):
        try:
            import win32com.client  # noqa: F401
            return True, "Windows SAPI"
        except ImportError:
            pass
    if shutil.which("spd-say"):
        return True, "speech-dispatcher"
    if shutil.which("espeak-ng"):
        return True, "espeak-ng"
    if shutil.which("espeak"):
        return True, "espeak"
    return False, "未找到可用语音引擎（Windows 需 pywin32；Linux 需 espeak-ng）"


class TTSEngine:
    """同步朗读单句；stop() 可中断。"""

    def __init__(self):
        self._voice = None
        self._proc: subprocess.Popen | None = None
        self._stopped = False
        ok, _ = available()
        if not ok:
            raise RuntimeError("没有可用的语音引擎")

    def _win_voice(self):
        if self._voice is None:
            import win32com.client
            self._voice = win32com.client.Dispatch("SAPI.SpVoice")
        return self._voice

    def speak(self, text: str) -> None:
        """阻塞朗读一句（短句）。Windows SAPI 默认同步；中断靠 stop()/Skip。"""
        if self._stopped:
            return
        import sys
        if sys.platform.startswith("win"):
            self._win_voice().Speak(text)  # 同步朗读，Skip 可中断
        else:
            spd = shutil.which("spd-say")
            if spd:
                self._proc = subprocess.Popen(
                    [spd, "-w", "-l", "zh", text],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                es = shutil.which("espeak-ng") or shutil.which("espeak")
                self._proc = subprocess.Popen(
                    [es, "-v", "cmn", text],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if self._proc:
                while self._proc.poll() is None and not self._stopped:
                    import time
                    time.sleep(0.05)

    def stop(self) -> None:
        self._stopped = True
        import sys
        if sys.platform.startswith("win") and self._voice is not None:
            try:
                self._voice.Skip("Sentence")
            except Exception:
                pass
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
