# -*- coding: utf-8 -*-
"""运行期诊断日志的护栏测试。

这些用例对应 `gwtool/logs.py` 的四条设计约束，每条都可能被后人无意破坏：
  1. 非阻塞（队列式）
  2. 自身失败无害（任何异常都不能让主流程挂）
  3. 隐私红线（绝不记录公文正文）
  4. 有界（轮转，不无限增长）

另有一组静态断言，守住"装配点不能被无声挪走"——
比如 `logs.install()` 必须早于 QApplication 构造，否则启动期异常仍会丢。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gwtool import logs, paths

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def log_env(tmp_db):
    """隔离的日志环境：用例前后都复位模块级状态。

    `logs` 用的是模块级全局（_configured/_listener），不复位会跨用例串味：
    前一条装好、后一条就永远走不到安装分支，测试形同虚设。
    """
    logs.shutdown()
    yield
    logs.shutdown()


class TestRedact:
    """隐私红线：日志只允许记录"规模"，不允许记录内容。"""

    def test_redact_drops_content_and_keeps_length(self):
        secret = "关于某某敏感事项的请示"
        out = logs.redact(secret)
        assert secret not in out, "redact 泄漏了原文"
        assert str(len(secret)) in out, "redact 应保留长度信息"

    def test_redact_handles_non_string(self):
        # 传进来什么都不能抛：它在异常路径上被调用，抛了就是二次故障
        assert "已隐去" in logs.redact(None)
        assert "已隐去" in logs.redact(b"\x00\x01\x02")

    def test_redact_never_raises_on_broken_str(self):
        class Bad:
            def __str__(self):
                raise RuntimeError("故意的")
        # __len__ 也会失败，但 redact 必须兜住
        assert "已隐去" in logs.redact(Bad())


class TestSetupAndLifecycle:
    def test_setup_is_idempotent(self, log_env):
        """第二次 setup 不得重复挂 QueueHandler（挂重复=同一日志写多份）。

        恒定不变量是「恰好 1 个 QueueHandler」，而不是"恰好 1 个 handler"
        —— pytest 的 LogCaptureHandler 也会挂到同一条链上，按 handler 总数
        断言会随测试框架的变化而误报。
        """
        from logging.handlers import QueueHandler
        logs.setup()
        logs.setup()          # 第二次不得重复挂 handler
        lg = logs.get_logger()
        qhs = [h for h in lg.handlers if isinstance(h, QueueHandler)]
        assert len(qhs) == 1, f"QueueHandler 应恰好 1 个，实为 {len(qhs)}"

    def test_log_actually_reaches_disk(self, log_env):
        logs.setup()
        marker = "诊断自检标记-9271"
        logs.get_logger("test").info("写入检查 %s", marker)
        logs.shutdown()       # QueueListener.stop 会先处理完队列再退出
        path = logs.log_path()
        assert path is not None and path.exists()
        assert marker in path.read_text(encoding="utf-8")

    def test_shutdown_is_idempotent(self, log_env):
        logs.setup()
        logs.shutdown()
        logs.shutdown()       # 再调一次不得抛
        logs.setup()          # 且必须能重新启用

    def test_setup_survives_unavailable_log_dir(self, log_env, monkeypatch):
        """日志装不上（磁盘满/权限不足）绝不能让主流程挂。"""
        def boom():
            raise OSError("模拟磁盘满")
        monkeypatch.setattr(paths, "logs_dir", boom)
        logs.setup()                                  # 不得抛
        logs.get_logger("t").info("不会落盘，但不得报错")
        logs.shutdown()

    def test_log_path_is_none_when_dir_unavailable(self, monkeypatch):
        def boom():
            raise OSError("模拟权限不足")
        monkeypatch.setattr(paths, "logs_dir", boom)
        assert logs.log_path() is None                # 不得抛，返回 None

    def test_rotation_is_bounded(self, log_env):
        """有界：不加轮转的日志文件会无限增长，最终吃满用户磁盘。"""
        logs.setup()
        handlers = []
        try:
            from logging.handlers import QueueHandler
            for h in logs.get_logger().handlers:
                if isinstance(h, QueueHandler):
                    handlers.append(h)
        finally:
            logs.shutdown()
        # 轮转参数是模块常量，直接断言配置值
        assert logs._MAX_BYTES == 2 * 1024 * 1024
        assert logs._BACKUP_COUNT == 5


class TestExcepthooks:
    def test_install_replaces_excepthook(self, log_env):
        original = sys.excepthook
        try:
            logs.install_excepthooks()
            assert sys.excepthook is not original
        finally:
            sys.excepthook = original

    def test_uncaught_exception_is_written_to_log(self, log_env):
        """打包版 --windowed 无控制台：未捕获异常必须落到文件，否则不可诊断。

        但**只落摘要**：异常消息里经常有被处理的数据（如
        `ValueError: 无法识别的金额：'<正文片段>'`），原文进日志=正文进日志。
        排障需要的是"哪类错、在哪一行、数据多大"—— 这三样都不含正文。
        """
        logs.setup()
        original = sys.excepthook
        try:
            logs.install_excepthooks()
            try:
                raise ValueError("无人捕获的故障标记-4471")
            except ValueError:
                sys.excepthook(*sys.exc_info())
        finally:
            sys.excepthook = original
        logs.shutdown()
        text = logs.log_path().read_text(encoding="utf-8")
        # 异常类型必须保留：排障第一步是"什么类型的错"
        assert "ValueError" in text
        # ⚠️ 消息正文不得出现（隐私红线）：正文标记必须被隐去
        assert "无人捕获的故障标记-4471" not in text
        # 摘要必须带"内容已隐去"标记，证明走的是脱敏通道而非漏记
        assert "内容已隐去" in text

    def test_hook_does_not_raise_on_broken_exception(self, log_env):
        """异常自身的 repr 崩掉时，钩子也不能抛——否则遮住真实故障。"""
        logs.setup()
        original = sys.excepthook
        try:
            logs.install_excepthooks()

            class Broken(Exception):
                def __str__(self):
                    raise RuntimeError("连 str 都炸")

            try:
                raise Broken()
            except Broken:
                sys.excepthook(*sys.exc_info())        # 不得抛
        finally:
            sys.excepthook = original
        logs.shutdown()


class TestNoQtCoupling:
    def test_logs_module_defers_qt_import(self):
        """PySide6 只允许出现在函数体内（延迟导入）。

        顶层导入会让 `import gwtool.logs` 拉起整个 Qt —— 纯逻辑单测、
        CLI 诊断路径都不该付这个代价。
        """
        lines = (ROOT / "gwtool" / "logs.py").read_text(
            encoding="utf-8").splitlines()
        for i, ln in enumerate(lines, 1):
            stripped = ln.strip()
            if "PySide6" in ln and stripped.startswith(("import ", "from ")):
                assert ln != ln.lstrip(), (
                    f"logs.py:{i} 在顶层导入了 PySide6，应改为函数内延迟导入")

    def test_logs_module_has_only_stdlib_and_paths(self):
        """logs 不依赖 Qt、不依赖 db/core：它是所有模块的底座。"""
        src = (ROOT / "gwtool" / "logs.py").read_text(encoding="utf-8")
        top = src.split("def ", 1)[0]
        assert "from .db" not in top and "from .core" not in top


class TestWiringIsNotSilentlyRemoved:
    """静态断言：守住装配点，避免"改别处时顺手删掉"。

    这类缺陷不会让任何既有测试变红——它只在用户机上表现为"还是没日志"。
    """

    def test_app_installs_logs_before_qapplication(self):
        src = (ROOT / "gwtool" / "app.py").read_text(encoding="utf-8")
        assert "logs.install()" in src, "app.run 必须装配诊断日志"
        assert src.index("logs.install()") < src.index("_launch_qapp(sys.argv)"), (
            "logs.install() 必须早于 QApplication 构造，否则启动期异常仍会丢")

    def test_worker_threads_log_their_failures(self):
        """后台线程异常此前只 print_exc，打包版无控制台等于信息全丢。"""
        src = (ROOT / "gwtool" / "ui" / "workers.py").read_text(encoding="utf-8")
        assert "def _log_exc(" in src
        # 六类 worker 都要走 _log_exc，不允许残留裸 print_exc
        assert "traceback.print_exc()" in src, "_log_exc 内应保留 stderr 输出"
        assert src.count("_log_exc(") >= 7, "六处调用点 + 一处定义"

    def test_corrector_layers_log_degradation(self):
        """L4/L5 的静默降级是设计如此，但**必须留下痕迹**。

        否则"L4 没生效"与"L4 生效但没命中"在外部完全无法区分——
        这正是诊断能力最缺的一环。
        """
        for name in ("csc_neural.py", "csc_gec.py"):
            src = (ROOT / "gwtool" / "core" / name).read_text(encoding="utf-8")
            assert "已降级" in src, f"{name} 的降级路径必须记日志"
            assert "log.warning(" in src, f"{name} 未接入日志"

    def test_db_migration_real_failure_is_logged(self):
        """迁移里只有 "duplicate column" 属预期，其他失败必须留痕。"""
        src = (ROOT / "gwtool" / "db" / "schema.py").read_text(encoding="utf-8")
        assert "duplicate column" in src
        assert "语句执行失败" in src

    def test_pre_migrate_backup_failure_is_logged(self):
        """迁移前备份失败 = 升级失去安全网，不能静默。"""
        src = (ROOT / "gwtool" / "db" / "connection.py").read_text(
            encoding="utf-8")
        assert "无安全网" in src
