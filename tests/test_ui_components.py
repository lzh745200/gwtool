# -*- coding: utf-8 -*-
"""P1 状态与组件补全的契约测试（UI 设计方案 §7.6/§7.7/§11.2）。

守四件事：

1. **三个新组件不引入弹窗** —— P1 红线：新增 QMessageBox/QFileDialog 入口必须
   同步 mock 清单；EmptyState/StatusBarToast/StepIndicator 全部是纯 QWidget，
   这条用"组件可独立构造并渲染"来守护。
2. **轻提示是轻的** —— StatusBarToast 定时自清、不阻塞；样式色值必须来自
   theme token（禁用新造色值）。
3. **空状态不侵入宿主** —— EmptyState 覆盖层跟随宿主尺寸；无行动按钮时对
   鼠标透明（不挡列表右键菜单等既有交互）。
4. **五处接入真的接上了** —— 资料库/检索无结果/纠错结果/台账表格/回收站，
   每处都要能在空数据时显示、有数据时隐藏（isVisibleTo 断言：顶层窗口未
   show() 时 isVisible() 恒为 False，会假绿）。
"""
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QToolButton

from gwtool import paths
from gwtool.core import corrector
from gwtool.ui.widgets import (EmptyState, StatusBarToast, StepIndicator,
                               make_primary_tool_button)
from gwtool.ui import theme


# ---------------------------------------------------------------- EmptyState
def test_empty_state_basic(qapp):
    e = EmptyState("inbox", "资料库还是空的", hint="拖入文件")
    assert e.lbl_text.text() == "资料库还是空的"
    assert e.lbl_hint.isVisibleTo(e)          # 有 hint 时显示
    e2 = EmptyState("inbox", "空")            # 无 hint
    assert not e2.lbl_hint.isVisibleTo(e2)
    # 无行动按钮 → 对鼠标透明（不挡宿主右键菜单）
    assert e.testAttribute(Qt.WA_TransparentForMouseEvents)


def test_empty_state_actions_and_mount(qapp):
    fired = []
    host = __import__("PySide6.QtWidgets", fromlist=["QListWidget"]).QListWidget()
    e = EmptyState("inbox", "空", actions=[("点我", lambda: fired.append(1))]
                   ).mount(host)
    # 有行动按钮 → 不透明（按钮要能点）
    assert not e.testAttribute(Qt.WA_TransparentForMouseEvents)
    assert e.parent() is host and not e.isVisibleTo(host)
    e.set_visible(True)
    assert e.isVisibleTo(host)
    host.show()                   # 未显示的宿主不投递 Resize（推迟到 show）
    qapp.processEvents()
    host.resize(400, 300)
    qapp.processEvents()
    assert e.width() == 400 and e.height() == 300, "覆盖层未跟随宿主尺寸"
    # 触发行动按钮
    for btn in e.findChildren(__import__("PySide6.QtWidgets",
                                          fromlist=["QPushButton"]).QPushButton):
        btn.click()
    assert fired == [1]
    e.set_visible(False)
    assert not e.isVisibleTo(host)


def test_empty_state_unknown_icon_is_safe(qapp):
    e = EmptyState("不存在的图标名", "仍可构造")
    assert e.lbl_text.text() == "仍可构造"      # 未知图标返回空 QIcon，不抛


# ---------------------------------------------------------------- 轻提示
def test_toast_shows_and_auto_clears(qapp):
    holder = __import__("PySide6.QtWidgets", fromlist=["QWidget"]).QWidget()
    t = StatusBarToast(holder, seconds=30)
    t.show_message("已入库", "success")
    assert t.isVisibleTo(holder) and "已入库" in t.text()
    assert theme.SUCCESS_BG in t.styleSheet(), "样式色值必须来自 theme token"
    time.sleep(0.12)
    __import__("PySide6.QtWidgets", fromlist=["QApplication"]).QApplication.processEvents()
    assert not t.isVisibleTo(holder), "超时后必须自清"


def test_toast_unknown_kind_falls_back_to_info(qapp):
    t = StatusBarToast(seconds=60000)
    t.show_message("x", "不存在的kind")
    assert theme.INFO_BG in t.styleSheet()


def test_toast_timer_dies_with_widget(qapp):
    t = StatusBarToast(seconds=60000)
    t.show_message("x")
    t.deleteLater()
    __import__("PySide6.QtWidgets", fromlist=["QApplication"]).QApplication.processEvents()
    # QTimer 以 self 为 parent：组件销毁后 timer 不再活跃（无泄漏告警即通过）


# ---------------------------------------------------------------- 步骤指示器
def test_step_indicator_paints(qapp):
    s = StepIndicator(["选材料", "定模板", "出成品"])
    s.resize(400, 34)
    s.set_current(1)
    s.set_current(99)          # 越界钳制，不抛
    s.set_current(-5)
    pm = s.grab()
    assert not pm.isNull() and pm.toImage().bits() is not None


def test_step_indicator_empty_steps(qapp):
    s = StepIndicator([])
    s.resize(300, 34)
    assert not s.grab().isNull()          # 空步骤不崩


# ---------------------------------------------------------------- 主按钮
def test_primary_tool_button_keeps_action(qapp):
    from PySide6.QtGui import QAction
    a = QAction("一键汇编", None)
    btn = make_primary_tool_button(a)
    assert btn.objectName() == "btn_primary_action"
    assert btn.defaultAction() is a
    assert theme.PRIMARY in btn.styleSheet()      # 主色实底（§3.1）


# ---------------------------------------------------------------- 集成点
@pytest.fixture()
def win(tmp_db, qapp, monkeypatch):
    """主窗口（复用 UI 冒烟的构造路径）。"""
    from gwtool import app
    from gwtool.ui.main_window import MainWindow
    app.ensure_database_seeded()
    monkeypatch.setattr(paths, "_override", tmp_db.parent / "win_data")
    (tmp_db.parent / "win_data").mkdir(parents=True, exist_ok=True)
    from gwtool.db import connection as dbconn
    dbconn.configure(tmp_db)
    w = MainWindow()
    yield w
    w.close()


def test_main_window_has_toast_channel(win):
    assert hasattr(win, "toast") and hasattr(win, "_toast")
    assert win._toast.parent() is win.status or win._toast.parent() is None


def test_main_window_compile_button_is_primary(win):
    btns = [b for b in win.findChildren(QToolButton)
            if b.objectName() == "btn_primary_action"]
    assert len(btns) == 1, "汇编主按钮应恰好一个（§3.1 高频区主视觉）"
    a = btns[0].defaultAction()
    assert a is not None and "汇编" in a.text()


def test_reminder_badge_is_capsule(win):
    ss = win._reminder_btn.styleSheet()
    assert theme.DANGER in ss and "border-radius" in ss, "督办徽标应为 DANGER 胶囊"


def test_library_empty_state_visible_when_no_docs(win):
    lp = win.library
    lp._add_empty_hint()
    assert lp._empty_no_data.isVisibleTo(lp.doc_list)
    lp._add_empty_hint(searching=True)
    assert lp._empty_no_match.isVisibleTo(lp.doc_list)
    assert not lp._empty_no_data.isVisibleTo(lp.doc_list), "两种空互斥"


def test_corr_list_empty_state_states(win):
    rp = win.reference
    rp._corrections = []
    rp._checked_text = ""
    rp._fill_corr_list()
    assert rp._empty_corr.isVisibleTo(rp.corr_list)
    assert "还没有" in rp._empty_corr.lbl_text.text()
    # 检查过但 0 命中 → 成功语义
    rp._checked_text = "正文"
    rp._fill_corr_list()
    assert "未检出" in rp._empty_corr.lbl_text.text()
    # 有命中 → 隐藏
    rp._corrections = [corrector.Correction(0, 2, "布署", "部署",
                                            "错别字", "词库匹配", 0.9)]
    rp._fill_corr_list()
    assert not rp._empty_corr.isVisibleTo(rp.corr_list)
    # 有命中但全被置信度门槛滤掉 → 提示调阈值
    rp._corrections = [corrector.Correction(0, 2, "布署", "部署",
                                            "错别字", "词库匹配", 0.1)]
    rp._fill_corr_list()
    assert rp._empty_corr.isVisibleTo(rp.corr_list)
    assert "置信度" in rp._empty_corr.lbl_text.text() + rp._empty_corr.lbl_hint.text()


def test_registry_and_receive_empty_state(win):
    from gwtool.ui.receive_dialog import ReceiveDialog
    from gwtool.ui.registry_dialog import RegistryDialog
    rd = RegistryDialog(win)
    rd.reload()
    assert rd._empty.isVisibleTo(rd.table)
    rc = ReceiveDialog(win)
    rc.reload()
    assert rc._empty.isVisibleTo(rc.tbl)


def test_recycle_bin_empty_state(tmp_db, qapp):
    from gwtool.ui.material_dialogs import RecycleBinDialog
    dlg = RecycleBinDialog()
    dlg.reload()
    assert dlg._empty.isVisibleTo(dlg.table)


def test_import_dialog_has_stage_label(tmp_db, qapp):
    from gwtool.ui.import_dialog import ImportDialog
    dlg = ImportDialog()
    assert hasattr(dlg, "lbl_stage")
    assert not dlg.lbl_stage.isVisibleTo(dlg), "未开始导入时阶段文案应隐藏"


def test_compile_wizard_has_step_indicator(tmp_db, qapp):
    from gwtool.ui.compile_wizard import CompileWizard
    from gwtool.ui.widgets import StepIndicator as SI
    wiz = CompileWizard()
    assert isinstance(wiz.sideWidget(), SI), "步骤指示器应挂在向导扩展位"
    assert wiz._steps_bar._steps == ["选材料", "定模板", "出成品"]
