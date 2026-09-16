# -*- coding: utf-8 -*-
"""运行期诊断日志。

**为什么要它**：本产品此前**全包零日志**（`logging.` 使用次数为 0），同时有 62 处
静默吞咽异常。而目标环境是「远程、离线、无法现场调试」的党政机关机器 ——
用户报障时能提供的信息只有"卡住了""没反应"，我们拿不到任何可诊断的证据。
更麻烦的是纠错层的静默降级是**设计如此**：L4「没生效」与「生效但没命中」在外部
完全无法区分。本模块把这件事变成可查。

设计约束（四条，均有测试守住）：

1. **非阻塞** —— `QueueHandler` + `QueueListener`，写盘在独立线程，绝不卡 UI。
2. **自身失败无害** —— 本模块任何异常都就地吞掉；连"日志写不了"也不能让主流程挂。
3. **隐私红线** —— **绝不记录公文正文**。需要说明数据规模时用 `redact()`，
   它丢弃内容、只保留长度。用户多为党政机关与涉密单位，正文进日志不可接受。
4. **有界** —— 按大小轮转（2 MB × 5 份），落在 `paths.logs_dir()`，不无限增长。

不依赖 Qt：只 import 标准库与 `paths`，便于纯逻辑单测。Qt 消息处理器是延迟导入。
"""
from __future__ import annotations

import atexit
import logging
import logging.handlers
import queue
import sys
import threading
import traceback

# 所有日志挂在 gwtool 这个 logger 树下，便于按模块细分（gwtool.corrector 等）
_LOG_NAME = "gwtool"
_MAX_BYTES = 2 * 1024 * 1024
_BACKUP_COUNT = 5
_FMT = "%(asctime)s %(levelname)-7s %(name)s %(message)s"

_configured = False
_listener = None


def redact(value) -> str:
    """把可能含正文的值压成「只有长度」的标记，供日志使用。

    日志的目的是排障，不需要内容本身。这里**主动丢弃内容**，只保留长度，
    让排查者知道"处理了多大规模的数据"，同时不可能泄漏公文正文。
    """
    try:
        return f"<{len(str(value))} 字，内容已隐去>"
    except Exception:
        return "<长度未知，内容已隐去>"


def log_path():
    """当前日志文件路径；路径不可用时返回 None（绝不抛）。"""
    try:
        from .paths import logs_dir
        return logs_dir() / "gwtool.log"
    except Exception:
        return None


def setup() -> None:
    """安装文件日志。幂等；任何失败都降级为「无文件日志」而不抛异常。"""
    global _configured, _listener
    if _configured:
        return
    try:
        path = log_path()
        if path is None:
            _configured = True
            return
        handler = logging.handlers.RotatingFileHandler(
            str(path), maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT,
            encoding="utf-8", delay=True)
        handler.setFormatter(logging.Formatter(_FMT))
        # 队列把"产生日志"与"写盘"解耦：UI 线程只做一次入队
        q = queue.SimpleQueue()
        listener = logging.handlers.QueueListener(q, handler,
                                                   respect_handler_level=True)
        listener.start()
        logger = logging.getLogger(_LOG_NAME)
        logger.setLevel(logging.INFO)
        logger.addHandler(logging.handlers.QueueHandler(q))
        # 不向 root 传播：避免与第三方库的 handler 重复输出
        logger.propagate = False
        _listener = listener
        # 退出前停掉监听线程，否则队列里最后一批日志会丢
        atexit.register(shutdown)
    except Exception:
        # 日志装不上（磁盘满/权限不足/路径异常）绝不能影响主流程
        pass
    finally:
        _configured = True


def shutdown() -> None:
    """停止日志线程并落盘。幂等，异常吞掉。"""
    global _listener, _configured
    try:
        if _listener is not None:
            _listener.stop()
    except Exception:
        pass
    finally:
        _listener = None
        _configured = False


def get_logger(name: str = "") -> logging.Logger:
    """取子 logger（`gwtool.<name>`）；name 为空返回根 logger。"""
    try:
        return logging.getLogger(f"{_LOG_NAME}.{name}" if name else _LOG_NAME)
    except Exception:
        return logging.getLogger(_LOG_NAME)


def _format_exc(exc_type, exc, tb) -> str:
    try:
        return "".join(traceback.format_exception(exc_type, exc, tb))
    except Exception:
        try:
            return repr(exc)
        except Exception:
            return "<异常信息不可读>"


def install_excepthooks() -> None:
    """把未捕获异常落到日志。

    打包版是 `--windowed`（无控制台），未捕获异常此前**完全不可见**：
    用户只看到"程序没了"，我们连异常类型都拿不到。
    """
    def _hook(exc_type, exc, tb):
        try:
            get_logger("crash").error("未捕获异常\n%s",
                                      _format_exc(exc_type, exc, tb))
        except Exception:
            pass
        # 保留默认行为，开发态仍能在终端看到
        try:
            sys.__excepthook__(exc_type, exc, tb)
        except Exception:
            pass

    def _thread_hook(args):
        try:
            get_logger("crash").error(
                "线程未捕获异常（%s）\n%s", getattr(args, "thread", None),
                _format_exc(args.exc_type, args.exc_value, args.exc_traceback))
        except Exception:
            pass

    try:
        sys.excepthook = _hook
    except Exception:
        pass
    try:
        if hasattr(threading, "excepthook"):
            threading.excepthook = _thread_hook
    except Exception:
        pass


def install_qt_message_handler() -> None:
    """把 Qt 自身的告警/错误也导向日志。PySide6 缺席时静默跳过。"""
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    except Exception:
        return

    try:
        # 用 getattr 而非直接取属性：不同 Qt 版本枚举名有增删，
        # 直接写 QtMsgType.QtInfoMsg 在缺该成员的版本上会 AttributeError
        levels = {}
        for attr, lvl in (("QtDebugMsg", "DEBUG"), ("QtInfoMsg", "INFO"),
                          ("QtWarningMsg", "WARNING"),
                          ("QtCriticalMsg", "ERROR"), ("QtFatalMsg", "CRITICAL")):
            if hasattr(QtMsgType, attr):
                levels[getattr(QtMsgType, attr)] = getattr(logging, lvl)
    except Exception:
        return

    def _handler(mode, context, message):
        try:
            # context 带文件/行号，对定位 Qt 内部告警很有用
            where = ""
            try:
                if context is not None and context.file:
                    where = f" [{context.file}:{context.line}]"
            except Exception:
                where = ""
            get_logger("qt").log(levels.get(mode, logging.INFO),
                                 "%s%s", message, where)
        except Exception:
            pass

    try:
        qInstallMessageHandler(_handler)
    except Exception:
        pass


def install() -> None:
    """一次性装配：文件日志 + 全局异常钩子 + Qt 消息处理器。

    三个子步骤各自吞掉自己的异常，故本函数不会抛。
    """
    setup()
    install_excepthooks()
    install_qt_message_handler()
