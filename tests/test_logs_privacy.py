# -*- coding: utf-8 -*-
"""诊断日志与诊断包的**隐私泄露**专项测试。

为什么单独立文件：`test_logs.py` 证明"日志能写"，`test_diagpack.py` 证明
"包能生成"，但**两者都没证明"正文不在里面"**——而后者才是这条功能的
存在前提（党政机关/涉密单位，正文出内网是事故，不是体验问题）。

历史事实（2026-09-17 发现）：`redact()` 定义于 `logs.py:40` 却**全仓 0 处调用**；
`diagpack.build()` 把**整份原始日志**塞进 zip；而 UI（`main_window.py:438`）
向用户承诺"不含任何公文正文"。更关键的是，既有的
`test_pack_contains_no_document_text` 只把标记写进**数据库**、从不写日志，
对**日志通道零覆盖** —— 20 个用例全过而漏洞照旧。

本文件把"纪律"变成"可自动核查的事实"：
  1. 带参日志的**参数**不得进日志（`RedactingFilter` 在入队前脱敏）
  2. **崩溃摘要**不得含异常消息正文（excepthook 只留类型+位置+长度）
  3. **诊断包**（zip 内所有成员）不得含正文标记
另有一条反向断言：**短参数必须保留** —— 证明过滤器没有把排障信息一并抹掉
（否则这条隐私修复会变成"把可诊断性也一起修没了"）。
"""
from __future__ import annotations

import sys
import zipfile

import pytest

from gwtool import logs
from gwtool.core import diagpack

SECRET = ("机密正文标记-8802-关于某某敏感事项的请示全文，" * 12)[:-1]
# ⚠️ 样本长度是有意的：真实公文正文是数百字量级。`RedactingFilter` 的
# 参数阈值是 64 字 —— 为的是**保留**路径/计数/状态码这类有用短参数
# （诊断包本身就刻意包含数据目录路径）。25 字的标记低于阈值，
# 会被当短参数放行 —— 那是设计取舍不是缺陷，测试样本必须与真实体量一致。


@pytest.fixture()
def log_env(tmp_db):
    """隔离的日志环境（与 test_logs.log_env 相同的复位语义）。"""
    logs.shutdown()
    yield
    logs.shutdown()


def _log_text() -> str:
    path = logs.log_path()
    assert path is not None and path.exists(), "日志文件未生成"
    return path.read_text(encoding="utf-8")


def _pack_members(log_env, tmp_db, tmp_path) -> dict:
    """生成诊断包并返回 {成员名: 解码文本}。"""
    logs.setup()
    out = tmp_path / "diag.zip"
    diagpack.build(out)
    logs.shutdown()
    members = {}
    with zipfile.ZipFile(str(out)) as zf:
        for name in zf.namelist():
            members[name] = zf.read(name).decode("utf-8", errors="replace")
    return members


class TestLogChannel:
    """渠道一：带参日志。参数是调用方显式传的，过滤器必须接管。"""

    def test_long_arg_is_redacted(self, log_env):
        """参数体量 ≈ 正文体量时必须被压成长度标记。"""
        logs.setup()
        logs.get_logger("corrector").info("处理完成：%s", SECRET)
        logs.shutdown()
        text = _log_text()
        assert "处理完成" in text, "排障需要知道发生了什么"
        assert SECRET not in text, "正文经日志通道泄漏"
        assert "内容已隐去" in text, "应走脱敏通道而非漏记"

    def test_short_arg_is_kept(self, log_env):
        """短参数必须保留：不能把排障信息一并抹掉。"""
        logs.setup()
        logs.get_logger("corrector").info("重建索引完成，条数=%s", 40220)
        logs.shutdown()
        assert "40220" in _log_text()

    def test_format_string_without_args_is_capped(self, log_env):
        """f-string 直接拼正文（无参）是编码纪律问题，过滤器只能兜底截断。"""
        logs.setup()
        logs.get_logger("corrector").error("导入失败：" + SECRET * 40)
        logs.shutdown()
        assert SECRET not in _log_text()

    def test_exception_message_never_reaches_log(self, log_env):
        """异常消息经常携带被处理的数据（如'无法识别的金额：正文'）。"""
        logs.setup()
        original = sys.excepthook
        try:
            logs.install_excepthooks()
            try:
                raise ValueError(f"无法识别的金额：{SECRET}")
            except ValueError:
                sys.excepthook(*sys.exc_info())
        finally:
            sys.excepthook = original
        logs.shutdown()
        text = _log_text()
        assert "ValueError" in text, "排障第一步是'什么类型的错'"
        assert SECRET not in text, "异常消息正文经崩溃通道泄漏"


class TestDiagpackChannel:
    """渠道二：诊断包。它是唯一会被**发到内网之外**的产物。"""

    def test_pack_excludes_log_body(self, log_env, tmp_db, tmp_path):
        """日志通道与崩溃通道的标记都不得出现在包内任何成员里。"""
        marker1 = SECRET           # 与真实正文同量级（> 过滤器阈值 64）
        marker2 = "崩溃正文标记-7713"
        logs.setup()
        logs.get_logger("corrector").error("处理失败：%s", marker1)
        original = sys.excepthook
        try:
            logs.install_excepthooks()
            try:
                raise RuntimeError(f"处理 {marker2} 时崩溃")
            except RuntimeError:
                sys.excepthook(*sys.exc_info())
        finally:
            sys.excepthook = original

        members = _pack_members(log_env, tmp_db, tmp_path)
        assert members, "诊断包为空"
        joined = "\n".join(members.values())
        for marker in (marker1, marker2):
            assert marker not in joined, f"正文标记 {marker!r} 泄漏进诊断包"

    def test_pack_still_contains_diagnostic_facts(self, log_env, tmp_db, tmp_path):
        """脱敏不能把"能排障"也修没了：版本/能力/行数必须还在。"""
        members = _pack_members(log_env, tmp_db, tmp_path)
        joined = "\n".join(members.values())
        for fact in ("公文汇编助手", "依赖版本", "能力探测", "数据库概览"):
            assert fact in joined, f"脱敏后丢失了排障必需的信息：{fact}"

    def test_preview_promise_matches_reality(self, log_env, tmp_db, tmp_path):
        """UI 向用户承诺"不含正文"——这条承诺必须可核查。"""
        lines = diagpack.preview_lines()
        assert any("不包含" in l for l in lines), "预览必须说明不包含什么"

        members = _pack_members(log_env, tmp_db, tmp_path)
        joined = "\n".join(members.values())
        assert SECRET not in joined


class TestCapabilityErrorChannel:
    """渠道三：`capability_info` 会落 L4/L5 的"最近一次错误"。

    那条错误串可能携带被处理的数据（如"无法识别的金额：正文"），
    因此必须只落长度标记。
    """

    def test_l4_error_is_redacted(self, log_env, tmp_db, tmp_path, monkeypatch):
        import gwtool.core.csc_neural as neural

        def fake_last_error():
            return f"推理失败：{SECRET}"

        monkeypatch.setattr(neural, "last_error", fake_last_error, raising=False)
        rows = dict(diagpack.capability_info())
        hit = [v for k, v in rows.items() if "最近一次错误" in k]
        assert hit, "未找到 L4 最近一次错误条目"
        assert SECRET not in hit[0], "L4 错误串把正文带进了诊断包"
        assert "内容已隐去" in hit[0]

    def test_l5_error_is_redacted(self, log_env, tmp_db, tmp_path, monkeypatch):
        import gwtool.core.csc_gec as gec

        def fake_last_error():
            return f"解码失败：{SECRET}"

        monkeypatch.setattr(gec, "last_error", fake_last_error, raising=False)
        rows = dict(diagpack.capability_info())
        hit = [v for k, v in rows.items() if "最近一次错误" in k]
        assert hit and SECRET not in hit[0]
