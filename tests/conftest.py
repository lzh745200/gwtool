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
    # 导出目录一并重定向（GWTOOL_EXPORT_DIR，见 paths.export_dir）：
    # 全量测试逐用例构造主窗口会高频触发 export_dir 的写探针，
    # 真实 Documents 目录在部分环境下被间歇拦截 45~60s，造成假超时。
    monkey.setenv("GWTOOL_EXPORT_DIR", str(data_dir / "exports"))
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


@pytest.fixture(autouse=True)
def _no_modal_confirm(monkeypatch):
    """全局屏蔽"自定义确认按钮"的确认框（`ask(..., ok_text=...)`）。

    为什么必须全局：该分支走 ``QMessageBox.exec()``，而 QMessageBox 继承
    QDialog——各测试文件对 ``QDialog.exec`` 的屏蔽方式并不一致（有的根本没
    屏蔽），漏掉的地方会**真的弹出模态框**，在无人值守环境下把整个测试会话
    挂死（与 QInputDialog/QFileDialog 静态方法的老问题同源）。

    统一返回 True，与既有 ``QMessageBox.question → Yes`` 的屏蔽语义保持一致：
    测试里"危险操作的确认"照常成立，不会让依赖"确认后确实执行"的用例变成
    假失败。
    """
    from gwtool.ui import widgets as _widgets
    monkeypatch.setattr(_widgets, "_ask_custom", lambda *a, **k: True)


@pytest.fixture(autouse=True)
def _no_modal_messagebox(monkeypatch):
    """全局屏蔽 `QMessageBox` 的**静态方法**（真弹框会把整个会话挂死）。

    为什么必须在这里做、而不是各测试文件各写一份：`question` / `information`
    / `warning` / `critical` 是 C++ 侧静态方法，**不经过 `QDialog.exec`** ——
    各文件对 `QDialog.exec` 的屏蔽方式对它们完全无效。漏掉任何一个入口，
    无人值守下就是"无报错、日志十几分钟不增"的挂死，且只有真跑全量才暴露。

    实测（10-05）：reference_panel 的"全部替换"二次确认（§8.3）调用默认分支
    `ask()` → `QMessageBox.question`，把 1800+ 用例的全量会话卡在 87%；
    e2e 自检反而是绿的，问题只在 pytest 会话里复现。

    语义与既有屏蔽一致：确认类返回 Yes（"用户点了是"，危险操作的既有用例
    照常成立），提示类返回 None。
    """
    from PySide6.QtWidgets import QMessageBox
    yes = QMessageBox.StandardButton.Yes
    # 注意：**不要**在这里连 `widgets.ask` 一起 patch。`ask` 被各模块用
    # `from .widgets import ask` 值绑定导入，patch 模块属性对它们无效
    # （对真正会挂起的调用点恰恰无效）；而它会把 `ask` 的**真实现整体短路**，
    # 使"验证 ask 分发到 _ask_custom / QMessageBox.question"的回归用例
    # （tests/test_ui_design_specs.py 两条）拿到空记录而假失败。
    # 屏蔽 C++ 侧静态方法已足够：它们是运行期查类属性，必然生效。
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: yes))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda *a, **k: None))


@pytest.fixture()
def wait_bg(qapp):
    """等后台 worker 收工，并驱动事件循环把结果信号投递给槽函数。

    为什么不能只 `worker.wait()`：`ok`/`failed` 是**队列连接**，wait() 只
    保证线程退出，槽函数仍排在事件队列里没执行 —— 断言会读到"什么都没
    发生"的假失败。

    N3 把备份/恢复、全库打包、数据库维护、体检与报告导出移入后台线程后，
    "等后台任务"从个别用例的局部需求变成了通用需求，故上提到公共夹具：
    各文件自己写一份 `_drain` 的写法已经出现分叉（有的只 wait 不 pump）。

    用法：
        win._do_backup()
        assert wait_bg(getattr(win, "_backup_worker", None))
        # 或带判定条件
        wait_bg(worker, cond=lambda: shown)
    """
    import time

    def _wait(worker, cond=None, timeout_ms=20000):
        deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            qapp.processEvents()
            idle = worker is None or not worker.isRunning()
            if idle and (cond is None or cond()):
                for _ in range(3):      # 把队列里剩下的槽跑完再返回
                    qapp.processEvents()
                return True
            if time.monotonic() > deadline:
                return False
            if worker is not None:
                worker.wait(20)
            time.sleep(0.01)

    return _wait
