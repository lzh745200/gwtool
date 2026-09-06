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


def severity_color(severity: str) -> str:
    """error/warn/info -> 颜色。"""
    return {"error": DANGER, "warn": WARN}.get(severity, INFO)


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
QPushButton {{
    padding: 4px 14px;
    border: 1px solid {BORDER};
    border-radius: 3px;
    background: {BG};
    color: #333333;
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
    selection-background-color: {PRIMARY_DIM};
    selection-color: white;
    outline: none;
}}
QHeaderView::section {{
    background: {HEADING_BG};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 4px 6px;
    font-weight: bold;
    color: #5a4a3a;
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
    background: #3a3028;
    color: white;
    border: none;
    padding: 4px 8px;
    font-size: {SMALL}pt;
}}
"""
