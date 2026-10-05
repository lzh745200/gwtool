# -*- coding: utf-8 -*-
"""界面设计 token：色板 / 字号 / 间距 统一管理（精品浅色主题）。

所有 UI 代码从本模块取值，不允许散落字面量——保证全应用视觉一致，
调一处即全局生效。
"""
from __future__ import annotations

# ---------------------------------------------------------------- 色板
PRIMARY = "#9E2B25"       # 公文红（主按钮 / 强调 / 选中态 / 标题）
PRIMARY_DIM = "#7A1F1C"   # 主色深化（hover / 选中底色）
SURFACE = "#FAF7F2"       # 卡片 / 分组 / 交替行底色（纸面暖白）
BORDER = "#E0D8CE"        # 边框 / 分隔线 / Splitter
HEADING_BG = "#F0EAE0"    # 工具栏 / 表头 / 分组标题底色
BG = "#FFFFFF"            # 对话框 / 编辑区纯白底

# 语义色（浅色主题下对比度达标）
DANGER = "#b00020"        # 错误 / 确认错误
WARN = "#b26a00"          # 警告 / 疑似错误
INFO = "#606060"          # 提示
MUTED = "#757575"         # 次要说明文字
ICON = "#5f6368"          # 工具栏图标描边色

# 语义底色与补充态（v1.7 UI 方案 §4.1 新增，与前景色成对设计，
# 正文 12pt 下对比度 ≥ 4.5:1。只增不改——既有 token 保持原值）
SUCCESS = "#2E7D32"       # 成功 / 已办结 / 体检通过
SUCCESS_BG = "#E8F1E9"    # 成功横幅底色
DANGER_BG = "#F7E6E8"     # 错误横幅 / 确认错误行底色
WARN_BG = "#F7EFE0"       # 警告横幅 / 疑似错误行底色
INFO_BG = "#EEF0F2"       # 提示横幅 / info 行底色
SELECTED_BG = "#F5EAE7"   # 列表/树选中行底（浅红纸，替代满行深红）
HOVER_BG = "#F5F2EC"      # 列表悬停行底
DISABLED_FG = "#A8A29A"   # 禁用态文字（比 MUTED 更浅一档）
FOCUS_RING = "#9E2B254D"  # 键盘焦点外圈（主色 30% 透明）
SHADOW = "#3A2E2847"      # 卡片投影
TEXT = "#333333"          # 常规控件前景（按钮等）
HEADING_TEXT = "#5a4a3a"  # 表头文字（暖棕，与 HEADING_BG 成对）
TOOLTIP_BG = "#3a3028"    # 工具提示底色

# ---------------------------------------------------------------- 字号 (pt)
TITLE = 16                # 对话框标题 / 主窗口大标题
SECTION = 13              # 分组标题 / 面板小标题
BODY = 12                 # 正文 / 按钮 / 输入框（与全局 QSS 一致）
SMALL = 11                # 状态栏 / 辅助说明
MUTED_SMALL = 10.5        # 提示行 / 占位文字

# ---------------------------------------------------------------- 间距 (px)
MARGIN = 12               # 对话框 / 面板外边距
GROUP_GAP = 8             # 分组间距 / 控件组之间的间隙
ROW_GAP = 4               # 行内间距 / 紧凑布局


# ================================================================ 深色主题
# 深色调色板（UI 设计方案 §4.3/P2）：只覆盖"看起来会错"的键——字号/间距
# 不随主题变化。取值原则：底色走深墨暖色（与 PPT 封面的 DARK/DCARD 一致），
# 前景与语义色整体提亮一档保证对比度；公文红提亮为 #C4534B（深底上保持品牌）。
_DARK = {
    "PRIMARY": "#C4534B",
    "PRIMARY_DIM": "#8F2F2A",
    "SURFACE": "#2C2521",
    "BORDER": "#4A3F38",
    "HEADING_BG": "#342C27",
    "BG": "#1F1917",
    "DANGER": "#E57373",
    "WARN": "#E5A044",
    "INFO": "#B8B2AB",
    "MUTED": "#A99C90",
    "ICON": "#C7BDB2",
    "SUCCESS": "#81C784",
    "SUCCESS_BG": "#253528",
    "DANGER_BG": "#3A2528",
    "WARN_BG": "#3A3021",
    "INFO_BG": "#2E3134",
    "SELECTED_BG": "#3D2A27",
    "HOVER_BG": "#322B26",
    "DISABLED_FG": "#6B6259",
    "TEXT": "#EFE7DE",
    "HEADING_TEXT": "#C7B8A8",
    "TOOLTIP_BG": "#4A3F38",
}

_IS_DARK = False


def set_dark(flag: bool) -> None:
    """切换深浅主题：重绑本模块的色值常量（必须在创建任何窗口之前调用）。

    为什么用 globals().update 而不是让调用方读调色 dict：全仓已有大量
    ``theme.PRIMARY`` 形式的**模块属性访问**（动态取值），重绑常量后它们
    无需任何改动即切到深色。调用点只有 app.py 启动序列（读
    follow_system_theme + 系统深浅色），运行期不切换（切主题重启生效，
    与既有设置文案一致）。
    """
    global _IS_DARK
    # _LIGHT 在模块底部采集：set_dark(False) 必须能**还原**浅色值，
    # 否则先深后浅（测试反复调用）会残留深色值造成跨用例污染。
    globals().update(_DARK if flag else _LIGHT)
    _IS_DARK = bool(flag)


def is_dark() -> bool:
    return _IS_DARK


# 浅色基线的快照（set_dark(False) 的还原源）。必须在此刻采集——
# 上面任何 token 改值都会被这里记录为"浅色标准值"。
_LIGHT = {k: v for k, v in globals().items()
          if k.isupper() and isinstance(v, str)}


def severity_color(severity: str) -> str:
    """error/warn/info -> 颜色。"""
    return {"error": DANGER, "warn": WARN}.get(severity, INFO)


def group_title_style(spaced: bool = False) -> str:
    """分组标题 QLabel 样式：加粗；spaced=True 时附分组间上边距。

    既有 8 处对话框各自手写同一字面量（"font-weight:bold;margin-top:8pt;"），
    收敛到此一处——改主题/间距时不再逐处找。
    """
    return "font-weight:bold;" + ("margin-top:8pt;" if spaced else "")


def danger_button_style() -> str:
    """危险操作按钮样式（§6.8 / §7.1）：白底 + DANGER 描边与文字，hover 反色。

    仅用于不可逆操作（彻底删除 / 清空回收站 / 恢复覆盖），与主按钮的主色
    实底形成明确区分。
    """
    return (f"QPushButton {{ border:1px solid {DANGER}; color:{DANGER}; }}"
            f"QPushButton:hover {{ background:{DANGER}; color:white; }}")


def danger_group_style() -> str:
    """危险区分组框样式（§6.8）：DANGER 描边 + 深红标题。"""
    return (f"QGroupBox {{ border:1px solid {DANGER}; border-radius:4px;"
            f"margin-top:12px; padding-top:4px; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin; left:8px;"
            f"padding:0 4px; color:{DANGER}; font-weight:bold; }}")


def build_qss() -> str:
    """全局 QSS（精品浅色）：统一字体/间距/控件外观/交互反馈。"""
    return f"""
* {{
    font-family: "Microsoft YaHei";
    font-size: {BODY}pt;
}}
QDialog, QMainWindow {{
    background: {BG};
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
    padding: 3px 6px;
    border: 1px solid {BORDER};
    border-radius: 3px;
    background: {BG};
    selection-background-color: {PRIMARY_DIM};
    selection-color: white;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus {{
    border: 1px solid {PRIMARY};
}}
/* 正文编辑区（§6.2）：白底无边框——描边由外层容器表达，正文区只留纸面 */
QTextEdit#doc_editor {{
    border: none;
    background: {BG};
}}
QPushButton {{
    padding: 4px 14px;
    border: 1px solid {BORDER};
    border-radius: 3px;
    background: {BG};
    color: {TEXT};
}}
QPushButton:hover {{
    background: {SURFACE};
    border-color: {PRIMARY};
}}
QPushButton:pressed {{
    background: {HEADING_BG};
}}
QPushButton:default {{
    background: {PRIMARY};
    color: white;
    border-color: {PRIMARY};
    font-weight: bold;
}}
QPushButton:default:hover {{
    background: {PRIMARY_DIM};
}}
QPushButton:disabled {{
    color: {MUTED};
    background: {SURFACE};
}}
QListWidget, QTreeWidget, QTableWidget {{
    border: 1px solid {BORDER};
    border-radius: 3px;
    background: {BG};
    alternate-background-color: {SURFACE};
}}
/* 焦点可见化（UI 方案 §10）：不用 outline:none 一禁了之——
   键盘焦点用主色边框表达（与输入框 focus 同一语义），
   当前项用焦点底色表达（不再依赖原生虚线框）。 */
QListWidget:focus, QTreeWidget:focus, QTableWidget:focus {{
    border: 1px solid {PRIMARY};
}}
QListWidget::item:selected, QTreeWidget::item:selected,
QTableWidget::item:selected {{
    background: {SELECTED_BG};
    color: {PRIMARY_DIM};
}}
QListWidget::item:focus, QTreeWidget::item:focus,
QTableWidget::item:focus {{
    background: {SELECTED_BG};
    color: {PRIMARY_DIM};
}}
QListWidget::item:hover, QTreeWidget::item:hover,
QTableWidget::item:hover {{
    background: {HOVER_BG};
}}
/* 分类树选中节点：主色左侧 3px 指示条（§6.3） */
QTreeWidget#cat_tree::item:selected {{
    border-left: 3px solid {PRIMARY};
}}
QHeaderView::section {{
    background: {HEADING_BG};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 4px 6px;
    font-weight: bold;
    color: {HEADING_TEXT};
}}
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 4px;
    margin-top: 12px;
    padding-top: 4px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
    color: {PRIMARY};
    font-weight: bold;
}}
QToolBar {{
    background: {HEADING_BG};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 2px 4px;
    spacing: 2px;
}}
QToolBar::separator {{
    width: 1px;
    background: {BORDER};
    margin: 4px;
}}
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 0 0 3px 3px;
    background: {BG};
}}
QTabBar::tab {{
    padding: 5px 14px;
    border: 1px solid {BORDER};
    border-bottom: none;
    background: {SURFACE};
    color: {MUTED};
}}
QTabBar::tab:selected {{
    background: {BG};
    color: {PRIMARY};
    font-weight: bold;
    border-bottom: 2px solid {PRIMARY};
}}
QSplitter::handle {{
    background: {BORDER};
}}
QSplitter::handle:horizontal {{ width: 3px; }}
QSplitter::handle:vertical {{ height: 3px; }}
QStatusBar {{
    border-top: 1px solid {BORDER};
    background: {SURFACE};
    font-size: {SMALL}pt;
    color: {MUTED};
}}
QProgressBar {{
    border: 1px solid {BORDER};
    border-radius: 3px;
    background: {SURFACE};
    text-align: center;
    font-size: {SMALL}pt;
}}
QProgressBar::chunk {{
    background: {PRIMARY};
    border-radius: 2px;
}}
QMenuBar {{
    background: {HEADING_BG};
    border-bottom: 1px solid {BORDER};
}}
QMenuBar::item:selected {{
    background: {PRIMARY_DIM};
    color: white;
}}
QMenu {{
    background: {BG};
    border: 1px solid {BORDER};
}}
QMenu::item:selected {{
    background: {PRIMARY_DIM};
    color: white;
}}
QScrollBar:vertical {{
    width: 10px; background: {SURFACE}; border: none;
}}
QScrollBar::handle:vertical {{
    background: {BORDER}; border-radius: 4px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{
    height: 10px; background: {SURFACE}; border: none;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER}; border-radius: 4px; min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{ background: {MUTED}; }}
QToolTip {{
    background: {TOOLTIP_BG};
    color: white;
    border: none;
    padding: 4px 8px;
    font-size: {SMALL}pt;
}}
"""
