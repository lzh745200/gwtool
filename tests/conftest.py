# -*- coding: utf-8 -*-
"""pytest 公共夹具：临时数据库 + 进程内 QApplication（离屏）。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gwtool.db import connection as dbconn  # noqa: E402
from gwtool.core import corrector  # noqa: E402


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """每个测试使用独立临时数据库。"""
    db_file = tmp_path / "test_gwtool.db"
    dbconn.configure(db_file)
    corrector.invalidate_cache()
    yield db_file
    dbconn.close_current_thread()


@pytest.fixture(scope="session")
def qapp():
    """进程内 QApplication（渲染/字体测试需要）。

    Windows 会话内默认用原生平台（字体系统完整，PDF 文本层正常）；
    若 CI 显式设置了 QT_QPA_PLATFORM（如 offscreen）则尊重该设置；
    Linux 无显示环境时退回 offscreen。
    """
    import os
    import sys as _sys
    if _sys.platform.startswith("win"):
        if "QT_QPA_PLATFORM" not in os.environ:
            pass  # 用原生 windows 平台
    else:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
