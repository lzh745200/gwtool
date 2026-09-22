# -*- coding: utf-8 -*-
"""面向用户的错误文案：把 Python 异常翻译成「下一步该做什么」。

为什么需要它
------------
目标用户是文书岗人员，不是程序员；且本产品运行在**离线内网**，
没有客服、没有搜索引擎 —— 弹出的提示就是唯一的售后。

实测中批量纠错失败会弹出
    UnicodeDecodeError: 'gbk' codec can't decode byte 0xb5 ...
这类原文：异常类名是英文、正文是英文、且**不含任何可执行动作**。

设计原则
--------
1. 文案必须包含**用户能执行的下一步**（另存为 docx / 关闭占用程序 / 清理磁盘），
   而不是描述故障机理；
2. **原始异常只进日志**（`logs` 已在入队前脱敏），界面上不出现英文类名；
3. 未知异常不硬编：回落到「内部错误 + 已写入日志」的标准话术，绝不装作成功。
"""
from __future__ import annotations

import errno as _errno
import re as _re
import sqlite3 as _sqlite3

from .. import logs

log = logs.get_logger("ui.errmsg")

# 常见损坏/误投喂场景的可执行文案
_TEMPLATE_PACKAGE_NOT_FOUND = (
    "文件可能已损坏，或不是有效的 Word 文档。\n"
    "请用 Word/WPS 打开它，确认能正常显示后「另存为 .docx」，再重新导入。")
_TEMPLATE_FILE_NOT_FOUND = (
    "找不到这个文件 —— 它可能已被移动、重命名或删除。\n"
    "请确认文件位置后重试。")
_TEMPLATE_PERMISSION = (
    "文件正被其他程序占用，或当前用户没有访问权限。\n"
    "请关闭正在使用该文件的程序（如 Word/WPS/压缩软件）后重试。")
_TEMPLATE_DISK_FULL = (
    "磁盘空间不足，操作无法完成。\n"
    "请清理磁盘（或换一个有足够空间的保存位置）后重试。")
_TEMPLATE_ENCODING = (
    "无法识别文件的文字编码 —— 它可能不是 UTF-8/GBK 文本，或文件已损坏。\n"
    "请用记事本打开确认内容正常后另存为 UTF-8 再试。")
_TEMPLATE_REGEX = (
    "查找/替换的表达式写法有误：{detail}\n"
    "请检查括号、星号等符号是否配对后重试。")
_TEMPLATE_DB = (
    "数据库操作未能完成（可能正被其他任务占用，或磁盘已满）。\n"
    "请稍后重试；若反复出现，请生成诊断包反馈。")
_TEMPLATE_INTERNAL = (
    "{action}未完成：发生内部错误（{kind}）。\n"
    "详细信息已写入运行日志，可通过「帮助 → 生成诊断包」导出反馈。")


def friendly(exc: BaseException, action: str = "操作") -> str:
    """把异常翻译成中文可执行文案；原始异常只进日志。

    参数
    ----
    exc      捕获到的异常
    action   用户视角的动作名（"批量导入"、"保存设置"…），用于未知异常话术
    """
    log.warning("%s 失败：%s: %s", action, type(exc).__name__, exc)

    if isinstance(exc, ModuleNotFoundError):
        # importer 依赖（如 OCR 的 Tesseract 缺失）会以它表达
        name = getattr(exc, "name", "") or ""
        if name and "pyzipper" in name:
            return "读取加密备份需要 pyzipper 组件，当前环境未安装。"
        return _TEMPLATE_PACKAGE_NOT_FOUND if "docx" in name else (
            f"缺少组件 {name}，该功能在当前环境不可用。")

    if isinstance(exc, _re.error):
        return _TEMPLATE_REGEX.format(detail=str(exc))

    if isinstance(exc, UnicodeDecodeError):
        return _TEMPLATE_ENCODING

    if isinstance(exc, PermissionError):
        return _TEMPLATE_PERMISSION

    if isinstance(exc, FileNotFoundError):
        return _TEMPLATE_FILE_NOT_FOUND

    if isinstance(exc, _sqlite3.OperationalError):
        msg = str(exc).lower()
        if "disk" in msg and ("full" in msg or "i/o" in msg):
            return _TEMPLATE_DISK_FULL
        return _TEMPLATE_DB

    if isinstance(exc, OSError):
        if exc.errno in (_errno.ENOSPC, _errno.EDQUOT):
            return _TEMPLATE_DISK_FULL
        if exc.errno == _errno.EACCES:
            return _TEMPLATE_PERMISSION
        if exc.errno == _errno.ENOENT:
            return _TEMPLATE_FILE_NOT_FOUND
        return _TEMPLATE_INTERNAL.format(action=action,
                                         kind="文件读写失败")

    # python-docx 对损坏包统一抛 PackageNotFoundError
    kind = type(exc).__name__
    if "PackageNotFound" in kind or "BadZipFile" in kind:
        return _TEMPLATE_PACKAGE_NOT_FOUND

    return _TEMPLATE_INTERNAL.format(action=action, kind="内部错误")


# importer 对可预期失败（OCR 缺包、不支持格式、空文档）返回的**已是**
# 精心写好的中文指引 —— 这些前缀视为"无需翻译"，原样展示。
_KNOWN_CN_REASONS = ("检测到 Tesseract", "图片识别需安装", "不支持的格式",
                     "未提取到文字", "内容与已有材料重复", "已手动停止")


def friendly_parse_error(reason: str) -> str:
    """把 importer 返回的失败原因字符串翻成用户文案。

    两种输入形态：
      1. importer 主动给出的中文指引 → 原样返回（它本就是可执行文案）；
      2. except 兜底拼的 "类型: 消息" 英文串 → 按类型映射成中文；
         映射不到的给标准话术，**原文只进日志**。
    """
    if not reason:
        return "未能导入（原因未知）"
    if any(reason.startswith(p) or p in reason for p in _KNOWN_CN_REASONS):
        return reason
    # "PackageNotFoundError: Package not found at 'C:\...'" 形态
    kind = reason.split(":", 1)[0].strip()
    log.warning("导入失败原因：%s", reason)
    if "PackageNotFound" in kind or "BadZipFile" in kind:
        return _TEMPLATE_PACKAGE_NOT_FOUND
    if kind == "FileNotFoundError":
        return _TEMPLATE_FILE_NOT_FOUND
    if kind == "PermissionError":
        return _TEMPLATE_PERMISSION
    if kind == "UnicodeDecodeError":
        return _TEMPLATE_ENCODING
    if kind in ("OSError", "IOError"):
        return _TEMPLATE_INTERNAL.format(action="导入", kind="文件读写失败")
    return _TEMPLATE_INTERNAL.format(action="导入", kind="内部错误")
