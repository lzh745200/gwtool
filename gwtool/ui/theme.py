# -*- coding: utf-8 -*-
"""界面设计 token：色板 / 字号 / 间距 统一管理（精品浅色主题）。

所有 UI 代码从本模块取值，不允许散落字面量——保证全应用视觉一致，
调一处即全局生效。

**对比度口径（v1.9 起，P1-32）**：关键文字对必须 ≥ 4.5:1（WCAG 2.1 AA，
正文/次要说明/警告/错误/成功/提示/选中行/表头），非文字图形（图标、
焦点描边）≥ 3:1。深浅两套调色板都按这个口径取值，`tests/test_theme_contrast_v19.py`
用纯计算守住（不依赖 Qt 渲染）。

主色有两种角色，深浅两套**无法用同一个值同时满足**：白字压主色实底需要
主色偏深（亮度低），主色文字压页面底需要主色偏亮（亮度高）。因此按角色
拆 token：`PRIMARY`（实底/描边）、`ACCENT_FG`（主色系文字）、
`SELECTED_FG`（选中行文字）、`ON_DANGER`（危险色实底上的文字）。
既有 token 名一个都不改，只调不足 4.5:1 的色值。
"""
from __future__ import annotations

# ---------------------------------------------------------------- 色板
PRIMARY = "#9E2B25"       # 公文红（主按钮实底 / 强调 / 描边 / 标题）
PRIMARY_DIM = "#7A1F1C"   # 主色深化（hover / 菜单选中底）
SURFACE = "#FAF7F2"       # 卡片 / 分组 / 交替行底色（纸面暖白）
BORDER = "#E0D8CE"        # 边框 / 分隔线 / Splitter
HEADING_BG = "#F0EAE0"    # 工具栏 / 表头 / 分组标题底色
BG = "#FFFFFF"            # 对话框 / 编辑区纯白底

# 语义色（浅色主题下对比度达标；v1.9 把不足 4.5:1 的 WARN / MUTED / SUCCESS
# 调深一档，色相不变——原值分别是 #b26a00 / #757575 / #2E7D32）
DANGER = "#b00020"        # 错误 / 确认错误
WARN = "#8A5200"          # 警告 / 疑似错误（原 #b26a00：压 WARN_BG 仅 3.71:1）
INFO = "#606060"          # 提示
MUTED = "#666666"         # 次要说明文字（原 #757575：压 HEADING_BG 仅 3.85:1）
ICON = "#5f6368"          # 工具栏图标描边色

# 语义底色与补充态（v1.7 UI 方案 §4.1 新增，与前景色成对设计，
# 正文 12pt 下对比度 ≥ 4.5:1。只增不改——既有 token 保持原值）
SUCCESS = "#2A7430"       # 成功 / 已办结 / 体检通过（原 #2E7D32：压底 4.44:1）
SUCCESS_BG = "#E8F1E9"    # 成功横幅底色
DANGER_BG = "#F7E6E8"     # 错误横幅 / 确认错误行底色
WARN_BG = "#F7EFE0"       # 警告横幅 / 疑似错误行底色
INFO_BG = "#EEF0F2"       # 提示横幅 / info 行底色
SELECTED_BG = "#F5EAE7"   # 列表/树选中行底（浅红纸，替代满行深红）
HOVER_BG = "#F5F2EC"      # 列表悬停行底
DISABLED_FG = "#A8A29A"   # 禁用态文字（WCAG 对 inactive 控件豁免对比度）
FOCUS_RING = "#9E2B254D"  # 键盘焦点外圈（主色 30% 透明）
SHADOW = "#3A2E2847"      # 卡片投影
TEXT = "#333333"          # 常规控件前景（按钮等）
HEADING_TEXT = "#5a4a3a"  # 表头文字（暖棕，与 HEADING_BG 成对）
TOOLTIP_BG = "#3a3028"    # 工具提示底色

# 角色色（v1.9 P1-32 新增，只增不改）：把"主色系文字"从实底主色里拆出来，
# 使深浅两套都能同时满足 4.5:1。浅色下与 PRIMARY / PRIMARY_DIM 同值。
ACCENT_FG = PRIMARY       # 主色系文字：分组标题 / 选中标签页 / 链接
SELECTED_FG = PRIMARY_DIM  # 列表/树选中行文字（与 SELECTED_BG 成对）
ON_DANGER = "#FFFFFF"      # 危险色实底上的文字（危险按钮 hover）
ON_PRIMARY = "#FFFFFF"     # 主色实底上的文字（工具栏主按钮；v1.9）

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
# 前景与语义色整体提亮一档保证对比度；公文红提亮（深底上保持品牌）。
# v1.9 P1-32 复测（WCAG）：选中行 1.67:1、主色文字 3.87:1、白字压主色 4.48:1
# 均不达标，按角色拆 token 后修复（见文件头"对比度口径"）。
_DARK = {
    "PRIMARY": "#BC4B43",      # 实底/描边；白字压底 4.97:1（原 #C4534B 仅 4.48）
    "PRIMARY_DIM": "#8F2F2A",
    "SURFACE": "#2C2521",
    "BORDER": "#4A3F38",
    "HEADING_BG": "#342C27",
    "BG": "#1F1917",
    "DANGER": "#E57373",       # 压 DANGER_BG 4.77:1（v1.9 复测达标，故保持原值）
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
    # 角色色：主色系文字 5.00~6.34:1；选中行文字 9.82:1；危险底上文字 6.07:1
    "ACCENT_FG": "#DD847D",
    "SELECTED_FG": "#F2D6D1",
    "ON_DANGER": "#241012",
    # 主色实底上的文字：深色 PRIMARY=#BC4B43，白字 4.97:1（浅色 7.3:1）
    "ON_PRIMARY": "#FFFFFF",
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
    实底形成明确区分。hover 的文字色取 ``ON_DANGER``：深色主题的 DANGER 为
    保证"错误文字压页面底"而提亮，白字压它会掉到 3:1 以下，故底色实底上
    统一走角色 token（浅色白字 / 深色深字）。
    """
    return (f"QPushButton {{ border:1px solid {DANGER}; color:{DANGER}; }}"
            f"QPushButton:hover {{ background:{DANGER}; color:{ON_DANGER}; }}")


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
    border: 1px solid {ACCENT_FG};
}}
/* 正文编辑区（§6.2）：白底无边框——描边由外层容器表达，正文区只留纸面 */
QTextEdit#doc_editor {{
    border: none;
    background: {BG};
}}
/* 资料库检索框（§6.3）：圆角 6px + SURFACE 底，与普通输入框区分 */
QLineEdit#search_box {{
    border-radius: 6px;
    background: {SURFACE};
}}
/* 文档列表（§6.3）：行高 32px（标题 1 行 + 副行）。
   min-height 是**下限**：副行参与排版时自然高度可能略高于 32px，
   这里保证行不会被压得更矮，命中尺寸与视觉密度都达标。 */
QListWidget#doc_list::item {{
    min-height: 32px;
}}
/* 编辑器大纲树（§6.2）：SURFACE 底、无边框（当前节高亮走全局选中态） */
QTreeWidget#outline {{
    background: {SURFACE};
    border: none;
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
    border: 1px solid {ACCENT_FG};
}}
/* 选中行 / 焦点行文字走 SELECTED_FG：深色下 PRIMARY_DIM 压 SELECTED_BG 只有
   1.67:1，选中行是高频阅读位置，必须≥4.5:1（v1.9 P1-32）。 */
QListWidget::item:selected, QTreeWidget::item:selected,
QTableWidget::item:selected {{
    background: {SELECTED_BG};
    color: {SELECTED_FG};
}}
QListWidget::item:focus, QTreeWidget::item:focus,
QTableWidget::item:focus {{
    background: {SELECTED_BG};
    color: {SELECTED_FG};
}}
QListWidget::item:hover, QTreeWidget::item:hover,
QTableWidget::item:hover {{
    background: {HOVER_BG};
}}
/* 分类树选中节点：主色左侧 3px 指示条（§6.3）—— 用 ACCENT_FG 保证深色下
   压 SURFACE 仍有 ≥3:1 的边界可见度（PRIMARY 实底色在深色下仅 2.66:1）。 */
QTreeWidget#cat_tree::item:selected {{
    border-left: 3px solid {ACCENT_FG};
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
    color: {ACCENT_FG};
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
    color: {ACCENT_FG};
    font-weight: bold;
    border-bottom: 2px solid {ACCENT_FG};
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
