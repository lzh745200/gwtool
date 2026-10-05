# -*- coding: utf-8 -*-
"""P2 深色主题与三档密度的契约测试（UI 设计方案 §4.3/§3.2/§9.1）。

守四件事：

1. **深色切换可还原** —— set_dark(True) 重绑 token、set_dark(False) 必须逐键
   还原浅色基线（否则先深后浅的调用序列会残留深色值造成跨用例污染）。
2. **QSS 跟随调色板** —— build_qss() 在深色下产出深底浅字（底色/前景/表头
   三处此前是字面量，重绑 token 后必须同步变化）。
3. **轻提示/主按钮等"构造期取色"的组件跟随主题** —— 取色必须在调用期完成。
4. **三档密度与折叠只动布局参数** —— 紧凑档换工具栏样式、折叠把右栏压到
   0、再展开还原；全程不新增控件、不碰业务逻辑。
"""
import pytest

from gwtool.ui import theme
from gwtool.ui.widgets import _toast_style


@pytest.fixture()
def light_after():
    """无论测试成败，离开时恢复浅色（token 是模块级全局，必须兜底还原）。"""
    snapshot = {k: getattr(theme, k) for k in theme._LIGHT}
    yield
    for k, v in snapshot.items():
        setattr(theme, k, v)
    theme._IS_DARK = False


def test_set_dark_rebinds_and_restores(light_after):
    light_primary = theme.PRIMARY
    theme.set_dark(True)
    assert theme.is_dark()
    assert theme.PRIMARY == theme._DARK["PRIMARY"]
    assert theme.BG == "#1F1917"
    theme.set_dark(False)
    assert not theme.is_dark()
    assert theme.PRIMARY == light_primary, "还原后必须回到浅色基线"
    # 逐键核对：所有 _LIGHT 键都与模块当前值一致（防残留）
    for k, v in theme._LIGHT.items():
        assert getattr(theme, k) == v, f"token {k} 未还原"


def test_build_qss_follows_palette(light_after):
    theme.set_dark(False)
    qss_light = theme.build_qss()
    assert theme.BG in qss_light and theme.TEXT in qss_light
    theme.set_dark(True)
    qss_dark = theme.build_qss()
    assert "#1F1917" in qss_dark and "#EFE7DE" in qss_dark
    assert theme.BG in qss_dark
    # 旧的浅色底不应再出现
    assert "#FFFFFF" not in qss_dark


def test_toast_style_resolves_at_call_time(light_after):
    """取色必须在**调用期**：import 期固化的 dict 会让深色下的轻提示
    仍用浅色底（这正是 §7.7 组件"构造期取色"要防的坑）。"""
    theme.set_dark(True)
    bg, fg = _toast_style("success")
    assert bg == theme._DARK["SUCCESS_BG"] and fg == theme._DARK["SUCCESS"]
    theme.set_dark(False)
    bg2, _ = _toast_style("success")
    assert bg2 == theme._LIGHT["SUCCESS_BG"]


def test_unknown_toast_kind_falls_back(light_after):
    theme.set_dark(True)
    bg, _fg = _toast_style("不存在的")
    assert bg == theme._DARK["INFO_BG"]


# ---------------------------------------------------------------- 三档密度
@pytest.fixture()
def win(tmp_db, qapp):
    from gwtool import app
    from gwtool.ui.main_window import MainWindow
    app.ensure_database_seeded()
    from gwtool.db import connection as dbconn
    dbconn.configure(tmp_db)
    w = MainWindow()
    yield w
    w.close()


def test_density_tiers_switch_toolbar_style(win):
    from PySide6.QtCore import Qt
    win._apply_density(1000)
    assert win._density == "compact"
    assert win._tb.toolButtonStyle() == Qt.ToolButtonTextOnly
    win._apply_density(1366)
    assert win._density == "standard"
    assert win._tb.toolButtonStyle() == Qt.ToolButtonTextUnderIcon
    win._apply_density(1920)
    assert win._density == "wide"


def test_density_keeps_right_panel_visible(win):
    """紧凑档收窄右栏但不强制折叠——折叠必须只由用户触发（§3.2）。"""
    win._apply_density(1000)
    assert win._split.sizes()[2] > 0
    assert not win._right_collapsed


def test_collapse_and_restore_right_panel(win):
    win._apply_density(1366)                       # 标准档：右栏 300
    before = win._split.sizes()[2]
    assert before > 0
    win._toggle_right_panel()
    assert win._right_collapsed
    assert win._split.sizes()[2] == 0, "折叠后右栏必须压到 0"
    assert win._btn_toggle_right.text() == "◂"
    win._toggle_right_panel()
    assert not win._right_collapsed
    assert win._split.sizes()[2] > 0, "展开后右栏必须还原"


def test_window_resize_does_not_fight_user_drag(win, qapp):
    """非档位变化的普通缩放不得按档位默认值重排 splitter（否则用户拖动
    被覆盖）。注意：QSplitter 在窗口变大时本身会**按比例**拉伸各栏，
    所以这里断言的是"比例保持"，而不是像素不变。"""
    win.show()
    qapp.processEvents()
    win.resize(1366, 700)
    qapp.processEvents()
    win._apply_density(1366)                       # 标准档
    win._split.setSizes([400, 500, 100])           # 模拟用户拖动
    qapp.processEvents()
    before = win._split.sizes()
    win.resize(1380, 700)                          # 同档内微调
    qapp.processEvents()
    after = win._split.sizes()
    assert win._density == "standard"
    # 比例保持（±15% 容差）：210 那种"整栏被重置"才算失败
    for b, a in zip(before, after):
        assert abs(a - b) <= max(30, b * 0.15), (before, after)
