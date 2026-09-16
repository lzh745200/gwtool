# -*- coding: utf-8 -*-
"""一键诊断包。

离线单机部署下，排障的最大障碍往往不是"没有日志"，而是**让用户把日志发回来**
这个动作本身有门槛：找不到目录、不知道该发什么、不会压缩。本模块把它变成
一次点击：收集环境与能力信息、各表行数、运行日志，打成一个 zip。

隐私纪律（不可妥协）
--------------------
生成的内容只含：系统与依赖**版本**、能力探测结果、各表**行数**、日志文件、
以及数据目录**路径**。**绝不含任何公文正文、附件内容或词条正文** ——
本产品的用户多为党政机关与涉密单位。

`preview_lines()` 会把"将包含什么、不包含什么"逐项列给用户看，
让用户自己确认后再生成；这是把隐私承诺变成可核查事实的唯一办法。
"""
from __future__ import annotations

import importlib
import platform
import sys
import zipfile
from datetime import datetime

# 日志只取末尾这么多字节：完整日志可能有多个轮转文件，
# 全塞进诊断包既无必要也影响用户上传。
LOG_TAIL_BYTES = 200 * 1024


def system_info() -> list:
    """系统与运行环境。含数据目录路径（排障必需），由预览页明确告知用户。"""
    from ..paths import app_data_dir, is_portable

    rows = [
        ("操作系统", f"{platform.system()} {platform.release()}"),
        ("CPU 架构", platform.machine()),
        ("Python", platform.python_version()),
        ("打包运行", str(bool(getattr(sys, "frozen", False)))),
        ("数据目录", str(app_data_dir())),
        ("便携模式", str(is_portable())),
    ]
    try:
        name, ver = platform.libc_ver()
        if name:
            rows.append(("libc", f"{name} {ver}".strip()))
    except Exception:
        pass
    return rows


def dependency_info() -> list:
    """关键三方库的版本。用分发名取版本（包名与分发名不一致，如 docx/python-docx）。"""
    from importlib import metadata

    rows = []
    for dist in ("PySide6", "onnxruntime", "tokenizers", "numpy",
                 "python-docx", "PyMuPDF", "jieba", "pyzipper"):
        try:
            rows.append((dist, metadata.version(dist)))
        except Exception:
            rows.append((dist, "未安装"))
    return rows


def capability_info() -> list:
    """能力面：L4/L5 是否可启用、OCR 引擎与中文包。

    与 `main.py --runtime-report` 同源——那份报告是给打包冒烟用的，
    这份是给用户排障用的，两者必须回答同一批问题，否则会出现
    "冒烟说没问题、诊断包说有问题"的扯皮。
    """
    rows = []
    for layer, modname in (("L4 神经精排", "csc_neural"), ("L5 语法纠错", "csc_gec")):
        try:
            mod = importlib.import_module(f"gwtool.core.{modname}")
            rows.append((f"{layer} · 依赖就绪", str(mod.runtime_available())))
            rows.append((f"{layer} · 可启用", str(mod.available())))
            err = ""
            try:
                err = str(mod.last_error() or "")
            except Exception:
                pass
            if err:
                # 这一句能直接回答"为什么 L4 没生效"——外部原本无法区分
                # "层没开启"与"开启了但没命中"
                rows.append((f"{layer} · 最近一次错误", err))
        except Exception as exc:
            rows.append((layer, f"探测失败：{type(exc).__name__}"))
    try:
        from . import ocr
        rows.append(("OCR · 可用", str(ocr.available())))
        rows.append(("OCR · 走自带引擎", str(ocr.using_bundled())))
        rows.append(("OCR · 引擎路径", str(ocr.tesseract_path() or "未找到")))
    except Exception as exc:
        rows.append(("OCR", f"探测失败：{type(exc).__name__}"))
    return rows


def database_info() -> list:
    """数据库概览：**只有行数与体积**，不含任何正文字段。"""
    from ..db import connection as dbconn
    from ..db import dao
    from . import dbhealth

    rows = []
    try:
        rows.append(("数据库文件", str(dbconn.current_db_file())))
    except Exception:
        rows.append(("数据库文件", "无法确定"))
    rows.append(("文件占用", dbhealth.human_size(dbhealth.db_file_size())))
    try:
        ok, detail = dbhealth.quick_check()
        rows.append(("完整性自检", "正常" if ok else f"异常：{detail}"))
    except Exception as exc:
        rows.append(("完整性自检", f"无法执行：{type(exc).__name__}"))

    for label, fn in (("资料库文档", "count_documents"),
                      ("回收站文档", "count_deleted_documents"),
                      ("附件", "count_attachments"),
                      ("发文登记", "count_dispatch"),
                      ("收文登记", "count_receive"),
                      ("纠错对", "count_error_pairs")):
        func = getattr(dao, fn, None)
        if func is None:
            rows.append((label, "本版本无此统计"))
            continue
        try:
            rows.append((label, f"{func()} 条"))
        except Exception as exc:
            rows.append((label, f"读取失败：{type(exc).__name__}"))
    return rows


def log_tail(max_bytes: int = LOG_TAIL_BYTES) -> str:
    """运行日志的末尾若干字节。"""
    from .. import logs

    path = logs.log_path()
    if path is None or not path.exists():
        return "（暂无日志文件）"
    try:
        data = path.read_bytes()
    except OSError as exc:
        return f"（日志读取失败：{exc}）"
    prefix = ""
    if len(data) > max_bytes:
        data = data[-max_bytes:]
        prefix = f"（日志较长，此处仅保留最后 {max_bytes // 1024} KB）\n"
    return prefix + data.decode("utf-8", errors="replace")


def build_text_report() -> str:
    """把各段落拼成一份可读的文本报告。"""
    sections = (
        ("系统信息", system_info()),
        ("依赖版本", dependency_info()),
        ("能力探测", capability_info()),
        ("数据库概览（仅计数，不含正文）", database_info()),
    )
    lines = ["公文汇编助手 · 诊断报告",
             f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
             ""]
    for title, rows in sections:
        lines.append(f"===== {title} =====")
        for key, value in rows:
            lines.append(f"{key}: {value}")
        lines.append("")
    return "\n".join(lines)


def preview_lines() -> list:
    """生成前展示给用户的内容清单（含"不包含什么"）。

    纯文本，不用 Markdown 标记——它会被原样显示在 Qt 对话框里。
    """
    return [
        "系统信息：操作系统、CPU 架构、Python 版本、是否打包运行",
        "数据目录与输出目录的路径",
        "依赖版本：PySide6 / onnxruntime / tokenizers / numpy / "
        "python-docx / PyMuPDF / jieba",
        "能力探测：L4、L5 是否可用及其最近一次错误；OCR 引擎路径与中文包",
        "数据库概览：各表行数、文件体积、完整性自检结果",
        f"运行日志末尾（最多 {LOG_TAIL_BYTES // 1024} KB）",
        "",
        "不包含：任何公文正文、附件内容、词典条目或纠错对内容。",
    ]


def build(out_path) -> str:
    """生成诊断包，返回输出路径。"""
    with zipfile.ZipFile(str(out_path), "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("诊断报告.txt", build_text_report().encode("utf-8"))
        zf.writestr("运行日志.txt", log_tail().encode("utf-8"))
    return str(out_path)


def default_filename() -> str:
    return f"gwtool_诊断包_{datetime.now():%Y%m%d_%H%M%S}.zip"
