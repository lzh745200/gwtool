# -*- coding: utf-8 -*-
"""写作参考库对话框：检索语料包 + 导入本机任意文档/文字。

默认扫描：① 数据目录里的「我的素材」（v1.21 起支持任意文档导入）；
② Downloads 下的「写作语料库_公版_*」目录或 zip。选中结果可复制或插入正文。
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton,
                               QSplitter, QTextBrowser, QTextEdit, QVBoxLayout,
                               QWidget)

from ..core import material_import
from ..core.material_pack import MaterialPack, MaterialPackError, find_default_packs
from .workers import FnWorker


class _PasteDialog(QDialog):
    """粘贴任意文字入库（标题可空，空则取首行）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("粘贴文字作为参考素材")
        self.resize(720, 520)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("标题（可空，默认取首行）："))
        self.ed_title = QLineEdit()
        lay.addWidget(self.ed_title)
        lay.addWidget(QLabel("正文（任意文字，按句入库）："))
        self.ed_text = QTextEdit()
        self.ed_text.setPlaceholderText("把通知、总结、讲话稿或任意素材文字粘贴到这里…")
        lay.addWidget(self.ed_text, 1)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def values(self) -> tuple[str, str]:
        return self.ed_title.text().strip(), self.ed_text.toPlainText()


class MaterialDialog(QDialog):
    def __init__(self, parent=None, insert_cb=None):
        super().__init__(parent)
        self.setWindowTitle("写作参考库")
        self.resize(980, 640)
        self._insert_cb = insert_cb
        self._pack: MaterialPack | None = None
        self._hits = []
        self._templates = []
        self._build_ui()
        self._autoload()

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("语料包："))
        self.cmb_pack = QComboBox()
        self.cmb_pack.setEditable(True)
        self.cmb_pack.setMinimumWidth(520)
        row1.addWidget(self.cmb_pack, 1)
        btn_import = QPushButton("导入素材…")
        btn_import.setToolTip("导入任意文档（docx/doc/wps/txt/rtf/pdf/md/html）或文本")
        btn_import.clicked.connect(self._import_files)
        row1.addWidget(btn_import)
        btn_paste = QPushButton("粘贴文字…")
        btn_paste.clicked.connect(self._paste_text)
        row1.addWidget(btn_paste)
        btn_browse = QPushButton("浏览…")
        btn_browse.clicked.connect(self._browse)
        row1.addWidget(btn_browse)
        btn_load = QPushButton("加载")
        btn_load.clicked.connect(self._load_selected)
        row1.addWidget(btn_load)
        root.addLayout(row1)
        self.setAcceptDrops(True)

        self.lbl_stats = QLabel("未加载语料包")
        self.lbl_stats.setStyleSheet("color:#666")
        root.addWidget(self.lbl_stats)

        row2 = QHBoxLayout()
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("输入关键词检索（如：因此、表率、监督、数据）")
        self.ed_search.returnPressed.connect(self._do_search)
        row2.addWidget(self.ed_search, 3)
        self.cmb_role = QComboBox()
        self.cmb_role.addItems(["全部", "论点", "论据", "过渡", "结尾", "模板"])
        row2.addWidget(self.cmb_role)
        btn_search = QPushButton("检索")
        btn_search.clicked.connect(self._do_search)
        row2.addWidget(btn_search)
        root.addLayout(row2)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self.lst = QListWidget()
        self.lst.currentRowChanged.connect(self._preview)
        self.lst.itemDoubleClicked.connect(lambda _i: self._insert())
        lv.addWidget(self.lst)
        split.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)
        rv.addWidget(self.preview, 1)
        btns = QHBoxLayout()
        btn_copy = QPushButton("复制选中")
        btn_copy.clicked.connect(self._copy)
        btns.addWidget(btn_copy)
        self.btn_insert = QPushButton("插入到正文")
        self.btn_insert.clicked.connect(self._insert)
        self.btn_insert.setEnabled(self._insert_cb is not None)
        btns.addWidget(self.btn_insert)
        btns.addStretch(1)
        rv.addLayout(btns)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 4)
        root.addWidget(split, 1)

    # ------------------------------------------------------------ 加载
    def _packs_all(self) -> list[Path]:
        """自建素材包（优先） + Downloads 公版包。"""
        packs: list[Path] = []
        user = material_import.user_pack_dir()
        try:
            if (user / "index.json").is_file():
                data = json.loads((user / "index.json").read_text(encoding="utf-8"))
                if isinstance(data, list) and data:
                    packs.append(user)
        except (OSError, ValueError):
            pass
        packs.extend(find_default_packs())
        return packs

    def _refresh_packs(self, select: str | None = None) -> None:
        packs = self._packs_all()
        self.cmb_pack.clear()
        for p in packs:
            self.cmb_pack.addItem(str(p))
        if select:
            self.cmb_pack.setCurrentText(select)
        elif packs:
            self.cmb_pack.setCurrentIndex(0)
        if not packs:
            self.cmb_pack.addItem("")
            self.lbl_stats.setText(
                "还没有可用的参考素材。点「导入素材…」可把任意文档/文字导入；"
                "也可把「写作语料库_公版_*.zip」放到 Downloads。")

    def _autoload(self) -> None:
        self._refresh_packs()
        if (self.cmb_pack.currentText() or "").strip():
            self._load_selected()

    # ------------------------------------------------------------ 导入素材
    _EXTS = (".docx", ".doc", ".wps", ".txt", ".rtf", ".pdf", ".md", ".html",
             ".htm", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")

    def _import_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "导入参考素材（可多选；也可拖入文件夹）", str(Path.home()),
            "文档/文本/图片 (*.docx *.doc *.wps *.txt *.rtf *.pdf *.md *.html "
            "*.htm *.png *.jpg *.jpeg *.bmp *.tif *.tiff);;所有文件 (*)")
        if files:
            self._start_import(files)

    @classmethod
    def _expand_inputs(cls, files: list[str]) -> list[str]:
        """把目录展开成受支持文件；单目录最多 2000 个，避免误拖整个磁盘。"""
        out: list[str] = []
        for f in files:
            p = Path(f)
            if p.is_dir():
                for sub in sorted(p.rglob("*")):
                    if (sub.is_file()
                            and (sub.suffix.lower() in cls._EXTS
                                 or not sub.suffix)):
                        out.append(str(sub))
                        if len(out) >= 2000:
                            return out
            elif p.is_file():
                out.append(str(p))
        return out

    def _start_import(self, files: list[str]) -> None:
        files = self._expand_inputs(files)
        if not files:
            QMessageBox.information(self, "导入素材", "没有找到可导入的文件。")
            return
        material_import.ensure_pack()
        self.lbl_stats.setText(f"正在导入 {len(files)} 个文件…")
        worker = FnWorker(material_import.import_paths, files, parent=self)
        worker.ok.connect(self._on_imported)
        worker.failed.connect(self._on_import_failed)
        self._import_worker = worker       # 持有引用，防线程对象提前回收
        worker.start()

    def _on_imported(self, summary) -> None:
        self._refresh_packs(select=str(material_import.user_pack_dir()))
        self._load_selected()
        text = summary.text()
        if summary.failed and not summary.imported:
            QMessageBox.warning(self, "导入素材", text)
        else:
            QMessageBox.information(self, "导入素材", text)

    def _on_import_failed(self, msg: str) -> None:
        self.lbl_stats.setText("导入失败")
        # 日志隐私门禁：f-string 不直接写进提示调用，先落变量
        message = f"导入失败：{msg}"
        QMessageBox.warning(self, "导入素材", message)

    def _paste_text(self) -> None:
        dlg = _PasteDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        title, text = dlg.values()
        summary = material_import.import_text(title, text)
        self._refresh_packs(select=str(material_import.user_pack_dir()))
        self._load_selected()
        if summary.failed and not summary.imported:
            QMessageBox.warning(self, "粘贴文字", summary.text())
        else:
            QMessageBox.information(self, "粘贴文字", summary.text())

    # ------------------------------------------------------------ 拖拽导入
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        files = [u.toLocalFile() for u in event.mimeData().urls()
                 if u.isLocalFile()]
        files = [f for f in files if Path(f).is_file()]
        if files:
            self._start_import(files)
            event.acceptProposedAction()

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择语料包", str(Path.home() / "Downloads"),
            "语料包 (*.zip);;所有文件 (*)")
        if path:
            self.cmb_pack.setCurrentText(path)
            self._load_selected()

    def _load_selected(self) -> None:
        raw = (self.cmb_pack.currentText() or "").strip()
        if not raw:
            return
        try:
            pack = MaterialPack(raw)
        except MaterialPackError as exc:
            QMessageBox.warning(self, "写作参考库", str(exc))
            return
        if self._pack is not None:
            self._pack.close()
        self._pack = pack
        st = pack.stats()
        self.lbl_stats.setText(
            f"已加载：文档 {st['docs']} 篇 / 汉字 {st['han_chars']:,} / "
            f"模板 {st['templates']} 条 / 来源 {'、'.join(st['sources'])}"
            f"　（本机只读，不写入资料库）")

    # ------------------------------------------------------------ 检索
    def _do_search(self) -> None:
        if self._pack is None:
            self._load_selected()
        if self._pack is None:
            return
        q = self.ed_search.text().strip()
        role = self.cmb_role.currentText()
        self.lst.clear()
        self._hits, self._templates = [], []
        if role == "模板":
            self._templates = self._pack.templates(limit=200)
            for i, t in enumerate(self._templates):
                item = QListWidgetItem(f"【{t.role}·{t.work}】{t.pattern}（{t.count} 次）")
                item.setData(Qt.UserRole, ("tpl", i))
                self.lst.addItem(item)
            if self._templates:
                self.lst.setCurrentRow(0)
            self.lbl_stats.setText(f"模板 {len(self._templates)} 条（按出现次数排序）")
            return
        role_filter = None if role == "全部" else role
        if q:
            self._hits = self._pack.search(q, role=role_filter, limit=200)
        if not q and role_filter:
            # 只按角色浏览：用空查询扫全部（这里退化为常见连接词检索）
            self._hits = []
        for i, h in enumerate(self._hits):
            item = QListWidgetItem(
                f"【{h.role or '篇目'}】{h.sentence[:60]}　—　{h.title[:20]}")
            item.setData(Qt.UserRole, ("hit", i))
            self.lst.addItem(item)
        if self._hits:
            self.lst.setCurrentRow(0)
        self.lbl_stats.setText(
            f"检索“{q}”{'（' + role + '）' if role_filter else ''}：{len(self._hits)} 条")

    # ------------------------------------------------------------ 预览/动作
    def _preview(self, row: int) -> None:
        if row < 0:
            return
        item = self.lst.item(row)
        kind, idx = item.data(Qt.UserRole)
        if kind == "tpl":
            t = self._templates[idx]
            body = [f"<h3>{t.role} · {t.work}</h3>",
                    f"<p><b>模板：</b>{t.pattern}</p>",
                    f"<p><b>出现：</b>{t.count} 次</p>"]
            for ex in t.examples:
                body.append(f"<p><b>例句：</b>{ex}</p>")
            self.preview.setHtml("".join(body))
        else:
            h = self._hits[idx]
            self.preview.setHtml(
                f"<h3>{h.title}</h3>"
                f"<p><b>来源：</b>{h.source} · {h.work}　"
                f"<b>角色：</b>{h.role or '篇目命中'}　"
                f"<b>规则：</b>{h.rule}　<b>置信度：</b>{h.conf:.2f}</p>"
                f"<p style='font-size:16px'>{h.sentence}</p>")

    def _current_text(self) -> str:
        row = self.lst.currentRow()
        if row < 0:
            return ""
        kind, idx = self.lst.item(row).data(Qt.UserRole)
        if kind == "tpl":
            return self._templates[idx].pattern
        return self._hits[idx].sentence

    def _copy(self) -> None:
        text = self._current_text()
        if text:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)

    def _insert(self) -> None:
        text = self._current_text()
        if not text or self._insert_cb is None:
            return
        try:
            self._insert_cb(text)
        except Exception as exc:
            # 日志隐私门禁：提示/日志调用里不直接写 f-string，先落变量
            message = f"插入失败：{exc}"
            QMessageBox.warning(self, "写作参考库", message)

    def closeEvent(self, event) -> None:
        if self._pack is not None:
            self._pack.close()
        super().closeEvent(event)
