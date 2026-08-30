# -*- coding: utf-8 -*-
"""主窗口：三栏布局（资料库 | 编辑器 | 纠错与参考）+ 全部功能入口。"""
from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (QApplication, QFileDialog, QLabel, QMainWindow,
                               QSplitter, QStatusBar)

from .. import APP_NAME, __version__
from ..core.backup import create_backup, list_backups, restore_backup
from ..core.template import default_template
from ..db import dao
from ..paths import db_path, export_dir
from .compile_wizard import CompileWizard
from .dict_manager import DictManager
from .editor_panel import EditorPanel
from .feature_dialogs import (BulkReplaceDialog, InspectorDialog,
                              SecurityDialog, SimilarityDialog,
                              SkeletonDialog, SnapshotsDialog)
from .import_dialog import ImportDialog
from .library_panel import LibraryPanel
from .reference_panel import ReferencePanel
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
        try:
            create_backup(note="定时备份")
            self.status.showMessage("已完成定时备份", 5000)
        except Exception:
            pass

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
        for a, ic in ((act_new, "new_doc"), (act_import, "import"),
                      (act_compile, "compile"), (act_tpl, "template"),
                      (act_check, "check"), (act_inspect, "inspect"),
                      (act_tts, "tts"), (act_clip, "clipboard"),
                      (act_fmt, "cleanup"), (act_compare, "compare"),
                      (act_dict, "book"), (act_backup, "backup")):
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
        m_file.addSeparator()
        m_file.addAction("退出", self.close, "Ctrl+Q")

        m_tool = self.menuBar().addMenu("工具(&T)")
        m_tool.addAction("文字纠错", lambda: self.reference.run_check(), "F7")
        m_tool.addAction("公文格式体检…", self.open_inspector, "F8")
        m_tool.addAction("朗读校对 开/停", self.toggle_tts, "F9")
        m_tool.addAction("一键排版微调", lambda: self.editor.run_formatter())
        m_tool.addSeparator()
        m_tool.addAction("跨文档批量查找替换…", self.open_bulk_replace)
        m_tool.addAction("相似文档查重…", self.open_similarity)
        m_tool.addAction("文档对比…", self.open_compare)
        m_tool.addSeparator()
        m_tool.addAction("历史版本（快照）…", self.open_snapshots)
        m_tool.addAction("词典与词库管理…", self.open_dict_manager)
        m_tool.addAction("备份…", self._do_backup)
        m_tool.addAction("恢复…", self._do_restore)

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
    def new_skeleton_doc(self):
        dlg = SkeletonDialog(self)
        if dlg.exec() != dlg.Accepted:
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
                            Qt.KeepAnchor)
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
            items = list_backups()
            info(self, "\n".join(f"{x['created']}  {x['note']}\n    {x['file']}"
                                 for x in items) or "暂无备份。")

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
        try:
            path = create_backup(note="手动备份", password=pw)
            info(self, f"备份成功：\n{path}" + ("（已 AES 加密）" if pw else ""))
        except Exception as exc:  # noqa: BLE001
            warn(self, f"备份失败：{exc}")

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
        if ask(self, "恢复将覆盖当前全部数据（恢复前会自动再备份一次），确定继续？"):
            try:
                restore_backup(path, password=pw)
                info(self, "恢复成功，请重启程序使数据完全生效。")
            except Exception as exc:  # noqa: BLE001
                warn(self, f"恢复失败（口令错误或文件损坏）：{exc}")

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
        # 退出自动备份
        if dao.get_setting("auto_backup", "1") == "1":
            try:
                create_backup(note="退出自动备份")
            except Exception:
                pass
        if self._tts_worker is not None and self._tts_worker.isRunning():
            self._tts_worker.stop()
            self._tts_worker.wait(2000)
        event.accept()
