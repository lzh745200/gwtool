# -*- coding: utf-8 -*-
"""新增功能对话框集合：骨架向导 / 格式体检 / 批量替换 / 历史快照 /
相似查重 / 安全设置 / 锁屏。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QProgressBar, QPushButton,
                               QRadioButton, QSpinBox, QSplitter, QTabWidget,
                               QTableWidget, QTableWidgetItem, QTextBrowser,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from ..core import inspector, simhash, toolbox
from ..core import skeletons as skeleton
from ..core.backup import create_backup, restore_backup
from ..core.security import clear_password, has_password, set_password
from ..db import dao
from .widgets import ask, info, warn

# ================================================================ 骨架向导
class SkeletonDialog(QDialog):
    """新建公文：选文种 -> 填要素 -> 生成骨架到编辑器。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建公文（法定文种骨架）")
        self.resize(780, 560)
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("文种："))
        self.kind_combo = QComboBox()
        self.kind_combo.addItems(skeleton.kinds())
        self.kind_combo.currentTextChanged.connect(self._on_kind)
        row.addWidget(self.kind_combo, 1)
        v.addLayout(row)

        form = QHBoxLayout()
        form.addWidget(QLabel("发文单位："))
        self.ed_org = QLineEdit()
        form.addWidget(self.ed_org, 1)
        form.addWidget(QLabel("主送机关："))
        self.ed_rec = QLineEdit()
        form.addWidget(self.ed_rec, 1)
        v.addLayout(form)

        form2 = QHBoxLayout()
        form2.addWidget(QLabel("事由（标题）："))
        self.ed_matter = QLineEdit()
        form2.addWidget(self.ed_matter, 1)
        v.addLayout(form2)

        self.preview = QPlainTextEdit()
        self.preview.setPlaceholderText("生成预览…")
        v.addWidget(self.preview, 1)

        self.lbl_note = QLabel("")
        self.lbl_note.setWordWrap(True)
        self.lbl_note.setStyleSheet("color:#555;")
        v.addWidget(self.lbl_note)

        btns = QHBoxLayout()
        btn_gen = QPushButton("生成到编辑器")
        btn_gen.clicked.connect(self._gen)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.reject)
        btns.addStretch(1)
        btns.addWidget(btn_gen)
        btns.addWidget(btn_close)
        v.addLayout(btns)
        self._on_kind(self.kind_combo.currentText())

    def _on_kind(self, kind: str):
        sk = skeleton.get(kind)
        self.lbl_note.setText("提示：" + sk.note)
        self._refresh_preview()

    def _build(self) -> str:
        kind = self.kind_combo.currentText()
        sk = skeleton.get(kind)
        matter = self.ed_matter.text().strip() or "××××"
        title = sk.title_hint.replace("{org}", self.ed_org.text().strip() or "××单位") \
            .replace("{matter}", matter)
        return sk.render(title=title,
                         org=self.ed_org.text().strip() or "××单位",
                         recipients=self.ed_rec.text().strip() or "有关单位：",
                         matter=matter,
                         date="2026年×月×日")

    def _refresh_preview(self):
        self.preview.setPlainText(self._build())

    def _gen(self):
        self.accept()

    @property
    def draft_text(self) -> str:
        return self._build()


# ================================================================ 格式体检
class InspectorDialog(QDialog):
    """GB/T 9704 公文格式体检：当前文档 或 本地 docx 文件。"""

    def __init__(self, editor_text_getter, parent=None):
        super().__init__(parent)
        self._get_text = editor_text_getter
        self.setWindowTitle("公文格式体检（GB/T 9704）")
        self.resize(720, 520)
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        self.rb_editor = QRadioButton("体检当前文档")
        self.rb_editor.setChecked(True)
        self.rb_file = QRadioButton("体检 docx 文件：")
        self.file_path = QLineEdit()
        self.file_path.setPlaceholderText("选择 .docx 路径…")
        btn_pick = QPushButton("浏览…")
        btn_pick.clicked.connect(self._pick)
        row.addWidget(self.rb_editor)
        row.addWidget(self.rb_file)
        row.addWidget(self.file_path, 1)
        row.addWidget(btn_pick)
        v.addLayout(row)
        btn_run = QPushButton("开始体检")
        btn_run.clicked.connect(self._run)
        v.addWidget(btn_run)
        self.result_list = QListWidget()
        v.addWidget(self.result_list, 1)
        self.lbl_stat = QLabel("")
        v.addWidget(self.lbl_stat)

    def _pick(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 docx", "", "Word (*.docx)")
        if path:
            self.file_path.setText(path)
            self.rb_file.setChecked(True)

    def _run(self):
        findings: list[inspector.Finding] = []
        if self.rb_file.isChecked():
            path = self.file_path.text().strip()
            if not Path(path).exists():
                warn(self, "请选择有效的 docx 文件")
                return
            findings = inspector.inspect_docx(path)
        else:
            findings = inspector.inspect_text(self._get_text())
        self.result_list.clear()
        colors = {"error": "#b00020", "warn": "#b26a00", "info": "#444"}
        for f in findings:
            item = QListWidgetItem(f.label)
            item.setForeground(Qt.GlobalColor.black)
            item.setToolTip(f"等级：{f.severity}")
            self.result_list.addItem(item)
            self.result_list.item(self.result_list.count() - 1).setData(
                Qt.UserRole, f.severity)
        n_err = sum(1 for f in findings if f.severity == "error")
        n_warn = sum(1 for f in findings if f.severity == "warn")
        self.lbl_stat.setText(
            f"体检完成：问题 {n_err} 项、建议 {n_warn} 项、提示 {len(findings) - n_err - n_warn} 项。"
            + ("" if findings else "未发现问题。"))


# ================================================================ 批量替换
class _BulkReplaceWorker(QThread):
    progress = Signal(int, int)
    done = Signal(list)

    def __init__(self, doc_ids, find, repl, use_regex):
        super().__init__()
        self.doc_ids, self.find, self.repl, self.use_regex = \
            doc_ids, find, repl, use_regex

    def run(self):
        import re as _re
        results = []
        flags = 0 if self.use_regex else 0
        for i, did in enumerate(self.doc_ids):
            self.progress.emit(i + 1, len(self.doc_ids))
            d = dao.get_document(did)
            if not d:
                continue
            text = d.content_text
            try:
                if self.use_regex:
                    new_text, n = _re.subn(self.find, self.repl, text)
                else:
                    n = text.count(self.find)
                    new_text = text.replace(self.find, self.repl) if n else text
            except _re.error as exc:
                self.done.emit([("正则错误", str(exc), 0)])
                return
            if n:
                dao.update_document_content(did, d.title, new_text)
                results.append((d.title, "", n))
        self.done.emit(results)


class BulkReplaceDialog(QDialog):
    """跨文档批量查找替换（带预览）。"""

    def __init__(self, category_id: int | None, parent=None):
        super().__init__(parent)
        self.category_id = category_id
        self.setWindowTitle("跨文档批量查找替换")
        self.resize(680, 480)
        v = QVBoxLayout(self)
        g = QHBoxLayout()
        g.addWidget(QLabel("查找："))
        self.ed_find = QLineEdit()
        g.addWidget(self.ed_find, 1)
        v.addLayout(g)
        g2 = QHBoxLayout()
        g2.addWidget(QLabel("替换为："))
        self.ed_repl = QLineEdit()
        g2.addWidget(self.ed_repl, 1)
        self.chk_regex = QCheckBox("按正则")
        g2.addWidget(self.chk_regex)
        v.addLayout(g2)
        g3 = QHBoxLayout()
        g3.addWidget(QLabel("范围："))
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("全部文档", None)
        if category_id:
            self.scope_combo.addItem("当前分类", category_id)
        g3.addWidget(self.scope_combo, 1)
        v.addLayout(g3)
        self.btn_preview = QPushButton("预览命中")
        self.btn_preview.clicked.connect(self._preview)
        self.btn_apply = QPushButton("执行替换")
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._apply)
        v.addWidget(self.btn_preview)
        self.preview_list = QListWidget()
        v.addWidget(self.preview_list, 1)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        v.addWidget(self.progress)
        row = QHBoxLayout()
        row.addWidget(self.btn_apply)
        row.addStretch(1)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.reject)
        row.addWidget(btn_close)
        v.addLayout(row)
        self._worker = None

    def _docs(self) -> list[int]:
        docs = dao.list_documents(self.scope_combo.currentData())
        return [d.id for d in docs]

    def _preview(self):
        find = self.ed_find.text()
        if not find:
            warn(self, "请输入查找内容")
            return
        import re as _re
        self.preview_list.clear()
        total = 0
        for did in self._docs():
            d = dao.get_document(did)
            if not d:
                continue
            try:
                if self.chk_regex.isChecked():
                    n = len(_re.findall(find, d.content_text))
                    snippet = d.content_text[:0]
                else:
                    n = d.content_text.count(find)
                if n:
                    idx = d.content_text.find(
                        find if not self.chk_regex.isChecked()
                        else _re.search(find, d.content_text).group(0))
                    ctx = d.content_text[max(0, idx - 15): idx + 40].replace("\n", " ")
                    item = QListWidgetItem(f"{d.title}（{n} 处）：…{ctx}…")
                    item.setData(Qt.UserRole, did)
                    self.preview_list.addItem(item)
                    total += n
            except _re.error as exc:
                warn(self, f"正则错误：{exc}")
                return
        self.btn_apply.setEnabled(total > 0)
        info(self, f"预览完成：{self.preview_list.count()} 篇文档、{total} 处命中。")

    def _apply(self):
        if not ask(self, "替换将直接写入资料库（可先备份），确定执行？"):
            return
        ids = [self.preview_list.item(i).data(Qt.UserRole)
               for i in range(self.preview_list.count())]
        self.progress.setVisible(True)
        self.progress.setRange(0, len(ids))
        self.btn_apply.setEnabled(False)
        self._worker = _BulkReplaceWorker(ids, self.ed_find.text(),
                                          self.ed_repl.text(),
                                          self.chk_regex.isChecked())
        self._worker.progress.connect(self.progress.setValue)
        self._worker.done.connect(self._done)
        self._worker.start()

    def _done(self, results):
        self.progress.setVisible(False)
        n_docs = len(results)
        n_all = sum(r[2] for r in results)
        info(self, f"替换完成：{n_docs} 篇文档，共 {n_all} 处。")
        self.accept()


# ================================================================ 历史快照
class SnapshotsDialog(QDialog):
    """历史版本：列表 + 差异预览 + 回滚。"""

    def __init__(self, doc_id: int | None, current_text: str, parent=None,
                 apply_callback=None):
        super().__init__(parent)
        self.doc_id = doc_id
        self.current_text = current_text
        self.apply_callback = apply_callback
        self.setWindowTitle("历史版本（自动保存快照）")
        self.resize(900, 560)
        v = QVBoxLayout(self)
        split = QSplitter(Qt.Horizontal)
        self.list_w = QListWidget()
        self.list_w.currentRowChanged.connect(self._preview)
        split.addWidget(self.list_w)
        self.preview = QTextBrowser()
        split.addWidget(self.preview)
        split.setSizes([280, 620])
        v.addWidget(split, 1)
        row = QHBoxLayout()
        btn_restore = QPushButton("回滚到此版本")
        btn_restore.clicked.connect(self._restore)
        btn_del = QPushButton("删除该快照")
        btn_del.clicked.connect(self._delete)
        row.addWidget(btn_restore)
        row.addWidget(btn_del)
        row.addStretch(1)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.reject)
        row.addWidget(btn_close)
        v.addLayout(row)
        self._fill()

    def _fill(self):
        self.list_w.clear()
        for s in dao.list_snapshots(self.doc_id):
            item = QListWidgetItem(
                f"{s['created_time']}  [{s['reason']}]  {len(s['content'])}字")
            item.setData(Qt.UserRole, s["id"])
            self.list_w.addItem(item)

    def _preview(self, row):
        if row < 0:
            return
        sid = self.list_w.item(row).data(Qt.UserRole)
        s = dao.get_snapshot(sid)
        from ..core import differ
        html = differ.diff_to_html(s["content"], self.current_text,
                                   f"快照 {s['created_time']}", "当前内容")
        self.preview.setHtml(html)

    def _restore(self):
        row = self.list_w.currentRow()
        if row < 0:
            return
        sid = self.list_w.item(row).data(Qt.UserRole)
        s = dao.get_snapshot(sid)
        if ask(self, f"回滚到 {s['created_time']} 的快照？当前未保存内容将替换。"):
            if self.apply_callback:
                self.apply_callback(s["content"])
            self.reject()

    def _delete(self):
        row = self.list_w.currentRow()
        if row < 0:
            return
        sid = self.list_w.item(row).data(Qt.UserRole)
        from ..db.connection import get_conn
        get_conn().execute("DELETE FROM snapshots WHERE id=?", (sid,))
        get_conn().commit()
        self._fill()


# ================================================================ 相似查重
class SimilarityDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("相似文档查重（SimHash）")
        self.resize(680, 480)
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        self.sp_thresh = QSpinBox()
        self.sp_thresh.setRange(40, 99)
        self.sp_thresh.setValue(70)
        self.sp_thresh.setSuffix("%")
        row.addWidget(QLabel("相似度阈值："))
        row.addWidget(self.sp_thresh)
        btn = QPushButton("开始查重")
        btn.clicked.connect(self._run)
        row.addWidget(btn)
        row.addStretch(1)
        v.addLayout(row)
        self.result_list = QListWidget()
        v.addWidget(self.result_list, 1)
        self.lbl = QLabel("在全部资料中找出内容高度相似的材料对。")
        v.addWidget(self.lbl)

    def _run(self):
        from ..db.connection import get_conn
        docs = {d.id: d.title for d in dao.list_documents()}
        if len(docs) < 2:
            info(self, "资料库中至少需要 2 篇材料。")
            return
        texts = {}
        for did in docs:
            texts[did] = dao.get_document(did).content_text
        pairs = simhash.find_similar(texts, self.sp_thresh.value() / 100)
        self.result_list.clear()
        by_id = {d.id: d for d in dao.list_documents()}
        for a, b, sim in pairs:
            t = f"相似度 {sim * 100:.0f}%：{by_id[a].title}  ↔  {by_id[b].title}"
            item = QListWidgetItem(t)
            item.setData(Qt.UserRole, (a, b))
            self.result_list.addItem(item)
        self.lbl.setText(f"检出 {len(pairs)} 对相似材料（阈值 {self.sp_thresh.value()}%）。")


# ================================================================ 安全设置
class SecurityDialog(QDialog):
    """口令锁、自动备份、OCR 设置。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置 —— 系统与安全")
        self.resize(560, 420)
        v = QVBoxLayout(self)

        g1 = QLabel("口令锁（启动程序时需输入口令）")
        g1.setStyleSheet("font-weight:bold;")
        v.addWidget(g1)
        row = QHBoxLayout()
        self.ed_pw = QLineEdit()
        self.ed_pw.setEchoMode(QLineEdit.Password)
        self.ed_pw.setPlaceholderText("输入新口令（留空=不修改）")
        row.addWidget(self.ed_pw, 1)
        btn_set = QPushButton("设置/修改口令")
        btn_set.clicked.connect(self._set_pw)
        row.addWidget(btn_set)
        v.addLayout(row)
        if has_password():
            row2 = QHBoxLayout()
            self.lbl_pw_state = QLabel("口令锁：已启用")
            btn_clear = QPushButton("解除口令锁")
            btn_clear.clicked.connect(self._clear_pw)
            row2.addWidget(self.lbl_pw_state)
            row2.addWidget(btn_clear)
            row2.addStretch(1)
            v.addLayout(row2)
        else:
            self.lbl_pw_state = QLabel("口令锁：未启用")
            v.addWidget(self.lbl_pw_state)

        g2 = QLabel("自动备份")
        g2.setStyleSheet("font-weight:bold;margin-top:8pt;")
        v.addWidget(g2)
        self.chk_auto_backup = QCheckBox("退出程序时自动备份（保留最近 20 份）")
        self.chk_auto_backup.setChecked(dao.get_setting("auto_backup", "1") == "1")
        self.chk_auto_backup.toggled.connect(
            lambda on: dao.set_setting("auto_backup", "1" if on else "0"))
        v.addWidget(self.chk_auto_backup)

        g3 = QLabel("OCR（扫描件识别，需已安装 Tesseract）")
        g3.setStyleSheet("font-weight:bold;margin-top:8pt;")
        v.addWidget(g3)
        row3 = QHBoxLayout()
        self.ed_tess = QLineEdit(dao.get_setting("tesseract_path", ""))
        self.ed_tess.setPlaceholderText("tesseract 可执行文件路径（留空=自动搜索 PATH）")
        row3.addWidget(self.ed_tess, 1)
        btn_browse = QPushButton("浏览…")
        btn_browse.clicked.connect(self._browse_tess)
        row3.addWidget(btn_browse)
        v.addLayout(row3)
        self.lbl_tess = QLabel("")
        v.addWidget(self.lbl_tess)

        v.addStretch(1)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        v.addWidget(btn_close, 0, Qt.AlignRight)
        self._check_tess()

    def _check_tess(self):
        from ..core.ocr import available, has_chi_sim, tesseract_path
        if available():
            ok = has_chi_sim()
            self.lbl_tess.setText(
                f"已找到 tesseract：{tesseract_path()}"
                + ("" if ok else "（缺少中文包 chi_sim，请安装 tesseract-ocr-chi-sim）"))
        else:
            self.lbl_tess.setText("未找到 tesseract：扫描件 OCR 功能不可用（文字版 PDF 不受影响）")

    def _browse_tess(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 tesseract", "",
                                              "可执行文件 (*)")
        if path:
            self.ed_tess.setText(path)
            dao.set_setting("tesseract_path", path)
            self._check_tess()

    def _set_pw(self):
        pw = self.ed_pw.text()
        if len(pw) < 4:
            warn(self, "口令至少 4 位。请务必牢记：口令无法找回！")
            return
        set_password(pw)
        dao.set_setting("lock_enabled", "1")
        info(self, "口令锁已启用，下次启动程序时生效。请牢记口令！")
        self.lbl_pw_state.setText("口令锁：已启用")
        self.accept()

    def _clear_pw(self):
        if ask(self, "确定解除口令锁？"):
            clear_password()
            dao.set_setting("lock_enabled", "0")
            self.lbl_pw_state.setText("口令锁：未启用")


# ================================================================ 锁屏
class LockDialog(QDialog):
    """启动口令锁。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("公文汇编助手 —— 已锁定")
        self.setFixedSize(360, 150)
        v = QVBoxLayout(self)
        v.addWidget(QLabel("本资料库已启用口令锁，请输入口令解锁："))
        self.ed_pw = QLineEdit()
        self.ed_pw.setEchoMode(QLineEdit.Password)
        self.ed_pw.returnPressed.connect(self._try)
        v.addWidget(self.ed_pw)
        self.lbl = QLabel("")
        self.lbl.setStyleSheet("color:#b00020;")
        v.addWidget(self.lbl)
        btn = QPushButton("解锁")
        btn.clicked.connect(self._try)
        v.addWidget(btn)
        self._fail_count = 0

    def _try(self):
        from ..core.security import verify_password
        if verify_password(self.ed_pw.text()):
            self.accept()
            return
        self._fail_count += 1
        self.lbl.setText(f"口令错误（已失败 {self._fail_count} 次）")
        if self._fail_count >= 5:
            from PySide6.QtCore import QEventLoop, QTimer
            self.lbl.setText("失败次数过多，请等待 10 秒后重试…")
            loop = QEventLoop()
            QTimer.singleShot(10000, loop.quit)
            loop.exec()
            self._fail_count = 0
