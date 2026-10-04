# -*- coding: utf-8 -*-
"""新增功能对话框集合：骨架向导 / 格式体检 / 批量替换 / 批量纠错 / 历史快照 /
相似查重 / 安全设置 / 锁屏。"""
from __future__ import annotations

import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                               QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPlainTextEdit,
                               QProgressBar, QPushButton, QRadioButton,
                               QScrollArea, QSpinBox, QSplitter, QTextBrowser,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from .. import logs
from ..core import inspector, simhash
from ..core import skeletons as skeleton
from ..core import backup as backup_core
from ..core.security import clear_password, has_password, set_password
from ..db import dao
from . import theme
from . import errmsg
from .widgets import ThreadSafeDialog, ask, info, warn
from .workers import _close_thread_conn

log = logs.get_logger("ui.pack")


def _fit_dialog(dlg, want_w: int, want_h: int) -> None:
    """按目标尺寸开对话框，但**绝不超出屏幕可用区域**。

    与 main_window._fit_to_screen 同源的问题：硬编码高度在 1366×768 笔记本上
    会超出屏幕。屏幕过小（离屏测试 800×800）时以屏幕为准。
    """
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        dlg.resize(want_w, want_h)
        return
    avail = screen.availableGeometry()
    dlg.resize(min(want_w, max(320, avail.width() - 40)),
               min(want_h, max(240, avail.height() - 60)))

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
        self.lbl_note.setStyleSheet(f"color:{theme.MUTED};")
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

    @property
    def draft_title(self) -> str:
        """入库标题：文种 + 事由（无事由时仅文种）。"""
        matter = self.ed_matter.text().strip()
        kind = self.kind_combo.currentText()
        return f"{kind}：{matter}" if matter else kind


# ================================================================ 格式体检
class InspectorDialog(ThreadSafeDialog, QDialog):
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
        self.btn_run = QPushButton("开始体检")
        self.btn_run.clicked.connect(self._run)
        self.btn_export = QPushButton("导出报告…")
        self.btn_export.setToolTip("把体检结果导出为规范 DOCX 报告，可归档或转交拟稿人整改")
        self.btn_export.clicked.connect(self._export_report)
        ops = QHBoxLayout()
        ops.addWidget(self.btn_run)
        ops.addWidget(self.btn_export)
        ops.addStretch(1)
        v.addLayout(ops)
        self._findings: list[inspector.Finding] = []
        self._source = ""
        # 「是否真的拿到过体检结果」的独立标记：不能用 lbl_stat 文案替代
        # （"体检中…"与"体检未完成"都非空），详见 _export_report。
        self._has_result = False
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
        """开始体检（后台线程执行，见 FnWorker）。

        体检要把整篇文档过一遍 20+ 条规则（docx 路径还要先解析包），
        放主线程会在这几秒里冻住对话框 —— 而这个对话框恰恰是用户
        在等结果的地方，冻住会让他以为是卡死了。
        """
        from .workers import FnWorker

        if self.rb_file.isChecked():
            path = self.file_path.text().strip()
            if not Path(path).exists():
                warn(self, "请选择有效的 docx 文件")
                return
            source = Path(path).name

            def work():
                return inspector.inspect_docx(path)
        else:
            # 取值必须在主线程：QTextEdit 不是线程安全的
            text = self._get_text()
            source = "当前编辑文档"

            def work():
                return inspector.inspect_text(text)

        worker = getattr(self, "_inspect_worker", None)
        if worker is not None and worker.isRunning():
            warn(self, "上一次体检仍在进行，请稍候。")
            return
        self.btn_run.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.lbl_stat.setText("体检中…")
        self.result_list.clear()
        w = FnWorker(work, parent=self)
        w.ok.connect(lambda findings: self._run_done(findings, source))
        w.failed.connect(self._run_failed)
        self._inspect_worker = w
        w.start()

    def _run_done(self, findings, source: str):
        self.btn_run.setEnabled(True)
        self.btn_export.setEnabled(True)
        self._findings = findings
        self._source = source
        self._has_result = True       # 有结果了，_export_report 才允许导出
        self.result_list.clear()
        colors = {"error": theme.DANGER, "warn": theme.WARN, "info": theme.INFO}
        for f in findings:
            item = QListWidgetItem(f.label)
            item.setForeground(QColor(colors.get(f.severity, "#000000")))
            item.setToolTip(f"等级：{f.severity}")
            self.result_list.addItem(item)
            self.result_list.item(self.result_list.count() - 1).setData(
                Qt.UserRole, f.severity)
        n_err = sum(1 for f in findings if f.severity == "error")
        n_warn = sum(1 for f in findings if f.severity == "warn")
        self.lbl_stat.setText(
            f"体检完成：问题 {n_err} 项、建议 {n_warn} 项、提示 {len(findings) - n_err - n_warn} 项。"
            + ("" if findings else "未发现问题。"))

    def _run_failed(self, msg: str):
        # 失败必须解锁按钮并改掉"体检中…"：否则用户面对一个永远转着的
        # 对话框，既不知道失败、也点不了重试。
        self.btn_run.setEnabled(True)
        self.btn_export.setEnabled(True)
        self.lbl_stat.setText("体检未完成")
        warn(self, msg)

    def _export_report(self):
        # 判据必须是"是否真拿到过体检结果"，**不能**用 lbl_stat 文案非空 ——
        # `_run` 一开始就写"体检中…"、`_run_failed` 写"体检未完成"，两者都非空，
        # 于是"体检失败后点导出"会带着空 findings 进后台，产出一份结论为空的
        # 报告，看起来像成功了。加独立标记 _has_result 来区分这三种状态。
        if not self._findings and not getattr(self, "_has_result", False):
            warn(self, "请先点击「开始体检」，等体检完成后再导出报告。")
            return
        from datetime import datetime

        default = f"公文格式体检报告_{datetime.now():%Y%m%d_%H%M}.docx"
        path, _sel = QFileDialog.getSaveFileName(
            self, "导出体检报告", default,
            "Word 文档 (*.docx);;Excel 整改清单 (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith((".docx", ".xlsx")):
            path += ".docx"
        # 导出要排版全文 + 逐条渲染正式版式，秒级操作：放后台，导出期间
        # 按钮置灰防重复点击，失败/成功都复位 —— 不能让用户对着一个
        # 看起来还能点的按钮反复触发。
        findings = list(self._findings)
        source = self._source
        from .workers import FnWorker

        def work():
            from ..core import report
            # 以后缀为准：DOCX 适合归档流转（有正式版式），
            # xlsx 适合逐条整改销号（可自行加"处理意见"列筛选）
            if path.lower().endswith(".xlsx"):
                return ("xlsx", report.export_report_xlsx(
                    findings, path, source_name=source))
            report.export_report(findings, path, source_name=source)
            return ("docx", report.verdict(findings))

        worker = getattr(self, "_export_worker", None)
        if worker is not None and worker.isRunning():
            warn(self, "上一次导出仍在进行，请稍候。")
            return
        self.btn_export.setEnabled(False)
        self.lbl_stat.setText("正在导出体检报告…")
        w = FnWorker(work, parent=self)
        w.ok.connect(lambda r: self._export_report_done(path, r))
        w.failed.connect(self._export_report_failed)
        self._export_worker = w
        w.start()

    def _export_report_done(self, path: str, result):
        self.btn_export.setEnabled(True)
        kind, payload = result
        if kind == "xlsx":
            info(self, f"体检整改清单已导出：\n{path}\n\n"
                       f"共 {payload} 条，可直接在表中登记处理意见与备注。")
            return
        info(self, f"体检报告已导出：\n{path}\n\n结论：{payload}")

    def _export_report_failed(self, msg: str):
        self.btn_export.setEnabled(True)
        self.lbl_stat.setText("体检报告导出未完成")
        warn(self, f"导出失败：{msg}")


# ================================================================ 批量替换
class _BulkReplaceWorker(QThread):
    progress = Signal(int, int)
    done = Signal(list)
    failed = Signal(str)

    def __init__(self, doc_ids, find, repl, use_regex, parent=None):
        super().__init__(parent)
        self.doc_ids, self.find, self.repl, self.use_regex = \
            doc_ids, find, repl, use_regex

    def run(self):
        # 整段包 try：老实现没有异常通路，任何一次 dao 调用抛错
        # （库被占用、磁盘满、sqlite 异常）都会让 run() 直接结束而不发任何信号，
        # 对话框的「执行替换」按钮与进度条永远停在忙碌态，只能关窗口。
        try:
            self._run()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.failed.emit(errmsg.friendly(exc, action="批量替换"))
        finally:
            try:
                from ..db import connection as _dbconn
                _dbconn.close_current_thread()
            except Exception:
                pass

    def _run(self):
        import re as _re
        # 空查找串是**破坏性**输入而非"无操作"：
        #   非正则模式下 text.replace("", x) 会在每个字符间插入 x；
        #   正则模式下 re.subn("", x, text) 更甚 —— 连首尾都会插。
        # 实测「某市人民政府办公室文件」+ 替换为「【空】」→
        # 「【空】某【空】市【空】…【空】件【空】」，一篇正文被彻底破坏，
        # 而用户会以为"什么都没改"。宁可整批拒绝，也不产生这种结果。
        if not self.find:
            self.failed.emit("查找内容不能为空，已中止（空查找会把替换文本插入"
                             "每一个字符之间，破坏全部正文）。")
            return
        results = []
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
                # 完整中文文案（含下一步指引）随结果表回传
                self.done.emit([("正则错误", errmsg.friendly(exc, action="批量替换"), 0)])
                return
            if n:
                # 写回前快照 + 结构保全（第 23 轮修复）：不传 blocks_json 会把
                # 结构清成 "[]"——跨文档批量替换清空所有受影响文档的表格/标题
                # 层级。查找可能是跨行正则，无法映射回旧块，故按标题正则从新
                # 文本重建块结构（与编辑器保存的重建策略一致）；并补上此前
                # 文档承诺的「写回前每篇留快照」。
                snap_note = ""
                try:
                    dao.add_snapshot(did, d.title, d.content_text,
                                     reason="批量替换前")
                except Exception:
                    # 与批量纠错同型（batch.py）：快照失败不拦住替换，
                    # 但必须出现在结果里 —— 结果元组第二项就是「说明」位，
                    # 空串表示一切正常，非空会被对话框列出来。
                    snap_note = ("正文已替换，但改动前的回滚快照未保存："
                                 "这篇无法用「历史版本」退回改前状态")
                from ..core.importer import _text_to_tree
                blocks = _text_to_tree(new_text).to_json()
                dao.update_document_content(did, d.title, new_text, blocks_json=blocks)
                results.append((d.title, snap_note, n))
        self.done.emit(results)


class BulkReplaceDialog(ThreadSafeDialog, QDialog):
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
        # 预览条件快照（None = 尚未预览或预览已失效）；见 _apply 的说明。
        self._plan_key = None
        # 启动预览那一刻的条件快照，用于完成时校验（见 _preview / _on_preview_done）
        self._preview_key: tuple | None = None
        # 条件一起变就立即置灰「执行替换」：让"预览已失效"在按钮上就看得见，
        # 比等用户点了执行再弹提示更早、更直观。
        self.ed_find.textChanged.connect(self._invalidate_plan)
        self.ed_repl.textChanged.connect(self._invalidate_plan)
        self.chk_regex.toggled.connect(self._invalidate_plan)

    def _invalidate_plan(self, *_):
        """查找内容/替换内容/「按正则」任一变动 → 预览失效，禁用执行。"""
        if self._plan_key is not None and self._current_key() != self._plan_key:
            self._plan_key = None
            self.btn_apply.setEnabled(False)

    def _docs(self, scope=None) -> list[int]:
        """当前范围内的文档 id。

        scope 可以从主线程预取后传入：本方法会在工作线程里被调用，
        而传入前就读取控件值（`currentData()`）属于跨线程访问 QObject，
        在麒麟/Wayland 下可能读到脏值或崩溃。
        """
        if scope is None:
            scope = self.scope_combo.currentData()
        docs = dao.list_documents(scope)
        return [d.id for d in docs]

    def _preview(self):
        find = self.ed_find.text()
        if not find:
            warn(self, "请输入查找内容")
            return
        from .workers import FnWorker

        # 条件快照必须在**启动时**拍，不能在完成回调里读控件：预览全库要跑
        # 数秒，用户完全可能在此期间改查找词/替换词/正则开关。若完成时才读，
        # 就会把"用旧条件扫出的文档清单"配上"新条件"交给执行 —— 正是
        # _plan_key 要挡住的那类事故（预览所见 ≠ 实际所改）。`_invalidate_plan`
        # 在预览期间是空转的（此刻 _plan_key 还是 None），所以校验只能放这里。
        self._preview_key = self._current_key()

        # 主线程先把所有控件值取出来，worker 里只碰纯数据
        scope_id = self.scope_combo.currentData()
        use_regex = self.chk_regex.isChecked()

        def work():
            """后台：逐文档统计命中数与上下文（正则编译在 worker 内完成）。"""
            import re as _re
            rows = []
            total = 0
            regex_error = None
            for did in self._docs(scope_id):
                d = dao.get_document(did)
                if not d:
                    continue
                try:
                    if use_regex:
                        n = len(_re.findall(find, d.content_text))
                    else:
                        n = d.content_text.count(find)
                    if n:
                        idx = d.content_text.find(
                            find if not use_regex
                            else _re.search(find, d.content_text).group(0))
                        ctx = d.content_text[max(0, idx - 15): idx + 40].replace(
                            "\n", " ")
                        rows.append((did, d.title, n, ctx))
                        total += n
                except _re.error as exc:
                    regex_error = errmsg.friendly(exc, action="批量替换预览")
                    break
            return rows, total, regex_error

        self.btn_preview.setEnabled(False)
        self.btn_apply.setEnabled(False)
        self.preview_list.clear()
        # 记住本次预览所用的**条件**：执行时必须与之完全一致。
        # 只存"命中了哪些文档"是不够的 —— 用户预览之后完全可能改动查找框
        # （清空、换词、开关"按正则"），而 btn_apply 的启用状态只取决于
        # **上次预览**的 total>0，按钮仍是亮的。此时点执行会用**新条件**
        # 改**旧清单**里的文档：用户看过的预览彻底失效，改动范围完全不可预期
        # （最坏档见 _run 里对空查找串的说明）。
        # 对照 core/batch.py 的既有契约——那里明确要求"执行时必须带上预览得到
        # 的 plans"，即预览与执行共用同一份计划；本对话框漏了这一条。
        self._plan_key = None
        self._preview_worker = FnWorker(work, parent=self)
        self._preview_worker.ok.connect(self._on_preview_done)
        self._preview_worker.finished.connect(
            lambda: self.btn_preview.setEnabled(True))
        self._preview_worker.start()

    def _current_key(self) -> tuple:
        """当前界面上的替换条件（与预览快照比对用）。"""
        return (self.ed_find.text(), self.ed_repl.text(),
                bool(self.chk_regex.isChecked()))

    def _on_preview_done(self, result):
        rows, total, regex_error = result
        if regex_error:
            # errmsg.friendly 已给出完整中文可执行文案，不再叠加"正则错误"前缀
            warn(self, regex_error)
            return
        # 与启动时拍的快照比对（不是与此刻的控件值比）：不一致说明用户在预览
        # 期间改过条件，本次预览作废。宁可让用户重来一次，也不能按"新条件"去改
        # "旧清单"里的文档。
        if getattr(self, "_preview_key", None) != self._current_key():
            self.preview_list.clear()
            self._plan_key = None
            self.btn_apply.setEnabled(False)
            info(self, "预览期间查找条件有改动，本次预览已作废，请重新预览。")
            return
        for did, title, n, ctx in rows:
            item = QListWidgetItem(f"{title}（{n} 处）：…{ctx}…")
            item.setData(Qt.UserRole, did)
            self.preview_list.addItem(item)
        self._plan_key = self._preview_key if total > 0 else None
        self.btn_apply.setEnabled(total > 0)
        info(self, f"预览完成：{len(rows)} 篇文档、{total} 处命中。")

    def _apply(self):
        # 条件漂移检查放在确认框**之前**：先让用户知道"预览已失效"，
        # 而不是先问"确定执行吗"再拒绝 —— 后者像是程序在耍人。
        if self._plan_key is None:
            warn(self, "请先「预览命中」，确认改动范围后再执行。")
            return
        if self._current_key() != self._plan_key:
            self.btn_apply.setEnabled(False)
            warn(self, "查找/替换内容或「按正则」开关在预览之后被改动，"
                       "原先的预览结果已失效。\n\n"
                       "为避免在您没看过的范围内改动，已阻止执行。"
                       "请重新「预览命中」后再执行替换。")
            return
        # 防重入必须在弹确认框**之前**：`QMessageBox.question` 是模态嵌套事件
        # 循环，它弹着的时候事件仍在派发，按钮也仍是可用的 —— 连按回车/空格
        # 或极快的双击都能重入本函数，弹出第二个确认框。用户在两个框上都点
        # "是"就会起两个 worker，对同一批 doc_id 各跑一遍替换（替换文本含
        # 查找词时结果被叠加成"我的我的…"），且 self._worker 被覆盖、前一个
        # 失去强引用。旧实现把 setEnabled(False) 放在 ask() 之后，挡不住。
        if getattr(self, "_worker", None) is not None and self._worker.isRunning():
            return
        self.btn_apply.setEnabled(False)
        if not ask(self, "替换将直接写入资料库（可先备份），确定执行？"):
            self.btn_apply.setEnabled(True)
            return
        ids = [self.preview_list.item(i).data(Qt.UserRole)
               for i in range(self.preview_list.count())]
        self.progress.setVisible(True)
        self.progress.setRange(0, len(ids))
        # 用**预览时的快照**而不是实时控件值喂 worker：两者既然已校验一致，
        # 传快照能顺带消掉"工作线程读 QObject"这一跨线程访问。
        find, repl, use_regex = self._plan_key
        # parent=self：无父 QThread 只被 self._worker 强引用，对话框先销毁时
        # 唯一引用被释放会让运行中的线程被 GC 析构 → Qt qFatal 强杀进程。
        self._worker = _BulkReplaceWorker(ids, find, repl, use_regex, parent=self)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.done.connect(self._done)
        self._worker.failed.connect(self._failed)
        self._worker.start()

    def _failed(self, msg: str):
        """替换失败：必须复位按钮/进度条，否则对话框永久卡在忙碌态。"""
        self.progress.setVisible(False)
        self.btn_apply.setEnabled(True)
        warn(self, f"批量替换未完成：{msg}\n\n已替换的部分不受影响，可修正后重试。")

    def _done(self, results):
        self.progress.setVisible(False)
        n_docs = len(results)
        n_all = sum(r[2] for r in results)
        info(self, f"替换完成：{n_docs} 篇文档，共 {n_all} 处。")
        self.accept()


# ================================================================ 批量纠错
# 预览树的显示上限：命中很多时全部塞进控件会让界面卡住，
# 计划本身（self._plans）不截断，执行仍按全部命中处理。
_MAX_SHOW_DOCS = 300
_MAX_SHOW_HITS = 4000


def _correct_categories() -> list[str]:
    """可选纠错类别：内置规则类别 + 词库里的真实类别（去掉统计占位名）。"""
    names = {"错别字", "易混词", "标点", "机构沿革", "用户词库",
             "语义冗余", "搭配", "关联词", "表达提示", "标点规范"}
    try:
        names |= {c for c, _n in dao.error_pair_categories()
                  if c and c not in ("未分类", "未标注")}
    except Exception:
        pass
    return sorted(names)


def _brief_failures(failures, limit: int = 8) -> str:
    """失败清单摘要（弹窗里不能刷几百行）。"""
    lines = [f"· {title}：{reason}" for title, reason in list(failures)[:limit]]
    if len(failures) > limit:
        lines.append(f"…另有 {len(failures) - limit} 项")
    return "\n".join(lines)


class _BatchScanWorker(QThread):
    """批量纠错扫描（预览）。全库逐篇跑纠错耗时，必须在后台线程。"""
    progress = Signal(int, int)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, category_id, min_conf, categories, parent=None):
        super().__init__(parent)
        self.category_id = category_id
        self.min_conf = min_conf
        self.categories = categories

    def run(self):
        from ..core import batch
        try:
            res = batch.batch_correct(
                category_id=self.category_id, min_confidence=self.min_conf,
                categories=self.categories,
                progress_cb=lambda i, n: self.progress.emit(i, n))
            self.done.emit(res)
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(errmsg.friendly(exc, action="批量纠错"))
        finally:
            # 本线程走了 dao（thread-local 连接）：不关会连同 -wal/-shm 句柄
            # 随线程一起泄漏，反复"预览/纠错"在麒麟上触发 too many open files；
            # 同时注销连接登记表里的本条记录，否则恢复备份会误报"数据库被占用"。
            _close_thread_conn()


class _BatchApplyWorker(QThread):
    """批量纠错执行：按预览计划写回（DAO 内部同步 FTS 索引）。"""
    progress = Signal(int, int)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, plans, parent=None):
        super().__init__(parent)
        self.plans = plans

    def run(self):
        from ..core import batch
        try:
            res = batch.batch_correct(
                apply=True, plans=self.plans,
                progress_cb=lambda i, n: self.progress.emit(i, n))
            self.done.emit(res)
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(errmsg.friendly(exc, action="批量纠错"))
        finally:
            _close_thread_conn()


class BatchCorrectDialog(ThreadSafeDialog, QDialog):
    """按分类批量纠错：先预览命中（哪篇、每处 错→对、位置），确认后才写回。

    交互范式沿用 BulkReplaceDialog（预览 -> 确认 -> 后台执行 -> 汇总）。
    写回前每篇都会留一份历史快照，改错了可在「历史版本」里逐篇回滚。
    """

    def __init__(self, category_id: int | None = None, parent=None):
        super().__init__(parent)
        self.category_id = category_id
        self._plans: list = []
        self._scan_worker = None
        self._apply_worker = None
        self.setWindowTitle("按分类批量纠错")
        self.resize(840, 560)
        v = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("范围："))
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("全部文档", None)
        for c in dao.list_categories():
            self.scope_combo.addItem(c.name, c.id)
        if category_id:
            idx = self.scope_combo.findData(category_id)
            if idx >= 0:
                self.scope_combo.setCurrentIndex(idx)
        self.scope_combo.currentIndexChanged.connect(self._invalidate)
        row.addWidget(self.scope_combo, 1)
        row.addWidget(QLabel("类别："))
        self.kind_combo = QComboBox()
        self.kind_combo.addItem("全部类别", "")
        for name in _correct_categories():
            self.kind_combo.addItem(name, name)
        self.kind_combo.currentIndexChanged.connect(self._invalidate)
        row.addWidget(self.kind_combo)
        row.addWidget(QLabel("置信度≥"))
        self.sp_conf = QDoubleSpinBox()
        self.sp_conf.setRange(0.0, 1.0)
        self.sp_conf.setDecimals(2)
        self.sp_conf.setSingleStep(0.05)
        self.sp_conf.setValue(0.80)
        self.sp_conf.setToolTip(
            "批量写回默认只改高置信命中：精标词库≥0.85，上下文与标点规则 0.85~0.95，"
            "语义冗余/搭配/关联词 0.72~0.90，程序生成的混淆对 0.55，机构沿革对照 0.70。\n"
            "「数字用法」「表达提示」「标点规范」三类只给提示不给替换文本，已整类排除。")
        self.sp_conf.valueChanged.connect(self._invalidate)
        row.addWidget(self.sp_conf)
        v.addLayout(row)

        ops = QHBoxLayout()
        self.btn_preview = QPushButton("预览命中")
        self.btn_preview.clicked.connect(self._preview)
        self.btn_apply = QPushButton("执行纠错")
        self.btn_apply.setEnabled(False)
        self.btn_apply.setToolTip("按上面预览到的命中写回资料库（全文索引随之同步）")
        self.btn_apply.clicked.connect(self._apply)
        ops.addWidget(self.btn_preview)
        ops.addWidget(self.btn_apply)
        ops.addStretch(1)
        v.addLayout(ops)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["文档 / 命中（错 → 对）", "位置", "类别", "置信度"])
        self.tree.setUniformRowHeights(True)
        self.tree.setColumnWidth(0, 430)
        v.addWidget(self.tree, 1)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        v.addWidget(self.progress)

        self.lbl = QLabel("先「预览命中」确认无误，再「执行纠错」。执行会直接写入资料库，"
                          "建议先做一次备份。")
        self.lbl.setWordWrap(True)
        self.lbl.setStyleSheet(f"color:{theme.MUTED};")
        v.addWidget(self.lbl)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.reject)
        bottom.addWidget(btn_close)
        v.addLayout(bottom)

    # ------------------------------------------------------------ 预览
    def _invalidate(self, *_):
        """筛选条件一变，旧的命中计划立即作废（防止按过期预览写回）。"""
        self._plans = []
        self.tree.clear()
        self.btn_apply.setEnabled(False)

    def _preview(self):
        scope = self.scope_combo.currentData()
        self._invalidate()
        try:
            total = dao.count_documents(scope)
        except Exception as exc:
            warn(self, f"读取资料库失败：{exc}")
            return
        if not total:
            info(self, "该范围内没有文档可纠错。")
            return
        kind = self.kind_combo.currentData() or ""
        self.btn_preview.setEnabled(False)
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.lbl.setText(f"正在扫描 {total} 篇文档…")
        self._scan_worker = _BatchScanWorker(scope, self.sp_conf.value(),
                                             (kind,) if kind else (), parent=self)
        self._scan_worker.progress.connect(self._on_progress)
        self._scan_worker.done.connect(self._on_preview_done)
        self._scan_worker.failed.connect(self._on_failed)
        self._scan_worker.start()

    def _on_progress(self, i: int, total: int):
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(min(i, total))

    def _reset_buttons(self):
        self.progress.setVisible(False)
        self.btn_preview.setEnabled(True)
        # 「执行」也必须复位：它在 _apply 一开始就被置灰，若只在成功路径
        # 复位，用户在确认框点"取消"或写回失败后就再也没法重试了（按钮
        # 永久灰着，只能重开对话框）。启用与否由 _plans 是否非空决定。
        self.btn_apply.setEnabled(bool(self._plans))

    def _on_failed(self, msg: str):
        self._reset_buttons()
        self.lbl.setText("已中止，未改动任何文档。")
        warn(self, f"批量纠错失败：{msg}")

    def _on_preview_done(self, res):
        self._reset_buttons()
        self._plans = list(res.plans)
        self._fill_tree(res)
        hits = res.hit_total
        self.btn_apply.setEnabled(hits > 0)
        text = (f"预览完成：扫描 {res.scanned} 篇，{len(self._plans)} 篇有命中，"
                f"共 {hits} 处。")
        if res.failures:
            text += f"（{len(res.failures)} 篇扫描失败，已跳过）"
        self.lbl.setText(text)
        if not hits:
            info(self, "在当前范围与筛选条件下没有可批量修正的命中。"
                       "\n可试着降低置信度门槛或换类别。")
        elif res.failures:
            warn(self, "以下文档扫描失败（不影响其余）：\n"
                       + _brief_failures(res.failures))

    def _fill_tree(self, res):
        self.tree.clear()
        shown_docs = shown_hits = 0
        for plan in res.plans:
            if shown_docs >= _MAX_SHOW_DOCS or shown_hits >= _MAX_SHOW_HITS:
                break
            top = QTreeWidgetItem([f"{plan.title}（{plan.count} 处）", "", "", ""])
            top.setData(0, Qt.UserRole, plan.doc_id)
            self.tree.addTopLevelItem(top)
            shown_docs += 1
            for h in plan.hits:
                if shown_hits >= _MAX_SHOW_HITS:
                    break
                child = QTreeWidgetItem([h.label, f"{h.start}-{h.end}",
                                         h.category, f"{h.confidence:.2f}"])
                child.setToolTip(0, f"上下文：…{h.context}…")
                top.addChild(child)
                shown_hits += 1
            if shown_docs <= 20:
                top.setExpanded(True)
        if len(res.plans) > shown_docs:
            self.tree.addTopLevelItem(QTreeWidgetItem(
                [f"…另有 {len(res.plans) - shown_docs} 篇命中未显示"
                 f"（执行时按全部 {res.hit_total} 处处理）", "", "", ""]))

    # ------------------------------------------------------------ 执行
    def _apply(self):
        if not self._plans:
            warn(self, "请先「预览命中」，确认要改哪些地方。")
            return
        # 防重入：`QMessageBox.question` 是模态嵌套事件循环，它弹着的时候按钮
        # 仍可用 —— 连按回车能重入、起第二个 worker 对同一批文档再改一遍。
        # 因此必须**在 ask() 之前**置灰（旧实现放在 ask() 之后，挡不住）。
        if (getattr(self, "_apply_worker", None) is not None
                and self._apply_worker.isRunning()):
            return
        hits = sum(p.count for p in self._plans)
        self.btn_preview.setEnabled(False)
        self.btn_apply.setEnabled(False)
        if not ask(self, f"将修改 {len(self._plans)} 篇文档、共 {hits} 处，"
                         f"直接写入资料库（每篇改前会留一份历史快照）。\n确定执行？"):
            self._reset_buttons()
            return
        self.progress.setRange(0, len(self._plans))
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.lbl.setText("正在写回资料库…")
        self._apply_worker = _BatchApplyWorker(self._plans, parent=self)
        self._apply_worker.progress.connect(self._on_progress)
        self._apply_worker.done.connect(self._on_apply_done)
        self._apply_worker.failed.connect(self._on_failed)
        self._apply_worker.start()

    def _on_apply_done(self, res):
        self._reset_buttons()
        self._plans = []
        self.tree.clear()
        lines = [f"批量纠错完成：{len(res.applied)} 篇文档、{res.changes} 处已写回，"
                 f"全文索引已同步。"]
        if res.skipped:
            lines.append(f"另有 {res.skipped} 处因文档已变化未能替换，可重新预览。")
        if res.failures:
            lines.append(f"{len(res.failures)} 篇失败（不影响其余）：")
            lines.append(_brief_failures(res.failures))
        self.lbl.setText(lines[0])
        info(self, "\n".join(lines))
        self.accept()


# ================================================================ 历史快照
class SnapshotsDialog(ThreadSafeDialog, QDialog):
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
        if not s:
            # 快照可能在本对话框开着的时候被裁掉：`dao.add_snapshot` 每次都
            # `_prune_snapshots`（每文档只留 SNAPSHOT_KEEP=30 条），而编辑器的
            # 自动快照定时器在模态对话框期间照常触发。`get_snapshot` 返回
            # dict | None，对 None 取下标会抛 TypeError、功能直接中断。
            self._fill()
            return
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
        if not s:
            self._fill()          # 同上：已被裁剪，重画列表并放弃回滚
            return
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
class SimilarityDialog(ThreadSafeDialog, QDialog):
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
        self._run_btn = btn
        row.addWidget(btn)
        row.addStretch(1)
        v.addLayout(row)
        self.result_list = QListWidget()
        v.addWidget(self.result_list, 1)
        self.lbl = QLabel("在全部资料中找出内容高度相似的材料对。")
        v.addWidget(self.lbl)

    def _run(self):
        if len(dao.list_documents()) < 2:
            info(self, "资料库中至少需要 2 篇材料。")
            return
        from .workers import FnWorker

        # 主线程取阈值，避免工作线程读 QSpinBox（跨线程访问 QObject）
        threshold = self.sp_thresh.value() / 100

        def work():
            """后台：读正文 + 查表 SimHash 粗筛 + 精算 Jaccard。"""
            docs = {d.id: d.title for d in dao.list_documents()}
            texts = {did: dao.get_document(did).content_text for did in docs}
            pairs = simhash.find_similar(texts, threshold,
                                         hashes=dao.all_simhashes())
            return docs, pairs

        self.lbl.setText("正在比对（大库可能需要片刻）…")
        self._run_btn.setEnabled(False)
        self._worker = FnWorker(work, parent=self)
        self._worker.ok.connect(self._on_done)
        self._worker.failed.connect(lambda m: self.lbl.setText(f"查重失败：{m}"))
        self._worker.finished.connect(lambda: self._run_btn.setEnabled(True))
        self._worker.start()

    def _on_done(self, result):
        docs, pairs = result
        self.result_list.clear()
        for a, b, sim in pairs:
            t = f"相似度 {sim * 100:.0f}%：{docs[a]}  ↔  {docs[b]}"
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
        _fit_dialog(self, 640, 720)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        # 内容 sizeHint 实测需 982×798，远超 1366×768 笔记本的可用高度；
        # 不套滚动区时底部的「清理附件」「关闭」按钮会落在屏幕外且无法触及。
        # 与 receive_dialog.ReceiveForm 的写法保持一致。
        holder = QWidget()
        v = QVBoxLayout(holder)
        scroll = QScrollArea()
        scroll.setWidget(holder)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll, 1)

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
        row_bk = QHBoxLayout()
        row_bk.addWidget(QLabel("定时备份间隔（小时，0=关闭）："))
        self.sp_backup_hours = QDoubleSpinBox()
        self.sp_backup_hours.setRange(0, 168)
        self.sp_backup_hours.setDecimals(1)
        self.sp_backup_hours.setSingleStep(0.5)
        self.sp_backup_hours.setValue(
            float(dao.get_setting("backup_interval_hours", "0") or 0))
        self.sp_backup_hours.valueChanged.connect(
            lambda val: dao.set_setting("backup_interval_hours", f"{val:g}"))
        row_bk.addWidget(self.sp_backup_hours)
        row_bk.addStretch(1)
        v.addLayout(row_bk)

        # 附件体积上限：备份包从「几百 KB 的库」变成「库 + 全部附件」之后，
        # 退出时的自动备份会连带压缩全部附件，而轮转保留 20 份 → 备份目录变成
        # 20 × 全量附件，关程序越来越卡。这里就是那道闸门。
        # 超限的附件绝不静默丢弃：包内写 manifest/excluded_attachments.txt 清单、
        # 恢复时明确提醒去哪儿补、自动备份另记 logs/backup.log。
        row_lim = QHBoxLayout()
        row_lim.addWidget(QLabel("手动备份附件上限："))
        self.sp_backup_limit = self._make_limit_spin(backup_core.MODE_MANUAL)
        self.sp_backup_limit.valueChanged.connect(
            lambda val: dao.set_setting(backup_core.SETTING_LIMIT_MB, str(val)))
        row_lim.addWidget(self.sp_backup_limit)
        row_lim.addStretch(1)
        v.addLayout(row_lim)

        row_alim = QHBoxLayout()
        row_alim.addWidget(QLabel("退出/定时自动备份附件上限："))
        self.sp_auto_backup_limit = self._make_limit_spin(backup_core.MODE_AUTO)
        self.sp_auto_backup_limit.valueChanged.connect(
            lambda val: dao.set_setting(backup_core.SETTING_AUTO_LIMIT_MB, str(val)))
        row_alim.addWidget(self.sp_auto_backup_limit)
        row_alim.addStretch(1)
        v.addLayout(row_alim)

        from ..core import attachments as att_core
        atts = dao.list_all_attachments()
        total = sum(int(a.size or 0) for a in atts)
        self.lbl_backup_limit = QLabel(
            f"当前附件共 {len(atts)} 个 / {att_core.human_size(total)}。"
            "自动备份只是防丢失的安全网，上限设小一些退出才不卡；"
            "要换电脑迁移请用「备份…」（手动备份），必要时把手动上限设为不限制。")
        self.lbl_backup_limit.setWordWrap(True)
        self.lbl_backup_limit.setStyleSheet(f"color:{theme.MUTED};")
        v.addWidget(self.lbl_backup_limit)

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

        g4 = QLabel("外观")
        g4.setStyleSheet("font-weight:bold;margin-top:8pt;")
        v.addWidget(g4)
        self.chk_dark = QCheckBox("跟随系统深浅色（重启生效）")
        self.chk_dark.setChecked(dao.get_setting("follow_system_theme", "0") == "1")
        self.chk_dark.toggled.connect(
            lambda on: dao.set_setting("follow_system_theme", "1" if on else "0"))
        v.addWidget(self.chk_dark)

        g5 = QLabel("朗读校对（语速/音色）")
        g5.setStyleSheet("font-weight:bold;margin-top:8pt;")
        v.addWidget(g5)
        self.sp_tts_rate = QSpinBox()
        self.sp_tts_rate.setRange(-10, 10)
        self.sp_tts_rate.setValue(int(dao.get_setting("tts_rate", "0") or 0))
        self.sp_tts_rate.setPrefix("语速 ")
        self.sp_tts_rate.setToolTip("SAPI 语速等级（-10 最慢，10 最快）")
        self.sp_tts_rate.valueChanged.connect(
            lambda val: dao.set_setting("tts_rate", str(val)))
        row_tts = QHBoxLayout()
        row_tts.addWidget(self.sp_tts_rate)
        self.cmb_tts_voice = QComboBox()
        from ..core.tts import list_voices
        voices = list_voices()
        if voices:
            self.cmb_tts_voice.addItem("系统默认", "")
            cur = dao.get_setting("tts_voice", "")
            idx = 0
            for i, desc in enumerate(voices):
                self.cmb_tts_voice.addItem(desc, desc)
                if cur and cur in desc:
                    idx = i + 1
            self.cmb_tts_voice.setCurrentIndex(idx)
            self.cmb_tts_voice.currentIndexChanged.connect(
                lambda: dao.set_setting("tts_voice",
                                        self.cmb_tts_voice.currentData() or ""))
        else:
            self.cmb_tts_voice.addItem("跟随系统默认（无可选音色）", "")
            self.cmb_tts_voice.setEnabled(False)
        row_tts.addWidget(self.cmb_tts_voice, 1)
        v.addLayout(row_tts)

        g6 = QLabel("文字纠错 —— 精度增强（可选）")
        g6.setStyleSheet("font-weight:bold;margin-top:8pt;")
        v.addWidget(g6)
        self.lbl_neural_help = QLabel(
            "纠错默认走「精确词表 → 上下文规则 → 词边界保护」三级流水线，"
            "完全离线、零依赖。如需更高精度，可额外导入神经网络模型"
            "（拼写与语法各自独立，可只装其一，也可两个都装）：\n"
            "· 模型不随主包分发（动辄上百 MB），须在一台有网机器上取得 .zip 增强包后"
            "拷到本机、在此**离线导入**；\n"
            "· 需要在系统里预先安装 onnxruntime（见 requirements-optional.txt）；"
            "语法纠错包还需要 tokenizers；\n"
            "· 未导入或未开启时，纠错行为与之前**完全一致**，也不会变慢；\n"
            "· 增强包必须携带许可证标识（面向党政机关，来源须可追溯）。")
        self.lbl_neural_help.setWordWrap(True)
        self.lbl_neural_help.setStyleSheet(f"color:{theme.MUTED};")
        v.addWidget(self.lbl_neural_help)

        # ---- 拼写纠错包（csc，第四级） ----
        v.addWidget(self._build_pack_group(
            kind="csc",
            title="拼写纠错包（第四级精排）",
            checkbox_text="启用神经网络精排（作为第四级，仅补充前三级未覆盖处）",
        ))

        # ---- 语法纠错包（cgec，第五级） ----
        v.addWidget(self._build_pack_group(
            kind="cgec",
            title="语法纠错包（第五级，Seq2Seq）",
            checkbox_text="启用语法纠错（作为第五级，可发现增字/删字/语序类语病）",
        ))

        self._refresh_pack_groups()

        g7 = QLabel("数据维护")
        g7.setStyleSheet("font-weight:bold;margin-top:8pt;")
        v.addWidget(g7)
        self.lbl_fts = QLabel("全文检索异常（搜不到已导入材料）时可重建索引。")
        self.lbl_fts.setStyleSheet(f"color:{theme.MUTED};")
        v.addWidget(self.lbl_fts)
        btn_fts = QPushButton("重建全文索引")
        btn_fts.clicked.connect(self._rebuild_fts)
        v.addWidget(btn_fts, 0, Qt.AlignLeft)
        self.lbl_attach = QLabel(
            "彻底删除材料时若附件正被其他程序占用，数据目录里会留下无人引用的"
            "附件文件，可在此清理。")
        self.lbl_attach.setWordWrap(True)
        self.lbl_attach.setStyleSheet(f"color:{theme.MUTED};")
        v.addWidget(self.lbl_attach)
        btn_sweep = QPushButton("清理无引用的附件文件")
        btn_sweep.clicked.connect(self._sweep_attachments)
        v.addWidget(btn_sweep, 0, Qt.AlignLeft)

        v.addStretch(1)
        # 「关闭」放在滚动区**外**：内容再长也始终可见，不必滚到底才能关窗。
        btn_bar = QHBoxLayout()
        btn_bar.addStretch(1)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        btn_bar.addWidget(btn_close)
        outer.addLayout(btn_bar)
        self._check_tess()

    @staticmethod
    def _make_limit_spin(mode: str) -> QSpinBox:
        """附件体积上限输入框（手动/自动共用），值取自 settings。"""
        sp = QSpinBox()
        sp.setRange(-1, 1024 * 1024)          # 最小值 -1 显示为「不限制」
        sp.setSuffix(" MB")
        sp.setSingleStep(10)
        sp.setSpecialValueText("不限制（全部附件随包）")
        sp.setToolTip("单个备份包里附件总体积的上限。\n"
                      "超出的附件不会静默丢弃：备份包内会写下缺失清单，\n"
                      "恢复该备份时程序会明确提醒哪些附件不在包里、去哪儿补；\n"
                      "自动备份还会记一条 logs/backup.log。\n"
                      "0 = 完全不打包附件；不限制 = 全部随包（包很大、退出会变慢）。")
        sp.setValue(backup_core.attachment_limit_mb(mode))
        return sp

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

    # ------------------------------------------------ 文字纠错：精度增强包（L4/L5）
    def _build_pack_group(self, kind: str, title: str, checkbox_text: str) -> QWidget:
        """构造一个增强包分组（导入 / 卸载 / 开关 / 状态）。

        kind 取 "csc"（拼写，第四级）或 "cgec"（语法，第五级）。
        两组靠 `_pack_ui` 字典各自持有控件，逻辑共用本类的方法。
        """
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 6, 0, 0)

        lbl = QLabel(title)
        lbl.setStyleSheet("font-weight:bold;")
        v.addWidget(lbl)

        chk = QCheckBox(checkbox_text)
        chk.toggled.connect(lambda on, k=kind: self._on_pack_toggled(k, on))
        v.addWidget(chk)

        state = QLabel("")
        state.setWordWrap(True)
        state.setStyleSheet(f"color:{theme.MUTED};")
        v.addWidget(state)

        row = QHBoxLayout()
        btn_imp = QPushButton("导入增强包…")
        btn_imp.clicked.connect(lambda _=False, k=kind: self._import_pack(k))
        btn_rm = QPushButton("卸载增强包")
        btn_rm.clicked.connect(lambda _=False, k=kind: self._remove_pack(k))
        row.addWidget(btn_imp)
        row.addWidget(btn_rm)
        row.addStretch(1)
        v.addLayout(row)

        if not hasattr(self, "_pack_ui"):
            self._pack_ui: dict = {}
        self._pack_ui[kind] = {
            "chk": chk, "state": state, "import": btn_imp, "remove": btn_rm,
        }
        return box

    def _pack_modules(self):
        from ..core import csc_gec, csc_neural
        return {"csc": csc_neural, "cgec": csc_gec}

    def _refresh_pack_groups(self):
        for kind in ("csc", "cgec"):
            self._refresh_pack_group(kind)

    def _refresh_pack_group(self, kind: str):
        """刷新某个分组的开关可用性与状态文案。"""
        from ..core import enhance_pack
        ui = getattr(self, "_pack_ui", {}).get(kind)
        mod = self._pack_modules().get(kind)
        if ui is None or mod is None:
            return
        ui["syncing"] = True
        try:
            ui["chk"].setChecked(
                str(dao.get_setting(mod.SETTING_KEY, "0")).strip() == "1")
        finally:
            ui["syncing"] = False
        ready = mod.available()
        ui["chk"].setEnabled(ready)
        ui["chk"].setToolTip("" if ready else "尚未就绪：" + mod.status_text())
        ui["state"].setText("状态：" + mod.status_text())
        installed = enhance_pack.installed_pack(kind)
        ui["remove"].setEnabled(installed is not None)

    def _on_pack_toggled(self, kind: str, on: bool):
        ui = getattr(self, "_pack_ui", {}).get(kind) or {}
        if ui.get("syncing"):
            return                      # 程序回填勾选态时不落库
        mod = self._pack_modules().get(kind)
        if mod is None:
            return
        try:
            saved = mod.set_setting(bool(on))
            mod.reset()                 # 下次纠错时按需（重新）加载
        except Exception as exc:
            warn(self, f"保存设置失败：{exc}")
        else:
            if not saved:
                # 本次会话已生效，但没写进库 → 必须说清"重启后会变回去"
                warn(self, "开关已在本次运行中生效，但**未能保存到数据库**，"
                           "重启程序后会恢复为原来的设置。\n\n"
                           "请检查数据目录是否可写、磁盘是否已满。")
        self._refresh_pack_group(kind)

    def _import_pack(self, kind: str = "csc"):
        from ..core import enhance_pack
        mod = self._pack_modules().get(kind)
        if mod is None:
            return
        label = "拼写纠错" if kind == "csc" else "语法纠错"
        path, _ = QFileDialog.getOpenFileName(
            self, f"选择{label}增强包", "", "增强包 (*.zip)")
        if not path:
            return
        if not ask(self, f"导入将以新{label}包替换现有同类型包（如有）。\n"
                         "增强包须离线取得、携带许可证标识，导入时会做完整性校验。\n"
                         "确定导入？"):
            return
        try:
            pack = enhance_pack.install_pack(path, kind=kind)
        except enhance_pack.EnhancePackError as exc:
            warn(self, f"导入失败：{exc}")
            return
        except Exception as exc:
            warn(self, f"导入失败：{exc}")
            return
        mod.reset()
        try:
            mod.set_setting(True)      # 导入成功即默认开启，用户可随时关
        except Exception as exc:
            # 不打断导入流程（包已就位），但"自动开启"没存下来要说出口：
            # 否则用户下次启动发现开关又关了，会以为导入没生效。
            log.warning("增强包导入后未能自动开启（%s）：%s", kind, exc)
        self._refresh_pack_group(kind)
        info(self, f"已导入{label}增强包：\n{pack.summary()}\n\n"
                   + self._pack_post_import_hint(mod))

    @staticmethod
    def _pack_post_import_hint(mod) -> str:
        """导入成功后按**真实运行时能力**给提示，不再无条件说"已开启该层"。

        老实现不管运行时就绪与否都回一句"已为你开启该层"：而同一对话框里的
        复选框却是灰的、状态写着"未安装 onnxruntime"——在一个完全离线的产品里
        这构成死循环（用户被告知已开启，却无法让开关变绿、也找不到出网安装的途径）。
        这里以 `mod.available()`（运行时 + 包都就绪）为准，不就绪时明确说"尚未启用"
        并给出可执行的下一步。
        """
        try:
            ready = bool(mod.available())
            status = mod.status_text()
        except Exception:
            return "增强包已安装。请在上方状态行确认本层是否已就绪后再使用。"
        if ready:
            return ("已为你开启该层：下次纠错时会作为补充层自动生效。"
                    "（如需停用，取消上方勾选即可。）")
        return (f"增强包已安装，但**当前尚未真正启用**。原因：{status}\n\n"
                "下一步怎么做（任选其一即可）：\n"
                "  1) 若本程序已随包附带所需运行时，重启一次本程序后本层即可勾选启用；\n"
                "  2) 若确缺少运行库，请在一台有网机器上按 requirements-optional.txt "
                "取得对应的离线安装包（wheel），拷到本机安装后再回到此界面；\n"
                "  3) 运行时补齐后，重新勾选本组的启用开关即可生效。")

    def _remove_pack(self, kind: str = "csc"):
        from ..core import enhance_pack
        mod = self._pack_modules().get(kind)
        if mod is None:
            return
        label = "拼写纠错" if kind == "csc" else "语法纠错"
        if not ask(self, f"卸载当前{label}增强包？\n"
                         "卸载后该层停用，其余纠错功能不受影响。"):
            return
        removed = enhance_pack.remove_pack(kind)
        mod.reset()
        try:
            mod.set_setting(False)
        except Exception as exc:
            log.warning("增强包卸载后未能关闭开关（%s）：%s", kind, exc)
        self._refresh_pack_group(kind)
        info(self, f"已卸载{label}增强包。" if removed
             else f"当前没有已安装的{label}增强包。")

    def _rebuild_fts(self):
        """重建全文索引（后台线程 + 阶段进度）。

        老实现三处问题，一并在这里收口：
        1. `dao.rebuild_fts()` 在主线程直调：万篇库要逐条分词，界面整窗冻结；
        2. **整段没有 try/except**：失败时点按钮零提示，用户只知道"点了没反应"
           （同文件的 `_sweep_attachments` 一直是有 try/warn 的）；
        3. 重建结果为 0 条时静默显示"索引已重建"—— 与"索引坏了"无从区分。
        """
        if not ask(self, "重建全文索引可能需要片刻（与资料库大小相关），继续？"):
            return
        from ..db import dao as dao_mod
        from .workers import FnWorker

        worker = getattr(self, "_fts_worker", None)
        if worker is not None and worker.isRunning():
            warn(self, "重建仍在进行，请稍候。")
            return
        self.lbl_fts.setText("正在重建全文索引…")
        w = FnWorker(dao_mod.rebuild_fts, parent=self, want_progress=True)
        w.progress.connect(self.lbl_fts.setText)
        w.ok.connect(self._rebuild_fts_done)
        w.failed.connect(self._rebuild_fts_failed)
        self._fts_worker = w
        w.start()

    def _rebuild_fts_done(self, counts):
        docs = int((counts or {}).get("documents", 0) or 0)
        phrases = int((counts or {}).get("phrases", 0) or 0)
        self.lbl_fts.setText(f"索引已重建：资料 {docs} 篇、句式 {phrases} 条。")
        if docs == 0 and phrases == 0:
            # "重建成功"但一条都没有：必须说清是资料库本来就是空的，
            # 否则用户会把"检索不到任何东西"当成检索功能坏了。
            warn(self, "索引已重建，但当前**没有可索引的内容**"
                       "（资料 0 篇、句式 0 条）。\n\n"
                       "如果资料库并非空的，请生成诊断包反馈；"
                       "否则请先通过「导入材料」把公文入库。")

    def _rebuild_fts_failed(self, msg: str):
        self.lbl_fts.setText("索引重建未完成")
        log.warning("重建全文索引失败：%s", msg)
        warn(self, f"重建索引失败：{msg}")

    def _sweep_attachments(self):
        """清掉彻底删除材料时残留（文件被占用没删掉）的孤儿附件。"""
        if not ask(self, "清理数据目录里已没有任何材料引用的附件文件？\n"
                         "在用的附件不受影响。"):
            return
        from ..core import attachments
        try:
            n = attachments.sweep_orphans()
        except Exception as exc:
            warn(self, f"清理失败：{exc}")
            return
        self.lbl_attach.setText(
            f"已清理 {n} 个无引用的附件文件。" if n else "没有需要清理的附件文件。")
        info(self, f"已清理 {n} 个无引用的附件文件。" if n
             else "没有需要清理的附件文件。")

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
        self.lbl.setStyleSheet(f"color:{theme.DANGER};")
        v.addWidget(self.lbl)
        self.btn = QPushButton("解锁")
        self.btn.clicked.connect(self._try)
        v.addWidget(self.btn)
        self._fail_count = 0
        self._lock_until_timer = QTimer(self)
        self._lock_until_timer.setSingleShot(True)
        self._lock_until_timer.timeout.connect(self._unlock_input)

    def _try(self):
        from ..core.security import verify_password
        if self.btn.isEnabled() is False:
            return                              # 罚时期间忽略连点/回车
        if verify_password(self.ed_pw.text()):
            self.accept()
            return
        self._fail_count += 1
        self.lbl.setText(f"口令错误（已失败 {self._fail_count} 次）")
        if self._fail_count >= 5:
            # 原实现用嵌套 QEventLoop 阻塞 10 秒：模态框内仍可继续点击，
            # 递归 exec() 会互相干扰。改为禁用输入 + 定时恢复。
            self._fail_count = 0
            self.lbl.setText("失败次数过多，请等待 10 秒后重试…")
            self.ed_pw.setEnabled(False)
            self.btn.setEnabled(False)
            self._lock_until_timer.start(10000)

    def _unlock_input(self):
        self.ed_pw.setEnabled(True)
        self.btn.setEnabled(True)
        self.ed_pw.setFocus()
        self.lbl.setText("")


# ================================================================ 段落参考（v7）
class AlignDialog(QDialog):
    """内容对齐：参考清单 ↔ 当前草稿。

    **只提示，不自动改草稿**。对齐的价值在于"让你看见结构差异"——缺了哪一节、
    序号跳号、章节顺序颠倒。任何自动插入或移动都会让用户失去对自己文档的掌控，
    而这恰恰是公文写作里最不能开的口子。
    """

    def __init__(self, refs, draft_text: str, parent=None):
        super().__init__(parent)
        from ..core import paragraph_ref
        self._refs = list(refs or [])
        self._draft_text = draft_text or ""
        self._pr = paragraph_ref
        self.setWindowTitle("内容对齐（参考清单 ↔ 当前草稿）")
        _fit_dialog(self, 980, 620)
        v = QVBoxLayout(self)

        head = QLabel(
            f"参考段落 {len(self._refs)} 条；只比较**结构与角色**，不比较语义，"
            f"也不会改动你的草稿。")
        head.setWordWrap(True)
        head.setStyleSheet(f"color:{theme.MUTED};")
        v.addWidget(head)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("参考段落"))
        self.lst_ref = QListWidget()
        lv.addWidget(self.lst_ref, 1)
        split.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(QLabel("当前草稿段落"))
        self.lst_draft = QListWidget()
        rv.addWidget(self.lst_draft, 1)
        split.addWidget(right)

        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        v.addWidget(split, 2)

        v.addWidget(QLabel("差异与提示"))
        self.lst_diff = QListWidget()
        v.addWidget(self.lst_diff, 2)

        self.lbl_sum = QLabel("")
        self.lbl_sum.setWordWrap(True)
        v.addWidget(self.lbl_sum)

        btns = QHBoxLayout()
        btn_re = QPushButton("重新对齐")
        btn_re.clicked.connect(self.run)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.reject)
        btns.addStretch(1)
        btns.addWidget(btn_re)
        btns.addWidget(btn_close)
        v.addLayout(btns)
        self.run()

    def run(self):
        """执行一次对齐并刷新三个列表。任何异常都降级为"提示文本"，不抛。"""
        try:
            draft = self._pr.split_draft(self._draft_text)
            aligns = self._pr.align_to_draft(self._draft_text, self._refs)
        except Exception as exc:
            self.lbl_sum.setText(errmsg.friendly(exc, action="内容对齐"))
            return
        self.lst_ref.clear()
        for r in self._refs:
            self.lst_ref.addItem(
                f"第 {r.ordinal + 1} 段 · {r.doc_title}\n    {r.text[:70]}")
        self.lst_draft.clear()
        for d in draft:
            self.lst_draft.addItem(f"第 {d.ordinal + 1} 段\n    {d.text[:70]}")

        self.lst_diff.clear()
        counts = {}
        for a in aligns:
            counts[a.relation] = counts.get(a.relation, 0) + 1
            if a.relation == "matched":
                continue              # 已对齐的不占列表；只看需要关注的
            item = QListWidgetItem(self._mark(a) + a.hint)
            item.setForeground(QColor(theme.severity_color(
                "error" if a.relation == "missing" else "warn")))
            self.lst_diff.addItem(item)
        self.lbl_sum.setText(
            f"已对齐 {counts.get('matched', 0)} 项；"
            f"缺失 {counts.get('missing', 0)} 项、"
            f"多余 {counts.get('extra', 0)} 项、"
            f"层级/顺序 {counts.get('level_mismatch', 0) + counts.get('order_swap', 0)} 项。"
            f"提示仅供参照，需由你决定是否调整。")

    @staticmethod
    def _mark(a) -> str:
        return {"missing": "【缺】", "extra": "【多】",
                "level_mismatch": "【层级/序号】", "order_swap": "【顺序】"}.get(
            a.relation, "【提示】")


class SkeletonFromRefsDialog(QDialog):
    """从参考段落派生骨架 → 填槽 → 生成草稿（L3）。

    **这不是"AI 代笔"**。系统做的是：把参考段落里能确定识别出的具体值
    （单位名、事项名、日期、时限、数量、引用公文）抽象成槽位，由**用户**
    填写，再拼回骨架。系统不产出任何未被用户或参考原文提供的语义内容 ——
    对话框顶部那句话是刻意写给人看的，避免造成"自动成文"的误解。
    """

    def __init__(self, refs, parent=None):
        super().__init__(parent)
        from ..core import paragraph_ref
        from ..core import skeletons as sk
        self._pr = paragraph_ref
        self._refs = list(refs or [])
        self._saved = False
        self.setWindowTitle("从参考段落生成草稿（结构复用 + 槽位填充）")
        _fit_dialog(self, 980, 660)
        v = QVBoxLayout(self)

        head = QLabel(
            "本功能**只复用参考文献的结构骨架**：系统把参考段落里识别到的具体值"
            "抽成槽位（{org}/{matter}/{date} 等），由你填写后拼回骨架。"
            "系统不会自动撰写内容，也不编造任何未提供的信息。")
        head.setWordWrap(True)
        head.setStyleSheet(f"color:{theme.MUTED};")
        v.addWidget(head)

        row = QHBoxLayout()
        row.addWidget(QLabel("骨架名称："))
        self.ed_name = QLineEdit("从参考派生")
        row.addWidget(self.ed_name, 1)
        row.addWidget(QLabel("文种："))
        self.cmb_kind = QComboBox()
        self.cmb_kind.addItem("（不限）", "")
        # 必须逐项显式带 userData：`QComboBox.addItems` 只填文本、**不设 userData**，
        # 于是 currentData() 恒为 None —— 用户明明选了文种，取到的却是空串
        # （实测 itemData(1) is None），派生与落库的文种都会静默丢失。
        for _kind in sk.kinds():
            self.cmb_kind.addItem(_kind, _kind)
        row.addWidget(self.cmb_kind, 1)
        self.cmb_saved = QComboBox()
        self.cmb_saved.setToolTip("已保存的骨架；选中后点「载入」")
        row.addWidget(self.cmb_saved, 1)
        btn_load = QPushButton("载入")
        btn_load.clicked.connect(self._load_saved)
        row.addWidget(btn_load)
        v.addLayout(row)

        v.addWidget(QLabel("骨架模板（可编辑；{名称} 为槽位占位符）"))
        self.ed_tpl = QPlainTextEdit()
        v.addWidget(self.ed_tpl, 2)
        self.ed_tpl.textChanged.connect(self._rebuild_slots)

        v.addWidget(QLabel("槽位（留空则保留 {占位符}，不会静默清空）"))
        self.slot_host = QWidget()
        self.slot_form = QVBoxLayout(self.slot_host)
        self.slot_form.setContentsMargins(0, 0, 0, 0)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(self.slot_host)
        v.addWidget(area, 1)
        self._slot_edits: dict[str, QLineEdit] = {}

        v.addWidget(QLabel("生成预览"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        v.addWidget(self.preview, 2)

        btns = QHBoxLayout()
        self.btn_save = QPushButton("保存为骨架")
        self.btn_save.clicked.connect(self._save)
        btn_gen = QPushButton("生成到编辑器")
        btn_gen.clicked.connect(self._generate)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.reject)
        btns.addStretch(1)
        btns.addWidget(self.btn_save)
        btns.addWidget(btn_gen)
        btns.addWidget(btn_close)
        v.addLayout(btns)

        self._skeleton = self._pr.derive_skeleton(
            self._refs, kind=self.cmb_kind.currentData() or "")
        self._pending_paras = ",".join(str(p) for p in self._skeleton.source_paras)
        # 槽位示例（参考原值）：模板每次被编辑都要重建表单，而重建是从模板
        # 文本反推槽位的，示例必须在此留存，否则 placeholder 只剩"请填写"。
        self._slot_examples: dict[str, str] = {
            s.name: s.example for s in self._skeleton.slots if s.example}
        self.ed_tpl.blockSignals(True)
        self.ed_tpl.setPlainText(self._skeleton.template)
        self.ed_tpl.blockSignals(False)
        self._refresh_saved_combo()
        self._rebuild_slots()

    # -------------------------------------------------- 槽位表单
    def _rebuild_slots(self):
        """按模板里的 {占位符} 重建表单，**保留用户已填的值**。

        保留是必须的：用户在"改模板"（比如删掉一段）时，已填好的槽位值
        不该被清空——那会让每一次微调模板都变成一次重填。
        """
        kept = {k: e.text() for k, e in self._slot_edits.items()}
        while self.slot_form.count():
            item = self.slot_form.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._slot_edits = {}

        tpl = self.ed_tpl.toPlainText()
        src = [int(x) for x in (self._pending_paras or "").split(",")
               if x.strip().isdigit()]
        skel = self._pr.skeleton_from_template(
            tpl, source_paras=src, examples=self._slot_examples)
        self._skeleton = skel
        # 新出现的槽位若带着示例（模板里手动加了 {org} 且此前抽到过），一并记下
        for s in skel.slots:
            if s.example and not self._slot_examples.get(s.name):
                self._slot_examples[s.name] = s.example
        if not skel.slots:
            self.slot_form.addWidget(QLabel("该模板没有槽位（纯结构复用）。"))
        for s in skel.slots:
            r = QHBoxLayout()
            label = f"{s.name}"
            if s.label:
                label += f"（{s.label}）"
            r.addWidget(QLabel(label + "："))
            ed = QLineEdit(kept.get(s.name, s.example or ""))
            ed.setPlaceholderText(s.example or "请填写")
            ed.textChanged.connect(self._refresh_preview)
            r.addWidget(ed, 1)
            self._slot_edits[s.name] = ed
            holder = QWidget()
            holder.setLayout(r)
            self.slot_form.addWidget(holder)
        self.slot_form.addStretch(1)
        self._refresh_preview()

    def _values(self) -> dict:
        return {k: e.text() for k, e in self._slot_edits.items()}

    def _refresh_preview(self):
        try:
            self.preview.setPlainText(
                self._pr.render_draft(self.ed_tpl.toPlainText(), self._values()))
        except Exception as exc:
            self.preview.setPlainText(errmsg.friendly(exc, action="生成预览"))

    # -------------------------------------------------- 存 / 生成
    def _save(self):
        name = self.ed_name.text().strip()
        if not name:
            warn(self, "请先填写骨架名称。")
            return
        try:
            dao.save_user_skeleton(
                name=name,
                kind=self.cmb_kind.currentData() or "",
                template=self.ed_tpl.toPlainText(),
                slots_json=self._pr.to_json_slots(self._skeleton.slots),
                source_paras=",".join(str(p) for p in self._skeleton.source_paras),
                note=f"从 {len(self._refs)} 条参考段落派生")
            self._saved = True
            self._refresh_saved_combo()
            info(self, f"已保存骨架「{name}」，下次可在此「载入」。")
        except Exception as exc:
            warn(self, errmsg.friendly(exc, action="保存骨架"))

    def _load_saved(self):
        """载入已保存骨架。

        用下拉框而不是 `QInputDialog.getItem`：模态静态方法在**未 mock 的测试**
        里会真弹框把整个用例挂死（无报错、日志不增），本项目为此付出过代价。
        下拉框是普通控件，不引入新的模态入口。
        """
        items = self._saved_skeletons()
        if not items:
            info(self, "还没有保存过骨架。生成后可点「保存为骨架」。")
            return
        idx = self.cmb_saved.currentIndex()
        if idx < 0 or idx >= len(items):
            warn(self, "请先在下拉框中选择一个已保存的骨架。")
            return
        s = items[idx]
        self.ed_name.setText(s.name)
        # 文种必须一起回填：不回填的话，"载入后微调再保存"会按当前下拉框的
        # 值（多为「不限」）把 kind 静默覆盖成空串，文种标签再也找不回来。
        kidx = self.cmb_kind.findData(s.kind or "")
        self.cmb_kind.setCurrentIndex(kidx if kidx >= 0 else 0)
        self._pending_paras = s.source_paras or ""
        self.ed_tpl.setPlainText(s.template or "")
        # 示例随骨架走：载入的应是**这条**骨架自己存的示例，而不是上一次的
        self._slot_examples = {
            x.name: x.example
            for x in self._pr.from_json_slots(s.slots_json or "") if x.example}
        self._rebuild_slots()

    def _saved_skeletons(self) -> list:
        try:
            return dao.list_user_skeletons()
        except Exception:
            return []

    def _refresh_saved_combo(self):
        self.cmb_saved.clear()
        for s in self._saved_skeletons():
            self.cmb_saved.addItem(f"{s.name}（{s.kind or '不限'}）")

    def _generate(self):
        text = self.preview.toPlainText()
        if not text.strip():
            warn(self, "生成结果为空，请检查骨架模板。")
            return
        self.accept()

    def text(self) -> str:
        return self.preview.toPlainText()

    def saved(self) -> bool:
        return self._saved


# ================================================================ 公文风格校验
class StyleCheckDialog(QDialog):
    """公文风格校验：把写作风格量成指标，对照文体指纹做三层判定。

    **三层，只有第一层算错**：硬冲突（文种识别错误）判错；软提示只提醒；
    参数对照只展示。理由见 `core/style_profile` 模块头 —— 真实优秀作品个体
    差异极大，拿均值当合格线会把好作品判成不合格。

    右栏同时给出该文体的**骨架公式**与写作约束（L3：结构复用），
    可直接填入编辑器 —— 填入的是**带【待补】占位**的结构，不是成文内容。
    """

    def __init__(self, text_getter, parent=None, insert_cb=None):
        super().__init__(parent)
        from ..core import style_data as st_data
        from ..core import style_profile as st_prof
        self._st = st_data
        self._sp = st_prof
        self._getter = text_getter
        self._insert_cb = insert_cb
        self.setWindowTitle("公文风格校验（量化指标 · 对照文体指纹）")
        _fit_dialog(self, 1080, 660)
        v = QVBoxLayout(self)

        head = QLabel(
            "把写作风格量成指标，再对照该文体的语料指纹判断「像不像这个文种」。"
            "判定分三层：**硬冲突**（文种识别错误）判错，**软提示**只提醒，"
            "**参数对照**只展示 —— 区间外不等于错：好文章不整齐，整齐的往往是平庸作品。")
        head.setWordWrap(True)
        head.setStyleSheet(f"color:{theme.MUTED};")
        v.addWidget(head)

        row = QHBoxLayout()
        row.addWidget(QLabel("文种："))
        self.cmb_style = QComboBox()
        for s in st_data.STYLES:
            fam = st_data.FAMILY_OF.get(s, "")
            self.cmb_style.addItem(f"{s}（{fam}）", s)
        self.cmb_style.currentIndexChanged.connect(self.run)
        row.addWidget(self.cmb_style, 1)
        btn_check = QPushButton("校验当前文本")
        btn_check.clicked.connect(self.run)
        row.addWidget(btn_check)
        self.lbl_stat = QLabel("")
        row.addWidget(self.lbl_stat, 1)
        v.addLayout(row)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("校验报告（硬冲突 → 软提示 → 参数对照 → 修辞 → 避坑词）"))
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        lv.addWidget(self.report, 1)
        split.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(QLabel("骨架公式与写作约束（结构复用，不含成文内容）"))
        self.skeleton = QPlainTextEdit()
        self.skeleton.setReadOnly(True)
        rv.addWidget(self.skeleton, 1)
        brow = QHBoxLayout()
        self.btn_insert = QPushButton("把骨架填入编辑器")
        self.btn_insert.setToolTip("填入结构占位（【待补】），不生成任何成文内容")
        self.btn_insert.clicked.connect(self._insert_skeleton)
        brow.addWidget(self.btn_insert)
        brow.addStretch(1)
        rv.addLayout(brow)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        v.addWidget(split, 1)

        btns = QHBoxLayout()
        btn_save = QPushButton("保存报告…")
        btn_save.clicked.connect(self._save)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.reject)
        btns.addStretch(1)
        btns.addWidget(btn_save)
        btns.addWidget(btn_close)
        v.addLayout(btns)
        self.run()

    def style(self) -> str:
        return self.cmb_style.currentData() or self._st.STYLES[0]

    def run(self):
        """执行一次校验并刷新两栏。**任何异常都降级为提示文本，不向外抛**。"""
        style = self.style()
        try:
            text = self._getter() or ""
        except Exception:
            text = ""
        try:
            verdict = self._sp.verdict(text, style)
        except Exception as exc:
            self.report.setPlainText(errmsg.friendly(exc, action="风格校验"))
            return
        self.report.setPlainText("\n".join(self._sp.report_lines(verdict)))
        try:
            self.skeleton.setPlainText(self._sp.skeleton_draft(style))
        except Exception as exc:
            self.skeleton.setPlainText(errmsg.friendly(exc, action="骨架生成"))
        self.lbl_stat.setText(
            ("✓ 通过硬冲突检查" if verdict.ok
             else f"✗ 硬冲突 {len(verdict.hard)} 项（判为文种识别错误）")
            + f"；软提示 {len(verdict.soft)} 项")
        self.btn_insert.setEnabled(bool(self._insert_cb))

    def _insert_skeleton(self):
        if not self._insert_cb:
            return
        text = self.skeleton.toPlainText()
        if not text.strip():
            return
        try:
            self._insert_cb(text)
        except Exception as exc:
            warn(self, errmsg.friendly(exc, action="填入骨架"))

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存风格校验报告", "风格校验报告.txt", "文本文件 (*.txt)")
        if not path:
            return
        try:
            Path(path).write_text(self.report.toPlainText(), encoding="utf-8")
            info(self, f"已保存：{path}")
        except OSError as exc:
            warn(self, errmsg.friendly(exc, action="保存报告"))


# ================================================================ 写作提示与灵感
class WritingHintsDialog(QDialog):
    """写作提示与灵感建议：**只给建议，不改正文**。

    三个能力：
      ① **写作提示** —— 对照文种骨架指出缺失要素、结构问题、文风偏离；
      ② **文种识别** —— 复用风格量化指纹回答"这篇更像哪个文种"；
      ③ **灵感建议** —— 离线句式库（按写作环节分组）＋ 资料库/词典可借鉴写法。

    与「文字纠错」的边界（结构性守住的约束）：本对话框**不提供任何写入编辑器
    的入口**。纠错命中是错误、可自动替换；写作建议不是错误、只能由人决定是否
    采用。需要套用句式时用「复制选中建议」自行粘贴。
    """

    def __init__(self, text_getter, parent=None, kinds=None, topic: str = ""):
        super().__init__(parent)
        from ..core import writing_hints as wh
        self._wh = wh
        self._getter = text_getter
        self.setWindowTitle("写作提示与灵感建议（只给建议，不改正文）")
        _fit_dialog(self, 1080, 660)
        v = QVBoxLayout(self)

        head = QLabel(
            "写作辅助与文字纠错是两件事：纠错指出「写错了」，可以自动改；"
            "这里指出「还缺什么、可以怎么写」，**只作建议，绝不改动正文**。"
            "灵感来自内置公文句式库与本地资料库，全程离线。")
        head.setWordWrap(True)
        head.setStyleSheet(f"color:{theme.MUTED};")
        v.addWidget(head)

        row = QHBoxLayout()
        row.addWidget(QLabel("文种："))
        self.cmb_kind = QComboBox()
        self.cmb_kind.addItem("不限", "")
        for k in (kinds if kinds is not None else wh.available_kinds()):
            self.cmb_kind.addItem(k, k)
        row.addWidget(self.cmb_kind, 1)
        row.addWidget(QLabel("主题/关键词："))
        self.ed_topic = QLineEdit(topic)
        self.ed_topic.setPlaceholderText("填写后可检索本地资料库中的可借鉴写法（可留空）")
        row.addWidget(self.ed_topic, 2)
        btn_hint = QPushButton("生成写作提示")
        btn_hint.clicked.connect(self.run_hints)
        row.addWidget(btn_hint)
        btn_idea = QPushButton("获取灵感建议")
        btn_idea.clicked.connect(self.run_ideas)
        row.addWidget(btn_idea)
        v.addLayout(row)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("写作提示（结构 / 要素 / 衔接 / 文风）"))
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        lv.addWidget(self.report, 1)
        split.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(QLabel("灵感建议（句式库 / 本地资料）"))
        self.ideas = QListWidget()
        rv.addWidget(self.ideas, 1)
        irow = QHBoxLayout()
        btn_copy = QPushButton("复制选中建议")
        btn_copy.setToolTip("只复制到剪贴板，由你决定是否使用（本对话框不写入正文）")
        btn_copy.clicked.connect(self._copy_idea)
        irow.addWidget(btn_copy)
        irow.addStretch(1)
        rv.addLayout(irow)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        v.addWidget(split, 1)

        btns = QHBoxLayout()
        self.lbl_stat = QLabel("")
        btns.addWidget(self.lbl_stat, 1)
        btn_save = QPushButton("保存提示…")
        btn_save.clicked.connect(self._save)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.reject)
        btns.addWidget(btn_save)
        btns.addWidget(btn_close)
        v.addLayout(btns)
        self.run_hints()
        self.run_ideas()

    # ------------------------------------------------------------ 取文本
    def kind(self) -> str:
        return self.cmb_kind.currentData() or ""

    def _text(self) -> str:
        try:
            return self._getter() or ""
        except Exception:
            return ""

    # ------------------------------------------------------------ 提示
    def run_hints(self):
        """生成写作提示。**任何异常都降级为提示文本，绝不向外抛。**"""
        text = self._text()
        try:
            hs = self._wh.hints(text, self.kind())
            lines = self._wh.report_lines(hs)
            warn_n = sum(1 for h in hs if h.level == "warn")
        except Exception as exc:
            self.report.setPlainText(errmsg.friendly(exc, action="写作提示"))
            return
        self.report.setPlainText("\n".join(lines))
        self.lbl_stat.setText(f"提示 {len(lines)} 条" + (f"（其中缺失项 {warn_n} 条）"
                                                        if warn_n else ""))

    # ------------------------------------------------------------ 灵感
    def run_ideas(self):
        try:
            items = self._wh.inspiration(self.ed_topic.text().strip(),
                                         self.kind(), limit=8)
        except Exception as exc:
            self.ideas.clear()
            self.ideas.addItem(errmsg.friendly(exc, action="灵感建议"))
            return
        self.ideas.clear()
        for it in items:
            label = f"[{it.source}·{it.label}]" if it.label else f"[{it.source}]"
            item = QListWidgetItem(f"{label} {it.text}")
            item.setData(Qt.UserRole, it.text)
            item.setToolTip(it.text)
            self.ideas.addItem(item)

    def _copy_idea(self):
        rows = self.ideas.selectedItems()
        if not rows:
            info(self, "请先在右侧选中一条建议。")
            return
        text = "\n".join(str(r.data(Qt.UserRole) or r.text()) for r in rows)
        try:
            QGuiApplication.clipboard().setText(text)
            self.lbl_stat.setText("已复制到剪贴板（是否使用由你决定）")
        except Exception as exc:
            warn(self, errmsg.friendly(exc, action="复制建议"))

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存写作提示", "写作提示.txt", "文本文件 (*.txt)")
        if not path:
            return
        try:
            Path(path).write_text(self.report.toPlainText(), encoding="utf-8")
            info(self, f"已保存：{path}")
        except OSError as exc:
            warn(self, errmsg.friendly(exc, action="保存提示"))

