# -*- coding: utf-8 -*-
"""任意文档纠错：对任意格式文档（docx/doc/pdf/txt/rtf/md/html）或粘贴文本
做全文纠错，核心是**纠错标记**——原文按错误类别底色高亮、随附〔建议〕、
命名锚点支持从结果列表点击定位；修正可逐处或整篇应用，并保结构导出
修正后的 DOCX/TXT。

数据流：文件/剪贴板 -> parse_any -> tree_to_blocks（kind/text/rows 轻量块）
  -> 工作线程逐块 check_text（偏移为块内局部偏移）-> to_marked_html 渲染标记
  -> 应用修正（块内从后往前替换）-> 重算标记。
全部离线，无网络调用。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QHBoxLayout, QListWidget,
    QListWidgetItem, QPlainTextEdit, QPushButton, QTextBrowser, QSplitter,
    QVBoxLayout, QWidget, QLabel,
)

from ..core import corrector, importer
from ..db import dao
from .theme import MUTED, severity_color
from .widgets import ThreadSafeDialog
from .workers import FnWorker

# 逐处确认、不参与"全部应用"的类别（与纠错面板口径一致）
_APPLY_SKIP = ("数字用法",)


class AnyDocCorrectDialog(ThreadSafeDialog, QDialog):
    """任意文档纠错（带标记视图）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("任意文档纠错")
        self.resize(1080, 720)
        self._title = ""
        self._blocks: list[dict] = []   # {"kind": heading/para/table, "text", "rows"}
        self._corrs: list[list] = []
        self._hits: list[dict] = []
        self._source = ""
        self._loading_source = ""       # 后台解析中暂存的文件名（回调里落 _source）
        self._loading = False           # 程序性写输入框时抑制 textChanged 重建
        self._worker: FnWorker | None = None
        self._build_ui()

    # ------------------------------------------------------------ 界面
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.btn_file = QPushButton("选择文件…")
        self.btn_clip = QPushButton("使用剪贴板")
        self.btn_run = QPushButton("开始纠错")
        self.btn_run.setEnabled(False)
        self.lbl_source = QLabel("未选择内容（支持 docx/doc/pdf/txt/rtf/md/html，或直接粘贴）")
        self.lbl_source.setStyleSheet(f"color:{MUTED};")
        bar.addWidget(self.btn_file)
        bar.addWidget(self.btn_clip)
        bar.addWidget(self.lbl_source, 1)
        bar.addWidget(self.btn_run)
        root.addLayout(bar)

        self.input_box = QPlainTextEdit()
        self.input_box.setPlaceholderText(
            "把要纠错的文字粘贴到这里，再点「开始纠错」。\n"
            "或点「选择文件…」：解析后按块纠错，表格逐单元格标记。")
        self.input_box.setMaximumHeight(120)
        root.addWidget(self.input_box)

        split = QSplitter(Qt.Horizontal)
        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.setPlaceholderText(
            "纠错标记显示在这里：按错误类别底色高亮 + 下划线，随附〔建议〕，\n"
            "点击右侧列表任一条，左侧自动滚动到对应标记。")
        split.addWidget(self.browser)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.listw = QListWidget()
        rv.addWidget(self.listw, 1)
        row1 = QHBoxLayout()
        self.btn_apply_one = QPushButton("应用此修正")
        self.btn_apply_all = QPushButton("全部应用")
        self.btn_ignore = QPushButton("忽略此词")
        for b in (self.btn_apply_one, self.btn_apply_all, self.btn_ignore):
            row1.addWidget(b)
        rv.addLayout(row1)
        row2 = QHBoxLayout()
        self.btn_prev = QPushButton("上一处")
        self.btn_next = QPushButton("下一处")
        self.btn_docx = QPushButton("导出修正后 DOCX")
        self.btn_txt = QPushButton("导出 TXT")
        for b in (self.btn_prev, self.btn_next, self.btn_docx, self.btn_txt):
            row2.addWidget(b)
        rv.addLayout(row2)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        root.addWidget(split, 1)

        self.lbl_stat = QLabel("就绪")
        self.lbl_stat.setStyleSheet(f"color:{MUTED};")
        root.addWidget(self.lbl_stat)

        self.btn_file.clicked.connect(self.pick_file)
        self.btn_clip.clicked.connect(self.use_clipboard)
        self.btn_run.clicked.connect(self.start_check)
        self.input_box.textChanged.connect(self._on_input_changed)
        self.listw.currentRowChanged.connect(self._goto_hit)
        self.listw.itemDoubleClicked.connect(lambda _i: self.apply_selected())
        self.btn_apply_one.clicked.connect(self.apply_selected)
        self.btn_apply_all.clicked.connect(self.apply_all_blocks)
        self.btn_ignore.clicked.connect(self.ignore_selected)
        self.btn_prev.clicked.connect(lambda: self._step(-1))
        self.btn_next.clicked.connect(lambda: self._step(1))
        self.btn_docx.clicked.connect(self.export_docx)
        self.btn_txt.clicked.connect(self.export_txt)
        self._set_busy(False)

    # ------------------------------------------------------------ 来源
    def pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择要纠错的文档", "",
            "可导入文档 (*.docx *.doc *.txt *.rtf *.pdf *.md *.markdown *.html *.htm);;所有文件 (*)")
        if not path:
            return
        # 解析放后台线程：单个 0.1 MB 的 docx 实测要 3.78 秒（6000 段），
        # 扫描件走 OCR 更是分钟级。放在 GUI 线程上就是整个窗口冻结，
        # 用户会以为程序卡死。ImportWorker 走的是同一条 parse_any，
        # 这里口径保持一致。
        if self._worker is not None and self._worker.isRunning():
            self.lbl_stat.setText("上一次任务仍在进行，请稍候")
            return
        self._set_busy(True)
        self.lbl_stat.setText(f"正在解析 {Path(path).name}…")
        self._loading_source = Path(path).name
        self._worker = FnWorker(importer.parse_any, path, parent=self)
        self._worker.ok.connect(self._on_file_parsed)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_file_parsed(self, r) -> None:
        self._set_busy(False)
        if not r.ok or r.tree is None:
            self.lbl_stat.setText(f"解析失败：{(r.error or '未知原因')[:120]}")
            return
        self._loading = True
        try:
            self._title = r.tree.title
            self._blocks = corrector.tree_to_blocks(r.tree)
            self._source = self._loading_source
            self.lbl_source.setText(f"{self._source}（{len(self._blocks)} 块）")
            self.input_box.setPlainText(
                "\n".join(b["text"] for b in self._blocks if b["kind"] != "table"))
        finally:
            self._loading = False
        self.start_check()

    def use_clipboard(self) -> None:
        text = (QApplication.clipboard().text() or "")
        if not text.strip():
            self.lbl_stat.setText("剪贴板为空")
            return
        self._load_text(text, "剪贴板")

    def _load_text(self, text: str, source: str) -> None:
        self._loading = True
        try:
            self._title = ""
            self._blocks = [{"kind": "para", "text": seg, "rows": None}
                            for seg in text.splitlines()]
            self._source = source
            self.lbl_source.setText(f"{source}（{len(self._blocks)} 块）")
            self.input_box.setPlainText(text)
        finally:
            self._loading = False
        self.start_check()

    def _on_input_changed(self) -> None:
        """用户手改文本：按行重建为普通段落块（表格/标题结构让位于最新文本）。

        程序性写输入框（载入文件/同步修正结果）经由 _loading 抑制，不会走到这里。

        必须同时清掉 `_title`：标题命中也参与纠错（_render 里对 _title 单独
        check_text 一次），而 `_title` 只代表"载入时那份文档"。用户把输入框
        清空后 _blocks 变空，_title 却还留着旧文件名 —— 下一次纠错回调里
        `_hit_text(-1, ...)` 去取 `_blocks[-1]`，空列表直接 IndexError，
        异常从 _on_checked 冒出去，结果是**纠错跑完了却什么都不显示**
        （列表空、状态停在"纠错中…"）。
        """
        if self._loading:
            return
        text = self.input_box.toPlainText()
        self._blocks = [{"kind": "para", "text": seg, "rows": None}
                        for seg in text.splitlines()]
        self._source = self._source or "粘贴"
        self._title = ""
        self._hits = []

    # ------------------------------------------------------------ 纠错
    def start_check(self) -> None:
        if not self._blocks:
            if not self.input_box.toPlainText().strip():
                self.lbl_stat.setText("请先选择文件或粘贴文本")
                return
            self._on_input_changed()
        if self._worker is not None and self._worker.isRunning():
            self.lbl_stat.setText("上一次纠错仍在进行，已跳过本次触发")
            return
        self._set_busy(True)
        self.lbl_stat.setText("纠错中…")
        blocks_snapshot = [dict(b) for b in self._blocks]
        # parent=self：不设 parent 的 QThread 只被 self._worker 强引用，
        # 对话框一旦先于线程销毁（用户 Esc 关窗），唯一引用被释放会让仍在运行的
        # 线程被 GC 析构 → Qt qFatal 强杀进程（0xC0000409）。挂上父对象后
        # 线程生命周期跟随对话框，主窗口 closeEvent 的 findChildren(QThread)
        # 也能找到并等待它。
        self._worker = FnWorker(_scan_blocks, blocks_snapshot, parent=self)
        self._worker.ok.connect(self._on_checked)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_checked(self, corrs_by_block) -> None:
        self._corrs = corrs_by_block
        self._render()
        self._set_busy(False)
        n = sum(len(x) for x in corrs_by_block)
        self.lbl_stat.setText(f"完成：发现 {n} 处问题" + ("，已按类别标记" if n else ""))

    def _on_failed(self, msg: str) -> None:
        self._set_busy(False)
        self.lbl_stat.setText(f"纠错失败：{msg[:120]}")

    def _set_busy(self, busy: bool) -> None:
        has = bool(self._blocks)
        for b in (self.btn_run, self.btn_apply_one, self.btn_apply_all,
                  self.btn_ignore, self.btn_docx, self.btn_txt):
            b.setEnabled(has and not busy)
        self.btn_file.setEnabled(not busy)
        self.btn_clip.setEnabled(not busy)
        self.btn_run.setText("纠错中…" if busy else "开始纠错")

    # ------------------------------------------------------------ 渲染与明细
    def _render(self) -> None:
        self._hits = []
        html_parts: list[str] = []
        if self._title:
            tcs = corrector.check_text(self._title)
            html_parts.append('<p style="color:#757575;">【标题】</p>'
                              + corrector.to_marked_html(self._title, tcs,
                                                         anchor_prefix="tm")
                              + "<br>")
            self._collect_hits(-1, None, None, tcs, "tm", "标题")
        for bi, b in enumerate(self._blocks):
            cs = self._corrs[bi] if bi < len(self._corrs) else []
            if b["kind"] == "table":
                html_parts.append('<p style="color:#757575;">〖表格〗</p>')
                by_cell: dict[tuple[int, int], list] = {}
                for c in cs:
                    key = getattr(c, "_cell", None) or (0, 0)
                    by_cell.setdefault(key, []).append(c)
                for ri, row in enumerate(b.get("rows") or []):
                    for ci, cell in enumerate(row):
                        cc = by_cell.get((ri, ci))
                        if not cc:
                            continue
                        anchor = f"b{bi}r{ri}c{ci}m"
                        html_parts.append(
                            f'<p style="color:#757575;">r{ri + 1}c{ci + 1}：</p>'
                            + corrector.to_marked_html(cell, cc, anchor_prefix=anchor))
                        self._collect_hits(bi, ri, ci, cc, anchor,
                                           f"表格r{ri + 1}c{ci + 1}")
                continue
            anchor = f"b{bi}m"
            html_parts.append(corrector.to_marked_html(b["text"], cs,
                                                       anchor_prefix=anchor))
            html_parts.append("<br>")
            self._collect_hits(bi, None, None, cs, anchor,
                               "标题" if b["kind"] == "heading" else "正文")
        self.browser.setHtml("".join(html_parts))
        self.listw.clear()
        for idx, h in enumerate(self._hits):
            c = h["corr"]
            it = QListWidgetItem(
                f"[{h['where']}·第{corrector.paragraph_no(h['text'], c.start)}段] "
                f"{c.label}（{c.category} {c.confidence:.2f}）")
            it.setForeground(QColor(severity_color("error") if c.confidence >= 0.8
                                    else severity_color("warn")))
            it.setData(Qt.UserRole, idx)
            self.listw.addItem(it)

    def _collect_hits(self, bi, ri, ci, cs, anchor, where) -> None:
        for j, c in enumerate(cs):
            self._hits.append({"bi": bi, "ri": ri, "ci": ci, "corr": c,
                               "anchor": f"{anchor}{j}", "where": where,
                               "text": self._hit_text(bi, ri, ci)})

    def _hit_text(self, bi, ri, ci) -> str:
        """命中所在的原文片段。

        bi == -1 表示"命中的是标题"（不在 _blocks 里）：必须返回 self._title。
        老实现不管 bi 直接 `self._blocks[bi]` —— 取到的是**最后一个块**，
        于是标题命中的"第N段"算错；输入框被清空后 _blocks 为空，
        直接 IndexError 把整次纠错结果丢掉（列表空、状态永远停在"纠错中…"）。
        """
        if bi < 0:
            return self._title or ""
        if not (0 <= bi < len(self._blocks)):
            return ""
        b = self._blocks[bi]
        if b.get("kind") == "table" and ri is not None:
            try:
                return b["rows"][ri][ci]
            except (IndexError, TypeError, KeyError):
                return ""
        return b.get("text", "") or ""

    def _goto_hit(self, row: int) -> None:
        if 0 <= row < len(self._hits):
            self.browser.scrollToAnchor(self._hits[row]["anchor"])

    def _step(self, delta: int) -> None:
        n = self.listw.count()
        if not n:
            return
        self.listw.setCurrentRow((self.listw.currentRow() + delta) % n)

    def _current_hit(self) -> dict | None:
        row = self.listw.currentRow()
        return self._hits[row] if 0 <= row < len(self._hits) else None

    # ------------------------------------------------------------ 修正动作
    def apply_selected(self) -> None:
        h = self._current_hit()
        if h is None:
            self.lbl_stat.setText("请先在右侧选择一处")
            return
        c, bi, ri, ci = h["corr"], h["bi"], h["ri"], h["ci"]
        if bi == -1:  # 标题
            self._title = self._title[:c.start] + c.suggestion + self._title[c.end:]
            self._sync_input_box()
            self.start_check()
            return
        b = self._blocks[bi]
        if b["kind"] == "table":
            b["rows"][ri][ci] = (b["rows"][ri][ci][:c.start] + c.suggestion
                                 + b["rows"][ri][ci][c.end:])
        else:
            b["text"] = b["text"][:c.start] + c.suggestion + b["text"][c.end:]
        self._sync_input_box()
        self.start_check()

    def apply_all_blocks(self) -> None:
        n = 0
        if self._title:
            self._title, tcs = corrector.correct_block(self._title,
                                                       skip_categories=_APPLY_SKIP)
            n += len(tcs)
        for b in self._blocks:
            if b["kind"] == "table":
                for ri, row in enumerate(b.get("rows") or []):
                    for ci, cell in enumerate(row):
                        fixed, cs = corrector.correct_block(cell, skip_categories=_APPLY_SKIP)
                        n += len(cs)
                        b["rows"][ri][ci] = fixed
            else:
                fixed, cs = corrector.correct_block(b["text"], skip_categories=_APPLY_SKIP)
                n += len(cs)
                b["text"] = fixed
        self._sync_input_box()
        self.start_check()
        self.lbl_stat.setText(f"整篇应用完成：修正 {n} 处")

    def ignore_selected(self) -> None:
        h = self._current_hit()
        if h is None:
            return
        dao.add_ignore_word(h["corr"].wrong)
        from ..core.corrector import invalidate_cache
        invalidate_cache()
        self.start_check()

    def _sync_input_box(self) -> None:
        text = "\n".join(b["text"] for b in self._blocks if b["kind"] != "table")
        self._loading = True
        try:
            if self.input_box.toPlainText() != text:
                self.input_box.setPlainText(text)
        finally:
            self._loading = False

    # ------------------------------------------------------------ 导出
    def _start_export(self, work, done_text: str) -> None:
        """把导出任务丢进后台线程（DOCX 排版/逐块拼接都是秒级操作）。

        完成后写状态文本；失败也写状态文本 —— 这个对话框没有模态提示，
        `lbl_stat` 就是用户唯一的结果反馈通道，静默失败等于"点了没反应"。
        """
        worker = getattr(self, "_export_worker", None)
        if worker is not None and worker.isRunning():
            self.lbl_stat.setText("上一次导出仍在进行，请稍候。")
            return
        self.lbl_stat.setText("正在导出…")
        w = FnWorker(work, parent=self)
        w.ok.connect(lambda _r: self.lbl_stat.setText(done_text))
        w.failed.connect(lambda msg: self.lbl_stat.setText(f"导出失败：{msg}"))
        self._export_worker = w
        w.start()

    def export_docx(self) -> None:
        if not self._blocks:
            return
        out, _ = QFileDialog.getSaveFileName(self, "导出修正后 DOCX",
                                             "纠错后文档.docx", "Word (*.docx)")
        if not out:
            return
        # 快照后交给后台：worker 里不能再读 self._blocks（主线程可能正在改）
        blocks, title = list(self._blocks), self._title

        def work():
            from ..core.docxgen import generate_docx
            from ..core.template import default_template
            tree = corrector.blocks_to_tree(title, blocks)
            generate_docx([tree], default_template(), out)

        self._start_export(work, f"已导出：{out}")

    def export_txt(self) -> None:
        if not self._blocks:
            return
        out, _ = QFileDialog.getSaveFileName(self, "导出修正后 TXT",
                                             "纠错后文档.txt", "文本 (*.txt)")
        if not out:
            return
        blocks, title = list(self._blocks), self._title

        def work():
            parts = [title] if title else []
            for b in blocks:
                if b["kind"] == "table":
                    parts.extend(" ｜ ".join(row) for row in (b.get("rows") or []))
                else:
                    parts.append(b["text"])
            Path(out).write_text("\n".join(parts), encoding="utf-8")

        self._start_export(work, f"已导出：{out}")


def _scan_blocks(blocks):
    """工作线程入口：逐块纠错，返回与块对齐的命中列表。

    表格块的命中按单元格平铺（Correction 动态附加 _cell=(r,c) 供定位）。
    """
    out = []
    for b in blocks:
        if b["kind"] == "table":
            flat = []
            for ri, row in enumerate(b.get("rows") or []):
                for ci, cell in enumerate(row):
                    for c in corrector.check_text(cell):
                        c._cell = (ri, ci)
                        flat.append(c)
            out.append(flat)
        else:
            out.append(corrector.check_text(b["text"]))
    return out
