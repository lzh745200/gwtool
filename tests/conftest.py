# -*- coding: utf-8 -*-
"""pytest 公共夹具：临时数据库 + 进程内 QApplication（离屏）。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gwtool.db import connection as dbconn
from gwtool.core import corrector
from gwtool import paths


@pytest.fixture(scope="session", autouse=True)
def _isolate_data_dir(tmp_path_factory):
    """**整个测试会话**的数据目录都不许指向真实用户目录。

    为什么必须是会话级 autouse：`tmp_db` 只隔离用到它的测试，而
    `test_correct_dialog.py` 里有 4 个测试只用 `corrector`、没要 `tmp_db`，
    于是它们读的是 `%APPDATA%/gwtool/gwtool.db` —— **开发者自己的真实库**。
    后果分两层：

      1. 测试结果取决于本机用户数据。本机把「精度增强包」的 L4/L5 打开过，
         `corrector.check_text("项目布署情况")` 就多出一条 目→眈 的神经纠错，
         4 个测试当场失败；换台没装增强包的机器又是全绿。
      2. 测试还会往真实库里写设置/文档，污染用户数据。

    CI 一直绿纯属 CI 没有那份用户数据 —— 这是最典型的环境依赖型假绿，
    必须在最外层堵住，而不是逐个测试补 `tmp_db`。
    """
    data_dir = tmp_path_factory.mktemp("session_data") / "gwtool_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkey = pytest.MonkeyPatch()
    monkey.setattr(paths, "_override", data_dir)
    dbconn.configure(data_dir / "gwtool.db")
    # 增强层默认关闭：它的开关存在数据库里，而"是否有增强包"取决于本机
    # ~/…/gwtool/enhance 目录。测试必须与这两者都无关，否则同一份代码
    # 在不同机器上给出不同结果。
    yield
    monkey.undo()


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """每个测试使用独立临时数据库与隔离的数据目录。

    数据目录若不隔离，附件/备份/日志会写进真实用户目录 %APPDATA%/gwtool
    （曾在逐模块探测中实际泄漏：附件文件落入真实 attachments/）。
    """
    data_dir = tmp_path / "data"
    monkeypatch.setattr(paths, "_override", data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)  # 首个 DAO 调用前就可能写文件
    db_file = data_dir / "test_gwtool.db"
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
