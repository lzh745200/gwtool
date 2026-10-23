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

from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QHBoxLayout, QListWidget,
    QListWidgetItem, QPlainTextEdit, QPushButton, QTextBrowser, QSplitter,
    QVBoxLayout, QWidget, QLabel,
)

from ..core import corrector, importer
from ..db import dao
from . import theme
from .theme import severity_color
from .widgets import Banner, ThreadSafeDialog
from .workers import FnWorker

# 逐处确认、不参与"全部应用"的类别（与纠错面板口径一致）。
# 清单来自 core 的单一事实来源：提示类类别的 suggestion 是标签而非替换文本。
_APPLY_SKIP = corrector.ADVISORY_CATEGORIES

# P1-15 阈值：小文档同步更快（避免线程往返与信号调度），大文档才走 worker。
# 8 万字实测整篇应用 0.74 s + 渲染 0.85 s，是本次要挪走的阻塞；2 000 字以下的
# 文档两条路径都在几十毫秒内，同步反而少一层延迟。两条路径共用同一套
# worker 函数，保证结果逐字节一致（见 tests/test_ui_perf_v19b.py）。
_UI_ASYNC_MIN_CHARS = 2_000


def _content_char_count(blocks, title: str = "") -> int:
    """正文 + 标题的总字符数（表格按单元格累加），用于选择同步/异步路径。"""
    total = len(title or "")
    for b in blocks or []:
        if b.get("kind") == "table":
            for row in (b.get("rows") or []):
                total += sum(len(str(cell)) for cell in row)
        else:
            total += len(b.get("text", "") or "")
    return total


def _hit_text_of(blocks, title: str, bi, ri, ci) -> str:
    """命中所在的原文片段（纯函数版，供 worker 与主线程共用）。"""
    if bi < 0:
        return title or ""
    if not (0 <= bi < len(blocks)):
        return ""
    b = blocks[bi]
    if b.get("kind") == "table" and ri is not None:
        try:
            return b["rows"][ri][ci]
        except (IndexError, TypeError, KeyError):
            return ""
    return b.get("text", "") or ""


def _render_payload(blocks, title: str, corrs_by_block) -> dict:
    """在调用线程里把标记视图组织成纯数据（HTML + 命中明细）。

    P1-15：这段是 8 万字文档渲染耗时的主体（to_marked_html 逐块转义 + 锚点）。
    抽成纯函数后既能放进 FnWorker，也能被 `_render()`（同步兼容路径）复用，
    两条路径产出完全一致，不会出现"后台渲染与前台渲染细节不同"。
    """
    html_parts: list[str] = []
    hits: list[dict] = []

    def _collect(bi, ri, ci, cs, anchor, where) -> None:
        for j, c in enumerate(cs):
            text = _hit_text_of(blocks, title, bi, ri, ci)
            hits.append({"bi": bi, "ri": ri, "ci": ci, "corr": c,
                         "anchor": f"{anchor}{j}", "where": where,
                         "text": text,
                         "label": (
                             f"[{where}·第{corrector.paragraph_no(text, c.start)}段] "
                             f"{c.label}（{c.category} {c.confidence:.2f}）")})

    if title:
        tcs = corrector.check_text(title)
        html_parts.append(f'<p style="color:{theme.MUTED};">【标题】</p>'
                          + corrector.to_marked_html(title, tcs,
                                                     anchor_prefix="tm")
                          + "<br>")
        _collect(-1, None, None, tcs, "tm", "标题")
    for bi, b in enumerate(blocks):
        cs = corrs_by_block[bi] if bi < len(corrs_by_block) else []
        if b["kind"] == "table":
            html_parts.append(f'<p style="color:{theme.MUTED};">〖表格〗</p>')
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
                        f'<p style="color:{theme.MUTED};">r{ri + 1}c{ci + 1}：</p>'
                        + corrector.to_marked_html(cell, cc, anchor_prefix=anchor))
                    _collect(bi, ri, ci, cc, anchor, f"表格r{ri + 1}c{ci + 1}")
            continue
        anchor = f"b{bi}m"
        html_parts.append(corrector.to_marked_html(b["text"], cs,
                                                   anchor_prefix=anchor))
        html_parts.append("<br>")
        _collect(bi, None, None, cs, anchor,
                 "标题" if b["kind"] == "heading" else "正文")

    return {"html": "".join(html_parts), "hits": hits,
            "blocks": blocks, "title": title,
            "corrs": corrs_by_block}


def _scan_and_render_worker(blocks, title: str) -> dict:
    """后台入口（大文档）：一次扫描 + 渲染，返回纯数据结果。

    结果只含字符串/普通 dataclass 列表，主线程据此更新列表与浏览器；
    调用期间不读写窗口对象，天然满足"跨线程只回填主线程"。
    """
    corrs = _scan_blocks(blocks)
    return _render_payload(blocks, title, corrs)


def _apply_all_blocks_worker(blocks, title: str) -> dict:
    """后台入口：整篇应用（逐块 correct_block），返回新块与修正数。

    blocks 由主线程 deepcopy 后传入；本函数只读入参、只写自己的返回值，
    不触碰任何 Qt 对象。
    """
    n = 0
    new_title = title or ""
    if new_title:
        new_title, tcs = corrector.correct_block(new_title,
                                                 skip_categories=_APPLY_SKIP)
        n += len(tcs)
    out: list[dict] = []
    for b in blocks:
        nb = dict(b)
        if nb.get("kind") == "table":
            rows = [list(row) for row in (nb.get("rows") or [])]
            for ri, row in enumerate(rows):
                for ci, cell in enumerate(row):
                    fixed, cs = corrector.correct_block(
                        cell, skip_categories=_APPLY_SKIP)
                    n += len(cs)
                    rows[ri][ci] = fixed
            nb["rows"] = rows
        else:
            fixed, cs = corrector.correct_block(nb.get("text", "") or "",
                                                skip_categories=_APPLY_SKIP)
            n += len(cs)
            nb["text"] = fixed
        out.append(nb)
    return {"blocks": out, "title": new_title, "n": n}



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
        self._apply_worker: FnWorker | None = None
        # P0-1 竞态防护：每次扫描绑定一个自增 token + 输入指纹。
        # 结果回来时两者任一与当前不符，就说明"扫描期间正文变了"——
        # 此时命中偏移（块内局部 offset）已不能套到新文本上，必须整批丢弃。
        self._scan_token = 0
        self._scan_fp: tuple | None = None
        self._rescan_timer = QTimer(self)
        self._rescan_timer.setSingleShot(True)
        self._rescan_timer.timeout.connect(self._rescan_after_stale)
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
        self.lbl_source.setStyleSheet(f"color:{theme.MUTED};")
        bar.addWidget(self.btn_file)
        bar.addWidget(self.btn_clip)
        bar.addWidget(self.lbl_source, 1)
        bar.addWidget(self.btn_run)
        root.addLayout(bar)

        # 结论横幅（UI 方案 §6.7）：顶部全宽，绿色通过 / 橙色警告 / 红色错误
        self.banner = Banner()
        root.addWidget(self.banner)

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
        # 校对留痕：只导出修正稿没有过程记录（谁、何时、对哪份文件、改了哪些、
        # 依据什么）。按钮文案压到「导出报告」——row2 已有 4 个按钮，1080 宽
        # 的窗口里第 5 个再长就会把布局顶出去。完整含义放 tooltip。
        self.btn_report = QPushButton("导出报告")
        self.btn_report.setToolTip(
            "导出校对报告（DOCX）：记录校对对象/时间/依据、逐处明细与置信度统计，"
            "可归档或转交拟稿人")
        for b in (self.btn_prev, self.btn_next, self.btn_docx, self.btn_txt,
                  self.btn_report):
            row2.addWidget(b)
        rv.addLayout(row2)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        root.addWidget(split, 1)

        self.lbl_stat = QLabel("就绪")
        self.lbl_stat.setStyleSheet(f"color:{theme.MUTED};")
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
        self.btn_report.clicked.connect(self.export_report)
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

        必须同时清掉 `_title`：标题命中也参与纠错（_render 里对 `_title` 单独
        check_text 一次），而 `_title` 只代表"载入时那份文档"。用户把输入框
        清空后 _blocks 变空，`_title` 却还留着旧文件名 —— 下一次纠错回调里
        `_hit_text(-1, ...)` 去取 `_blocks[-1]`，空列表直接 IndexError，
        异常从 _on_checked 冒出去，结果是**纠错跑完了却什么都不显示**
        （列表空、状态停在"纠错中…"）。

        同时作废旧一轮结果（P0-1）：旧命中的偏移按旧文本算，文本一改即失效。
        `_scan_fp` 保留旧快照指纹，扫描回调与应用侧据此判定"过期"。
        """
        if self._loading:
            return
        text = self.input_box.toPlainText()
        self._blocks = [{"kind": "para", "text": seg, "rows": None}
                        for seg in text.splitlines()]
        self._source = self._source or "粘贴"
        self._title = ""
        self._hits = []
        self._corrs = []

    # ------------------------------------------------------------ 竞态防护
    def _content_fingerprint(self) -> tuple:
        """当前输入内容的指纹（标题 + 每块 kind/text/rows）。

        只用于判断"扫描时看到的正文"与"现在要应用修正的正文"是否同一份；
        不持久化，不要求稳定哈希算法 —— tuple 可直接比较。
        """
        blocks = tuple(
            (b.get("kind", ""), b.get("text", "") or "",
             tuple(tuple(str(cell) for cell in row)
                   for row in (b.get("rows") or ())))
            for b in self._blocks)
        return (self._title or "", blocks)

    def _results_stale(self) -> bool:
        """当前结果是否已过期（正文在扫描后/扫描期间变过）。"""
        return (self._scan_fp is not None
                and self._content_fingerprint() != self._scan_fp)

    def _discard_results(self) -> None:
        """丢弃当前命中与标记视图（偏移已不可信）。"""
        self._corrs = []
        self._hits = []
        self.listw.clear()
        self.browser.clear()

    def _reject_if_stale(self) -> bool:
        """应用修正前的兜底校验；过期则拒绝并提示，返回 True。"""
        if not self._results_stale():
            return False
        self._discard_results()
        self.lbl_stat.setText("文本已变更，结果已过期，请重新纠错")
        return True

    def _drop_stale_result(self, token: int) -> None:
        """丢弃迟到的扫描结果（P0-1 核心）。

        两种情况：
          · token 不是最新一轮：用户已发起新扫描。**什么都不动** —— 旧结果
            既不渲染也不清空视图：清空会误伤新一轮已渲染/正在渲染的结果，
            动 busy 会把只读与按钮状态弄坏；
          · token 是最新一轮但正文指纹变了（扫描期间改了输入框）：丢弃结果、
            恢复可编辑、提示并自动补扫一次（单次定时器，不递归）。
        """
        if token != self._scan_token:
            return
        self._discard_results()
        self._set_busy(False)
        self.banner.clear()
        self.lbl_stat.setText("文本已变更，已重新扫描")
        self._rescan_timer.start(0)

    def _rescan_after_stale(self) -> None:
        """自动补扫（只触发一次）：上一轮 worker 尚未完全退出时稍后重试。"""
        worker = self._worker
        if worker is not None and worker.isRunning():
            self._rescan_timer.start(50)
            return
        if self._blocks or self.input_box.toPlainText().strip():
            self.start_check()

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
        # P0-1：本轮扫描绑定 token + 指纹。回调带 token 回来，与最新 token、
        # 当前指纹任一不符即视为过期结果（见 _on_checked）。
        self._scan_token += 1
        token = self._scan_token
        self._scan_fp = self._content_fingerprint()
        # parent=self：不设 parent 的 QThread 只被 self._worker 强引用，
        # 对话框一旦先于线程销毁（用户 Esc 关窗），唯一引用被释放会让仍在运行的
        # 线程被 GC 析构 → Qt qFatal 强杀进程（0xC0000409）。挂上父对象后
        # 线程生命周期跟随对话框，主窗口 closeEvent 的 findChildren(QThread)
        # 也能找到并等待它。
        # P1-15：大文档把扫描与渲染一起放进 worker（8 万字渲染 ~0.85 s，原在
        # 主线程）；小文档保留原 `_scan_blocks` 单层 worker，两条路径的回调都
        # 走 `_on_checked`，token/指纹校验完全一致。
        if _content_char_count(blocks_snapshot, self._title) >= _UI_ASYNC_MIN_CHARS:
            self._worker = FnWorker(_scan_and_render_worker, blocks_snapshot,
                                    self._title, parent=self)
            self._worker.ok.connect(
                lambda payload, _t=token: self._on_checked(payload, _t))
        else:
            self._worker = FnWorker(_scan_blocks, blocks_snapshot, parent=self)
            self._worker.ok.connect(
                lambda corrs, _t=token: self._on_checked(corrs, _t))
        self._worker.failed.connect(lambda msg, _t=token: self._on_failed(msg, _t))
        self._worker.start()

    def _on_checked(self, corrs_by_block, token: int | None = None) -> None:
        """扫描完成回调。

        corrs_by_block 可以是 `list[list[Correction]]`（小文档/既有同步调用）
        或 `_scan_and_render_worker` 返回的纯数据 dict（大文档，含预渲染好的
        HTML 与命中列表）。token=None 是既有同步调用路径（测试/内部直接喂
        结果）：以当前正文为基准刷新指纹后接受，保持向后兼容。真实 worker
        一律带 token：
          · token 不是最新 → 用户已发起新扫描，旧结果整批丢弃；
          · token 是最新但正文指纹变了 → 扫描期间改过输入框，丢弃并自动重扫。
        两条都命中 P0-1：绝不能把旧偏移套到新文本上渲染/应用。
        """
        if token is None:
            self._scan_fp = self._content_fingerprint()
        elif token != self._scan_token or self._results_stale():
            self._drop_stale_result(token)
            return
        if isinstance(corrs_by_block, dict):
            self._apply_render_payload(corrs_by_block)
        else:
            self._corrs = corrs_by_block
            self._render()
        self._set_busy(False)
        n = sum(len(x) for x in self._corrs)
        self.lbl_stat.setText(f"完成：发现 {n} 处问题" + ("，已按类别标记" if n else ""))
        self._show_banner(self._corrs)

    def _show_banner(self, corrs_by_block) -> None:
        """刷新结论横幅：按置信度与提示类分档统计（§6.7）。

        提示类（`ADVISORY_CATEGORIES`）单列一档：它们的建议是标签而不是替换
        文本，必须让用户一眼看出"这些不会被自动改写"。
        """
        items = [c for group in corrs_by_block for c in group]
        sure = sum(1 for c in items
                   if c.confidence >= 0.85
                   and c.category not in corrector.ADVISORY_CATEGORIES)
        maybe = sum(1 for c in items
                    if 0.55 <= c.confidence < 0.85
                    and c.category not in corrector.ADVISORY_CATEGORIES)
        advice = sum(1 for c in items
                     if c.category in corrector.ADVISORY_CATEGORIES)
        if not items:
            self.banner.show_result("success", "未发现问题：正文符合当前词库与规则",
                                    [("0 处", "success")])
            return
        kind = "danger" if sure else "warn"
        chips = []
        if sure:
            chips.append((f"确认错误 {sure}", "danger"))
        if maybe:
            chips.append((f"疑似 {maybe}", "warn"))
        if advice:
            chips.append((f"提示类 {advice}（不自动替换）", "info"))
        self.banner.show_result(kind, f"共发现 {len(items)} 处问题，可逐处确认后应用",
                                chips)

    def _on_failed(self, msg: str, token: int | None = None) -> None:
        if token is not None and (token != self._scan_token
                                  or self._results_stale()):
            self._drop_stale_result(token)
            return
        self._set_busy(False)
        self.lbl_stat.setText(f"纠错失败：{msg[:120]}")
        self.banner.show_result("danger", f"纠错未完成：{msg[:80]}")

    def _set_busy(self, busy: bool) -> None:
        has = bool(self._blocks)
        if busy:
            self.banner.clear()      # 新一轮开始：收起上一轮结论（§6.7）
        for b in (self.btn_run, self.btn_apply_one, self.btn_apply_all,
                  self.btn_ignore, self.btn_docx, self.btn_txt,
                  self.btn_report):
            b.setEnabled(has and not busy)
        self.btn_file.setEnabled(not busy)
        self.btn_clip.setEnabled(not busy)
        # P0-1：扫描期间输入框只读。旧结果套新文本的竞态有一半来自"扫描中
        # 用户继续改字"，只读是最直接的预防；程序性写入（载入文件/应用修正后
        # 回填）不受 setReadOnly 限制，故不影响既有同步流程。
        self.input_box.setReadOnly(busy)
        self.btn_run.setText("纠错中…" if busy else "开始纠错")

    # ------------------------------------------------------------ 渲染与明细
    def _render(self) -> None:
        """同步渲染（兼容路径：小文档/既有同步调用直接喂结果）。"""
        self._apply_render_payload(
            _render_payload(self._blocks, self._title, self._corrs))

    def _apply_render_payload(self, payload: dict) -> None:
        """主线程合并 worker 产出的纯数据：命中、列表项、浏览器 HTML。"""
        self._corrs = payload.get("corrs") or []
        self._hits = payload.get("hits") or []
        self.browser.setHtml(payload.get("html") or "")
        self.listw.clear()
        for idx, h in enumerate(self._hits):
            c = h["corr"]
            label = h.get("label") or (
                f"[{h['where']}·第{corrector.paragraph_no(h['text'], c.start)}段] "
                f"{c.label}（{c.category} {c.confidence:.2f}）")
            it = QListWidgetItem(label)
            it.setForeground(QColor(severity_color("error") if c.confidence >= 0.8
                                    else severity_color("warn")))
            it.setData(Qt.UserRole, idx)
            self.listw.addItem(it)

    def _hit_text(self, bi, ri, ci) -> str:
        """命中所在的原文片段。

        bi == -1 表示"命中的是标题"（不在 _blocks 里）：必须返回 self._title。
        老实现不管 bi 直接 `self._blocks[bi]` —— 取到的是**最后一个块**，
        于是标题命中的"第N段"算错；输入框被清空后 _blocks 为空，
        直接 IndexError 把整次纠错结果丢掉（列表空、状态永远停在"纠错中…"）。
        """
        return _hit_text_of(self._blocks, self._title, bi, ri, ci)

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
        # P0-1：命中里的 start/end 是**扫描时那份文本**的块内偏移。应用前
        # 再核一次指纹，并在原文片段上复核 wrong 是否仍落在该偏移处；
        # 任一不符都拒绝，绝不把旧偏移套到新文本上（主控实测会把
        # "这是新文件内容。"改成"这是部署件内容。"）。
        if self._reject_if_stale():
            return
        c, bi, ri, ci = h["corr"], h["bi"], h["ri"], h["ci"]
        source = self._hit_text(bi, ri, ci)
        if c.start < 0 or c.end > len(source) or source[c.start:c.end] != c.wrong:
            self.lbl_stat.setText("文本已变更，已放弃本次应用")
            self._discard_results()
            return
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
        # P0-1：全部应用会按当前块内容重新纠错，本不会用旧偏移；但结果列表
        # 若已过期，用户看到的"N 处"与即将写入的内容对不上，同样应拒绝并
        # 提示先重新纠错（与逐处应用保持同一口径）。
        if self._reject_if_stale():
            return
        worker = self._apply_worker
        if worker is not None and worker.isRunning():
            self.lbl_stat.setText("上一次整篇应用仍在进行，请稍候。")
            return
        # P1-15：大文档整篇应用 8 万字实测 0.74 s，必须挪到后台；小文档
        # 同步更快，且既有测试/调用方依赖"调用返回后块已更新"的语义。
        # 两条路径复用同一个 worker 函数，保证产物一致。
        blocks_snapshot = deepcopy(self._blocks)
        title = self._title
        if _content_char_count(blocks_snapshot, title) < _UI_ASYNC_MIN_CHARS:
            self._adopt_applied(_apply_all_blocks_worker(blocks_snapshot, title))
            return
        token = self._scan_token
        fingerprint = self._content_fingerprint()
        self._set_busy(True)
        self.banner.clear()
        self.lbl_stat.setText("正在应用整篇修正…")
        w = FnWorker(_apply_all_blocks_worker, blocks_snapshot, title, parent=self)
        w.ok.connect(lambda result, _t=token, _fp=fingerprint:
                     self._on_apply_all_done(result, _t, _fp))
        w.failed.connect(self._on_apply_all_failed)
        self._apply_worker = w
        w.start()

    def _adopt_applied(self, result: dict) -> None:
        """主线程合并整篇应用结果（同步/异步两条路径共用）。"""
        self._blocks = result["blocks"]
        self._title = result["title"]
        self._sync_input_box()
        self.start_check()
        self.lbl_stat.setText(f"整篇应用完成：修正 {result['n']} 处")

    def _on_apply_all_done(self, result: dict, token: int,
                           fingerprint: tuple) -> None:
        # P0-1：worker 回填前再核一次快照。文本在这几百毫秒里被改过
        # （程序性写入/新扫描）就不能套用结果，否则旧偏移会污染新正文。
        if token != self._scan_token or self._content_fingerprint() != fingerprint:
            if token == self._scan_token:
                self._set_busy(False)
            self.lbl_stat.setText("文本已变更，已放弃本次应用")
            return
        self._adopt_applied(result)

    def _on_apply_all_failed(self, msg: str) -> None:
        # 与导出同一约定：失败只写 lbl_stat，不弹模态框。
        self._set_busy(False)
        self.lbl_stat.setText(f"整篇应用失败：{msg[:120]}")

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
    def _start_export(self, work, done_text: str, buttons=()) -> None:
        """把导出任务丢进后台线程（DOCX 排版/逐块拼接都是秒级操作）。

        完成后写状态文本；失败也写状态文本 —— 这个对话框没有模态提示，
        `lbl_stat` 就是用户唯一的结果反馈通道，静默失败等于"点了没反应"。

        buttons：本次导出期间需要置灰的按钮（无论成败都在完成槽里复位）。
        默认空 —— 既有 export_docx / export_txt 的交互保持原样（它们靠
        `_set_busy` 与"上一次导出仍在进行"护栏防重复点击）。
        """
        worker = getattr(self, "_export_worker", None)
        if worker is not None and worker.isRunning():
            self.lbl_stat.setText("上一次导出仍在进行，请稍候。")
            return
        for b in buttons:
            b.setEnabled(False)
        self.lbl_stat.setText("正在导出…")
        w = FnWorker(work, parent=self)

        def _done(_r):
            for b in buttons:
                b.setEnabled(True)
            self.lbl_stat.setText(done_text)

        def _failed(msg):
            for b in buttons:
                b.setEnabled(True)
            self.lbl_stat.setText(f"导出失败：{msg}")

        w.ok.connect(_done)
        w.failed.connect(_failed)
        self._export_worker = w
        w.start()

    def export_docx(self) -> None:
        if not self._blocks:
            return
        out, _ = QFileDialog.getSaveFileName(self, "导出修正后 DOCX",
                                             "纠错后文档.docx", "Word (*.docx)")
        if not out:
            return
        # 快照后交给后台：worker 里不能再读 self._blocks（主线程可能正在改）。
        # 必须深拷贝：list() 浅拷贝后 dict 元素仍与主线程共享，导出期间
        # 用户点「应用此修正」会原地改同一批块，工作线程读到中途状态。
        import copy as _copy
        blocks, title = _copy.deepcopy(self._blocks), self._title

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
        # 与 export_docx 同口径：深拷贝快照，防止导出期间主线程原地改块
        import copy as _copy
        blocks, title = _copy.deepcopy(self._blocks), self._title

        def work():
            parts = [title] if title else []
            for b in blocks:
                if b["kind"] == "table":
                    parts.extend(" ｜ ".join(row) for row in (b.get("rows") or []))
                else:
                    parts.append(b["text"])
            Path(out).write_text("\n".join(parts), encoding="utf-8")

        self._start_export(work, f"已导出：{out}")

    def _current_text(self) -> str:
        """导出报告时随附的正文（含已应用的修正；表格按行拼回）。

        只在主线程调用：报告后台线程拿到的是字符串快照，不读 _blocks。
        """
        parts = [self._title] if self._title else []
        for b in self._blocks:
            if b["kind"] == "table":
                parts.extend(" ｜ ".join(row) for row in (b.get("rows") or []))
            else:
                parts.append(b.get("text", ""))
        return "\n".join(p for p in parts if p)

    def export_report(self) -> None:
        """导出「校对报告」DOCX（校对留痕）。

        与导出修正稿不同：报告记录**过程**——校对对象/时间/依据、逐处明细、
        置信度统计与（可选的）修正后正文，供归档与转交拟稿人。
        没有命中时也允许导出（报告结论为“未发现问题”），方便留一份“已校对”
        的凭证。失败只写 lbl_stat，不弹模态框（与 export_docx/export_txt 一致）。
        """
        if not self._blocks:
            self.lbl_stat.setText("请先载入文档或粘贴文本，再导出校对报告")
            return
        from datetime import datetime

        default = f"校对报告_{datetime.now():%Y%m%d_%H%M}.docx"
        out, _ = QFileDialog.getSaveFileName(self, "导出校对报告", default,
                                             "Word (*.docx)")
        if not out:
            return
        if not out.lower().endswith(".docx"):
            out += ".docx"
        # 快照：命中与正文都在主线程取好，后台线程不再读 self.*（主线程可能
        # 正在应用修正/重扫；Correction 只被读取，无需深拷贝）。
        hits = [h["corr"] for h in self._hits]
        source, fixed = self._source or "", self._current_text()

        def work():
            from ..core import report
            report.export_correction_report(hits, out, source_name=source,
                                            fixed_text=fixed)

        self._start_export(work, f"已导出校对报告：{out}",
                           buttons=(self.btn_report,))


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
