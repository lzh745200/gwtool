# -*- coding: utf-8 -*-
"""主窗口：三栏布局（资料库 | 编辑器 | 纠错与参考）+ 全部功能入口。"""
from __future__ import annotations

from PySide6.QtCore import QSize, QThread, QTimer, Qt
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QLabel,
                               QMainWindow, QPushButton, QSplitter, QStatusBar)

from .. import APP_NAME, __version__, logs
from ..core.backup import (MODE_AUTO, MODE_MANUAL, create_backup_detailed,
                           list_backups, restore_backup_detailed)
from ..db import dao
from ..paths import db_path, export_dir
from .compile_wizard import CompileWizard
from .dict_manager import DictManager
from .editor_panel import EditorPanel
from .feature_dialogs import (BatchCorrectDialog, BulkReplaceDialog,
                              InspectorDialog, SecurityDialog,
                              SimilarityDialog, SkeletonDialog,
                              SnapshotsDialog)
from .import_dialog import ImportDialog
from .library_panel import LibraryPanel
from .material_dialogs import RecycleBinDialog
from .reference_panel import ReferencePanel
from .receive_dialog import ReceiveDialog
from .registry_dialog import RegistryDialog
from .template_editor import TemplateEditor
from .widgets import (ask, info, missing_official_fonts, wait_for_threads, warn)

log = logs.get_logger("ui.main")


def _fit_to_screen(win, want_w: int, want_h: int) -> None:
    """按目标尺寸开窗，但**绝不超出屏幕可用区域**。

    为什么需要夹取：本产品目标环境是 1366×768 办公笔记本，扣掉任务栏与
    标题栏后可用高度约 700–730px。原先硬编码 resize(1360, 820) 会让底部
    状态栏与编辑器底边在**第一次启动时就被裁掉**，且用户不知道还能调高。

    最小尺寸护栏**让位于不超屏**：屏幕比最小尺寸还小（离屏测试 800×800、
    极端小屏）时，把窗口压到屏幕内比保住布局更重要。
    """
    MIN_W, MIN_H = 1024, 640
    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        avail = screen.availableGeometry()
        # 留余量：部分 WM 会在可用区域内再扣一层
        avail_w = max(320, avail.width() - 40)
        avail_h = max(240, avail.height() - 40)
        min_w = min(MIN_W, avail_w)
        min_h = min(MIN_H, avail_h)
        win.setMinimumSize(min_w, min_h)
        win.resize(max(min(want_w, avail_w), min_w),
                   max(min(want_h, avail_h), min_h))
        return
    win.setMinimumSize(MIN_W, MIN_H)
    win.resize(want_w, want_h)


def _wait_all_threads(win) -> list[str]:
    """等 win（含其子对象）以及所有打开对话框里的 QThread 收工。

    返回超时未退的线程名（供 UI 提示用户）。模块级函数而非实例方法，
    这样 closeEvent 的关键逻辑才能被轻量替身驱动的测试覆盖到。
    """
    stuck = wait_for_threads(win)
    app = QApplication.instance()
    if app is None:
        return stuck
    # 对话框自己起的 worker 挂在对话框（而非主窗口）上，这里一并兜住：
    # 用户可能把对话框开着就点了主窗口的关闭。
    win_finder = getattr(win, "findChildren", None)
    seen = {id(t) for t in win_finder(QThread)} if callable(win_finder) else set()
    for w in app.findChildren(QDialog):
        try:
            children = w.findChildren(QThread)
        except RuntimeError:
            continue
        for th in children:
            if id(th) in seen:
                continue
            seen.add(id(th))
            try:
                if th.isRunning():
                    if hasattr(th, "stop"):
                        th.stop()
                    if not th.wait(10000):
                        stuck.append(type(th).__name__)
            except RuntimeError:
                continue
    return stuck


def _terminate_stuck_threads(win) -> None:
    """closeEvent 最后的兜底：对等待超时后仍在运行的线程 terminate。

    QThread 存活时进程退出会触发 Qt qFatal（0xC0000409 直接崩掉整个退出
    流程）；而 Fn/Compile/PdfRender/Booklet 的 run() 都是单次长调用，没有
    可检查 requestInterruption 的循环，stop() 只能表达中断意图。走到这里
    说明用户已等满 10 秒并被明确告知"程序将退出"，terminate 可能留下半截
    输出文件——这个代价可以接受，换回的是退出流程本身不再崩溃。
    任何异常都必须咽掉：这条路径绝不能拦住 event.accept()。
    """
    targets: list = []
    finder = getattr(win, "findChildren", None)
    if callable(finder):
        try:
            targets.extend(finder(QThread))
        except (RuntimeError, TypeError):
            pass
    app = QApplication.instance()
    if app is not None:
        # 对话框自己起的 worker 挂在对话框上（_wait_all_threads 同款范围），
        # 退出兜底必须覆盖同一批线程，否则对话框线程仍会触发 qFatal。
        try:
            dialogs = list(app.findChildren(QDialog))
        except (RuntimeError, TypeError):
            dialogs = []
        for w in dialogs:
            try:
                targets.extend(w.findChildren(QThread))
            except RuntimeError:
                continue
    seen: set[int] = set()
    for th in targets:
        if id(th) in seen:
            continue
        seen.add(id(th))
        try:
            if th.isRunning():
                th.terminate()
                th.wait(1500)
        except RuntimeError:
            continue
        except Exception:
            pass  # 退出路径上任何意外都不许抛出


def _run_bg(fn, *, on_ok=None, on_error=None, on_progress=None,
            want_progress=False, parent=None):
    """把耗时调用丢进后台线程执行，结果经信号回到主线程；返回 worker。

    **调用方必须持有返回的 worker 引用**（`self._xxx_worker = _run_bg(...)`）：
    Python 侧一旦被回收，QThread 对象可能在线程还没跑完时就被销毁。

    为什么统一走这里：备份/恢复、全库打包（`exporter.build`）、数据库维护
    （VACUUM + 全库重分词）实测都是秒级到十几秒。老实现全部在主线程同步
    调用，期间窗口**完全不响应** —— 用户以为死机，于是强杀进程，而备份或
    迁移正跑到一半，那才是真正危险的时刻。放进后台后主线程继续跑事件循环，
    状态栏可以持续显示"正在做什么"。

    需要"失败原因按原文分流"的路径（备份/恢复）请用 `_guarded` 包住 fn，
    让异常变成 `(None, 错误文本)` 由 `on_ok` 处理 —— worker 的 `failed`
    通道会给用户文案（经 errmsg 翻译），原文只进日志。
    """
    from PySide6.QtCore import QObject

    from .workers import FnWorker

    # 轻量替身（测试里的 _Win）不是 QObject，不能充当 parent；此时退化为
    # 无父线程，由调用方持有引用保证生命周期。
    kwargs = {"parent": parent} if isinstance(parent, QObject) else {}
    worker = FnWorker(fn, want_progress=want_progress, **kwargs)
    if on_ok is not None:
        worker.ok.connect(on_ok)
    if on_error is not None:
        worker.failed.connect(on_error)
    if on_progress is not None:
        worker.progress.connect(on_progress)
    worker.start()
    return worker


def _status_msg(win, text: str, ms: int = 0) -> None:
    """往状态栏发一条消息；没有状态栏的轻量替身静默跳过。

    后台任务把"正在做什么"写在状态栏：这是长任务期间用户唯一的进度来源，
    但**不能因为状态栏缺失就让操作本身失败**，故异常一律咽掉。
    """
    status = getattr(win, "status", None)
    if status is None:
        return
    try:
        status.showMessage(text, ms)
    except Exception:
        pass


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{__version__}（单机离线版）")
        _fit_to_screen(self, 1360, 820)
        from . import icons
        self.setWindowIcon(icons.icon("new_doc"))
        self._tts_worker = None
        self._external_import_worker = None
        self._build_ui()
        self._build_menu()
        self._wire()
        self._setup_backup_timer()
        self._update_status()
        self.refresh_reminders()
        # 数据库自检**不在这里**启动：它要开后台线程，而本方法在构造期执行，
        # 会与"构造完就高频操作界面"的调用方（如遍历触发全部动作的测试）竞争 ——
        # CI 上实测为 `Windows fatal exception: access violation`（后台线程在
        # dbhealth.run_scheduled_check 里访问数据库，主线程同时在跑事件循环）。
        # 改由 showEvent 触发：语义上也更合理（窗口真的显示出来再做后台检查）。
        #
        # 字体检同理（N7）：老实现是在构造期同步弹**模态** QMessageBox，
        # 用户第一次启动时窗口还没画出来就被一个技术性警告拦住；且带"只弹一次"
        # 开关，恰恰只在最需要好印象的首启出现。现改为状态栏常驻角标，
        # 在 showEvent 里刷新（见 _refresh_font_notice）。

    def _setup_backup_timer(self):
        """定时备份：间隔小时数存于 settings（0=关闭），复用现有轮转策略。

        可被反复调用（设置改动后重设）：先停掉旧定时器再按新值启动，
        避免重复 start 造成多个定时器叠加。
        """
        timer = getattr(self, "_backup_timer", None)
        if timer is None:
            self._backup_timer = QTimer(self)
            self._backup_timer.timeout.connect(self._scheduled_backup)
            timer = self._backup_timer
        timer.stop()
        try:
            hours = float(dao.get_setting("backup_interval_hours", "0") or 0)
        except ValueError:
            hours = 0.0
        if hours > 0:
            timer.start(int(hours * 3600 * 1000))

    def _scheduled_backup(self):
        """定时备份走自动档：附件预算小，不卡界面；有附件没随包时在状态栏留痕。

        打包整库 + 复制附件是秒级操作，且**触发时机由定时器决定**：用户
        此刻可能正在编辑器里打字，不能因为"备份到点了"就让输入卡住。
        故放后台线程，结果只走状态栏（不用模态框打断他正在做的事）。
        """
        worker = getattr(self, "_scheduled_backup_worker", None)
        if worker is not None and worker.isRunning():
            return          # 上一轮还没跑完：跳过本轮，不排队堆积
        self._scheduled_backup_worker = _run_bg(
            lambda: self._guarded(
                lambda: create_backup_detailed(note="定时备份", mode=MODE_AUTO)),
            on_ok=self._scheduled_backup_done, parent=self)

    def _scheduled_backup_done(self, result):
        rep, err = result
        if rep is None:
            # 自动档失败绝不弹模态框：用户没主动操作，弹窗只会打断他。
            # 但必须留痕（状态栏 + 日志）——"定时备份从来没成功过"这种事，
            # 用户往往在真需要恢复时才发现。
            log.warning("定时备份失败：%s", err)
            self.status.showMessage(f"定时备份未完成：{err}（详见运行日志）", 15000)
            return
        if rep.excluded:
            self.status.showMessage(
                f"已完成定时备份（{len(rep.excluded)} 个附件超出上限未随包，"
                f"详见备份包内清单）", 15000)
        else:
            self.status.showMessage("已完成定时备份", 5000)

    # ------------------------------------------------ UI
    def _build_ui(self):
        self.library = LibraryPanel()
        self.editor = EditorPanel()
        self.reference = ReferencePanel(lambda: self.editor.editor.toPlainText())

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.library)
        split.addWidget(self.editor)
        split.addWidget(self.reference)
        split.setSizes([300, 640, 380])
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        for i in range(3):
            split.setCollapsible(i, False)
        self.setCentralWidget(split)

        tb = self.addToolBar("主工具栏")
        tb.setMovable(False)
        tb.setIconSize(QSize(24, 24))
        from . import icons
        act_new = QAction("新建公文", self)
        act_new.setShortcut("Ctrl+Shift+N")
        act_new.triggered.connect(self.new_skeleton_doc)
        act_import = QAction("导入材料", self)
        act_import.triggered.connect(self.import_materials)
        act_compile = QAction("一键汇编", self)
        act_compile.setShortcut("Ctrl+N")
        act_compile.triggered.connect(self.open_compile_wizard)
        act_tpl = QAction("模板管理", self)
        act_tpl.triggered.connect(self.open_template_editor)
        act_check = QAction("文字纠错", self)
        act_check.setShortcut("F7")
        act_check.triggered.connect(lambda: self.reference.run_check())
        act_inspect = QAction("格式体检", self)
        act_inspect.triggered.connect(self.open_inspector)
        act_tts = QAction("朗读/停止", self)
        act_tts.setShortcut("F9")
        act_tts.triggered.connect(self.toggle_tts)
        act_clip = QAction("剪贴板入库", self)
        act_clip.setShortcut("Ctrl+Shift+B")
        act_clip.triggered.connect(self.import_clipboard)
        act_fmt = QAction("排版微调", self)
        act_fmt.triggered.connect(lambda: self.editor.run_formatter())
        act_compare = QAction("文档对比", self)
        act_compare.triggered.connect(self.open_compare)
        act_dict = QAction("词典与词库", self)
        act_dict.triggered.connect(self.open_dict_manager)
        act_backup = QAction("备份/恢复", self)
        act_backup.triggered.connect(self.backup_restore)
        act_registry = QAction("发文登记", self)
        act_registry.setShortcut("Ctrl+R")
        act_registry.setToolTip("发文登记台账：登记、查询、统计、导出")
        act_registry.triggered.connect(self.open_registry)
        act_receive = QAction("收文登记", self)
        act_receive.setShortcut("Ctrl+Shift+R")
        act_receive.setToolTip("收文登记台账：来文签收、拟办、批示、承办、办结与归档")
        act_receive.triggered.connect(self.open_receive)
        act_anydoc = QAction("任意文档纠错", self)
        act_anydoc.setShortcut("Ctrl+Shift+F7")
        act_anydoc.setToolTip("任意文档纠错：任意格式文档或粘贴文本，标记视图逐处修正，保结构导出")
        act_anydoc.triggered.connect(self.open_anydoc_correct)
        # tooltip 是离线内网里唯一的"这是什么"来源（没有在线文档、没有客服）：
        # 用户只能靠悬停判断该点哪个按钮。18 个动作此前只有 3 个有提示。
        for a, ic, tip in (
                (act_new, "new_doc", "新建公文：按文种骨架（通知/请示/报告…）起稿"),
                (act_import, "import", "导入材料：Word/PDF/图片扫描件等，可批量"),
                (act_compile, "compile", "一键汇编：勾选材料合并成一份正式公文（Ctrl+N）"),
                (act_tpl, "template", "模板管理：自定义字体、字号、页边距与版式"),
                ("SEP", "", ""),
                (act_check, "check", "文字纠错：查错别字、标点、数字用法（F7）"),
                (act_anydoc, "anydoc",
                 "任意文档纠错：任意格式或粘贴文本，标记视图逐处修正（Ctrl+Shift+F7）"),
                (act_inspect, "inspect", "格式体检：按 GB/T 9704 检查版式与要素（F8）"),
                ("SEP", "", ""),
                (act_tts, "tts", "朗读/停止：逐句朗读便于听校（F9）"),
                (act_clip, "clipboard", "剪贴板入库：把刚复制的内容直接存进资料库"),
                (act_fmt, "cleanup", "排版微调：清理多余空格、空行与全半角混排"),
                ("SEP", "", ""),
                (act_compare, "compare", "文档对比：查看两篇材料的差异"),
                (act_dict, "book", "词典与词库：维护自定义词、行业术语与保护词"),
                (act_registry, "registry",
                 "发文登记台账：登记、查询、统计、导出（Ctrl+R）"),
                (act_receive, "registry",
                 "收文登记台账：来文签收、拟办、批示、承办、办结与归档（Ctrl+Shift+R）"),
                ("SEP", "", ""),
                (act_backup, "backup", "备份/恢复：整库打包或从备份包还原")):
            if a == "SEP":
                tb.addSeparator()
                continue
            ic_obj = icons.icon(ic)
            if not ic_obj.isNull():
                a.setIcon(ic_obj)
            if tip:
                a.setToolTip(tip)
                a.setStatusTip(tip)
            tb.addAction(a)
        tb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        # 常驻信息（不会被瞬态 showMessage 覆盖）
        self._perm_label = QLabel()
        self.status.addPermanentWidget(self._perm_label)
        # 督办角标：启动时算一次、点开才拉清单，不做后台轮询
        # （理由见 core/reminder 模块说明：常驻定时器伤续航且对"天"级业务无意义）
        self._reminder_btn = QPushButton()
        self._reminder_btn.setFlat(True)
        self._reminder_btn.setCursor(Qt.PointingHandCursor)
        self._reminder_btn.setToolTip("点击查看待办收文清单")
        self._reminder_btn.clicked.connect(self.open_reminders)
        self._reminder_btn.hide()
        self.status.addPermanentWidget(self._reminder_btn)
        # 缺字体角标（N7）：公文字体缺失会让 Word/WPS 静默替换字体，版心
        # （22 行×28 字、固定行距 28 磅）实际不成立 —— 属"成品不合规"级别的事，
        # 必须让用户看得见；但**不能**用模态框在启动时拦住他。常驻状态栏 + 可点开详情。
        self._font_btn = QPushButton()
        self._font_btn.setFlat(True)
        self._font_btn.setCursor(Qt.PointingHandCursor)
        self._font_btn.setToolTip("本机缺少公文标准字体：点击查看影响与解决办法")
        self._font_btn.clicked.connect(self.show_font_notice)
        self._font_btn.hide()
        self.status.addPermanentWidget(self._font_btn)

    def _menu_action(self, menu, text: str, slot, tip: str, shortcut=None):
        """菜单项 + 悬停说明（tooltip 与状态栏提示各设一份）。

        为什么要专门给菜单补提示：目标用户是文书岗人员，且运行在**离线内网**
        —— 没有在线文档、没有客服，鼠标悬停是唯一能问"这是干什么的"的地方。
        此前全库 32 个菜单项**一个提示都没有**，只有名称。
        """
        act = (menu.addAction(text, slot, shortcut) if shortcut
               else menu.addAction(text, slot))
        if tip:
            act.setToolTip(tip)
            act.setStatusTip(tip)
        return act

    def _build_menu(self):
        m_file = self.menuBar().addMenu("文件(&F)")
        m_file.menuAction().setStatusTip("新建、导入、保存与退出")
        self._menu_action(m_file, "新建公文（文种骨架）…", self.new_skeleton_doc,
                          "按文种（通知/请示/报告…）套用规范骨架起稿",
                          "Ctrl+Shift+N")
        self._menu_action(m_file, "导入材料…", self.import_materials,
                          "把 Word/PDF/图片扫描件导入资料库（可批量、扫描件自动 OCR）",
                          "Ctrl+O")
        self._menu_action(m_file, "剪贴板入库", self.import_clipboard,
                          "把刚复制的内容直接存进资料库，不必先存成文件",
                          "Ctrl+Shift+B")
        self._menu_action(m_file, "一键汇编…", self.open_compile_wizard,
                          "勾选材料合并成一份正式公文（含封面、目录与页码）", "Ctrl+N")
        self._menu_action(m_file, "保存到资料库",
                          lambda: self.editor.save_to_db(),
                          "把编辑器里的当前内容存成一篇材料", "Ctrl+S")
        self._menu_action(m_file, "发文登记台账…", self.open_registry,
                          "本单位发文的登记、查询、统计与导出（含发文字号）", "Ctrl+R")
        self._menu_action(m_file, "收文登记台账…", self.open_receive,
                          "来文签收、拟办、批示、承办、办结与归档（含办理时限催办）",
                          "Ctrl+Shift+R")
        m_file.addSeparator()
        self._menu_action(m_file, "退出", self.close,
                          "关闭程序（有任务在跑时会先等它收工）", "Ctrl+Q")

        m_tool = self.menuBar().addMenu("工具(&T)")
        m_tool.menuAction().setStatusTip("纠错、体检、朗读、批量处理与备份")
        self._menu_action(m_tool, "文字纠错", lambda: self.reference.run_check(),
                          "对当前文档做文字纠错（错别字、标点、数字用法）", "F7")
        self._menu_action(m_tool, "任意文档纠错…", self.open_anydoc_correct,
                          "任意格式文档或粘贴文本，标记视图逐处修正后保结构导出",
                          "Ctrl+Shift+F7")
        self._menu_action(m_tool, "公文格式体检…", self.open_inspector,
                          "按 GB/T 9704 检查版式与要素（标题、字号、行距、页码…）",
                          "F8")
        self._menu_action(m_tool, "朗读校对 开/停", self.toggle_tts,
                          "逐句朗读当前文档便于听校，再点一次停止", "F9")
        self._menu_action(m_tool, "一键排版微调", lambda: self.editor.run_formatter(),
                          "清理多余空格、空行、全半角混排并统一标点")
        m_tool.addSeparator()
        self._menu_action(m_tool, "跨文档批量查找替换…", self.open_bulk_replace,
                          "在多篇材料里统一替换（支持正则与逐条确认）")
        self._menu_action(m_tool, "按分类批量纠错…", self.open_batch_correct,
                          "对某个分类下的全部材料批量纠错，改前自动留快照")
        self._menu_action(m_tool, "相似文档查重…", self.open_similarity,
                          "找出内容高度重复的材料，避免重复归档")
        self._menu_action(m_tool, "文档对比…", self.open_compare,
                          "逐行对比两篇材料的差异")
        m_tool.addSeparator()
        self._menu_action(m_tool, "历史版本（快照）…", self.open_snapshots,
                          "查看并回退到某次修改前的版本（批量纠错/替换前会自动留）")
        self._menu_action(m_tool, "词典与词库管理…", self.open_dict_manager,
                          "维护自定义词、行业术语、保护词与纠错对")
        self._menu_action(m_tool, "备份…", self._do_backup,
                          "把整库（含附件）打包成备份包，用于灾难恢复")
        self._menu_action(m_tool, "恢复…", self._do_restore,
                          "从备份包还原数据（恢复前会自动再备份一次）")
        self._menu_action(m_tool, "批量导出与移交包…", self.export_handover,
                          "按范围导出资料与清单，用于交接或专题归档")
        self._menu_action(m_tool, "校验移交包…", self.verify_handover,
                          "逐条核对包内文件与 manifest 的 sha256，交付前先验一遍")
        m_tool.addSeparator()
        self._menu_action(m_tool, "回收站…", self.open_recycle_bin,
                          "查看、恢复或彻底删除已移除的材料")

        m_tpl = self.menuBar().addMenu("模板(&P)")
        m_tpl.menuAction().setStatusTip("公文模板与版式")
        self._menu_action(m_tpl, "模板管理…", self.open_template_editor,
                          "自定义字体、字号、行距、页边距与封面要素")
        self._menu_action(m_tpl, "一键汇编…", self.open_compile_wizard,
                          "用当前模板把选中的材料合并成正式公文")

        m_set = self.menuBar().addMenu("设置(&S)")
        m_set.menuAction().setStatusTip("安全设置与数据库维护")
        self._menu_action(m_set, "系统与安全…", self.open_security,
                          "口令锁、附件体积上限、精度增强包与索引维护")
        self._menu_action(m_set, "数据库维护…", self.open_db_maintenance,
                          "碎片整理、重建索引与交叉一致性自检（需约 2 倍库体积空间）")

        m_help = self.menuBar().addMenu("帮助(&H)")
        m_help.menuAction().setStatusTip("诊断包与版本信息")
        self._menu_action(m_help, "生成诊断包…", self.export_diagpack,
                          "导出环境信息与运行日志（不含任何公文正文），供反馈问题")
        m_help.addSeparator()
        self._menu_action(m_help, "关于", lambda: info(
            self, f"{APP_NAME} v{__version__}\n\n单机离线版智能公文汇编与写作辅助工具\n"
                  f"数据目录：{db_path().parent}\n全程无网络请求。"),
            "版本号与数据目录位置")

    # ------------------------------------------------ 信号接线
    def _wire(self):
        self.library.open_document.connect(self.editor.load_document)
        self.library.import_requested.connect(self.import_materials)
        self.reference.insert_text.connect(self._insert_at_cursor)
        self.reference.apply_edit.connect(self._apply_edit)
        self.reference.corrections_ready.connect(self.editor.set_corrections)
        self.editor.content_modified.connect(self._on_editor_saved)
        sc_f3 = QShortcut(QKeySequence("F3"), self)
        sc_f3.activated.connect(self.library.focus_search)

    def _on_editor_saved(self):
        """编辑器内容落库（另存/更新）后联动刷新资料库列表。"""
        self.library.reload()
        self._update_status()

    def _insert_at_cursor(self, text: str):
        cur = self.editor.editor.textCursor()
        cur.insertText(text)
        self.editor.editor.setTextCursor(cur)

    def _apply_edit(self, start: int, end: int, replacement: str):
        """纠错定位替换：光标区间操作，保留撤销栈（Ctrl+Z 可反悔单次替换）。"""
        ed = self.editor.editor
        cur = ed.textCursor()
        cur.setPosition(start)
        cur.setPosition(end, QTextCursor.KeepAnchor)
        cur.insertText(replacement)

    # ------------------------------------------------ 功能入口
    def open_registry(self):
        """发文登记台账：登记、查询、统计、导出。"""
        dlg = RegistryDialog(self)
        dlg.exec()

    def open_receive(self):
        """收文登记台账：来文签收、拟办、批示、承办、办结与归档。"""
        dlg = ReceiveDialog(self)
        dlg.exec()
        self.refresh_reminders()      # 从台账回来可能已办结若干件

    def refresh_reminders(self):
        """刷新状态栏督办角标。无需关注时整个按钮隐藏，不占地方。"""
        from ..core import reminder
        try:
            items = reminder.pending()
            text = reminder.status_text(items)
        except Exception:
            # 提醒坏掉绝不能影响主流程
            self._reminder_btn.hide()
            return
        if not text:
            self._reminder_btn.hide()
            return
        self._reminder_btn.setText(f"督办：{text}")
        self._reminder_btn.show()

    def open_reminders(self):
        """点开督办角标：列出待办收文，可一键跳到收文台账。"""
        from ..core import reminder
        items = reminder.pending()
        if not items:
            info(self, "当前没有需要督办的收文。")
            self.refresh_reminders()
            return
        body = "\n".join(reminder.detail_lines(items))
        if ask(self, f"以下收文需要办理：\n\n{body}\n\n是否打开收文台账处理？"):
            self.open_receive()

    # ------------------------------------------------ 数据库健康
    def _maybe_db_health_check(self):
        """低频数据库自检（正常时**完全静默**）。

        放后台线程：quick_check 要扫全部页，大库上会明显拖慢启动。
        仅当距上次自检超过阈值才跑（见 core/dbhealth.CHECK_INTERVAL_DAYS）。
        """
        from ..core import dbhealth
        try:
            if not dbhealth.should_check():
                return
        except Exception:
            return
        from .workers import FnWorker
        worker = FnWorker(dbhealth.run_scheduled_check, parent=self)
        worker.ok.connect(self._on_db_check_done)
        self._db_check_worker = worker
        worker.start()

    def export_handover(self):
        """批量导出与移交包：按范围导出资料与清单，用于交接、专题归档。

        与「备份」的区别：备份是整库全量（灾难恢复用），本功能按范围导出，
        适合把某个分类的资料交给同事——整库交出去既过度又容易带出无关内容。
        """
        from ..core import exporter
        from PySide6.QtWidgets import QInputDialog

        cats = list(dao.list_categories())
        options = ["全部资料"] + [c.name for c in cats]
        choice, ok = QInputDialog.getItem(
            self, "批量导出与移交包", "导出范围（按分类）：", options, 0, False)
        if not ok:
            return
        category_id = None
        if choice != "全部资料":
            for c in cats:
                if c.name == choice:
                    category_id = c.id
                    break
        include_att = ask(
            self, "是否一并打包附件？\n\n"
                  "附件按体积上限纳入；超出的会写进包内清单并注明原因，"
                  "不会静默丢弃。")
        from PySide6.QtWidgets import QFileDialog
        default = str(export_dir() / exporter.default_filename())
        path, _sel = QFileDialog.getSaveFileName(
            self, "保存移交包", default, "ZIP 压缩包 (*.zip)")
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"

        # 全库打包（读全部文档 + 复制附件 + 逐个算 sha256）实测 1.5–2.1s@3000 篇：
        # 放后台线程，期间状态栏报进度，窗口仍可交互。
        _status_msg(self, "正在打包移交包…")
        self._handover_worker = _run_bg(
            lambda: self._guarded(lambda: exporter.build(exporter.ExportRequest(
                out_path=path, category_id=category_id,
                include_attachments=include_att,
                note=f"由 {choice} 导出"))),
            on_ok=lambda result: self._handover_done(path, result),
            parent=self)

    def _handover_done(self, path: str, result):
        rep, err = result
        if rep is None:
            log.warning("移交包导出失败：%s", err)
            _status_msg(self, "导出未完成", 8000)
            warn(self, f"导出失败：{err}")
            return
        _status_msg(self, "移交包已生成", 5000)
        tail = (f"，其中 {rep['excluded']} 个附件因超限未纳入（清单已注明原因）"
                if rep["excluded"] else "")
        info(self, f"移交包已生成：\n{path}\n\n"
                   f"文档 {rep['documents']} 篇、附件 {rep['attachments']} 个"
                   f"{tail}。\n"
                   f"包内 manifest.json 含每份文档的原文件名与 sha256 校验值。")

    def verify_handover(self):
        """校验移交包（D6）：交付前自己验一遍，别等对方用到时才发现包坏了。

        移交是责任转移 —— 包交出去后原件常被清理，传输环节（U 盘坏块、网盘
        截断）造成的问题必须在**交付前**暴露。
        """
        from ..core import exporter

        path, _sel = QFileDialog.getOpenFileName(
            self, "选择要校验的移交包", str(export_dir()),
            "ZIP 压缩包 (*.zip)")
        if not path:
            return
        _status_msg(self, "正在校验移交包…")
        self._verify_worker = _run_bg(
            lambda: self._guarded(lambda: exporter.verify_package(path)),
            on_ok=lambda result: self._verify_done(path, result), parent=self)

    def _verify_done(self, path: str, result):
        rep, err = result
        if rep is None:
            log.warning("移交包校验失败：%s", err)
            _status_msg(self, "校验未完成", 8000)
            warn(self, f"校验失败：{err}")
            return
        if rep["ok"]:
            _status_msg(self, "移交包校验通过", 5000)
            info(self, f"移交包校验通过：\n{path}\n\n"
                       f"文档 {rep['documents']} 篇、附件 {rep['attachments']} 个，"
                       "逐条 sha256 与包内 manifest 记录一致。")
            return
        log.warning("移交包校验未通过：%s", rep["problems"])
        _status_msg(self, "移交包校验未通过", 10000)
        shown = rep["problems"][:20]
        warn(self, "移交包校验未通过：\n\n· " + "\n· ".join(shown)
                   + (f"\n\n（共 {len(rep['problems'])} 项问题）"
                      if len(rep["problems"]) > len(shown) else "")
                   + "\n\n请不要把它当作正式移交件使用，建议重新生成一份。")

    def _on_db_check_done(self, problem):
        """自检回调：只有发现问题才提示。"""
        if problem:
            warn(self, str(problem))

    def export_diagpack(self):
        """生成诊断包：环境信息 + 能力探测 + 表计数 + 运行日志。

        **绝不含公文正文**。生成前把内容清单（含"不包含什么"）逐项摆给用户，
        让隐私承诺变成用户可核查的事实，而不是一句口头保证。
        """
        from ..core import diagpack

        body = "\n".join(f"· {ln}" if ln else "" for ln in diagpack.preview_lines())
        if not ask(self, f"将生成诊断包，包含以下内容：\n\n{body}\n\n是否继续？"):
            return
        from PySide6.QtWidgets import QFileDialog
        default = str(export_dir() / diagpack.default_filename())
        path, _sel = QFileDialog.getSaveFileName(
            self, "保存诊断包", default, "ZIP 压缩包 (*.zip)")
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        try:
            diagpack.build(path)
        except OSError as exc:
            warn(self, f"生成失败：{exc}")
            return
        info(self, f"诊断包已生成：\n{path}\n\n"
                   f"可直接发给技术支持。内含环境信息与运行日志，"
                   f"不含任何公文正文。")

    def open_db_maintenance(self):
        """数据库维护：自检 + 碎片整理 + 重建检索索引。"""
        from ..core import dbhealth

        ok, detail = dbhealth.quick_check()
        status = "正常" if ok else f"发现问题 —— {detail}"
        size = dbhealth.human_size(dbhealth.db_file_size())
        if not ask(self, f"数据库自检：{status}\n当前占用：{size}\n\n"
                         f"维护将执行：碎片整理（VACUUM）、索引重建（REINDEX）"
                         f"与全文检索索引重建。\n"
                         f"过程中需要约 2 倍库体积的临时磁盘空间，"
                         f"请勿中途关闭程序。\n\n是否开始？"):
            return
        # VACUUM + 全库重新分词实测 2.4–3.4s@3000 篇，且不可中断：必须放后台，
        # 否则维护期间窗口完全无响应，用户以为死机就去强杀进程 —— 而 VACUUM
        # 正写到一半，那是真正可能损坏库的时刻。
        _status_msg(self, "正在维护数据库（VACUUM + 重建索引）…")
        self._maintenance_worker = _run_bg(
            lambda: self._guarded(dbhealth.maintenance),
            on_ok=self._maintenance_done, parent=self)

    def _maintenance_done(self, result):
        from ..core import dbhealth

        rep, err = result
        if rep is None:
            log.warning("数据库维护失败：%s", err)
            _status_msg(self, "数据库维护未完成", 8000)
            warn(self, f"维护失败：{err}")
            return
        if not rep.get("ok"):
            _status_msg(self, "数据库维护未完成", 8000)
            warn(self, rep.get("reason", "维护失败"))
            return
        _status_msg(self, "数据库维护完成", 5000)
        fts = rep.get("fts_rows", -1)
        text = (f"维护完成。\n\n"
                f"占用：{dbhealth.human_size(rep['before'])} → "
                f"{dbhealth.human_size(rep['after'])}\n"
                f"回收：{dbhealth.human_size(rep['saved'])}\n"
                f"检索索引：{'未重建' if fts < 0 else f'{fts} 条'}")
        # 维护后的交叉一致性结果：有问题必须点名，不能让用户以为
        # "点了维护 = 一切正常"。空白表示三项检查全部通过。
        problems = rep.get("consistency_problems") or []
        if problems:
            text += "\n\n⚠ 一致性检查发现以下问题：\n  · " + "\n  · ".join(problems)
        else:
            text += "\n\n一致性检查：正常。"
        info(self, text)

    def new_skeleton_doc(self):
        dlg = SkeletonDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        from ..db import dao
        text = dlg.draft_text
        title = dlg.draft_title
        did = dao.add_document(dao.Document(title=title, content_text=text))
        self.library.reload()
        self._update_status()
        if did > 0:
            self.editor.load_document(did)
            self.editor.tabs.setCurrentIndex(0)
            self.editor._update_status(f"已新建公文：{title}")
        else:
            info(self, "相同内容此前已入库（重复），未重复创建。")

    def import_materials(self, category_id: int = 0):
        dlg = ImportDialog(category_id, self)
        dlg.exec()
        self.library.reload()
        self._update_status()

    def import_external_file(self, path: str):
        """外部入口（右键菜单 --import）：导入单个文件并打开。

        parse_any 对扫描版 PDF 会整本 OCR，是**分钟级**操作；老实现直接同步
        调用，用户双击一个 .pdf 关联到本程序后主窗口会整个冻结、毫无提示。
        改走后台线程（workers.FnWorker），状态栏给出页级进度。
        """
        from .workers import FnWorker

        worker = getattr(self, "_external_import_worker", None)
        if worker is not None and worker.isRunning():
            info(self, "上一次导入仍在进行，请稍候。")
            return
        from ..core.importer import parse_any

        self.status.showMessage(f"正在解析 {path}（扫描版 PDF 可能耗时较久）…")

        def ocr_progress(page, n_pages, _path=path):
            self.status.showMessage(f"正在识别 {_path}（OCR 第{page}/{n_pages}页）…")

        self._external_import_worker = FnWorker(
            parse_any, path, ocr_progress_cb=ocr_progress, parent=self)
        self._external_import_worker.ok.connect(
            lambda r: self._external_import_done(path, r))
        self._external_import_worker.failed.connect(self._external_import_failed)
        self._external_import_worker.start()

    def _external_import_failed(self, msg: str):
        self.status.showMessage("导入失败", 8000)
        warn(self, f"导入失败：{msg}")

    def _external_import_done(self, path: str, r):
        from pathlib import Path

        from ..db import dao
        if not r.ok or not r.tree:
            self.status.showMessage("导入失败", 8000)
            warn(self, f"导入失败：{r.error}")
            return
        did = dao.add_document(dao.Document(
            title=r.tree.title, content_text=r.tree.plain_text(),
            blocks_json=r.tree.to_json(), file_path=path,
            file_type=Path(path).suffix.lstrip(".")))
        self.library.reload()
        if did > 0:
            self.editor.load_document(did)
            self.status.showMessage(f"已导入：{r.tree.title}", 8000)
        else:
            info(self, "该文件此前已导入（内容重复），已在列表中。")
            self.status.showMessage("该文件此前已导入（内容重复）", 8000)
        self._update_status()

    def import_clipboard(self):
        """剪贴板文字一键入库。"""
        text = QApplication.clipboard().text().strip()
        if not text:
            info(self, "剪贴板为空。")
            return
        from ..db import dao
        title = text.splitlines()[0][:50] if text.splitlines() else "剪贴板内容"
        did = dao.add_document(dao.Document(title=title, content_text=text))
        self.library.reload()
        if did > 0:
            info(self, f"已入库：{title}")
        else:
            info(self, "剪贴板内容此前已入库（重复）。")
        self._update_status()

    def open_compile_wizard(self):
        dlg = CompileWizard(self)
        dlg.exec()
        self._update_status()

    def open_template_editor(self):
        dlg = TemplateEditor(self)
        dlg.exec()

    def open_compare(self):
        from .compare_dialog import CompareDialog
        dlg = CompareDialog(self)
        dlg.exec()

    def open_anydoc_correct(self):
        from .correct_dialog import AnyDocCorrectDialog
        dlg = AnyDocCorrectDialog(self)
        dlg.exec()

    def open_dict_manager(self):
        dlg = DictManager(self)
        dlg.exec()

    def open_inspector(self):
        dlg = InspectorDialog(lambda: self.editor.editor.toPlainText(), self)
        dlg.exec()

    def open_bulk_replace(self):
        dlg = BulkReplaceDialog(self.library.current_category(), self)
        dlg.exec()
        self.library.reload()
        self.editor.update_preview()

    def open_batch_correct(self):
        """按分类批量纠错：先预览命中，用户确认后才写回（后台线程执行）。"""
        dlg = BatchCorrectDialog(self.library.current_category(), self)
        dlg.exec()
        self.library.reload()
        self.editor.update_preview()

    def open_recycle_bin(self):
        """回收站：恢复或彻底删除已删除的材料。"""
        dlg = RecycleBinDialog(self)
        dlg.exec()
        self.library.reload()
        self._update_status()

    def open_similarity(self):
        dlg = SimilarityDialog(self)
        dlg.exec()

    def open_snapshots(self):
        dlg = SnapshotsDialog(self.editor.doc_id, self.editor.editor.toPlainText(),
                              self, apply_callback=self._restore_snapshot)
        dlg.exec()

    def _restore_snapshot(self, content: str):
        self.editor.replace_document_text(content)
        self.editor._dirty = True
        self.editor._update_status("● 已回滚到历史快照（未保存）")

    def open_security(self):
        dlg = SecurityDialog(self)
        dlg.exec()
        # 设置里可能改过「定时备份间隔」，关闭后重读并重启定时器，
        # 否则要重启程序才生效（用户会以为改了没用）。
        self._setup_backup_timer()

    # ------------------------------------------------ 朗读校对
    def toggle_tts(self):
        from .workers import TTSWorker
        if self._tts_worker is not None and self._tts_worker.isRunning():
            self._tts_worker.stop()
            self._tts_worker = None
            self.status.showMessage("朗读已停止", 5000)
            return
        text = self.editor.editor.toPlainText()
        if not text.strip():
            info(self, "当前文档为空。")
            return
        from ..core import tts as tts_core
        ok, desc = tts_core.available()
        if not ok:
            warn(self, f"朗读不可用：{desc}")
            return
        self._tts_worker = TTSWorker(text, self)
        self._tts_worker.sentence.connect(self._highlight_sentence)
        self._tts_worker.finished_ok.connect(
            lambda: self.status.showMessage("朗读完成", 5000))
        self._tts_worker.failed.connect(lambda m: warn(self, f"朗读失败：{m}"))
        self._tts_worker.start()
        self.status.showMessage(f"朗读中（引擎：{desc}）——再次点击可停止")

    def _highlight_sentence(self, idx: int, total: int, sentence: str):
        ed = self.editor.editor
        pos = ed.toPlainText().find(sentence[:20], 0)
        if pos >= 0:
            cur = ed.textCursor()
            cur.setPosition(pos)
            cur.setPosition(min(pos + len(sentence), len(ed.toPlainText())),
                            QTextCursor.KeepAnchor)
            ed.setTextCursor(cur)
        self.status.showMessage(f"朗读中 {idx + 1}/{total}（F9 停止）")

    # ------------------------------------------------ 备份恢复
    def backup_restore(self):
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setWindowTitle("备份 / 恢复")
        box.setText("请选择操作：")
        b1 = box.addButton("备份…", QMessageBox.ActionRole)
        b2 = box.addButton("加密备份…", QMessageBox.ActionRole)
        b3 = box.addButton("恢复…", QMessageBox.ActionRole)
        b4 = box.addButton("查看备份列表", QMessageBox.ActionRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b1:
            self._do_backup()
        elif clicked is b2:
            self._do_backup(encrypted=True)
        elif clicked is b3:
            self._do_restore()
        elif clicked is b4:
            lines = []
            for x in list_backups():
                if x.get("attachments_excluded"):
                    att = (f"  附件 {x.get('attachments_included', 0)} 个"
                           f"（另有 {x['attachments_excluded']} 个超出上限未随包）")
                elif x.get("attachments_included"):
                    att = f"  附件 {x['attachments_included']} 个"
                else:
                    att = ""
                lines.append(f"{x['created'] or '????-??-??'}  {x['note']}{att}\n    {x['file']}")
            info(self, "\n".join(lines) or "暂无备份。")

    @staticmethod
    def _format_backup_items(items, limit: int = 15) -> str:
        """把备份/恢复明细里的附件条目排成缩进列表（超出条数只给个总数）。"""
        from ..core.attachments import human_size
        lines = [f"    · {it.get('name') or '附件'}"
                 f"（{human_size(int(it.get('size') or 0))}）"
                 + (f"：{it['reason']}" if it.get("reason") else "")
                 for it in (items or [])[:limit]]
        rest = len(items or []) - limit
        if rest > 0:
            lines.append(f"    …其余 {rest} 个见备份包内 excluded_attachments.txt")
        return "\n".join(lines)

    @staticmethod
    def _guarded(fn):
        """跑 fn()，把任何异常变成 `(None, 错误文本)`，**绝不向外抛**；返回二元组。

        为什么不让异常直接冒到 worker 的 `failed` 通道：备份/恢复的失败文案
        要按**原文**分流（`_restore_failure_text` 按 "password"/"占用"/"不完整"
        判定是口令错、库被占用还是包损坏），而 `failed` 通道给的是经 errmsg
        翻译的用户话术 —— 翻过之后这层判定就失效了，用户会丢掉"包是好的、
        等任务结束重试即可"这个关键区别。

        注：等待光标**不在这里设置**。这些调用已移入后台线程，而
        `setOverrideCursor` 是 GUI 线程专属操作，跨线程调用未定义；进度
        改由状态栏承载（这也正是"操作期间窗口仍可交互"的前提）。
        """
        try:
            return fn(), ""
        except Exception as exc:
            return None, str(exc)

    def _do_backup(self, encrypted: bool = False):
        pw = ""
        if encrypted:
            from PySide6.QtWidgets import QInputDialog, QLineEdit
            pw, ok = QInputDialog.getText(
                self, "加密备份", "设置备份口令（AES）：", QLineEdit.Password)
            if not ok or len(pw) < 4:
                if ok:
                    warn(self, "口令至少 4 位。")
                return
        worker = getattr(self, "_backup_worker", None)
        if worker is not None and worker.isRunning():
            info(self, "上一次备份仍在进行，请稍候。")
            return
        # 整库打包 + 逐个附件复制实测秒级：放后台，期间窗口可交互。
        # 结果一律经 _guarded 的 (结果, 错误文本) 二元组回来，保持
        # 「失败原因按原文分流」的能力（见 _guarded 与 _restore_failure_text）。
        _status_msg(self, "正在备份（整库 + 附件）…")
        self._backup_worker = _run_bg(
            lambda: self._guarded(lambda: create_backup_detailed(
                note="手动备份", password=pw, mode=MODE_MANUAL)),
            on_ok=lambda result: self._backup_done(result, password=pw),
            parent=self)

    def _backup_done(self, result, password: str = ""):
        from ..core.attachments import human_size
        rep, err = result
        if rep is None:
            log.warning("手动备份失败：%s", err)
            _status_msg(self, "备份未完成", 8000)
            warn(self, f"备份失败：{err}")
            return
        _status_msg(self, "备份完成", 5000)
        msg = f"备份成功：\n{rep.path}" + ("（已 AES 加密）" if password else "")
        if rep.excluded:
            # 绝不静默丢附件：当场告诉用户哪些没随包、为什么、去哪儿改上限
            limit_text = "不限制" if rep.limit_bytes < 0 else human_size(rep.limit_bytes)
            warn(self, msg + f"\n\n注意：{len(rep.excluded)} 个附件未随本备份包备份"
                             f"（附件体积上限 {limit_text}，"
                             f"未随包合计 {human_size(rep.excluded_bytes)}）：\n"
                             + self._format_backup_items(rep.excluded)
                             + "\n\n这些附件的原件仍在数据目录的 attachments 子目录里，"
                               "并未被删除；恢复此备份时程序会再次提醒。\n"
                               "需要完整迁移包时，请在「设置 → 系统与安全」"
                               "调高附件体积上限（或设为不限制）后重新备份。")
        else:
            info(self, msg + f"\n\n附件 {len(rep.included)} 个已随包备份"
                             f"（{human_size(rep.included_bytes)}）。")

    @staticmethod
    def _restore_failure_text(err: str) -> str:
        """把恢复失败的**真实原因**如实讲给用户，不臆断成"口令错误/文件损坏"。

        老实现把一切失败都写成"恢复失败（口令错误或文件损坏）"：用户遇到
        "库被后台任务占用"这类**可重试**的失败时，会以为手里的备份包坏了，
        从而把一份其实完好的备份丢弃、白白损失一次灾难恢复机会。
        这里先原样透出底层异常文本，只在**能确定**原因时才补一句提示。
        """
        msg = f"恢复失败：{err}"
        low = err.lower()
        if "password" in low or "口令" in err or "not a zip" in low:
            return msg + "\n\n原因看起来是口令不正确——请确认这确实是该备份的口令。"
        if "占用" in err:
            return (msg + "\n\n库正被后台任务占用，备份包本身没有问题："
                           "等任务结束后重试即可（当前数据未被改动）。")
        if any(k in err for k in ("不完整", "业务表", "校验", "SQLite", "损坏")) \
                or "not a zip" in low or "badzip" in low:
            return msg + "\n\n这个备份文件本身不可用（内容损坏或并非本程序导出的备份）。"
        return msg

    def _do_restore(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择备份文件",
                                              str(db_path().parent / "backups"),
                                              "备份包 (*.zip)")
        if not path:
            return
        pw = ""
        if "_加密" in path:
            from PySide6.QtWidgets import QInputDialog, QLineEdit
            pw, ok = QInputDialog.getText(self, "加密备份", "输入备份口令：",
                                          QLineEdit.Password)
            if not ok:
                return
        if not ask(self, "恢复将覆盖当前全部数据（恢复前会自动再备份一次），确定继续？"):
            return
        # 恢复放后台线程执行（解包 + 自动备份 + 还原附件都是秒级，且最怕
        # "用户以为死机去强杀"——恢复写库到一半被中断比慢几秒危险得多）。
        #
        # 但必须先关掉**主线程自己**的数据库连接：restore_backup_detailed 的
        # 占用检查（dbconn.live_connection_threads）会点名列其它线程持有的
        # 连接，只排除"当前线程"（此刻是 worker）。主线程若还攥着一条连接，
        # 就会被自己挡住 —— 实测报"数据库正被后台任务占用（MainThread）"。
        # 这次的 dao 调用会自动重开连接，指向恢复后的新库。
        from ..db import connection as _dbconn

        try:
            _dbconn.close_current_thread()
        except Exception:
            pass
        _status_msg(self, "正在恢复备份…")
        self._restore_worker = _run_bg(
            lambda: self._guarded(
                lambda: restore_backup_detailed(path, password=pw)),
            on_ok=self._restore_done, parent=self)

    def _restore_done(self, result):
        rep, err = result
        if rep is None:
            log.warning("恢复备份失败：%s", err)
            _status_msg(self, "恢复未完成", 8000)
            warn(self, self._restore_failure_text(err))
            return
        _status_msg(self, "恢复完成", 8000)
        if rep.missing or rep.warnings:
            # 降级必须可见：恢复是灾难场景，"恢复成功"四个字不能让用户
            # 以为一切都好 —— 自动备份没做成、附件没还原成功，都要点名。
            parts = ["数据库已恢复，但有以下情况需要你知道："]
            if rep.warnings:
                parts += ["", "· " + "\n· ".join(rep.warnings)]
            if rep.missing:
                parts += ["", f"有 {len(rep.missing)} 个附件不在此备份包内：",
                          self._format_backup_items(rep.missing),
                          "", "怎么补回来：这些附件的原件在「备份来源电脑」的数据目录 "
                              "attachments 子目录里，把同名文件复制回本机同一目录即可：",
                          f"    {rep.attachments_dir}",
                          "（恢复不会删除本机已有的附件文件；若来源电脑已不可用，"
                          "只能从原始出处重新添加。）"]
            parts += ["", "请重启程序使数据完全生效。"]
            warn(self, "\n".join(parts))
        else:
            info(self, f"恢复成功（附件 {rep.restored_files} 个已还原），"
                       "请重启程序使数据完全生效。")

    # ------------------------------------------------ 启动检查
    def _refresh_font_notice(self):
        """公文字体缺失 -> 状态栏常驻角标（**不阻塞启动**、不再"只提示一次"）。

        老实现：在 `__init__` 里同步弹模态 `QMessageBox`，并靠 settings 里的
        `font_warning_shown` 只弹一次。两个问题：
          1. 用户第一次启动时窗口还没显示就被技术性警告拦住；
          2. 那份提示只在**最需要好印象的首启**出现，之后再也不提 —— 用户
             装没装字体、排版是否合规，界面上一概看不出来。
        现在：状态栏角标常驻（缺几种就写几种），点击展开影响与解决办法；
        另外每次生成公文时，汇编结果提示里也会带上字体说明
        （`core/fontcheck.missing_note`，见 N9）。
        """
        btn = getattr(self, "_font_btn", None)
        if btn is None:
            return
        try:
            missing = missing_official_fonts()
        except Exception:
            btn.hide()
            return
        if not missing:
            btn.hide()
            return
        btn.setText(f"⚠ 缺少公文字体 {len(missing)} 种")
        btn.setToolTip("本机未安装：" + "、".join(missing)
                       + "\n点击查看影响与解决办法")
        btn.show()

    def show_font_notice(self):
        """点开字体角标：说清后果与下一步（不给用户一个没有出路的警告）。"""
        from ..core.fontcheck import missing_note

        try:
            note = missing_note()
        except Exception:
            note = ""
        if not note:
            note = "本机公文标准字体齐全。"
        info(self, note + "\n\n安装字体后重启本程序，状态栏角标会自动消失。")

    def _update_status(self):
        n = dao.count_documents()
        pairs = dao.count_error_pairs()
        from ..paths import is_portable
        mode = "（便携模式）" if is_portable() else ""
        self._perm_label.setText(
            f"资料 {n} 篇 | 纠错库 {pairs} 条 | 完全离线运行{mode}")
        self.status.showMessage(
            f"输出目录：{export_dir()} | 数据目录：{db_path().parent}", 10000)

    # ------------------------------------------------ 关闭
    def showEvent(self, event):
        """窗口显示后才启动后台数据库自检。

        为什么不在 __init__ 里启动：那会在构造期就开后台线程，与"构造完立即
        高频操作界面"的调用方（遍历触发全部动作的测试）竞争，CI 上实测为
        `Windows fatal exception: access violation`。放到 showEvent 后，
        测试只构造不显示窗口即不会启动线程；真实使用中窗口一定会显示，
        功能不受影响。

        只启动一次：最小化后恢复、切页等都会再次触发 showEvent。
        """
        super().showEvent(event)
        if getattr(self, "_db_check_started", False):
            return
        self._db_check_started = True
        try:
            self._refresh_font_notice()
        except Exception:
            pass
        try:
            self._maybe_db_health_check()
        except Exception:
            pass

    def _wait_for_background_threads(self) -> list[str]:
        """退出前等所有后台 QThread 收工；返回仍在跑的线程名（超时未退）。

        实现放在模块级 ``_wait_all_threads(win)``：退出路径的关键逻辑不该是
        实例方法——测试里用轻量替身驱动 closeEvent 时会因为替身没有这个方法
        而直接 AttributeError，把"退出备份"这类关键行为挡在测试之外。

        为什么必须等：本程序的后台 worker 大多以 self（或子控件）为 parent，
        窗口关闭 → 解释器销毁 MainWindow → 子对象级联销毁 → 仍在运行的
        QThread 被析构，Qt 直接 qFatal 强杀进程。实测退出码
        -1073740791 (0xC0000409)，stderr 只有一句
        "QThread: Destroyed while thread is still running"。
        触发场景很日常：F7 纠错、相似查重、PDF 预览渲染还没跑完就关窗口。
        老实现只对 TTS 一个 worker 做了 stop+wait。
        """
        return _wait_all_threads(self)

    def closeEvent(self, event):
        # 未保存修改三选：保存 / 放弃 / 取消退出
        if not self.editor.confirm_discard_changes():
            event.ignore()
            return
        # 先停后台线程再备份：备份要独占数据库文件（Windows 上被占住就替换不了），
        # 而且线程若在进程退出时还活着会直接崩掉退出流程。
        stuck = _wait_all_threads(self)
        # 退出自动备份：走自动档（附件预算默认 8 MB，可在设置里改），
        # 退出路径的耗时因此不随附件增多而失控。
        # 这里刻意**同步**执行、不开后台线程：进程马上就要退出，线程若还在写 zip，
        # 解释器销毁 QThread 会直接终止进程（Qt 报 "Destroyed while thread is still
        # running"），留下半截备份包。同步 + 小预算 + core.backup 的 .part 原子改名
        # 三者合起来，才既快又不会写出损坏的包。
        if dao.get_setting("auto_backup", "1") == "1":
            try:
                create_backup_detailed(note="退出自动备份", mode=MODE_AUTO)
            except Exception as exc:
                # 不能静默：用户会以为数据已备份。落日志 + 最后再兜一次提示，
                # 但绝不拦住退出（关不掉程序比备份失败更糟）。
                try:
                    from ..core.backup import _append_log
                    _append_log([f"退出自动备份失败：{type(exc).__name__}: {exc}"])
                except Exception:
                    pass
        if stuck:
            try:
                warn(self, "以下后台任务未能在 10 秒内结束，程序将退出：\n  "
                           + "\n  ".join(stuck)
                           + "\n\n如果刚在生成 PDF/汇编，请退出后检查输出目录是否完整。")
            except Exception:
                pass
            # 用户已确认退出：对超时线程 terminate 兜底，避免 QThread 存活时
            # 进程退出触发 Qt qFatal（0xC0000409）。必须在 accept() 之前。
            _terminate_stuck_threads(self)
        if self._tts_worker is not None and self._tts_worker.isRunning():
            self._tts_worker.stop()
            self._tts_worker.wait(2000)
            if self._tts_worker.isRunning():
                # SAPI 朗读卡在 COM 调用里时 stop/wait 都唤不回，同款兜底。
                try:
                    self._tts_worker.terminate()
                    self._tts_worker.wait(1500)
                except Exception:
                    pass
        event.accept()
