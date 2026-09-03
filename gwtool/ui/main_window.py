# -*- coding: utf-8 -*-
"""主窗口：三栏布局（资料库 | 编辑器 | 纠错与参考）+ 全部功能入口。"""
from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QLabel,
                               QMainWindow, QSplitter, QStatusBar)

from .. import APP_NAME, __version__
from ..core.backup import (MODE_AUTO, MODE_MANUAL, create_backup_detailed,
                           list_backups, restore_backup_detailed)
from ..core.template import default_template
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
from .registry_dialog import RegistryDialog
from .template_editor import TemplateEditor
from .widgets import ask, info, missing_official_fonts, warn


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{__version__}（单机离线版）")
        self.resize(1360, 820)
        self._tts_worker = None
        self._build_ui()
        self._build_menu()
        self._wire()
        self._first_run_checks()
        self._setup_backup_timer()
        self._update_status()

    def _setup_backup_timer(self):
        """定时备份：间隔小时数存于 settings（0=关闭），复用现有轮转策略。"""
        self._backup_timer = QTimer(self)
        self._backup_timer.timeout.connect(self._scheduled_backup)
        try:
            hours = float(dao.get_setting("backup_interval_hours", "0") or 0)
        except ValueError:
            hours = 0.0
        if hours > 0:
            self._backup_timer.start(int(hours * 3600 * 1000))

    def _scheduled_backup(self):
        """定时备份走自动档：附件预算小，不卡界面；有附件没随包时在状态栏留痕。"""
        try:
            rep = create_backup_detailed(note="定时备份", mode=MODE_AUTO)
        except Exception:
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
        self.setCentralWidget(split)

        tb = self.addToolBar("主工具栏")
        tb.setMovable(False)
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
        for a, ic in ((act_new, "new_doc"), (act_import, "import"),
                      (act_compile, "compile"), (act_tpl, "template"),
                      (act_check, "check"), (act_inspect, "inspect"),
                      (act_tts, "tts"), (act_clip, "clipboard"),
                      (act_fmt, "cleanup"), (act_compare, "compare"),
                      (act_dict, "book"), (act_backup, "backup"),
                      (act_registry, "registry")):
            ic_obj = icons.icon(ic)
            if not ic_obj.isNull():
                a.setIcon(ic_obj)
            tb.addAction(a)
        tb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)

        self.status = QStatusBar()
        self.setStatusBar(self.status)

    def _build_menu(self):
        m_file = self.menuBar().addMenu("文件(&F)")
        m_file.addAction("新建公文（文种骨架）…", self.new_skeleton_doc, "Ctrl+Shift+N")
        m_file.addAction("导入材料…", self.import_materials, "Ctrl+O")
        m_file.addAction("剪贴板入库", self.import_clipboard, "Ctrl+Shift+B")
        m_file.addAction("一键汇编…", self.open_compile_wizard, "Ctrl+N")
        m_file.addAction("保存到资料库", lambda: self.editor.save_to_db(), "Ctrl+S")
        m_file.addAction("发文登记台账…", self.open_registry, "Ctrl+R")
        m_file.addSeparator()
        m_file.addAction("退出", self.close, "Ctrl+Q")

        m_tool = self.menuBar().addMenu("工具(&T)")
        m_tool.addAction("文字纠错", lambda: self.reference.run_check(), "F7")
        m_tool.addAction("公文格式体检…", self.open_inspector, "F8")
        m_tool.addAction("朗读校对 开/停", self.toggle_tts, "F9")
        m_tool.addAction("一键排版微调", lambda: self.editor.run_formatter())
        m_tool.addSeparator()
        m_tool.addAction("跨文档批量查找替换…", self.open_bulk_replace)
        m_tool.addAction("按分类批量纠错…", self.open_batch_correct)
        m_tool.addAction("相似文档查重…", self.open_similarity)
        m_tool.addAction("文档对比…", self.open_compare)
        m_tool.addSeparator()
        m_tool.addAction("历史版本（快照）…", self.open_snapshots)
        m_tool.addAction("词典与词库管理…", self.open_dict_manager)
        m_tool.addAction("备份…", self._do_backup)
        m_tool.addAction("恢复…", self._do_restore)
        m_tool.addSeparator()
        m_tool.addAction("回收站…", self.open_recycle_bin)

        m_tpl = self.menuBar().addMenu("模板(&P)")
        m_tpl.addAction("模板管理…", self.open_template_editor)
        m_tpl.addAction("一键汇编…", self.open_compile_wizard)

        m_set = self.menuBar().addMenu("设置(&S)")
        m_set.addAction("系统与安全…", self.open_security)

        m_help = self.menuBar().addMenu("帮助(&H)")
        m_help.addAction("关于", lambda: info(
            self, f"{APP_NAME} v{__version__}\n\n单机离线版智能公文汇编与写作辅助工具\n"
                  f"数据目录：{db_path().parent}\n全程无网络请求。"))

    # ------------------------------------------------ 信号接线
    def _wire(self):
        self.library.open_document.connect(self.editor.load_document)
        self.library.import_requested.connect(self.import_materials)
        self.reference.insert_text.connect(self._insert_at_cursor)
        self.reference.apply_edit.connect(self._apply_edit)
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
        """外部入口（右键菜单 --import）：导入单个文件并打开。"""
        from ..core.importer import parse_any
        from ..db import dao
        r = parse_any(path)
        if not r.ok or not r.tree:
            warn(self, f"导入失败：{r.error}")
            return
        did = dao.add_document(dao.Document(
            title=r.tree.title, content_text=r.tree.plain_text(),
            blocks_json=r.tree.to_json(), file_path=path,
            file_type=__import__("pathlib").Path(path).suffix.lstrip(".")))
        self.library.reload()
        if did > 0:
            self.editor.load_document(did)
        else:
            info(self, "该文件此前已导入（内容重复），已在列表中。")
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
                lines.append(f"{x['created']}  {x['note']}{att}\n    {x['file']}")
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
        """在等待光标下跑一段可能耗时几秒的备份/恢复，返回 (结果, 错误文本)。

        光标必须在弹窗**之前**复位：override cursor 是全应用生效的，
        带着沙漏弹模态框会让用户以为程序还卡着。
        """
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            return fn(), ""
        except Exception as exc:  # noqa: BLE001
            return None, str(exc)
        finally:
            QApplication.restoreOverrideCursor()

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
        from ..core.attachments import human_size
        rep, err = self._guarded(lambda: create_backup_detailed(
            note="手动备份", password=pw, mode=MODE_MANUAL))
        if rep is None:
            warn(self, f"备份失败：{err}")
            return
        msg = f"备份成功：\n{rep.path}" + ("（已 AES 加密）" if pw else "")
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
        rep, err = self._guarded(lambda: restore_backup_detailed(path, password=pw))
        if rep is None:
            warn(self, f"恢复失败（口令错误或文件损坏）：{err}")
            return
        if rep.missing:
            warn(self, f"数据库已恢复，但有 {len(rep.missing)} 个附件不在此备份包内：\n"
                       + self._format_backup_items(rep.missing)
                       + "\n\n怎么补回来：这些附件的原件在「备份来源电脑」的数据目录 "
                         "attachments 子目录里，把同名文件复制回本机同一目录即可：\n"
                         f"    {rep.attachments_dir}\n"
                         "（恢复不会删除本机已有的附件文件；若来源电脑已不可用，"
                         "只能从原始出处重新添加。）\n\n请重启程序使数据完全生效。")
        else:
            info(self, f"恢复成功（附件 {rep.restored_files} 个已还原），"
                       "请重启程序使数据完全生效。")

    # ------------------------------------------------ 启动检查
    def _first_run_checks(self):
        missing = missing_official_fonts()
        if missing:
            if dao.get_setting("font_warning_shown") != "1":
                warn(self, "未检测到以下公文字体，汇编出的 Word 仍会按名称引用，"
                           "在安装了这些字体的电脑上可正常显示：\n"
                           + "\n".join(missing))
                dao.set_setting("font_warning_shown", "1")

    def _update_status(self):
        n = dao.count_documents()
        pairs = dao.count_error_pairs()
        from ..paths import is_portable
        mode = "（便携模式）" if is_portable() else ""
        self.status.showMessage(
            f"资料 {n} 篇 | 纠错库 {pairs} 条 | 输出目录：{export_dir()} | "
            f"数据目录：{db_path().parent} {mode}| 完全离线运行")

    # ------------------------------------------------ 关闭
    def closeEvent(self, event):
        # 未保存修改三选：保存 / 放弃 / 取消退出
        if not self.editor.confirm_discard_changes():
            event.ignore()
            return
        # 退出自动备份：走自动档（附件预算默认 8 MB，可在设置里改），
        # 退出路径的耗时因此不随附件增多而失控。
        # 这里刻意**同步**执行、不开后台线程：进程马上就要退出，线程若还在写 zip，
        # 解释器销毁 QThread 会直接终止进程（Qt 报 "Destroyed while thread is still
        # running"），留下半截备份包。同步 + 小预算 + core.backup 的 .part 原子改名
        # 三者合起来，才既快又不会写出损坏的包。
        if dao.get_setting("auto_backup", "1") == "1":
            try:
                create_backup_detailed(note="退出自动备份", mode=MODE_AUTO)
            except Exception:
                pass
        if self._tts_worker is not None and self._tts_worker.isRunning():
            self._tts_worker.stop()
            self._tts_worker.wait(2000)
        event.accept()
