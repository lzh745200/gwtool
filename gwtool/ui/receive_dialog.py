# -*- coding: utf-8 -*-
"""收文登记台账：登记、查询、统计、导出。

与 `registry_dialog.py`（发文）是一对：公文实务是**收 + 发**两个方向，
此前只有发文，"别人发给我的"完全管不住。本模块补齐收文侧。

两者**刻意分成两个文件而非合并**：字段与流程差异很大（收文有拟办/批示/
承办/办结，发文有拟稿/核稿/签发），强行抽象成一个通用台账表单会让两边
都变得难读，且改一处极易碰坏另一处。
"""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog,
                               QFormLayout, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QPushButton, QScrollArea,
                               QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from ..core import receive
from ..db import dao
from .widgets import ask, info, warn

# 台账列表展示的列（顺序即列顺序）：只挑经办人最常看的几列，
# 全 25 列挤在表格里反而看不清。完整字段走导出。
_COLUMNS = (
    ("reg_no", "收文登记号"),
    ("incoming_no", "来文字号"),
    ("title", "标题"),
    ("from_org", "来文机关"),
    ("receive_date", "收到日期"),
    ("due_date", "应办结"),
    ("status", "状态"),
    ("handler_dept", "承办部门"),
)


def _attach_tip(widget, tip: str) -> None:
    """给表单控件挂上悬停说明（tooltip + 状态栏提示各一份）。

    U5：收文表单有 24 个字段，其中几对极易混淆（拟办意见 vs 领导批示、
    紧急程度 vs 应办结日期）。填错位置不会报错，只会让台账失真 —— 而目标
    用户在离线内网里没有任何在线帮助可查，悬停提示就是唯一的说明来源。
    """
    if not tip:
        return
    widget.setToolTip(tip)
    widget.setStatusTip(tip)


class ReceiveForm(QDialog):
    """单条收文登记的新增/编辑表单。"""

    def __init__(self, parent=None, record: dao.Receive | None = None):
        super().__init__(parent)
        self.record = record or dao.Receive()
        if not self.record.id and not self.record.receive_date:
            # 新建时预填今天：绝大多数收文就是当天签收的，省一次输入
            self.record.receive_date = date.today().isoformat()
        self.setWindowTitle("编辑收文登记" if record and record.id else "新增收文登记")
        self.setMinimumWidth(560)
        self.setMinimumHeight(520)
        self._fields: dict[str, QWidget] = {}

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        # 收文登记号 + 自动取号：与发文字号同源，但独立计数（两本账）
        no_row = QHBoxLayout()
        self.ed_reg_no = QLineEdit(self.record.reg_no)
        self.ed_reg_no.setPlaceholderText("如 收〔2026〕12号")
        btn_auto = QPushButton("自动取号")
        btn_auto.setToolTip("按年度流水自动取下一个收文登记号（与发文独立计数）")
        btn_auto.clicked.connect(self._auto_number)
        no_row.addWidget(self.ed_reg_no, 1)
        no_row.addWidget(btn_auto)
        form.addRow("收文登记号：", no_row)

        self._add_text(form, "incoming_no", "来文字号：", self.record.incoming_no,
                       "如 ×政发〔2026〕12号（可留空）",
                       tip="来文原件上的发文字号，照抄即可；用于检索与引用")
        self._add_text(form, "title", "标题：", self.record.title, "来文标题")
        self._add_combo(form, "doc_type", "文种：", receive.doc_types_available(),
                        self.record.doc_type, editable=True)
        self._add_text(form, "from_org", "来文机关：", self.record.from_org,
                       "如 ××市人民政府办公室")
        self._add_text(form, "main_send", "主送：", self.record.main_send)
        self._add_text(form, "cc", "抄送：", self.record.cc)
        self._add_combo(form, "secret_level", "密级：", list(receive.SECRET_LEVELS),
                        self.record.secret_level,
                        tip="按来文标注填写（绝密/机密/秘密）；无标注留空。"
                            "填错会影响台账的密级统计与借阅管理")
        self._add_combo(form, "urgency", "紧急程度：", list(receive.URGENCY_LEVELS),
                        self.record.urgency,
                        tip="公文标注的紧急程度：平急/急件/特急/特提。"
                            "它决定办理优先级，与「应办结日期」共同驱动督办提醒")
        self._add_text(form, "doc_date", "来文成文日期：", self.record.doc_date,
                       "YYYY-MM-DD")
        self._add_text(form, "receive_date", "收到日期：", self.record.receive_date,
                       "YYYY-MM-DD")
        self._add_int(form, "pages", "页数：", self.record.pages)
        self._add_int(form, "copies", "份数：", self.record.copies)
        self._add_text(form, "propose", "拟办意见：", self.record.propose,
                       tip="由拟办人员/办公室填写：建议交谁办、怎么办。"
                           "（与下方「领导批示」不同——批示是领导的决定）")
        self._add_text(form, "instruction", "领导批示：", self.record.instruction,
                       tip="领导对拟办意见的批示原文（同意/另办/请××阅处…）。"
                           "与「拟办意见」分开填，便于事后追溯决策过程")
        self._add_text(form, "handler_dept", "承办部门：", self.record.handler_dept)
        self._add_text(form, "handler", "承办人：", self.record.handler)
        self._add_text(form, "due_date", "应办结日期：", self.record.due_date,
                       "YYYY-MM-DD（填了才会进督办提醒）",
                       tip="填了才会进入督办提醒：到期未办结会在主窗口角标提示")
        self._add_text(form, "done_date", "办结日期：", self.record.done_date,
                       "YYYY-MM-DD")
        self._add_text(form, "result", "办理结果：", self.record.result)
        self._add_combo(form, "status", "状态：", list(receive.STATUSES),
                        self.record.status)
        self._add_combo(form, "retention", "保管期限：",
                        list(receive.RETENTION_LEVELS), self.record.retention,
                        tip="按《机关文件材料归档范围和文书档案保管期限规定》"
                            "鉴定：永久 / 30 年 / 10 年；未鉴定可留空，"
                            "归档时会写进移交清单")
        self._add_text(form, "archive_no", "档号：", self.record.archive_no)
        self._add_text(form, "archive_date", "归档日期：", self.record.archive_date,
                       "YYYY-MM-DD")
        self._add_text(form, "remark", "备注：", self.record.remark)

        # 字段较多（24 项），不套滚动区在小屏笔记本上会超出屏幕高度
        holder = QWidget()
        holder.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidget(holder)
        scroll.setWidgetResizable(True)

        btns = QHBoxLayout()
        btns.addStretch(1)
        btn_ok = QPushButton("保存")
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self._on_save)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_ok)
        btns.addWidget(btn_cancel)

        root = QVBoxLayout(self)
        root.addWidget(scroll, 1)
        root.addLayout(btns)

    # ------------------------------------------------ 表单构件
    def _add_text(self, form: QFormLayout, key: str, label: str,
                  value: str, placeholder: str = "", tip: str = "") -> None:
        ed = QLineEdit(value or "")
        if placeholder:
            ed.setPlaceholderText(placeholder)
        _attach_tip(ed, tip)
        self._fields[key] = ed
        form.addRow(label, ed)

    def _add_int(self, form: QFormLayout, key: str, label: str, value: int,
                 tip: str = "") -> None:
        ed = QLineEdit(str(value or 0))
        ed.setPlaceholderText("0")
        _attach_tip(ed, tip)
        self._fields[key] = ed
        form.addRow(label, ed)

    def _add_combo(self, form: QFormLayout, key: str, label: str,
                   options: list[str], value: str, editable: bool = False,
                   tip: str = "") -> None:
        cb = QComboBox()
        cb.setEditable(editable)
        _attach_tip(cb, tip)
        cb.addItems([o for o in options if o != ""])
        if value:
            idx = cb.findText(value)
            if idx >= 0:
                cb.setCurrentIndex(idx)
            else:
                cb.setEditText(value)
        self._fields[key] = cb
        form.addRow(label, cb)

    def _auto_number(self) -> None:
        """按当前年份取下一个收文登记号；已有号时沿用其前缀与年份。"""
        current = self.ed_reg_no.text().strip()
        prefix, year, _serial = receive.parse_incoming_no(current)
        if not prefix:
            prefix = "收"
        if not year:
            got = self._fields["receive_date"].text().strip()
            year = got[:4] if got[:4].isdigit() else str(date.today().year)
        self.ed_reg_no.setText(receive.next_reg_no(prefix, year))

    def _on_save(self) -> None:
        rec = self.record
        rec.reg_no = self.ed_reg_no.text().strip()
        for key, widget in self._fields.items():
            value = (widget.currentText() if isinstance(widget, QComboBox)
                     else widget.text()).strip()
            if key in ("pages", "copies"):
                try:
                    value = max(0, int(value or 0))
                except ValueError:
                    warn(self, f"「{key}」必须是整数。")
                    return
            setattr(rec, key, value)

        problems = receive.validate(rec)
        if problems:
            warn(self, "登记信息有误：\n" + "\n".join(f"· {p}" for p in problems))
            return
        self.accept()

    def value(self) -> dao.Receive:
        return self.record


class ReceiveDialog(QDialog):
    """收文登记台账主对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("收文登记台账")
        self.resize(1120, 680)
        self._rows: list[dao.Receive] = []

        tabs = QTabWidget()
        tabs.addTab(self._build_ledger_page(), "登记台账")
        tabs.addTab(self._build_stats_page(), "统计")
        root = QVBoxLayout(self)
        root.addWidget(tabs)
        self.reload()

    # ------------------------------------------------ 台账页
    def _build_ledger_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        flt = QHBoxLayout()
        self.ed_keyword = QLineEdit()
        self.ed_keyword.setPlaceholderText("标题 / 来文字号 / 来文机关 / 承办人…")
        self.ed_keyword.returnPressed.connect(self.reload)
        flt.addWidget(self.ed_keyword, 2)
        self.cb_org = QComboBox()
        self.cb_type = QComboBox()
        self.cb_status = QComboBox()
        self.cb_year = QComboBox()
        for cb, label in ((self.cb_org, "全部机关"), (self.cb_type, "全部文种"),
                          (self.cb_status, "全部状态"), (self.cb_year, "全部年度")):
            cb.setMinimumWidth(110)
            self._fill_combo(cb, [], keep_all=True)
            flt.addWidget(cb)
            cb.currentIndexChanged.connect(self.reload)
            cb.setToolTip(label)
        btn_query = QPushButton("查询")
        btn_query.clicked.connect(self.reload)
        btn_reset = QPushButton("重置")
        btn_reset.clicked.connect(self._reset_filters)
        flt.addWidget(btn_query)
        flt.addWidget(btn_reset)
        layout.addLayout(flt)

        self.tbl = QTableWidget(0, len(_COLUMNS))
        self.tbl.setHorizontalHeaderLabels([label for _k, label in _COLUMNS])
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.doubleClicked.connect(lambda *_: self.edit_selected())
        self.tbl.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        layout.addWidget(self.tbl, 1)

        ops = QHBoxLayout()
        self.lbl_count = QLabel("")
        ops.addWidget(self.lbl_count)
        ops.addStretch(1)
        for text, slot in (("新增登记", self.add_record),
                           ("编辑", self.edit_selected),
                           ("归档", self.archive_selected),
                           ("删除", self.delete_selected),
                           ("导出", self.export_table)):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            ops.addWidget(btn)
        layout.addLayout(ops)
        return page

    # ------------------------------------------------ 统计页
    def _build_stats_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.lbl_stats = QLabel("")
        self.lbl_stats.setTextFormat(Qt.RichText)
        self.lbl_stats.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.lbl_stats.setWordWrap(True)
        layout.addWidget(self.lbl_stats, 1)
        btn = QPushButton("刷新统计")
        btn.clicked.connect(self._refresh_stats)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn)
        layout.addLayout(row)
        return page

    # ------------------------------------------------ 数据
    def _fill_combo(self, cb: QComboBox, values: list[str],
                    keep_all: bool = True) -> None:
        cur = cb.currentText()
        cb.blockSignals(True)
        cb.clear()
        if keep_all:
            cb.addItem("全部")
        cb.addItems(values)
        if cur:
            idx = cb.findText(cur)
            if idx >= 0:
                cb.setCurrentIndex(idx)
        cb.blockSignals(False)

    def reload(self) -> None:
        kw = self.ed_keyword.text().strip()
        org = self.cb_org.currentText() if self.cb_org.currentIndex() > 0 else ""
        dtype = self.cb_type.currentText() if self.cb_type.currentIndex() > 0 else ""
        status = self.cb_status.currentText() if self.cb_status.currentIndex() > 0 else ""
        year = self.cb_year.currentText() if self.cb_year.currentIndex() > 0 else ""
        self._rows = dao.list_receive(keyword=kw, from_org=org, doc_type=dtype,
                                      status=status, year=year)
        self._refresh_filter_options()
        self._render_table()
        self._refresh_stats()

    def _refresh_filter_options(self) -> None:
        all_rows = dao.list_receive()
        self._fill_combo(self.cb_org, sorted({r.from_org for r in all_rows if r.from_org}))
        self._fill_combo(self.cb_type, sorted({r.doc_type for r in all_rows if r.doc_type}))
        self._fill_combo(self.cb_status, sorted({r.status for r in all_rows if r.status}))
        self._fill_combo(self.cb_year, dao.receive_years())

    def _render_table(self) -> None:
        self.tbl.setRowCount(len(self._rows))
        for i, r in enumerate(self._rows):
            values = {"reg_no": r.reg_no, "incoming_no": r.incoming_no,
                      "title": r.title, "from_org": r.from_org,
                      "receive_date": r.receive_date, "due_date": r.due_date,
                      "status": r.status, "handler_dept": r.handler_dept}
            for j, (key, _label) in enumerate(_COLUMNS):
                item = QTableWidgetItem(str(values.get(key, "") or ""))
                if key == "due_date" and receive.is_overdue(r):
                    item.setText((r.due_date or "") + "（已逾期）")
                self.tbl.setItem(i, j, item)
        self.lbl_count.setText(f"共 {len(self._rows)} 条")

    def _selected_ids(self) -> list[int]:
        ids = []
        for idx in self.tbl.selectionModel().selectedRows():
            row = idx.row()
            if 0 <= row < len(self._rows):
                ids.append(self._rows[row].id)
        return ids

    # ------------------------------------------------ 操作
    def add_record(self) -> None:
        dlg = ReceiveForm(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            dao.add_receive(dlg.value())
            self.reload()

    def edit_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            warn(self, "请先选中一条登记记录。")
            return
        rec = dao.get_receive(ids[0])
        if rec is None:
            warn(self, "该记录已不存在，可能已被删除。")
            self.reload()
            return
        dlg = ReceiveForm(self, rec)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            dao.update_receive(dlg.value())
            self.reload()

    def archive_selected(self) -> None:
        """把选中的已办结收文批量归档：编档号、定保管期限、出移交清单。

        在办件**不允许**归档——它会造成"卷内缺件"，比不归档更难收拾。
        """
        ids = set(self._selected_ids())
        if not ids:
            warn(self, "请先选中要归档的收文。")
            return
        from PySide6.QtWidgets import QInputDialog

        from ..core import archive

        rows = [r for r in self._rows if r.id in ids]
        unfinished = [r for r in rows if not receive.is_closed(r)]
        if unfinished:
            warn(self, f"选中的记录中有 {len(unfinished)} 件尚未办结。\n\n"
                       f"在办件归档会造成「卷内缺件」，请先办结再归档。")
            return
        prefix, ok = QInputDialog.getText(self, "批量归档", "档号前缀（如单位简称）：")
        if not ok:
            return
        retention, ok = QInputDialog.getItem(
            self, "批量归档", "保管期限：",
            list(archive.RETENTION_CHOICES), 0, False)
        if not ok:
            return
        rep = archive.batch_archive(rows, prefix, retention)
        note = (f"\n其中 {rep['skipped']} 件已有档号，未重复编号。"
                if rep["skipped"] else "")
        info(self, f"已归档 {rep['archived']} 件。\n"
                   f"保管期限：{rep['retention']}　归档日期：{rep['date']}{note}")
        self.reload()

        if not ask(self, "是否导出本次归档的移交清单（DOCX，可签字盖章随卷）？"):
            return
        from PySide6.QtWidgets import QFileDialog

        from ..paths import export_dir
        default = str(export_dir() / f"归档移交清单_{rep['date']}.docx")
        path, _sel = QFileDialog.getSaveFileName(
            self, "保存归档移交清单", default, "Word 文档 (*.docx)")
        if not path:
            return
        try:
            archive.export_manifest(rows, path)
        except OSError as exc:
            warn(self, f"导出失败：{exc}")
            return
        info(self, f"移交清单已导出：\n{path}")

    def delete_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            warn(self, "请先选中要删除的记录。")
            return
        if not ask(self, f"确定删除选中的 {len(ids)} 条收文登记？此操作不可撤销。"):
            return
        for rid in ids:
            dao.delete_receive(rid)
        self.reload()

    def export_table(self) -> None:
        """导出收文台账：默认 xlsx（附统计工作表），仍可选 CSV。"""
        if not self._rows:
            warn(self, "当前没有可导出的登记记录。")
            return
        from PySide6.QtWidgets import QFileDialog
        default = f"收文登记台账_{date.today():%Y%m%d}.xlsx"
        path, _sel = QFileDialog.getSaveFileName(
            self, "导出收文登记台账", default,
            "Excel 工作簿 (*.xlsx);;CSV 文件 (*.csv)")
        if not path:
            return
        # 以后缀为准：用户可能在文件名里手敲 .csv，而过滤器仍停在默认项
        try:
            if path.lower().endswith(".csv"):
                n = receive.export_csv(self._rows, path)
                info(self, f"已导出 {n} 条登记到：\n{path}\n"
                           f"（UTF-8 BOM 编码，Excel 可直接打开）")
                return
            if not path.lower().endswith(".xlsx"):
                path += ".xlsx"
            n = receive.export_xlsx(self._rows, path, with_stats=True)
        except OSError as exc:
            warn(self, f"导出失败：{exc}")
            return
        info(self, f"已导出 {n} 条登记到：\n{path}\n（含「统计」工作表）")

    def _reset_filters(self) -> None:
        self.ed_keyword.clear()
        for cb in (self.cb_org, self.cb_type, self.cb_status, self.cb_year):
            cb.setCurrentIndex(0)
        self.reload()

    def _refresh_stats(self) -> None:
        today = date.today().isoformat()
        summ = receive.summarize(self._rows, today=today)

        def block(title: str, data: dict) -> str:
            if not data:
                return f"<p><b>{title}</b>：暂无数据</p>"
            rows = "".join(
                f"<tr><td>{k}</td><td align='right'>{v}</td></tr>"
                for k, v in list(data.items())[:12])
            return (f"<p><b>{title}</b></p>"
                    f"<table border='0' cellspacing='0' cellpadding='2'>{rows}</table>")

        head = (f"<p><b>合计</b>：{summ['total']} 件 · 总份数 {summ['copies']} · "
                f"总页数 {summ['pages']} · 待办 {summ['pending']} 件 · "
                f"<span style='color:#c0392b'>已逾期 {summ['overdue']} 件</span></p>")
        self.lbl_stats.setText(
            head
            + block("按来文机关", summ["by_org"])
            + block("按文种", summ["by_type"])
            + block("按状态", summ["by_status"]))
