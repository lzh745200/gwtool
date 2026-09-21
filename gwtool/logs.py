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


# 单条参数/消息超过该长度即视为"可能携带正文"。
# 正常的排障参数（路径、行号、计数、错误码）都远小于此；而公文正文
# 动辄数百上千字。阈值取 64 是保守值 —— 短到能整体出现在日志里的
# 有用信息（函数名、状态码）都不会被误伤。
_MAX_ARG = 64
# 格式串本身（无参时即整条消息）的长度上限
_MAX_MSG = 512


class RedactingFilter(logging.Filter):
    """在日志**入队之前**把可能携带正文的内容压成长度标记。

    为什么必须在 `QueueHandler` 之前：一旦进了队列，`RotatingFileHandler`、
    `diagpack` 与 `preview_lines()` 看到的都是同一份内容 —— 在下游再脱敏
    只能靠调用方记得，而"靠调用方记得"正是本项目反复踩的坑。

    处理两类渠道：
    1. **带参日志**（`log("%s", value)`）：`args` 逐个压成长度标记；
    2. **无参长消息**（f-string 直接拼进 `msg`）：超过 `_MAX_MSG` 截断。
       —— f-string 拼正文是**编码纪律**问题，过滤器只能兜底（截断），
       根治靠"传参 + 让本过滤器接管"，这也是新增本过滤器的目的。

    过滤器自身任何异常都吞掉：日志坏了不能拖垮主流程。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            self._redact_record(record)
        except Exception:
            pass
        return True

    @classmethod
    def _short(cls, value) -> str:
        try:
            s = str(value)
        except Exception:
            return "<参数不可读，内容已隐去>"
        if len(s) <= _MAX_ARG:
            return s
        return redact(s)

    @classmethod
    def _redact_record(cls, record: logging.LogRecord) -> None:
        args = record.args
        if args:
            if isinstance(args, dict):
                record.args = {k: cls._short(v) for k, v in args.items()}
            elif isinstance(args, tuple):
                record.args = tuple(cls._short(a) for a in args)
            else:
                record.args = (cls._short(args),)
        msg = record.msg
        if isinstance(msg, str) and len(msg) > _MAX_MSG:
            # 长消息几乎必然是 f-string 拼了正文。**必须整体替换**而不是
            # 截断 —— 截断只丢掉后面的，标记若落在前 512 字里照样泄漏
            # （实测：`导入失败：正文×40` 截到 512 字仍有 20 份标记）。
            record.msg = redact(msg)


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
        # 队列把"产生日志"与"写盘"解耦：UI 线程只做一次入队。
        # RedactingFilter 挂在 QueueHandler 上 —— 过滤发生在**入队之前**，
        # 因此落盘的日志本身已脱敏，diagpack 与 preview_lines 自然安全。
        q = queue.SimpleQueue()
        listener = logging.handlers.QueueListener(q, handler,
                                                   respect_handler_level=True)
        listener.start()
        logger = logging.getLogger(_LOG_NAME)
        logger.setLevel(logging.INFO)
        qh = logging.handlers.QueueHandler(q)
        qh.addFilter(RedactingFilter())
        logger.addHandler(qh)
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
    """停止日志线程、摘掉 handler 并落盘。幂等，异常吞掉。

    必须把 QueueHandler 从 logger 上移除：只停 listener 会留下悬空 handler，
    下一次 `setup()` 会再挂一个 —— handler 数量随 stop/setup 往返无限增长
    （实测累积到 10 个），且旧 handler 持有的过滤器配置会遮住新配置。
    """
    global _listener, _configured
    try:
        if _listener is not None:
            _listener.stop()
    except Exception:
        pass
    try:
        logger = logging.getLogger(_LOG_NAME)
        for h in list(logger.handlers):
            if isinstance(h, logging.handlers.QueueHandler):
                logger.removeHandler(h)
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


def _crash_summary(exc_type, exc, tb) -> str:
    """崩溃日志只取「异常类型 + 消息长度 + 最末帧位置」，**绝不带消息正文**。

    为什么不能落完整 traceback：异常消息里经常有被处理的数据 ——
    实测 `ValueError: 无法识别的金额：'<正文片段>'` 会把正文片段原样带进
    `diagpack`。而排障真正需要的是"哪类错、在哪一行、数据多大"，
    这三样都不含正文。
    """
    try:
        frames = traceback.extract_tb(tb)
        last = frames[-1] if frames else None
        where = f"{last.filename}:{last.lineno}" if last else "<位置未知>"
    except Exception:
        where = "<位置未知>"
    try:
        name = exc_type.__name__ if isinstance(exc_type, type) else str(exc_type)
    except Exception:
        name = "未知异常"
    msg = redact(exc)
    return f"{name}（{msg}）@ {where}"


def install_excepthooks() -> None:
    """把未捕获异常落到日志。

    打包版是 `--windowed`（无控制台），未捕获异常此前**完全不可见**：
    用户只看到"程序没了"，我们连异常类型都拿不到。
    """
    def _hook(exc_type, exc, tb):
        try:
            # 摘要由 _crash_summary 构造，**天然不含正文**（类型+长度+位置）。
            # 因此这里**不带参**记录 —— 若作为 %s 参数传入，会被本模块的
            # RedactingFilter 当作"可能带正文的长参数"整体隐去，类型名就丢了
            # （实测：`未捕获异常：<66 字，内容已隐去>`，连 ValueError 都看不到）。
            get_logger("crash").error(
                "未捕获异常：" + _crash_summary(exc_type, exc, tb))
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
                "线程未捕获异常（%s）：" % getattr(args, "thread", None)
                + _crash_summary(args.exc_type, args.exc_value,
                                 args.exc_traceback))
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
