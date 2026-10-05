# -*- coding: utf-8 -*-
"""UI 公共组件与工具函数。

含三个"状态表达"组件（UI 设计方案 §7.6/§7.7/P1）：

  * :class:`EmptyState`      —— 空状态（图标 + 一句话 + 可选行动按钮），
    以**覆盖层**方式挂到列表/表格上，不侵入宿主的既有布局；
  * :class:`StatusBarToast`  —— 状态栏轻提示条（非模态，定时自清），
    落实"能用轻提示就不用弹窗"的反馈分级；
  * :class:`StepIndicator`   —— 向导步骤指示器（纯 paintEvent 自绘），
    供 ``QWizard.setTitleWidget`` 使用。

三者都是纯 QWidget 组合：不新增任何弹窗入口（模态框 mock 清单无需同步）、
不带线程、不碰业务层。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QFontDatabase, QPainter, QColor, QPen
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout,
                               QLabel, QMessageBox, QPushButton, QToolButton,
                               QVBoxLayout, QWidget)

from ..core.template import FONT_BODY, FONT_HEI, FONT_KAI, FONT_SONG, FONT_XBS
from . import icons, theme

# 公文常用字体（缺失时在界面上给出提示，仍允许选择）
OFFICIAL_FONTS = [FONT_XBS, FONT_BODY, FONT_HEI, FONT_KAI, FONT_SONG,
                  "仿宋", "楷体", "SimSun", "SimHei", "FangSong", "KaiTi"]


def font_families_official_first() -> list[str]:
    installed = set(QFontDatabase.families())
    ordered = [f for f in OFFICIAL_FONTS if f in installed]
    rest = sorted(f for f in installed if f not in set(ordered))
    return ordered + ["（以下为系统全部字体）"] + rest if rest else ordered


def missing_official_fonts() -> list[str]:
    installed = set(QFontDatabase.families())
    return [f for f in (FONT_BODY, FONT_HEI, FONT_KAI) if f not in installed]


def make_font_combo(current: str = "") -> QComboBox:
    cb = QComboBox()
    fams = font_families_official_first()
    cb.addItems(fams)
    # 缺失的公文标准字体在悬浮提示里标明（**不能**改显示文本加后缀——
    # currentText() 会被直接写进模板字体名，污染生成的 docx）
    try:
        from ..core.fontcheck import missing_fonts
        _missing = set(missing_fonts())
        for i in range(cb.count()):
            if cb.itemText(i) in _missing:
                cb.setItemData(i, "本机未安装该字体，生成公文时会被替换字体",
                               Qt.ToolTipRole)
    except Exception:
        pass
    if current:
        idx = cb.findText(current)
        if idx >= 0:
            cb.setCurrentIndex(idx)
    return cb


def make_size_combo(sizes: list[float], current: float = 0) -> QComboBox:
    cb = QComboBox()
    for s in sizes:
        cb.addItem(f"{s:g} pt", s)
        cb.setItemData(cb.count() - 1, s)
    if current:
        for i in range(cb.count()):
            if abs(cb.itemData(i) - current) < 0.01:
                cb.setCurrentIndex(i)
                break
    return cb


def make_mm_spin(value: float = 0, lo=0.0, hi=300.0) -> QDoubleSpinBox:
    sp = QDoubleSpinBox()
    sp.setRange(lo, hi)
    sp.setSuffix(" mm")
    sp.setDecimals(1)
    sp.setValue(value)
    return sp


def info(parent: QWidget | None, text: str, title: str = "提示") -> None:
    QMessageBox.information(parent, title, text)


def warn(parent: QWidget | None, text: str, title: str = "注意") -> None:
    QMessageBox.warning(parent, title, text)


def ask(parent: QWidget | None, text: str, title: str = "确认",
        ok_text: str = "") -> bool:
    """确认框。`ok_text` 非空时用**自定义确认按钮文字**（§6.8）。

    为什么要能自定义：危险/不可逆操作（彻底删除、清空回收站、恢复覆盖当前
    资料）的确认按钮必须写明后果，按钮写"是/否"时用户根本不知道自己点了什么。
    默认（`ok_text` 为空）仍走既有的 Yes/No 询问，行为与此前逐字节一致 ——
    既有调用方与相关测试不受影响。
    """
    if ok_text:
        return _ask_custom(parent, text, title, ok_text)
    ret = QMessageBox.question(parent, title, text,
                               QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
    return ret == QMessageBox.Yes


def _ask_custom(parent: QWidget | None, text: str, title: str,
                ok_text: str) -> bool:
    """自定义确认按钮的确认框（独立函数，便于测试整体替换）。"""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(title)
    box.setText(text)
    ok_btn = box.addButton(ok_text, QMessageBox.AcceptRole)
    cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
    box.setDefaultButton(cancel_btn)      # 危险操作默认落在"取消"上
    box.exec()
    return box.clickedButton() is ok_btn


def dialog_buttons(parent, *specs) -> QHBoxLayout:
    """统一按钮行：主按钮在右、次按钮在左、中间 stretch。

    specs 每项 = (text, callback, is_default)；仅一项时全宽（如"关闭"）。
    主按钮自动 setDefault(True) 以获得 QSS 主色底。
    """
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    if len(specs) == 1:
        layout.addStretch(1)
    for text, cb, *rest in specs:
        is_default = rest[0] if rest else False
        btn = QPushButton(text, parent)
        btn.clicked.connect(cb)
        if is_default:
            btn.setDefault(True)
            layout.addStretch(1)
        layout.addWidget(btn)
    return layout


def wait_for_threads(owner, timeout_ms: int = 10000) -> list:
    """等待 owner（含其子对象）启动的 QThread 收工；返回超时未退的线程名。

    为什么必须有这个：QThread 在**仍在运行时**被析构，Qt 会直接 qFatal 强杀
    整个进程（Windows 退出码 0xC0000409，stderr 只有一句
    "QThread: Destroyed while thread is still running"）。
    触发场景很日常：纠错/查重/PDF 渲染还没跑完就关窗口或关对话框。

    对支持的 worker 先调 stop()（协作式中断），再 wait()；超时的线程交给
    调用方提示用户，并在 closeEvent 的退出路径上由调用方决定是否 terminate
    （见下）。
    """
    from PySide6.QtCore import QThread
    stuck: list[str] = []
    # owner 可能是轻量替身（测试里驱动 closeEvent 的假窗口），没有 findChildren；
    # 这种情况说明它本来也没有子线程，直接返回空表而不是抛 AttributeError ——
    # 退出路径绝不该因为一个诊断调用而中断（那会让"退出自动备份"整段被跳过）。
    finder = getattr(owner, "findChildren", None)
    if not callable(finder):
        return stuck
    try:
        threads = list(finder(QThread))
    except (RuntimeError, TypeError):
        return stuck                    # owner 已随父对象销毁
    for th in threads:
        try:
            if not th.isRunning():
                continue
            if hasattr(th, "stop"):
                th.stop()
            if not th.wait(timeout_ms):
                stuck.append(th.objectName() or type(th).__name__)
        except RuntimeError:
            continue
    return stuck


class ThreadSafeDialog:
    """给"自己起后台线程"的对话框加一道退出保险。

    用法：``class Foo(ThreadSafeDialog, QDialog)``（混入必须排在 Qt 类之前）。

    不这么做的后果不是"线程泄漏"这么温和——QThread 在运行中被析构会让 Qt
    直接 qFatal 强杀进程。对话框先于线程关闭时（用户按 Esc / 点关闭），
    对话框析构 → 子线程析构 → 进程消失，且没有任何可读的错误提示。
    """

    def closeEvent(self, event):
        stuck = wait_for_threads(self, timeout_ms=8000)
        if stuck:
            try:
                warn(self, "以下后台任务未能在 8 秒内结束，窗口将关闭：\n  "
                           + "\n  ".join(stuck))
            except Exception:
                pass
        super().closeEvent(event)


# ================================================================ 空状态
class EmptyState(QWidget):
    """空状态组件：图标 + 一句话 + 说明行 + 行动按钮（可选）。

    用法（覆盖层模式，不侵入宿主布局）::

        empty = EmptyState("inbox", "资料库还是空的",
                           hint="拖入 docx / pdf / txt，或点击工具栏「导入材料」")
        empty.mount(host_list_widget)      # 自动跟随宿主尺寸
        ...
        empty.set_visible(not rows)        # 数据变化处控制显隐

    设计约定（UI 方案 §7.6）：纯 QWidget 组合、不引资源文件、
    图标复用 icons.py 的线性风格；**没有按钮也能用**（actions 可省）。
    """

    def __init__(self, icon_name: str, text: str, hint: str = "",
                 actions: "list[tuple[str, object]] | None" = None,
                 parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        v = QVBoxLayout(self)
        v.setContentsMargins(theme.MARGIN, theme.MARGIN, theme.MARGIN, theme.MARGIN)
        v.addStretch(1)
        self._icon = QLabel()
        ic = icons.icon(icon_name)
        if not ic.isNull():
            self._icon.setPixmap(ic.pixmap(48, 48))
        self._icon.setAlignment(Qt.AlignHCenter)
        v.addWidget(self._icon)
        self.lbl_text = QLabel(text)
        self.lbl_text.setAlignment(Qt.AlignHCenter)
        self.lbl_text.setWordWrap(True)
        v.addWidget(self.lbl_text)
        self.lbl_hint = QLabel(hint)
        self.lbl_hint.setAlignment(Qt.AlignHCenter)
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet(f"color:{theme.MUTED};"
                                    f"font-size:{theme.SMALL}pt;")
        self.lbl_hint.setVisible(bool(hint))
        v.addWidget(self.lbl_hint)
        if actions:
            row = QHBoxLayout()
            for label, cb in actions:
                btn = QPushButton(label)
                btn.clicked.connect(cb)
                row.addWidget(btn)
            row.addStretch(1)
            v.addLayout(row)
        else:
            # 无行动按钮时对鼠标完全透明：不挡宿主的右键菜单/双击等既有交互
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        v.addStretch(2)

    # ---- 覆盖层：跟随宿主尺寸 ----
    def mount(self, host: QWidget) -> "EmptyState":
        """把自己变成 host 的子控件并铺满它（宿主 resize 时自动跟随）。

        通过 eventFilter 监听宿主 Resize，**不改宿主的任何布局与行为**——
        这是它能把空状态带进"已经定型的页面"而不动布局的原因。
        """
        self.setParent(host)
        self.setGeometry(host.rect())
        host.installEventFilter(self)
        self.hide()
        return self

    def eventFilter(self, obj, event):
        # Resize：跟随宿主尺寸；Show：宿主显示时也要重铺一次——
        # Qt 对**未显示**的 widget 不投递 Resize 事件（推迟到 show），
        # 只听 Resize 会让"构造期挂载、显示后才有正确尺寸"的路径漏铺。
        if obj is self.parent() and event.type() in (QEvent.Type.Resize,
                                                     QEvent.Type.Show):
            self.setGeometry(self.parent().rect())
        return super().eventFilter(obj, event)

    # ---- 显隐 ----
    def set_visible(self, shown: bool) -> None:
        """空状态只在"确实没数据"时出现；show/hide 同时收起宿主焦点。"""
        self.setVisible(bool(shown))
        if shown:                       # 重新铺一次，防宿主在隐藏期间被 resize
            self.setGeometry(self.parent().rect())
            self.raise_()


# ================================================================ 轻提示条
def _toast_style(kind: str) -> "tuple[str, str]":
    """kind -> (底色, 前景色)。**调用期**取 theme 常量（而非 import 期固化）：
    深色主题下 set_dark 重绑 token 后，新弹出的提示自动跟随。未知 kind
    降级为 info（绝不因参数抛异常）。"""
    table = {
        "success": (theme.SUCCESS_BG, theme.SUCCESS),
        "info": (theme.INFO_BG, theme.INFO),
        "warn": (theme.WARN_BG, theme.WARN),
        "danger": (theme.DANGER_BG, theme.DANGER),
    }
    return table.get(kind, table["info"])


class StatusBarToast(QLabel):
    """状态栏轻提示条：SUCCESS/INFO 底色 + 定时自清（默认 4 秒）。

    反馈分级（§7.7）的"轻提示"通道——已保存/已忽略/已复制这类
    成功与次要反馈不再用 QMessageBox 打断。放在
    ``statusBar().addPermanentWidget``，不会被瞬态 showMessage 覆盖。

    QTimer 以 self 为 parent：随窗口销毁自动清理（红线 §11.2，
    参考 main_window 右键菜单的 deleteLater 纪律）。
    """

    def __init__(self, parent=None, seconds: int = 4000):
        super().__init__(parent)
        self._seconds = int(seconds)
        self.hide()
        self._timer = QTimer(self)          # parent=self → 随组件销毁
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._clear)

    def show_message(self, text: str, kind: str = "success",
                     seconds: int | None = None) -> None:
        """显示一条轻提示；未知 kind 降级为 info（绝不因参数抛异常）。"""
        bg, fg = _toast_style(kind)
        self.setText(text)
        self.setStyleSheet(
            f"background:{bg};color:{fg};border-radius:9px;"
            f"padding:2px 10px;font-size:{theme.SMALL}pt;")
        self.adjustSize()
        self.show()
        self._timer.start(self._seconds if seconds is None else int(seconds))

    def _clear(self) -> None:
        self.clear()
        self.hide()


# ================================================================ 步骤指示器
class StepIndicator(QWidget):
    """横向步骤指示器：已完成（描边对勾）→ 当前（实心）→ 未达（灰）。

    供 ``QWizard.setTitleWidget`` 使用（不侵入向导既有布局），
    纯 paintEvent 自绘、零动画依赖（§9.1 便携/低端机约束）。
    """

    def __init__(self, steps: "list[str]", parent=None):
        super().__init__(parent)
        self._steps = list(steps)
        self._current = 0
        self.setFixedHeight(34)
        self.setMinimumWidth(max(260, 110 * len(steps)))

    def set_current(self, index: int) -> None:
        self._current = max(0, min(int(index), len(self._steps) - 1))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        n = len(self._steps)
        if not n:
            return
        w = self.width()
        # 节点圆心均匀分布，留出两侧文字余量
        xs = [int(w * (i + 0.5) / n) for i in range(n)]
        cy = 15
        r = 9
        p.setFont(self.font())
        for i, name in enumerate(self._steps):
            x = xs[i]
            done, cur = i < self._current, i == self._current
            # 连接线：连接相邻节点，颜色随完成度推进
            if i:
                p.setPen(QPen(QColor(theme.PRIMARY if i <= self._current
                                     else theme.BORDER), 2))
                p.drawLine(xs[i - 1] + r + 4, cy, x - r - 4, cy)
            # 节点
            p.setPen(QPen(QColor(theme.PRIMARY if (done or cur)
                                 else theme.BORDER), 2))
            p.setBrush(QColor(theme.PRIMARY if cur else theme.BG))
            p.drawEllipse(x - r, cy - r, 2 * r, 2 * r)
            p.setPen(QColor("white" if cur else
                            (theme.PRIMARY if done else theme.MUTED)))
            fw = p.fontMetrics().horizontalAdvance("✓")
            p.drawText(x - fw / 2, cy + 4, "✓" if done else str(i + 1))
            # 名称
            p.setPen(QColor(theme.PRIMARY if cur else theme.MUTED))
            tw = p.fontMetrics().horizontalAdvance(name)
            p.drawText(x - tw / 2, cy + r + 14, name)
        p.end()


def make_primary_tool_button(action) -> QToolButton:
    """把 QAction 包装成主色实底的 QToolButton（工具栏"汇编"主入口，§3.1）。

    用 defaultAction 关联既有动作：动作的 trigger/快捷键/菜单引用全部保持，
    测试遍历 QAction 触发的行为不变。
    """
    btn = QToolButton()
    btn.setObjectName("btn_primary_action")
    btn.setDefaultAction(action)
    btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
    btn.setStyleSheet(
        f"QToolButton#btn_primary_action {{ background:{theme.PRIMARY};"
        f"border:1px solid {theme.PRIMARY}; border-radius:3px; padding:2px 8px; }}"
        f"QToolButton#btn_primary_action:hover {{ background:{theme.PRIMARY_DIM}; }}"
        f"QToolButton#btn_primary_action:pressed {{ background:{theme.HEADING_BG}; }}")
    return btn


# ================================================================ 徽标与横幅
#: 语义 → (底色, 前景色, 描边色)。全部取自 theme token，禁用新造色值（§7.4）。
_CHIP_KINDS = {
    "info": ("INFO_BG", "INFO"),
    "primary": ("BG", "PRIMARY"),
    "success": ("SUCCESS_BG", "SUCCESS"),
    "warn": ("WARN_BG", "WARN"),
    "danger": ("DANGER_BG", "DANGER"),
}


def make_chip(text: str, kind: str = "info") -> QLabel:
    """胶囊徽标（UI 方案 §7.4）：圆角 = 高度/2，配色取自语义 token。

    用途：状态徽标（在办/办结/归档）、格式徽标（docx/pdf/扫描）、
    计数徽标（纠错 N 处 / 督办 N 件逾期）。未知 kind 退化为 info。
    """
    bg_name, fg_name = _CHIP_KINDS.get(kind, _CHIP_KINDS["info"])
    bg, fg = getattr(theme, bg_name), getattr(theme, fg_name)
    chip = QLabel(text)
    chip.setAlignment(Qt.AlignCenter)
    chip.setStyleSheet(
        f"background:{bg};color:{fg};border:1px solid {fg};"
        f"border-radius:9px;padding:1px 8px;font-size:{theme.SMALL}pt;")
    return chip


def make_chip_cell(text: str, kind: str = "info") -> QWidget:
    """把徽标装进"左对齐的容器"，用于表格单元格（cellWidget）。

    为什么不直接把 QLabel 当 cellWidget：cellWidget 会被拉伸填满单元格，
    胶囊会被拉成方块。容器 + 弹簧让徽标保持自身尺寸。
    """
    holder = QWidget()
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(theme.ROW_GAP, 2, theme.ROW_GAP, 2)
    lay.addWidget(make_chip(text, kind))
    lay.addStretch(1)
    return holder


class Banner(QFrame):
    """对话框顶部结论横幅（UI 方案 §6.7 / §8.4）：图标 + 一句话 + 统计徽标。

    用法::

        self.banner = Banner()
        v.addWidget(self.banner)          # 放在对话框内容最上方
        ...
        self.banner.show_result("success", "体检通过：未发现必须整改的问题",
                                [("错误 0", "success"), ("建议 3", "warn")])
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("banner")
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.MARGIN, theme.GROUP_GAP,
                               theme.MARGIN, theme.GROUP_GAP)
        row.setSpacing(theme.GROUP_GAP)
        self._icon = QLabel()
        row.addWidget(self._icon)
        self._text = QLabel("")
        self._text.setWordWrap(True)
        row.addWidget(self._text, 1)
        self._chip_row = QHBoxLayout()
        self._chip_row.setSpacing(theme.ROW_GAP)
        row.addLayout(self._chip_row)
        self.hide()

    def show_result(self, kind: str, text: str,
                    chips: "list[tuple[str, str]] | None" = None) -> None:
        """展示结论。kind: success / warn / danger / info（未知按 info）。"""
        bg_name, fg_name = _CHIP_KINDS.get(kind, _CHIP_KINDS["info"])
        bg, fg = getattr(theme, bg_name), getattr(theme, fg_name)
        self.setStyleSheet(
            f"QFrame#banner {{ background:{bg};"
            f"border:1px solid {fg}; border-radius:4px; }}")
        icon_name = "check" if kind == "success" else "inspect"
        ic = icons.icon(icon_name)
        if not ic.isNull():
            self._icon.setPixmap(ic.pixmap(24, 24))
        self._text.setText(text)
        self._text.setStyleSheet(f"color:{fg};")
        while self._chip_row.count():                 # 清掉上一轮的徽标
            item = self._chip_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for label, chip_kind in (chips or []):
            self._chip_row.addWidget(make_chip(label, chip_kind))
        self.show()

    def clear(self) -> None:
        self.hide()
