# -*- coding: utf-8 -*-
"""v1.9 A 批：主题对比度（P1-32）+ 日志隐私护栏（P1-34）。

为什么两件事放一个文件：它们都是"审计发现、纯纪律、靠计算/静态扫描就能守住"
的缺陷，不值得各自开一套 Qt 夹具；文件按 `TestContrast*` / `TestLogPrivacy*`
两组组织。

P1-32（对比度）——审计实测：
  * 深色主题选中行前景/底色 **1.67:1**（PRIMARY_DIM #8F2F2A 压 SELECTED_BG #3D2A27）；
  * 浅色主题警告文字压警告底 **3.71:1**（WARN #b26a00 压 WARN_BG #F7EFE0）；
  * 次要说明（MUTED）、成功色（SUCCESS）、深色主色文字也不足 4.5:1。
本文件用 WCAG 2.1 相对亮度公式**纯计算**断言（不依赖 Qt 渲染，CI 无 GPU 也能跑），
把"正文 / 次要说明 / 语义色 / 选中行 / 表头 / 实底按钮文字"全部锁到 ≥4.5:1，
非文字图标锁 ≥3:1。禁用态文字按 WCAG 1.4.3 对 inactive 控件的豁免不设断言。

P1-34（日志隐私）——审计实测：`_MAX_MSG=512` 时 240 字的 f-string 正文可原样
落盘进日志/诊断包。本文件锁三件事：
  1. `_MAX_MSG` 已收紧到 ≤160（取值依据见 logs.py 注释）；
  2. 240 字正文样本被整条隐去，而正常排障消息（全仓最长 74 字）保留；
  3. AST 门禁：`gwtool/**/*.py` 不得出现 `logger.xxx(f"...")`（当前 0 处），
     且"内容安全标记"只允许 logs.py 自己用（否则业务模块能把正文标成安全）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from gwtool import logs
from gwtool.ui import theme

ROOT = Path(__file__).resolve().parent.parent
WHITE = "#FFFFFF"


# ================================================================ WCAG 计算
def _srgb_to_linear(channel: int) -> float:
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    assert len(h) == 6, f"不是 6 位十六进制色值：{hex_color!r}"
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return (0.2126 * _srgb_to_linear(r)
            + 0.7152 * _srgb_to_linear(g)
            + 0.0722 * _srgb_to_linear(b))


def _contrast(fg: str, bg: str) -> float:
    """WCAG 2.1 对比度：(L亮 + 0.05) / (L暗 + 0.05)。"""
    a, b = _luminance(fg), _luminance(bg)
    if a < b:
        a, b = b, a
    return (a + 0.05) / (b + 0.05)


def _resolve(palette: dict, token: str) -> str:
    if token == "WHITE":
        return WHITE
    return palette[token]


_PALETTES = {"light": theme._LIGHT, "dark": theme._DARK}

# (说明, 前景 token, 背景 token) —— 全部是 UI 里真实存在的"文字压底色"搭配
TEXT_PAIRS = [
    ("正文 TEXT/BG", "TEXT", "BG"),
    ("正文 TEXT/SURFACE", "TEXT", "SURFACE"),
    ("正文 TEXT/HEADING_BG", "TEXT", "HEADING_BG"),
    ("正文 TEXT/HOVER_BG", "TEXT", "HOVER_BG"),
    ("正文 TEXT/SELECTED_BG", "TEXT", "SELECTED_BG"),
    ("正文 TEXT/INFO_BG", "TEXT", "INFO_BG"),
    ("正文 TEXT/WARN_BG", "TEXT", "WARN_BG"),
    ("次要说明 MUTED/BG", "MUTED", "BG"),
    ("次要说明 MUTED/SURFACE", "MUTED", "SURFACE"),
    ("次要说明 MUTED/HEADING_BG", "MUTED", "HEADING_BG"),
    ("次要说明 MUTED/WARN_BG", "MUTED", "WARN_BG"),
    ("次要说明 MUTED/DANGER_BG", "MUTED", "DANGER_BG"),
    ("提示 INFO/INFO_BG", "INFO", "INFO_BG"),
    ("提示 INFO/BG", "INFO", "BG"),
    ("警告 WARN/WARN_BG", "WARN", "WARN_BG"),
    ("警告 WARN/BG", "WARN", "BG"),
    ("警告 WARN/SURFACE", "WARN", "SURFACE"),
    ("错误 DANGER/DANGER_BG", "DANGER", "DANGER_BG"),
    ("错误 DANGER/BG", "DANGER", "BG"),
    ("错误 DANGER/SURFACE", "DANGER", "SURFACE"),
    ("成功 SUCCESS/SUCCESS_BG", "SUCCESS", "SUCCESS_BG"),
    ("成功 SUCCESS/BG", "SUCCESS", "BG"),
    ("表头 HEADING_TEXT/HEADING_BG", "HEADING_TEXT", "HEADING_BG"),
    ("主色文字 ACCENT_FG/BG", "ACCENT_FG", "BG"),
    ("主色文字 ACCENT_FG/SURFACE", "ACCENT_FG", "SURFACE"),
    ("主色文字 ACCENT_FG/HEADING_BG", "ACCENT_FG", "HEADING_BG"),
]

# 选中行单独一组：审计点名的 1.67:1 就出在这里
SELECTED_PAIRS = [
    ("选中行 SELECTED_FG/SELECTED_BG", "SELECTED_FG", "SELECTED_BG"),
]

# 实底按钮/菜单：文字压在色块上
FILL_PAIRS = [
    ("主按钮文字/PRIMARY", "WHITE", "PRIMARY"),
    ("菜单选中文字/PRIMARY_DIM", "WHITE", "PRIMARY_DIM"),
    ("工具提示文字/TOOLTIP_BG", "WHITE", "TOOLTIP_BG"),
    ("危险按钮 hover 文字/DANGER", "ON_DANGER", "DANGER"),
]


class TestContrastHelper:
    def test_formula_endpoints(self):
        """公式自证：黑白 21:1、同色 1:1，避免断言建在错公式上。"""
        assert _contrast("#FFFFFF", "#000000") == pytest.approx(21.0, abs=0.01)
        assert _contrast("#123456", "#123456") == pytest.approx(1.0)

    def test_luminance_ignores_case_and_prefix(self):
        assert _luminance("#ffffff") == pytest.approx(_luminance("#FFFFFF"))


class TestContrastPairs:
    @pytest.mark.parametrize("palette_name", sorted(_PALETTES))
    @pytest.mark.parametrize("desc,fg, bg", TEXT_PAIRS + SELECTED_PAIRS + FILL_PAIRS)
    def test_pair_meets_wcag_aa(self, palette_name, desc, fg, bg):
        palette = _PALETTES[palette_name]
        ratio = _contrast(_resolve(palette, fg), _resolve(palette, bg))
        assert ratio >= 4.5, (
            f"[{palette_name}] {desc} 对比度 {ratio:.2f}:1 < 4.5:1 "
            f"（fg={_resolve(palette, fg)} bg={_resolve(palette, bg)}）")

    @pytest.mark.parametrize("palette_name", sorted(_PALETTES))
    def test_icon_meets_non_text_contrast(self, palette_name):
        """图标是非文字图形：WCAG 1.4.11 要求 ≥3:1。"""
        palette = _PALETTES[palette_name]
        ratio = _contrast(palette["ICON"], palette["HEADING_BG"])
        assert ratio >= 3.0, f"[{palette_name}] 工具栏图标对比度 {ratio:.2f}:1 < 3:1"

    @pytest.mark.parametrize("palette_name", sorted(_PALETTES))
    def test_focus_outline_meets_non_text_contrast(self, palette_name):
        """焦点/选中描边（主色系 ACCENT_FG）压页面底：非文字图形 ≥3:1。

        焦点描边用 ACCENT_FG 而非 PRIMARY 实底色：深色下 PRIMARY(#BC4B43)
        压 SURFACE 只有 2.66:1，作为"状态指示"是不可见的。
        """
        palette = _PALETTES[palette_name]
        for bg in ("BG", "SURFACE", "HEADING_BG"):
            ratio = _contrast(palette["ACCENT_FG"], palette[bg])
            assert ratio >= 3.0, (
                f"[{palette_name}] ACCENT_FG 描边压 {bg} 仅 {ratio:.2f}:1 < 3:1")

    def test_selected_row_regression_is_fixed(self):
        """历史值必须确实不达标，当前值必须达标——防止有人把旧色值调回来。"""
        assert _contrast("#8F2F2A", "#3D2A27") < 1.8, "旧深色选中行失真值变了"
        dark = theme._DARK
        assert _contrast(dark["SELECTED_FG"], dark["SELECTED_BG"]) >= 4.5
        assert _contrast(dark["SELECTED_FG"], dark["SELECTED_BG"]) >= 9.0, (
            "选中行应留足余量：它是最常驻的阅读位置")

    def test_light_warn_regression_is_fixed(self):
        assert _contrast("#b26a00", "#F7EFE0") < 4.0, "旧浅色警告失真值变了"
        light = theme._LIGHT
        assert _contrast(light["WARN"], light["WARN_BG"]) >= 4.5


class TestPaletteContract:
    def test_semantic_names_are_kept(self):
        """只调色值、不改语义常量名（上层大量 ``theme.WARN`` 式访问）。"""
        for name in ("PRIMARY", "PRIMARY_DIM", "SURFACE", "BORDER",
                     "HEADING_BG", "BG", "DANGER", "WARN", "INFO", "MUTED",
                     "ICON", "SUCCESS", "SUCCESS_BG", "DANGER_BG", "WARN_BG",
                     "INFO_BG", "SELECTED_BG", "HOVER_BG", "TEXT",
                     "HEADING_TEXT", "TOOLTIP_BG"):
            assert name in theme._LIGHT, f"浅色基线缺少 token {name}"
            assert name in theme._DARK, f"深色调色板缺少 token {name}"

    def test_role_tokens_exist_in_both_palettes(self):
        """角色色（主色文字/选中行/危险底文字）深浅两套都要有，否则深色下
        会残留浅色值 —— 视觉上表现为"选中行还是浅红纸"。"""
        for name in ("ACCENT_FG", "SELECTED_FG", "ON_DANGER"):
            assert isinstance(theme._LIGHT[name], str)
            assert isinstance(theme._DARK[name], str)
            assert theme._LIGHT[name] != theme._DARK[name] or name == "ON_DANGER"

    def test_set_dark_rebinds_role_tokens(self):
        try:
            theme.set_dark(True)
            for name in ("ACCENT_FG", "SELECTED_FG", "ON_DANGER"):
                assert getattr(theme, name) == theme._DARK[name], \
                    f"深色下 {name} 未随主题重绑"
            assert theme.is_dark()
        finally:
            theme.set_dark(False)
        for name in ("ACCENT_FG", "SELECTED_FG", "ON_DANGER"):
            assert getattr(theme, name) == theme._LIGHT[name], \
                f"还原浅色后 {name} 未回到基线"


class TestQssUsesRoleTokens:
    """QSS 必须引用角色 token，而不是把"文字色"硬绑在实底色上。"""

    @staticmethod
    def _block(qss: str, selector: str) -> str:
        assert selector in qss, f"QSS 缺少选择器 {selector}"
        return qss.split(selector, 1)[1].split("}", 1)[0]

    def test_light_selected_row_uses_selected_fg(self):
        theme.set_dark(False)
        try:
            block = self._block(theme.build_qss(), "QListWidget::item:selected")
            assert theme.SELECTED_FG in block
            assert _contrast(theme.SELECTED_FG, theme.SELECTED_BG) >= 4.5
        finally:
            theme.set_dark(False)

    def test_dark_selected_row_uses_selected_fg(self):
        try:
            theme.set_dark(True)
            block = self._block(theme.build_qss(), "QListWidget::item:selected")
            assert theme.SELECTED_FG in block
            assert "#8F2F2A" not in block, "1.67:1 的旧前景色不得回流"
            assert _contrast(theme.SELECTED_FG, theme.SELECTED_BG) >= 4.5
        finally:
            theme.set_dark(False)

    @pytest.mark.parametrize("palette_name", sorted(_PALETTES))
    def test_tab_and_group_title_use_accent(self, palette_name):
        theme.set_dark(palette_name == "dark")
        try:
            palette = _PALETTES[palette_name]
            qss = theme.build_qss()
            assert palette["ACCENT_FG"] in self._block(qss, "QTabBar::tab:selected")
            assert palette["ACCENT_FG"] in self._block(qss, "QGroupBox::title")
        finally:
            theme.set_dark(False)

    def test_danger_button_hover_uses_role_token(self):
        for flag, palette in ((False, theme._LIGHT), (True, theme._DARK)):
            theme.set_dark(flag)
            try:
                style = theme.danger_button_style()
                assert palette["ON_DANGER"] in style
                hover = style.split(":hover", 1)[1].split("}", 1)[0]
                fg = palette["ON_DANGER"]
                assert fg in hover
                assert _contrast(fg, palette["DANGER"]) >= 4.5
            finally:
                theme.set_dark(False)


# ================================================================ 日志隐私
LOG_METHODS = {"debug", "info", "warning", "warn", "error", "critical",
               "exception", "log"}


def _gwtool_sources() -> list[Path]:
    return sorted((ROOT / "gwtool").rglob("*.py"))


@pytest.fixture()
def log_env(tmp_db):
    """隔离日志环境（与 tests/test_logs.py 相同的复位语义）。"""
    logs.shutdown()
    yield
    logs.shutdown()


def _read_log() -> str:
    path = logs.log_path()
    assert path is not None and path.exists(), "日志文件未生成"
    return path.read_text(encoding="utf-8")


class TestLogPrivacyGate:
    def test_message_cap_is_tightened(self):
        """P1-34 核心：512 字的上限等于"整段正文可落盘"，必须收紧。"""
        assert logs._MAX_MSG <= 160, (
            f"_MAX_MSG={logs._MAX_MSG} 过宽：审计实测 240 字正文会原样落盘")
        # 也不能收得连正常排障消息都装不下（全仓最长无参消息 74 字）
        assert logs._MAX_MSG >= 96, "_MAX_MSG 过紧会误伤正常排障消息"
        assert logs._MAX_ARG == 64, "参数阈值 64 是隐私红线，不得放宽"

    def test_240_char_body_is_redacted(self, log_env):
        """审计样本量级（240 字）的 f-string 正文不得落盘。"""
        body = "机密正文标记-8802-" + "关于某某敏感事项的请示全文，" * 12
        assert len(body) > logs._MAX_MSG
        logs.setup()
        logs.get_logger("corrector").error("导入失败：" + body)
        logs.shutdown()
        text = _read_log()
        assert body not in text, "240 字正文经 f-string 通道泄漏"
        assert "内容已隐去" in text, "应走脱敏通道而非漏记"

    def test_normal_diagnostic_message_survives(self, log_env):
        """收紧不能把可诊断性修没了：全仓最长的正常消息必须完整保留。"""
        message = ("已返回 LibreOffice 转出的 PDF，但其目录页为占位文字；"
                   "补救：用「内置渲染器」导出 PDF，或在 Word/WPS 中「更新域」")
        assert len(message) <= logs._MAX_MSG
        logs.setup()
        logs.get_logger("compiler").warning(message)
        logs.shutdown()
        assert message in _read_log(), "正常排障消息被误伤"

    def test_content_free_summary_may_exceed_cap(self, log_env):
        """崩溃摘要（构造上不含正文）允许超上限，否则长路径会把异常类型隐去。"""
        summary = ("未捕获异常：ValueError（<12 字，内容已隐去>）@ C:\\"
                   + "非常深的安装路径\\" * 20 + "app.py:42")
        assert len(summary) > logs._MAX_MSG
        logs.setup()
        logs.get_logger("crash").error(
            summary, extra={logs._MSG_SAFE_ATTR: True})
        logs.shutdown()
        text = _read_log()
        assert "ValueError" in text, "崩溃摘要被整条隐去，排障第一步丢失"
        assert "app.py:42" in text

    def test_unmarked_summary_is_still_redacted(self, log_env):
        """对照组：同样的长消息不打标记就必须被隐去（标记不能被滥用）。"""
        summary = "未捕获异常：" + "Val" * 80
        logs.setup()
        logs.get_logger("crash").error(summary)
        logs.shutdown()
        text = _read_log()
        assert summary not in text
        assert "内容已隐去" in text

    def test_safe_marker_only_used_in_logs_module(self):
        """内容安全标记只允许 logs.py 自己用：业务模块若能用它，等于自证清白。"""
        marker = logs._MSG_SAFE_ATTR
        offenders = []
        for path in _gwtool_sources():
            if path.name == "logs.py":
                continue
            if marker in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(ROOT)))
        assert not offenders, (
            f"以下模块使用了内容安全标记 {marker!r}，仅 logs.py 内允许：{offenders}")

    def test_no_fstring_logging_calls(self):
        """AST 门禁：禁止 ``logger.xxx(f"...")``。

        f-string 在**调用前**就把正文拼进 msg，日志过滤器只能按长度兜底；
        正确写法是 ``logger.xxx("模板 %s", value)``，让 RedactingFilter 接管
        参数。当前全仓 0 处，此门禁防止以后再加。
        """
        offenders = []
        for path in _gwtool_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute):
                    continue
                if func.attr not in LOG_METHODS:
                    continue
                if any(isinstance(arg, ast.JoinedStr) for arg in node.args):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        assert not offenders, (
            "日志调用里出现 f-string（正文可能在调用前就拼进 msg）: "
            + ", ".join(offenders))
