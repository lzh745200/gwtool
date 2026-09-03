# -*- coding: utf-8 -*-
"""新增功能对话框集合：骨架向导 / 格式体检 / 批量替换 / 批量纠错 / 历史快照 /
相似查重 / 安全设置 / 锁屏。"""
from __future__ import annotations

import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                               QFileDialog, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPlainTextEdit, QProgressBar,
                               QPushButton, QRadioButton, QSpinBox, QSplitter,
                               QTabWidget, QTableWidget, QTableWidgetItem,
                               QTextBrowser, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from ..core import inspector, simhash, toolbox
from ..core import skeletons as skeleton
from ..core.backup import create_backup, restore_backup
from ..core.security import clear_password, has_password, set_password
from ..db import dao
from . import theme
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
        self.btn_export = QPushButton("导出报告…")
        self.btn_export.setToolTip("把体检结果导出为规范 DOCX 报告，可归档或转交拟稿人整改")
        self.btn_export.clicked.connect(self._export_report)
        ops = QHBoxLayout()
        ops.addWidget(btn_run)
        ops.addWidget(self.btn_export)
        ops.addStretch(1)
        v.addLayout(ops)
        self._findings: list[inspector.Finding] = []
        self._source = ""
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
        self._findings = findings
        self._source = (Path(self.file_path.text().strip()).name
                        if self.rb_file.isChecked() else "当前编辑文档")
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

    def _export_report(self):
        if not self._findings and not self.lbl_stat.text():
            warn(self, "请先点击「开始体检」。")
            return
        from datetime import datetime

        default = f"公文格式体检报告_{datetime.now():%Y%m%d_%H%M}.docx"
        path, _sel = QFileDialog.getSaveFileName(self, "导出体检报告", default,
                                                 "Word 文档 (*.docx)")
        if not path:
            return
        from ..core import report
        try:
            report.export_report(self._findings, path, source_name=self._source)
        except OSError as exc:
            warn(self, f"导出失败：{exc}")
            return
        info(self, f"体检报告已导出：\n{path}\n\n结论：{report.verdict(self._findings)}")


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
        from .workers import FnWorker

        def work():
            """后台：逐文档统计命中数与上下文（正则编译在 worker 内完成）。"""
            import re as _re
            rows = []
            total = 0
            regex_error = None
            for did in self._docs():
                d = dao.get_document(did)
                if not d:
                    continue
                try:
                    if self.chk_regex.isChecked():
                        n = len(_re.findall(find, d.content_text))
                    else:
                        n = d.content_text.count(find)
                    if n:
                        idx = d.content_text.find(
                            find if not self.chk_regex.isChecked()
                            else _re.search(find, d.content_text).group(0))
                        ctx = d.content_text[max(0, idx - 15): idx + 40].replace(
                            "\n", " ")
                        rows.append((did, d.title, n, ctx))
                        total += n
                except _re.error as exc:
                    regex_error = str(exc)
                    break
            return rows, total, regex_error

        self.btn_preview.setEnabled(False)
        self.btn_apply.setEnabled(False)
        self.preview_list.clear()
        self._preview_worker = FnWorker(work, parent=self)
        self._preview_worker.ok.connect(self._on_preview_done)
        self._preview_worker.finished.connect(
            lambda: self.btn_preview.setEnabled(True))
        self._preview_worker.start()

    def _on_preview_done(self, result):
        rows, total, regex_error = result
        if regex_error:
            warn(self, f"正则错误：{regex_error}")
            return
        for did, title, n, ctx in rows:
            item = QListWidgetItem(f"{title}（{n} 处）：…{ctx}…")
            item.setData(Qt.UserRole, did)
            self.preview_list.addItem(item)
        self.btn_apply.setEnabled(total > 0)
        info(self, f"预览完成：{len(rows)} 篇文档、{total} 处命中。")

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


# ================================================================ 批量纠错
# 预览树的显示上限：命中很多时全部塞进控件会让界面卡住，
# 计划本身（self._plans）不截断，执行仍按全部命中处理。
_MAX_SHOW_DOCS = 300
_MAX_SHOW_HITS = 4000


def _correct_categories() -> list[str]:
    """可选纠错类别：内置规则类别 + 词库里的真实类别（去掉统计占位名）。"""
    names = {"错别字", "易混词", "标点", "机构沿革", "用户词库"}
    try:
        names |= {c for c, _n in dao.error_pair_categories()
                  if c and c not in ("未分类", "未标注")}
    except Exception:      # noqa: BLE001  词库不可用时退回内置类别
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
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.failed.emit(str(exc))


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
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.failed.emit(str(exc))


class BatchCorrectDialog(QDialog):
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
            "程序生成的混淆对 0.55，机构沿革对照 0.70。\n"
            "「数字用法」类只给提示不给替换文本，已整类排除。")
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
        except Exception as exc:  # noqa: BLE001
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
        hits = sum(p.count for p in self._plans)
        if not ask(self, f"将修改 {len(self._plans)} 篇文档、共 {hits} 处，"
                         f"直接写入资料库（每篇改前会留一份历史快照）。\n确定执行？"):
            return
        self.btn_preview.setEnabled(False)
        self.btn_apply.setEnabled(False)
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

        def work():
            """后台：读正文 + 查表 SimHash 粗筛 + 精算 Jaccard。"""
            docs = {d.id: d.title for d in dao.list_documents()}
            texts = {did: dao.get_document(did).content_text for did in docs}
            pairs = simhash.find_similar(texts, self.sp_thresh.value() / 100,
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

        g6 = QLabel("数据维护")
        g6.setStyleSheet("font-weight:bold;margin-top:8pt;")
        v.addWidget(g6)
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

    def _rebuild_fts(self):
        if not ask(self, "重建全文索引可能需要片刻（与资料库大小相关），继续？"):
            return
        from ..db import dao as dao_mod
        counts = dao_mod.rebuild_fts()
        self.lbl_fts.setText(
            f"索引已重建：资料 {counts['documents']} 篇、句式 {counts['phrases']} 条。")

    def _sweep_attachments(self):
        """清掉彻底删除材料时残留（文件被占用没删掉）的孤儿附件。"""
        if not ask(self, "清理数据目录里已没有任何材料引用的附件文件？\n"
                         "在用的附件不受影响。"):
            return
        from ..core import attachments
        try:
            n = attachments.sweep_orphans()
        except Exception as exc:  # noqa: BLE001
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
