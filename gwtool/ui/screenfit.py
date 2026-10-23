# -*- coding: utf-8 -*-
"""小屏 / 高 DPI 的对话框夹取（P1-31）。

为什么需要它
------------
产品目标环境是 **1366×768 办公笔记本**，且不少单位仍按 100% 之外的高缩放
（125%/150%）使用 —— Windows 的"可用区域"会随缩放同步变小。而应用里有一批
对话框是按"设计尺寸"硬开的（如任意文档纠错 1080×720、收文台账 1120×680），
在可用区只有 1024×616 的机器上，标题栏或底部按钮会跑到屏幕外，用户既看不到
"确定"也不知道窗口还能拖动。

本模块只做**最小落地**：不改任何布局结构，只在对话框显示后把尺寸夹进
可用区域、并把它挪回可见范围。三层兜底：

1. `fit_dialog_to_screen(dlg)`：按 `availableGeometry()` 计算，超出才缩；
2. `install_dialog_autofit(app)`：装一个应用级事件过滤器，任何 QDialog 第一次
   Show 时用 `QTimer.singleShot(0, ...)` 延后夹取（等 Qt 应用完 sizeHint）；
3. 全程异常吞掉、幂等：夹取失败最多是"这个对话框没被夹"，绝不能让对话框
   打不开（诊断日志留痕）。

为什么用 singleShot(0) 而不是在 Show 事件里同步 resize：Show 事件到达时
Qt 还没把布局算完，同步 resize 会被随后的 sizeHint 应用覆盖；延后一拍到
事件循环，才能量到最终尺寸（实测同步改尺寸对手工 resize() 过的对话框无效）。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSize, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog

from .. import logs

log = logs.get_logger("ui.screenfit")

# 可用区域四周留的余量（像素）：部分窗口管理器/任务栏会在 availableGeometry
# 内再扣一层，不留余量时贴边窗口会被系统装饰吃掉几个像素。
DEFAULT_MARGIN = 24
# 极端小屏（如 800×600 远程桌面）下的硬下限：再小就没有可读性了，宁可略超屏
# 也不再压缩 —— 空间不够时"内容被裁"与"窗口不可读"之间选前者。
MIN_USABLE_W = 320
MIN_USABLE_H = 240

_FILTER: "_DialogAutofitFilter | None" = None


def _target_screen(widget):
    """优先用控件当前所在屏幕（多屏时别把副屏对话框拽回主屏）。"""
    try:
        screen = widget.screen()
    except (AttributeError, RuntimeError):
        screen = None
    if screen is None:
        try:
            screen = QGuiApplication.primaryScreen()
        except Exception:
            screen = None
    return screen


def available_rect(widget, margin: int = DEFAULT_MARGIN):
    """控件所在屏幕的可用区域（已扣 margin）；无屏幕时返回 None。"""
    screen = _target_screen(widget)
    if screen is None:
        return None
    avail = screen.availableGeometry()
    if margin <= 0:
        return avail
    return avail.adjusted(margin, margin, -margin, -margin)


def fit_dialog_to_screen(dlg, margin: int = DEFAULT_MARGIN) -> "QSize | None":
    """把对话框夹进屏幕可用区域，返回夹取后的尺寸（失败返回 None）。

    规则（与任务口径一致）：
      · 尺寸**没超出**可用区域 → 不动（幂等；第二次调用不会再改任何东西）；
      · 超出 → resize 到 `min(max(minimumSizeHint, sizeHint), 可用区域-margin)`，
        既不小于最小可读尺寸，也不大过屏幕；
      · 位置无论是否缩放都夹回可用区域内（居中到可用区域）。

    一个必须处理的现实：布局会把对话框的隐式最小尺寸定得比小屏可用区还大
    （例如任意文档纠错的布局最小宽 808，而 800×800 离屏/小屏的可用宽只有
    800）。Qt 的默认布局约束（SetDefaultConstraint）会把任何 resize **夹回
    布局最小尺寸**，于是"算得再小也落不下去"。因此当目标尺寸小于布局最小
    尺寸时，先用 setMinimumSize 显式下调 widget 最小尺寸（显式值优先于布局
    hint），再 resize —— 宁可内容略被压缩，也不能让标题栏/按钮跑到屏幕外。

    不抛异常：调用点可能是 show 事件过滤器，夹取失败绝不能连累对话框显示。
    """
    try:
        screen = _target_screen(dlg)
        if screen is None:
            return None
        avail = screen.availableGeometry()
        if avail.width() <= 0 or avail.height() <= 0:
            return None
        # 优先按 margin 收边；屏幕极小时才退到"贴边"（可用区域本身是硬边界）
        soft_w = max(MIN_USABLE_W, avail.width() - margin * 2)
        soft_h = max(MIN_USABLE_H, avail.height() - margin * 2)
        cur = dlg.size()
        min_hint = dlg.minimumSizeHint()
        hint = dlg.sizeHint()
        width, height = cur.width(), cur.height()
        if width > soft_w or height > soft_h:
            width = min(max(min_hint.width(), hint.width()), soft_w)
            height = min(max(min_hint.height(), hint.height()), soft_h)
            if width < min_hint.width() or height < min_hint.height():
                # 布局下限比屏幕还大：显式下调，否则 resize 会被 Qt 夹回去
                log.debug("对话框布局下限 %s 超过屏幕，改为 %s×%s",
                          min_hint, width, height)
                dlg.setMinimumSize(width, height)
            dlg.resize(width, height)
        # 位置夹取：整块窗口（含系统装饰）都要落在可用区域内。
        frame = dlg.frameGeometry()
        if (frame.right() > avail.right() or frame.left() < avail.left()
                or frame.bottom() > avail.bottom() or frame.top() < avail.top()):
            x = avail.left() + max(0, (avail.width() - frame.width()) // 2)
            y = avail.top() + max(0, (avail.height() - frame.height()) // 2)
            dlg.move(x, y)
        return QSize(dlg.size())
    except (RuntimeError, AttributeError, TypeError) as exc:
        # RuntimeError：对话框在 singleShot 排队期间已被关闭/销毁
        log.debug("对话框夹取跳过：%s", exc)
        return None
    except Exception as exc:                    # UI 兜底：夹取失败不连累对话框
        log.warning("对话框夹取失败（不影响使用）：%s", exc)
        return None


class _DialogAutofitFilter(QObject):
    """应用级事件过滤器：QDialog 每次 Show 后延后一拍夹取。"""

    def eventFilter(self, obj, event):
        try:
            if event.type() == QEvent.Show and isinstance(obj, QDialog):
                # 先确认对象仍然活着再排队：Show 与 singleShot 之间可能被关闭
                QTimer.singleShot(0, lambda dlg=obj: fit_dialog_to_screen(dlg))
        except Exception:
            pass
        return False


def install_dialog_autofit(app) -> bool:
    """给应用装全局对话框夹取过滤器（幂等；返回是否已安装）。

    只装一次：重复调用不会叠出多个过滤器（每次 Show 被夹两次虽然幂等，
    但会增加无意义的事件循环任务）。安装失败返回 False，调用方不必处理。
    """
    global _FILTER
    if app is None:
        return False
    if _FILTER is not None:
        return True
    try:
        _FILTER = _DialogAutofitFilter(app)
        app.installEventFilter(_FILTER)
        return True
    except Exception as exc:                    # 安装失败不阻断启动
        log.warning("对话框自动夹取安装失败（不影响使用）：%s", exc)
        _FILTER = None
        return False


def uninstall_dialog_autofit(app) -> None:
    """卸下过滤器（测试/退出用；幂等）。"""
    global _FILTER
    if app is None or _FILTER is None:
        return
    try:
        app.removeEventFilter(_FILTER)
    except Exception:
        pass
    _FILTER = None

