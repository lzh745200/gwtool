# -*- coding: utf-8 -*-
"""右侧面板：纠错建议 + 写作参考。

写作参考有两种粒度（v7 新增段落粒度）：

  · **段落**（默认）—— 命中到"某篇的第几段"，可多条累积成「参考清单」，
    可溯源、可跳回原处、可作为对齐与生成的输入。这是"段落参考"的主体。
  · **整篇** —— v1.6.0 之前的行为（`core/reference.lookup` 三源检索 +
    整篇插入）。保留它有两个理由：一是老习惯的用户能一键切回；二是
    段落功能若被开关关掉，这里就是**改动前的那条路径**。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QSplitter, QVBoxLayout, QWidget)

from ..core import corrector, reference
from ..db import dao

# 粒度取值（与 QComboBox 的插入顺序一一对应）
MODE_PARAGRAPH = "paragraph"
MODE_DOCUMENT = "document"


class ReferencePanel(QWidget):
    """纠错建议列表（可一键替换）+ 写作参考（检索/选段/溯源/插入）。"""
    insert_text = Signal(str)
    apply_edit = Signal(int, int, str)   # start, end, replacement -> 编辑器
    corrections_ready = Signal(list)     # 一次检查完成 -> 编辑器画波浪线
    # 段落参考（v7）
    open_source = Signal(object)         # ParagraphRef -> 主窗口跳转并高亮
    align_requested = Signal(list)       # list[ParagraphRef] -> 内容对齐对话框
    generate_requested = Signal(list)    # list[ParagraphRef] -> 骨架生成对话框
    hints_requested = Signal(str)        # 主题/关键词 -> 写作提示与灵感对话框

    def __init__(self, editor_getter, parent=None):
        super().__init__(parent)
        self._editor_getter = editor_getter   # () -> str，当前编辑器文本
        self._corrections: list[corrector.Correction] = []
        # 检索结果的真相源是 ref_list 里每项的 Qt.UserRole（`_picks` 另存参考清单）。
        # 此前还并存着一个 `_results` 列表，但它只被赋值、从无读取 ——
        # 那种"看起来也是权威结果集"的第二份状态，只会诱使后来者据它取值，
        # 而它与界面列表在换粒度、清空时机上并不保证同步。
        self._picks: list = []                # 参考清单（已选中的段落）
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        split = QSplitter(Qt.Vertical)

        # ---- 纠错区 ----
        corr_widget = QWidget()
        v1 = QVBoxLayout(corr_widget)
        v1.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.btn_check = QPushButton("检查当前文档")
        self.btn_check.clicked.connect(self.run_check)
        self.chk_min_conf = QDoubleSpinBox()
        self.chk_min_conf.setRange(0.0, 1.0)
        self.chk_min_conf.setValue(0.5)
        self.chk_min_conf.setPrefix("置信度≥")
        self.chk_min_conf.setToolTip("低于该置信度的建议不显示；精标词库≥0.85，程序生成混淆对0.55")
        self.lbl_count = QLabel("")
        bar.addWidget(self.btn_check)
        bar.addWidget(self.chk_min_conf)
        bar.addWidget(self.lbl_count)
        bar.addStretch(1)
        v1.addLayout(bar)

        self.corr_list = QListWidget()
        self.corr_list.itemDoubleClicked.connect(self._apply_one)
        v1.addWidget(self.corr_list, 1)
        bar2 = QHBoxLayout()
        btn_apply = QPushButton("替换所选")
        btn_apply.clicked.connect(self._apply_one)
        btn_apply_all = QPushButton("全部替换")
        btn_apply_all.clicked.connect(self._apply_all)
        btn_ignore = QPushButton("忽略本次")
        btn_ignore.setToolTip("仅本次列表中不再显示")
        btn_ignore.clicked.connect(self._ignore_one)
        btn_remember = QPushButton("永久忽略…")
        btn_remember.setToolTip("加入忽略名单并持久化，纠错不再提示该词")
        btn_remember.clicked.connect(self._remember_ignore)
        bar2.addWidget(btn_apply)
        bar2.addWidget(btn_apply_all)
        bar2.addWidget(btn_ignore)
        bar2.addWidget(btn_remember)
        bar2.addStretch(1)
        v1.addLayout(bar2)
        split.addWidget(corr_widget)

        # ---- 写作参考区 ----
        ref_widget = QWidget()
        v2 = QVBoxLayout(ref_widget)
        v2.setContentsMargins(0, 0, 0, 0)
        sbar = QHBoxLayout()
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItem("段落", MODE_PARAGRAPH)
        self.cmb_mode.addItem("整篇", MODE_DOCUMENT)
        self.cmb_mode.setToolTip(
            "段落：命中到具体段落，可累积成参考清单并溯源/对齐/生成\n"
            "整篇：按整篇检索并整篇插入（v1.6.0 原有行为）")
        self.cmb_mode.currentIndexChanged.connect(self._on_mode_changed)
        self.ref_input = QLineEdit()
        self.ref_input.setPlaceholderText("输入词语/主题，检索资料、词典与句式…")
        self.ref_input.returnPressed.connect(self.run_reference)
        btn_ref = QPushButton("检索")
        btn_ref.clicked.connect(self.run_reference)
        # 写作提示与灵感：把当前检索词作为主题带过去（留空也可用）。
        # **只弹对话框、不写正文** —— 写作辅助与文字纠错的边界在这里。
        btn_hints = QPushButton("写作提示…")
        btn_hints.setToolTip("按文种查看写作提示（缺什么/怎么组织）并推荐可借鉴的句式，"
                             "只作建议，不改动正文")
        btn_hints.clicked.connect(
            lambda: self.hints_requested.emit(self.ref_input.text().strip()))
        sbar.addWidget(self.cmb_mode)
        sbar.addWidget(self.ref_input, 1)
        sbar.addWidget(btn_ref)
        sbar.addWidget(btn_hints)
        v2.addLayout(sbar)

        self.ref_list = QListWidget()
        self.ref_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.ref_list.itemDoubleClicked.connect(self._insert_ref)
        self.ref_list.itemSelectionChanged.connect(self._update_ref_actions)
        v2.addWidget(self.ref_list, 1)
        bar3 = QHBoxLayout()
        btn_insert = QPushButton("插入到光标处")
        btn_insert.clicked.connect(self._insert_ref)
        self.btn_pick = QPushButton("加入参考清单")
        self.btn_pick.setToolTip("把选中的段落累积到下方清单，供对齐与生成使用")
        self.btn_pick.clicked.connect(self._add_picks)
        btn_add_phrase = QPushButton("存为常用句式")
        btn_add_phrase.clicked.connect(self._save_as_phrase)
        bar3.addWidget(btn_insert)
        bar3.addWidget(self.btn_pick)
        bar3.addWidget(btn_add_phrase)
        bar3.addStretch(1)
        v2.addLayout(bar3)

        # 参考清单（段落粒度才显示）
        self.lbl_picks = QLabel("参考清单（0 条）")
        v2.addWidget(self.lbl_picks)
        self.pick_list = QListWidget()
        self.pick_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.pick_list.itemDoubleClicked.connect(self._open_pick_source)
        self.pick_list.itemSelectionChanged.connect(self._update_ref_actions)
        v2.addWidget(self.pick_list, 1)
        bar4 = QHBoxLayout()
        self.btn_unpick = QPushButton("移除所选")
        self.btn_unpick.clicked.connect(self._remove_picks)
        self.btn_clear_picks = QPushButton("清空")
        self.btn_clear_picks.clicked.connect(self._clear_picks)
        self.btn_open = QPushButton("打开来源")
        self.btn_open.setToolTip("打开来源文档并高亮该段落（双击清单项同效）")
        self.btn_open.clicked.connect(lambda: self._open_pick_source())
        self.btn_align = QPushButton("内容对齐…")
        self.btn_align.setToolTip("把参考清单与当前草稿做结构对齐，指出缺/多/错位")
        self.btn_align.clicked.connect(self._request_align)
        self.btn_generate = QPushButton("生成草稿…")
        self.btn_generate.setToolTip("从参考清单派生骨架，填槽后生成草稿（结构复用，非自动成文）")
        self.btn_generate.clicked.connect(self._request_generate)
        for b in (self.btn_unpick, self.btn_clear_picks, self.btn_open,
                  self.btn_align, self.btn_generate):
            bar4.addWidget(b)
        bar4.addStretch(1)
        v2.addLayout(bar4)

        self.lbl_ref = QLabel("")
        self.lbl_ref.setWordWrap(True)
        v2.addWidget(self.lbl_ref)
        split.addWidget(ref_widget)

        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)

        self._apply_switch_state()
        self._update_ref_actions()

    # ------------------------------------------------ 开关与状态
    def _features(self) -> tuple[bool, bool, bool]:
        """读三个能力开关。**读失败一律当作开启**（详见 config._flag 的注释）。"""
        from .. import config
        try:
            return (config.para_ref_enabled(), config.para_align_enabled(),
                    config.para_gen_enabled())
        except Exception:
            return (True, True, True)

    def _apply_switch_state(self):
        ref_on, align_on, gen_on = self._features()
        if not ref_on:
            # 关掉段落能力：回到"整篇"这一条既有路径，并隐藏所有段落相关控件
            idx = self.cmb_mode.findData(MODE_DOCUMENT)
            if idx >= 0:
                self.cmb_mode.setCurrentIndex(idx)
            self.cmb_mode.setVisible(False)
        else:
            self.cmb_mode.setVisible(True)
        show_para = ref_on and self.mode() == MODE_PARAGRAPH
        for w in (self.btn_pick, self.pick_list, self.btn_unpick,
                  self.btn_clear_picks, self.btn_open, self.lbl_picks):
            w.setVisible(show_para)
        self.btn_align.setVisible(show_para and align_on)
        self.btn_generate.setVisible(show_para and gen_on)

    def refresh_switch_state(self):
        """供"设置改动开关"后刷新面板（不必重启）。"""
        self._apply_switch_state()
        self._update_ref_actions()

    def mode(self) -> str:
        return self.cmb_mode.currentData() or MODE_PARAGRAPH

    def _on_mode_changed(self, *_):
        # 换粒度就清空结果与清单：两套结果的 ref_id 语义不同（段落 id vs 文档 id），
        # 混在一起会让"插入"取到错误的实体。
        self._picks = []
        self.ref_list.clear()
        self.pick_list.clear()
        self._apply_switch_state()
        self._update_ref_actions()
        self.lbl_ref.setText("")

    def _update_ref_actions(self):
        has_pick = bool(self._picks)
        self.btn_unpick.setEnabled(has_pick)
        self.btn_clear_picks.setEnabled(has_pick)
        self.btn_open.setEnabled(bool(self.pick_list.selectedItems()))
        self.btn_align.setEnabled(has_pick)
        self.btn_generate.setEnabled(has_pick)
        self.btn_pick.setEnabled(bool(self.ref_list.selectedItems())
                                 and self.mode() == MODE_PARAGRAPH
                                 and self._features()[0])

    # ------------------------------------------------ 纠错
    def run_check(self):
        """后台线程执行全文纠错（jieba 分词 + 逐条匹配），避免长文档卡 UI。"""
        from .workers import FnWorker
        if getattr(self, "_check_worker", None) is not None \
                and self._check_worker.isRunning():
            return
        self._checked_text = self._editor_getter()
        self.btn_check.setEnabled(False)
        self.lbl_count.setText("检查中…")
        self._check_worker = FnWorker(corrector.check_text,
                                      self._checked_text, parent=self)
        self._check_worker.ok.connect(self._on_check_done)
        self._check_worker.failed.connect(self._on_check_failed)
        self._check_worker.start()

    def _on_check_done(self, corrections):
        self._corrections = corrections
        self.btn_check.setEnabled(True)
        # 渲染失败不能连累波浪线：列表填充与"通知编辑器"是两件事，
        # 老实现把 _fill_corr_list() 放在 emit 之前且不设防，一处渲染异常
        # 就让整条链路静默死掉（用户看到"点了 F7 什么都没发生"）。
        try:
            self._fill_corr_list()
        except Exception as exc:
            from .errmsg import friendly
            self.lbl_count.setText(friendly(exc, action="结果渲染"))
        # 通知编辑器把结果画成波浪线。这里发的坐标是**全文坐标**
        # （_checked_text 取自编辑器全文），与编辑器文档坐标天然对齐，
        # 无需任何换算。
        try:
            self.corrections_ready.emit(corrections)
        except Exception:
            pass

    def _on_check_failed(self, msg):
        self.btn_check.setEnabled(True)
        self.lbl_count.setText(f"检查失败：{msg}")

    def _fill_corr_list(self):
        from . import theme
        min_conf = self.chk_min_conf.value()
        text = getattr(self, "_checked_text", "") or self._editor_getter()
        self.corr_list.clear()
        shown = 0
        for i, c in enumerate(self._corrections):
            if c.confidence < min_conf:
                continue
            ctx_a = max(0, c.start - 10)
            ctx = ("…" if ctx_a > 0 else "") + \
                text[ctx_a:c.end + 12].replace("\n", " ")
            tier = "确认错误" if c.confidence >= 0.8 else "疑似错误"
            item = QListWidgetItem(f"[{c.category}] {c.wrong} → {c.suggestion}\n"
                                   f"    上下文：{ctx}…  ({c.confidence:.2f}，{tier})")
            # severity_color() 返回的是 "#b00020" 这样的**字符串**，
            # 而 QListWidgetItem.setForeground 只接受 QBrush/QColor：
            # 少这层 QColor 包装会抛 TypeError，异常从 _on_check_done 冒出去，
            # 结果是"建议列表 0 条 + 计数停在检查中… + 波浪线也不画"——
            # F7 文字纠错整条功能静默失效（打包版 stderr 不可见，用户只看到没反应）。
            item.setForeground(QColor(theme.severity_color(
                "error" if c.confidence >= 0.8 else "warn")))
            item.setData(Qt.UserRole, i)
            item.setToolTip(c.reason)
            self.corr_list.addItem(item)
            shown += 1
        self.lbl_count.setText(f"共 {len(self._corrections)} 项，显示 {shown} 项")

    def _selected_corrections(self) -> list[corrector.Correction]:
        idxs = [item.data(Qt.UserRole) for item in self.corr_list.selectedItems()]
        return [self._corrections[i] for i in idxs if i < len(self._corrections)]

    def _text_changed_since_check(self) -> bool:
        """检查之后用户又改过正文吗？

        `_corrections` 里的 start/end 是相对 `_checked_text` 的**偏移量**，
        只在 run_check() 时算一次。用户随后在正文里增删字符，这些偏移就整体
        错位，而主窗口的替换是按 (start,end) 直接定位 + insertText 的，
        于是"全部替换"会把不相干的文字改成建议词（实测：
        '这是布署。' 检查后在前面敲一个「前」，全部替换得到 '前这部署署。'，
        正文被静默改坏）。改过就必须重算，不能拿旧偏移去改新文本。
        """
        try:
            return self._editor_getter() != getattr(self, "_checked_text", "")
        except Exception:
            return True

    def _apply_one(self, *_):
        if self._text_changed_since_check():
            self.run_check()
            from .widgets import info
            info(self, "正文已改动，纠错结果已重新计算，请确认后再替换。")
            return
        for c in reversed(self._selected_corrections()):
            self.apply_edit.emit(c.start, c.end, c.suggestion)
        self.run_check()

    def _apply_all(self):
        if self._text_changed_since_check():
            self.run_check()
            from .widgets import info
            info(self, "正文已改动，纠错结果已重新计算，请确认后再替换。")
            return
        min_conf = self.chk_min_conf.value()
        to_apply = [c for c in self._corrections
                    if c.confidence >= min_conf
                    and c.category not in corrector.ADVISORY_CATEGORIES]
        # 从后往前替换：前面的偏移不受影响（同一批内的坐标是自洽的）
        for c in sorted(to_apply, key=lambda x: x.start, reverse=True):
            self.apply_edit.emit(c.start, c.end, c.suggestion)
        self.run_check()

    def _ignore_one(self):
        for item in self.corr_list.selectedItems():
            i = item.data(Qt.UserRole)
            if i < len(self._corrections):
                self._corrections[i] = None  # 占位
        self._corrections = [c for c in self._corrections if c is not None]
        self._fill_corr_list()

    def _remember_ignore(self):
        """纠错反馈闭环：把误报词写入持久忽略名单（词库管理中可撤销）。"""
        from .widgets import ask
        from ..core.corrector import invalidate_cache
        sel = self._selected_corrections()
        if not sel:
            return
        words = sorted({c.wrong for c in sel})
        if not ask(self, "将以下词加入忽略名单（纠错不再提示）？\n"
                   + "、".join(words)):
            return
        for w in words:
            dao.add_ignore_word(w, note="纠错面板永久忽略")
        invalidate_cache()
        self._ignore_one()

    # ------------------------------------------------ 写作参考
    def run_reference(self):
        """检索。按当前粒度分别走段落路径或整篇路径。"""
        q = self.ref_input.text().strip()
        self.ref_list.clear()
        if not q:
            self._update_ref_actions()
            return
        if self.mode() == MODE_PARAGRAPH and self._features()[0]:
            self._fill_paragraph_results(q)
        else:
            self._fill_document_results(q)
        self._update_ref_actions()

    def _fill_paragraph_results(self, q: str):
        from ..core import paragraph_ref
        try:
            items, relaxed = paragraph_ref.search_paragraphs_ex(q)
        except Exception as exc:
            from .errmsg import friendly
            self.lbl_ref.setText(friendly(exc, action="段落检索"))
            return
        for it in items:
            label = (f"[{it.source_label}·{it.kind_label}] {it.doc_title}"
                     f"（第 {it.ordinal + 1} 段）\n    {self._snippet(it.text)}")
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, it)
            item.setToolTip(self._tooltip(it))
            self.ref_list.addItem(item)
        # 放宽必须如实说明：不告知的话，用户会以为"这些同样精确相关"，
        # 进而对检索结果失去信任——那比少给几条结果更坏。
        note = "（精确命中较少，已放宽为「任一词命中」，排序中已降权）" if relaxed else ""
        self.lbl_ref.setText(
            f"命中 {len(items)} 个段落{note}；双击插入，选中后「加入参考清单」可累积")

    def _fill_document_results(self, q: str):
        items = reference.lookup(q)
        for it in items:
            label = f"[{it.source_label}] {it.title}\n    {it.snippet}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, it)
            item.setToolTip(it.snippet)
            self.ref_list.addItem(item)
        self.lbl_ref.setText(f"相关结果 {len(items)} 条（双击插入；按相关度排序）")

    @staticmethod
    def _snippet(text: str, n: int = 80) -> str:
        t = (text or "").replace("\n", " ").strip()
        return t if len(t) <= n else t[:n] + "…"

    def _tooltip(self, ref) -> str:
        """溯源信息：让用户一眼看清"这段从哪来"。"""
        lines = [ref.locator, f"相关度 {ref.score:.2f}"]
        off = int(getattr(ref, "char_offset", -1))
        lines.append(f"正文偏移 {off}" if off >= 0 else "正文偏移：未定位")
        h = (getattr(ref, "text_hash", "") or "")[:8]
        if h:
            lines.append(f"段落指纹 {h}")
        from ..core import paragraph_ref
        try:
            if paragraph_ref.reference_changed(ref):
                lines.append("⚠ 该段在资料库中已变更，插入的是最新版本")
        except Exception:
            pass
        lines.append("—" * 12)
        lines.append(self._snippet(ref.text, 160))
        return "\n".join(lines)

    # ------------------------------------------------ 参考清单
    def _add_picks(self):
        added = 0
        exist = {p.para_id for p in self._picks}
        for item in self.ref_list.selectedItems():
            ref = item.data(Qt.UserRole)
            if ref is None or not hasattr(ref, "para_id"):
                continue          # 整篇结果不参与清单
            if ref.para_id in exist:
                continue
            self._picks.append(ref)
            exist.add(ref.para_id)
            added += 1
        if added:
            self._fill_pick_list()
        self._update_ref_actions()

    def _remove_picks(self):
        drop = {item.data(Qt.UserRole) for item in self.pick_list.selectedItems()}
        if not drop:
            return
        self._picks = [p for p in self._picks if p.para_id not in drop]
        self._fill_pick_list()
        self._update_ref_actions()

    def _clear_picks(self):
        self._picks = []
        self._fill_pick_list()
        self._update_ref_actions()

    def _fill_pick_list(self):
        self.pick_list.clear()
        for p in self._picks:
            item = QListWidgetItem(f"{p.locator}\n    {self._snippet(p.text)}")
            item.setData(Qt.UserRole, p.para_id)
            item.setToolTip(self._tooltip(p))
            self.pick_list.addItem(item)
        self.lbl_picks.setText(f"参考清单（{len(self._picks)} 条）")

    def picks(self) -> list:
        return list(self._picks)

    def _open_pick_source(self, *_):
        items = self.pick_list.selectedItems()
        if not items:
            return
        pid = items[0].data(Qt.UserRole)
        for p in self._picks:
            if p.para_id == pid:
                self.open_source.emit(p)
                return

    def _request_align(self):
        if self._picks:
            self.align_requested.emit(self.picks())

    def _request_generate(self):
        if self._picks:
            self.generate_requested.emit(self.picks())

    # ------------------------------------------------ 插入
    def _insert_ref(self, *_):
        items = self.ref_list.selectedItems()
        if not items:
            return
        ref = items[0].data(Qt.UserRole)
        if ref is None:
            return
        # 段落粒度：直接插该段文本
        if hasattr(ref, "para_id") and hasattr(ref, "ordinal"):
            text = ref.text or ""
            if text:
                self.insert_text.emit(text)
            return
        # 整篇粒度：沿用 v1.6.0 行为（含词典/句式的特殊取法）
        src, ref_id = ref.source, ref.ref_id
        text = ""
        if src == "documents":
            text = reference.document_full_text(ref_id)
        elif src == "phrases":
            text = reference.phrase_full_text(ref_id)
        elif src == "dictionary":
            entry = reference.dictionary_entry(ref_id)
            if entry:
                text = f"{entry.get('word', '')}"
                if entry.get("pinyin"):
                    text += f"（{entry['pinyin']}）"
                if entry.get("definition"):
                    text += f"：{entry['definition']}"
        if text:
            self.insert_text.emit(text)

    def _save_as_phrase(self):
        items = self.ref_list.selectedItems()
        if not items:
            return
        ref = items[0].data(Qt.UserRole)
        if ref is None:
            return
        if hasattr(ref, "para_id") and hasattr(ref, "ordinal"):
            text = ref.text or ""
            if not text:
                return
            dao.add_phrase(text[:2000], context=text[:2000],
                           source="收藏", tag=f"来自资料库·{ref.doc_title}")
            self.lbl_ref.setText("已存为常用句式。")
            return
        if ref.source != "documents":
            return
        text = reference.document_full_text(ref.ref_id)
        if text:
            dao.add_phrase(text[:2000], context=text[:2000],
                           source="收藏", tag="来自资料库")
            self.lbl_ref.setText("已存为常用句式。")
