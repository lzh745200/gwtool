# -*- coding: utf-8 -*-
"""材料库辅助对话框：附件管理 / 回收站。

附件：文件本体复制进数据目录（core.attachments），本对话框只管列表与操作；
回收站：documents.deleted_time 非空即在此，可恢复或彻底删除（彻底删除才真删行、
删 FTS 行、删附件文件）。

两个对话框的所有回调都不让异常裸奔：文件操作 catch OSError，其余 catch Exception，
一律用 widgets.warn 提示后 return —— 单机离线工具最怕的就是点一下闪退。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFileDialog,
                               QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..core import attachments
from ..db import dao
from .widgets import ask, info, warn

# 附件表格列（右对齐与否在 _set_cell 调用处指定）
ATT_COLUMNS = ("文件名", "大小", "添加时间", "状态")

# 回收站表格列
BIN_COLUMNS = ("标题", "分类", "字数", "附件", "删除时间")


def _fill_table(table: QTableWidget, columns) -> None:
    """统一的表格外观：整行选择、不可编辑、末列拉伸。"""
    table.setColumnCount(len(columns))
    table.setHorizontalHeaderLabels(list(columns))
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    table.horizontalHeader().setStretchLastSection(True)


def _set_cell(table: QTableWidget, row: int, col: int, text: str,
              right: bool = False, data=None, tip: str = "") -> None:
    item = QTableWidgetItem(text)
    if right:
        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    if data is not None:
        item.setData(Qt.UserRole, data)
    if tip:
        item.setToolTip(tip)
    table.setItem(row, col, item)


# ================================================================ 附件管理
class AttachmentDialog(QDialog):
    """某篇材料的附件：列出、添加、打开、另存为、删除。

    添加即复制进数据目录 attachments/（重名自动加序号，绝不覆盖），
    这样备份/恢复与便携模式换机器后附件仍在；库里只存相对路径。
    """

    def __init__(self, doc_id: int = 0, parent=None, doc_title: str = ""):
        super().__init__(parent)
        self.doc_id = int(doc_id or 0)
        self.doc_title = doc_title or ""
        self._rows: list[dao.Attachment] = []
        self._worker = None
        title = f"附件管理 —— {self.doc_title}" if self.doc_title else "附件管理"
        self.setWindowTitle(title)
        self.resize(760, 420)

        root = QVBoxLayout(self)
        self.lbl_dir = QLabel("")
        self.lbl_dir.setWordWrap(True)
        self.lbl_dir.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.lbl_dir)

        self.table = QTableWidget(0, len(ATT_COLUMNS))
        _fill_table(self.table, ATT_COLUMNS)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.itemDoubleClicked.connect(lambda _i: self.open_selected())
        root.addWidget(self.table, 1)

        ops = QHBoxLayout()
        self.lbl_count = QLabel("")
        ops.addWidget(self.lbl_count)
        ops.addStretch(1)
        for text, slot in (("添加附件…", self.add_files),
                           ("打开", self.open_selected),
                           ("另存为…", self.save_as),
                           ("打开所在目录", self.open_folder),
                           ("删除", self.delete_selected)):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            ops.addWidget(btn)
        root.addLayout(ops)

        bottom = QHBoxLayout()
        self.lbl_busy = QLabel("")
        bottom.addWidget(self.lbl_busy, 1)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

        self.reload()

    # ------------------------------------------------------------ 数据
    def reload(self) -> None:
        try:
            self._rows = dao.list_attachments(self.doc_id) if self.doc_id else []
            storage = str(attachments.storage_dir())
        except Exception as exc:  # noqa: BLE001
            warn(self, f"读取附件列表失败：{exc}")
            return
        self.lbl_dir.setText(f"附件保存在数据目录内（随备份与便携模式一起走）：{storage}")
        self.table.setRowCount(len(self._rows))
        total = 0
        for r, att in enumerate(self._rows):
            ok = attachments.exists(att)
            total += int(att.size or 0)
            _set_cell(self.table, r, 0, att.file_name or Path(att.stored_path).name,
                      data=att.id, tip=att.stored_path)
            _set_cell(self.table, r, 1, attachments.human_size(att.size), right=True)
            _set_cell(self.table, r, 2, att.added_time or "")
            _set_cell(self.table, r, 3, "正常" if ok else "文件已丢失",
                      tip="" if ok else "磁盘上找不到该文件，可能被手工移动或删除")
        self.table.resizeColumnsToContents()
        self.lbl_count.setText(
            f"共 {len(self._rows)} 个附件，合计 {attachments.human_size(total)}")

    def _selected(self) -> list[dao.Attachment]:
        ids = set()
        for idx in self.table.selectedIndexes():
            if idx.column() == 0:
                item = self.table.item(idx.row(), 0)
                if item is not None and item.data(Qt.UserRole) is not None:
                    ids.add(int(item.data(Qt.UserRole)))
        return [a for a in self._rows if a.id in ids]

    def _one(self) -> dao.Attachment | None:
        picked = self._selected()
        if len(picked) != 1:
            warn(self, "请先选中一个附件。")
            return None
        return picked[0]

    # ------------------------------------------------------------ 操作
    def add_files(self) -> None:
        if not self.doc_id:
            warn(self, "没有关联的文档，请先在资料库里选中一篇材料。")
            return
        picked, _sel = QFileDialog.getOpenFileNames(
            self, "选择附件（可多选）", str(Path.home()), "所有文件 (*)")
        if not picked:
            return
        # 复制大文件可能耗时，交给后台线程，界面不冻结
        from .workers import FnWorker
        self._set_busy(f"正在复制 {len(picked)} 个附件到数据目录…")
        self._worker = FnWorker(attachments.add_many, self.doc_id, picked, parent=self)
        self._worker.ok.connect(self._on_added)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_added(self, result) -> None:
        self._set_busy("")
        added, failures = result
        self.reload()
        if failures:
            warn(self, f"以下 {len(failures)} 个文件未能添加：\n"
                       + "\n".join(f"· {name}：{why}" for name, why in failures[:8])
                       + ("\n…（其余省略）" if len(failures) > 8 else ""))
        if added:
            info(self, f"已添加 {len(added)} 个附件（原件已复制进数据目录，"
                       f"原文件保持不动）。")

    def _on_failed(self, msg: str) -> None:
        self._set_busy("")
        warn(self, f"添加附件失败：{msg}")

    def _set_busy(self, text: str) -> None:
        self.lbl_busy.setText(text)
        self.table.setEnabled(not text)

    def open_selected(self) -> None:
        att = self._one()
        if att is None:
            return
        p = attachments.resolve(att)
        if p is None or not p.exists():
            warn(self, f"附件文件已丢失：\n{att.stored_path}\n"
                       "可能被手工移动或删除，可删除该记录后重新添加。")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(p))):
            warn(self, f"系统没有可用于打开该类型的程序：\n{p}")

    def open_folder(self) -> None:
        att = self._one()
        p = attachments.resolve(att) if att is not None else None
        directory = p.parent if p is not None else attachments.storage_dir()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            warn(self, f"无法打开附件目录：{exc}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def save_as(self) -> None:
        att = self._one()
        if att is None:
            return
        src = attachments.resolve(att)
        if src is None or not src.exists():
            warn(self, "附件文件已丢失，无法另存。")
            return
        dest, _sel = QFileDialog.getSaveFileName(
            self, "另存附件", str(Path.home() / (att.file_name or src.name)),
            "所有文件 (*)")
        if not dest:
            return
        try:
            shutil.copy2(str(src), dest)
        except OSError as exc:
            warn(self, f"另存失败：{exc}")
            return
        info(self, f"已另存到：\n{dest}")

    def delete_selected(self) -> None:
        picked = self._selected()
        if not picked:
            warn(self, "请先选中要删除的附件（可多选）。")
            return
        if not ask(self, f"删除选中的 {len(picked)} 个附件？\n"
                         "附件文件会从数据目录一并删除，文档本身不受影响。"):
            return
        ok = 0
        stuck: list[str] = []
        for att in picked:
            try:
                if attachments.remove(att):
                    ok += 1
                else:
                    stuck.append(att.file_name)
            except Exception as exc:  # noqa: BLE001
                stuck.append(f"{att.file_name}（{exc}）")
        self.reload()
        if stuck:
            warn(self, "以下附件删除失败（文件可能正被其他程序占用，请关闭后重试）：\n"
                       + "\n".join(f"· {n}" for n in stuck[:8]))
        if ok:
            info(self, f"已删除 {ok} 个附件。")


# ================================================================ 回收站
class RecycleBinDialog(QDialog):
    """回收站：查看、恢复、彻底删除已删除的材料。

    删除材料只是移到这里（deleted_time 打标 + 摘掉全文索引行），正文、历史快照
    与附件都还在；「彻底删除」才真删行、删索引、删附件文件，不可恢复。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("回收站")
        self.resize(860, 480)
        self._rows: list[dao.Document] = []

        root = QVBoxLayout(self)
        tip = QLabel("删除的材料先进回收站：不再出现在资料库列表、计数与全文检索里，"
                     "可随时恢复；「彻底删除」才真删数据与附件文件，不可恢复。")
        tip.setWordWrap(True)
        root.addWidget(tip)

        self.table = QTableWidget(0, len(BIN_COLUMNS))
        _fill_table(self.table, BIN_COLUMNS)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        root.addWidget(self.table, 1)

        ops = QHBoxLayout()
        self.lbl_count = QLabel("")
        ops.addWidget(self.lbl_count)
        ops.addStretch(1)
        for text, slot in (("恢复", self.restore_selected),
                           ("彻底删除", self.purge_selected),
                           ("清空回收站", self.empty_bin)):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            ops.addWidget(btn)
        root.addLayout(ops)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

        self.reload()

    # ------------------------------------------------------------ 数据
    def reload(self) -> None:
        try:
            self._rows = dao.list_deleted_documents()
            counts = dao.attachment_counts([d.id for d in self._rows])
            cat_names = {c.id: c.name for c in dao.list_categories()}
        except Exception as exc:  # noqa: BLE001
            warn(self, f"读取回收站失败：{exc}")
            return
        self.table.setRowCount(len(self._rows))
        for r, d in enumerate(self._rows):
            _set_cell(self.table, r, 0, d.title, data=d.id,
                      tip=f"类型：{d.file_type or '文本'}　标签：{d.tags or '无'}")
            _set_cell(self.table, r, 1,
                      cat_names.get(d.category_id, "未分类") if d.category_id else "未分类")
            _set_cell(self.table, r, 2, str(d.word_count), right=True)
            _set_cell(self.table, r, 3, str(counts.get(d.id, 0)), right=True)
            _set_cell(self.table, r, 4, d.deleted_time or "")
        self.table.resizeColumnsToContents()
        self.lbl_count.setText(f"回收站内 {len(self._rows)} 篇材料")

    def _selected_ids(self) -> list[int]:
        ids: list[int] = []
        for idx in self.table.selectedIndexes():
            if idx.column() == 0:
                item = self.table.item(idx.row(), 0)
                if item is not None and item.data(Qt.UserRole) is not None:
                    ids.append(int(item.data(Qt.UserRole)))
        return ids

    # ------------------------------------------------------------ 操作
    def restore_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            warn(self, "请先选中要恢复的材料（可多选）。")
            return
        done = 0
        for did in ids:
            try:
                if dao.restore_document(did):
                    done += 1
            except Exception as exc:  # noqa: BLE001
                warn(self, f"恢复失败：{exc}")
                break
        self.reload()
        if done:
            info(self, f"已恢复 {done} 篇材料（全文索引已同步，可正常检索）。")

    def purge_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            warn(self, "请先选中要彻底删除的材料（可多选）。")
            return
        if not ask(self, f"彻底删除选中的 {len(ids)} 篇材料？\n"
                         "文档正文、历史快照与附件文件都会一并删除，不可恢复。"):
            return
        self._purge(ids)

    def empty_bin(self) -> None:
        try:
            ids = dao.deleted_document_ids()
        except Exception as exc:  # noqa: BLE001
            warn(self, f"读取回收站失败：{exc}")
            return
        if not ids:
            info(self, "回收站已经是空的。")
            return
        if not ask(self, f"清空回收站将彻底删除 {len(ids)} 篇材料"
                         "（含历史快照与附件文件），不可恢复。确定继续？"):
            return
        self._purge(ids)

    def _purge(self, ids: list[int]) -> None:
        try:
            stuck = attachments.purge_documents(ids)
        except Exception as exc:  # noqa: BLE001
            warn(self, f"彻底删除失败：{exc}")
            self.reload()
            return
        self.reload()
        if stuck:
            warn(self, "以下附件文件删不掉（可能正被其他程序占用），数据库记录已清理，"
                       "不影响使用；残留文件可到「设置 — 系统与安全」里点"
                       "「清理无引用的附件文件」清掉：\n"
                       + "\n".join(f"· {n}" for n in stuck[:8]))
        info(self, f"已彻底删除 {len(ids)} 篇材料。")
