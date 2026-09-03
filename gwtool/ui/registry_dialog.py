# -*- coding: utf-8 -*-
"""发文登记台账：登记、查询、统计、导出。

公文发出后的登记与统计原先完全缺失，本对话框补上这一段：
台账页支持组合筛选与增删改，统计页按文种/机关/状态/月份聚合，
导出为 UTF-8-BOM 的 CSV（Excel 双击直接可读）。
"""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMessageBox, QPushButton,
                               QTableWidget, QTableWidgetItem, QTabWidget,
                               QTextBrowser, QVBoxLayout, QWidget)

from ..core import registry
from ..db import dao
from .widgets import ask, info, warn

# 台账表格列：(字段名, 表头)
TABLE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("doc_no", "发文字号"),
    ("title", "标题"),
    ("doc_type", "文种"),
    ("org", "发文机关"),
    ("secret_level", "密级"),
    ("sign_date", "成文日期"),
    ("status", "状态"),
    ("drafter", "拟稿人"),
    ("approver", "签发人"),
)

_ALL = "全部"


class DispatchForm(QDialog):
    """单条发文登记的新增/编辑表单。"""

    def __init__(self, parent=None, record: dao.Dispatch | None = None):
        super().__init__(parent)
        self.record = record or dao.Dispatch()
        self.setWindowTitle("编辑发文登记" if record and record.id else "新增发文登记")
        self.setMinimumWidth(520)
        self._fields: dict[str, QWidget] = {}

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        # 发文字号 + 自动取号：号由机关代字与年度流水组成，手工编号容易撞号
        no_row = QHBoxLayout()
        self.ed_doc_no = QLineEdit(self.record.doc_no)
        self.ed_doc_no.setPlaceholderText("如 ×政办发〔2026〕12号")
        btn_auto = QPushButton("自动取号")
        btn_auto.setToolTip("按机关代字与年度自动取下一个序号")
        btn_auto.clicked.connect(self._auto_number)
        no_row.addWidget(self.ed_doc_no, 1)
        no_row.addWidget(btn_auto)
        form.addRow("发文字号：", no_row)

        self._add_text(form, "title", "标题：", self.record.title, "公文标题（必填）")
        self._add_combo(form, "doc_type", "文种：", registry.doc_types(),
                        self.record.doc_type, editable=True)
        self._add_text(form, "org", "发文机关：", self.record.org, "如 ××市人民政府办公室")
        self._add_text(form, "main_send", "主送：", self.record.main_send)
        self._add_text(form, "cc", "抄送：", self.record.cc)
        self._add_combo(form, "secret_level", "密级：", list(registry.SECRET_LEVELS),
                        self.record.secret_level)
        self._add_combo(form, "urgency", "紧急程度：", list(registry.URGENCY_LEVELS),
                        self.record.urgency)
        self._add_text(form, "sign_date", "成文日期：", self.record.sign_date, "YYYY-MM-DD")
        self._add_text(form, "print_date", "印发日期：", self.record.print_date, "YYYY-MM-DD")
        self._add_int(form, "pages", "页数：", self.record.pages)
        self._add_int(form, "copies", "印数：", self.record.copies)
        self._add_text(form, "drafter", "拟稿人：", self.record.drafter)
        self._add_text(form, "reviewer", "核稿人：", self.record.reviewer)
        self._add_text(form, "approver", "签发人：", self.record.approver)
        self._add_combo(form, "status", "状态：", list(registry.STATUSES),
                        self.record.status)
        self._add_text(form, "remark", "备注：", self.record.remark)

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
        root.addLayout(form, 1)
        root.addLayout(btns)

    # ------------------------------------------------ 表单构件
    def _add_text(self, form: QFormLayout, key: str, label: str,
                  value: str, placeholder: str = "") -> None:
        ed = QLineEdit(value or "")
        if placeholder:
            ed.setPlaceholderText(placeholder)
        self._fields[key] = ed
        form.addRow(label, ed)

    def _add_int(self, form: QFormLayout, key: str, label: str, value: int) -> None:
        ed = QLineEdit(str(value or 0))
        ed.setPlaceholderText("0")
        self._fields[key] = ed
        form.addRow(label, ed)

    def _add_combo(self, form: QFormLayout, key: str, label: str,
                   options: list[str], value: str, editable: bool = False) -> None:
        cb = QComboBox()
        cb.setEditable(editable)
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
        """按已填的机关名推导代字并取下一个序号；推不出来就提示手工填。"""
        org = self._fields["org"].text().strip()
        current = self.ed_doc_no.text().strip()
        prefix, year, _serial = registry.parse_doc_no(current)
        if not prefix:
            prefix = org or ""
        if not prefix:
            warn(self, "请先填写「发文机关」，或在发文字号里写明机关代字，"
                      "才能自动取号。")
            return
        if not year:
            sign = self._fields["sign_date"].text().strip()
            year = sign[:4] if sign[:4].isdigit() else str(date.today().year)
        self.ed_doc_no.setText(registry.next_doc_no(prefix, year))

    def _on_save(self) -> None:
        rec = self.record
        rec.doc_no = self.ed_doc_no.text().strip()
        for key, widget in self._fields.items():
            value = widget.currentText() if isinstance(widget, QComboBox) else widget.text()
            value = value.strip()
            if key in ("pages", "copies"):
                try:
                    value = max(0, int(value or 0))
                except ValueError:
                    warn(self, f"「{key}」必须是整数。")
                    return
            setattr(rec, key, value)

        problems = registry.validate(rec)
        if problems:
            warn(self, "登记信息有误：\n" + "\n".join(f"· {p}" for p in problems))
            return
        self.accept()

    def value(self) -> dao.Dispatch:
        return self.record


class RegistryDialog(QDialog):
    """发文登记台账主对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("发文登记台账")
        self.resize(1020, 620)
        self._rows: list[dao.Dispatch] = []

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_ledger_page(), "台账")
        self.tabs.addTab(self._build_stats_page(), "统计")

        root = QVBoxLayout(self)
        root.addWidget(self.tabs)

        self.reload()

    # ------------------------------------------------ 台账页
    def _build_ledger_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        bar = QHBoxLayout()
        self.ed_keyword = QLineEdit()
        self.ed_keyword.setPlaceholderText("搜索：标题/发文字号/机关/主送/拟稿人…")
        self.ed_keyword.returnPressed.connect(self.reload)
        self.cb_year = QComboBox()
        self.cb_type = QComboBox()
        self.cb_status = QComboBox()
        self.cb_org = QComboBox()
        for cb, label in ((self.cb_year, "年度"), (self.cb_type, "文种"),
                          (self.cb_status, "状态"), (self.cb_org, "机关")):
            cb.setMinimumWidth(96)
            cb.setToolTip(label)
        for cb in (self.cb_year, self.cb_type, self.cb_status, self.cb_org):
            cb.currentIndexChanged.connect(self.reload)
        bar.addWidget(QLabel("关键字"))
        bar.addWidget(self.ed_keyword, 1)
        bar.addWidget(QLabel("年度"))
        bar.addWidget(self.cb_year)
        bar.addWidget(QLabel("文种"))
        bar.addWidget(self.cb_type)
        bar.addWidget(QLabel("状态"))
        bar.addWidget(self.cb_status)
        bar.addWidget(QLabel("机关"))
        bar.addWidget(self.cb_org)
        btn_reset = QPushButton("重置")
        btn_reset.clicked.connect(self._reset_filters)
        bar.addWidget(btn_reset)
        layout.addLayout(bar)

        self.table = QTableWidget(0, len(TABLE_COLUMNS))
        self.table.setHorizontalHeaderLabels([h for _k, h in TABLE_COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemDoubleClicked.connect(lambda _i: self.edit_selected())
        layout.addWidget(self.table, 1)

        ops = QHBoxLayout()
        self.lbl_count = QLabel("")
        ops.addWidget(self.lbl_count)
        ops.addStretch(1)
        for text, slot in (("新增登记", self.add_record),
                           ("编辑", self.edit_selected),
                           ("删除", self.delete_selected),
                           ("导出 CSV", self.export_csv)):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            ops.addWidget(btn)
        layout.addLayout(ops)
        return page

    # ------------------------------------------------ 统计页
    def _build_stats_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("年度"))
        self.cb_stat_year = QComboBox()
        self.cb_stat_year.currentIndexChanged.connect(self.refresh_stats)
        bar.addWidget(self.cb_stat_year)
        bar.addWidget(QLabel("按"))
        self.cb_group = QComboBox()
        self.cb_group.addItems(["文种", "发文机关", "状态", "密级", "紧急程度",
                                "拟稿人", "签发人"])
        self.cb_group.currentIndexChanged.connect(self.refresh_stats)
        bar.addWidget(self.cb_group)
        bar.addStretch(1)
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self.refresh_stats)
        bar.addWidget(btn_refresh)
        layout.addLayout(bar)

        self.stats_view = QTextBrowser()
        self.stats_view.setOpenExternalLinks(False)
        layout.addWidget(self.stats_view, 1)
        return page

    # ------------------------------------------------ 数据装载
    def _fill_combo(self, cb: QComboBox, values: list[str], keep_all: bool = True) -> None:
        """重填下拉项并尽量保留当前选择（避免刷新筛选时把用户选择冲掉）。"""
        current = cb.currentText()
        cb.blockSignals(True)
        cb.clear()
        if keep_all:
            cb.addItem(_ALL)
        cb.addItems([v for v in values if v])
        idx = cb.findText(current)
        if idx >= 0:
            cb.setCurrentIndex(idx)
        cb.blockSignals(False)

    def reload(self) -> None:
        self._rows = dao.list_dispatch(
            keyword=self.ed_keyword.text(),
            org="" if self.cb_org.currentText() == _ALL else self.cb_org.currentText(),
            doc_type="" if self.cb_type.currentText() == _ALL else self.cb_type.currentText(),
            status="" if self.cb_status.currentText() == _ALL else self.cb_status.currentText(),
            year="" if self.cb_year.currentText() == _ALL else self.cb_year.currentText(),
        )
        self._render_table()
        self._refresh_filter_options()
        self.refresh_stats()

    def _refresh_filter_options(self) -> None:
        """筛选项来自库内真实数据，避免出现永远查不到结果的空选项。"""
        all_rows = dao.list_dispatch()
        years = sorted({registry.year_of(r) for r in all_rows if registry.year_of(r)},
                       reverse=True)
        self._fill_combo(self.cb_year, years)
        self._fill_combo(self.cb_type, sorted({r.doc_type for r in all_rows if r.doc_type}))
        self._fill_combo(self.cb_status, list(registry.STATUSES))
        self._fill_combo(self.cb_org, sorted({r.org for r in all_rows if r.org}))
        self._fill_combo(self.cb_stat_year, years or [str(date.today().year)])

    def _render_table(self) -> None:
        self.table.setRowCount(len(self._rows))
        for r, rec in enumerate(self._rows):
            for c, (key, _header) in enumerate(TABLE_COLUMNS):
                item = QTableWidgetItem(str(getattr(rec, key, "") or ""))
                item.setData(Qt.UserRole, rec.id)
                if key in ("pages", "copies"):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        total = dao.count_dispatch()
        self.lbl_count.setText(
            f"共 {total} 条登记" + (f"，当前筛出 {len(self._rows)} 条"
                                    if len(self._rows) != total else ""))

    def _selected_ids(self) -> list[int]:
        ids: list[int] = []
        for idx in self.table.selectedIndexes():
            if idx.column() == 0:
                rid = self.table.item(idx.row(), 0).data(Qt.UserRole)
                if rid:
                    ids.append(int(rid))
        return ids

    # ------------------------------------------------ 操作
    def add_record(self) -> None:
        form = DispatchForm(self)
        if form.exec() == QDialog.DialogCode.Accepted:
            dao.add_dispatch(form.value())
            self.reload()

    def edit_selected(self) -> None:
        ids = self._selected_ids()
        if len(ids) != 1:
            warn(self, "请先选中一条登记记录。")
            return
        rec = dao.get_dispatch(ids[0])
        if rec is None:
            warn(self, "该记录已不存在，可能已被删除。")
            self.reload()
            return
        form = DispatchForm(self, rec)
        if form.exec() == QDialog.DialogCode.Accepted:
            dao.update_dispatch(form.value())
            self.reload()

    def delete_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            warn(self, "请先选中要删除的登记记录。")
            return
        if not ask(self, f"确认删除选中的 {len(ids)} 条登记？此操作不可撤销。"):
            return
        for rid in ids:
            dao.delete_dispatch(rid)
        self.reload()

    def export_csv(self) -> None:
        if not self._rows:
            warn(self, "当前没有可导出的登记记录。")
            return
        from PySide6.QtWidgets import QFileDialog
        default = f"发文登记台账_{date.today():%Y%m%d}.csv"
        path, _sel = QFileDialog.getSaveFileName(self, "导出发文登记台账",
                                                 default, "CSV 文件 (*.csv)")
        if not path:
            return
        try:
            n = registry.export_csv(self._rows, path)
        except OSError as exc:
            warn(self, f"导出失败：{exc}")
            return
        info(self, f"已导出 {n} 条登记到：\n{path}\n"
                   f"（UTF-8 BOM 编码，Excel 可直接打开）")

    def _reset_filters(self) -> None:
        self.ed_keyword.clear()
        for cb in (self.cb_year, self.cb_type, self.cb_status, self.cb_org):
            cb.blockSignals(True)
            cb.setCurrentIndex(0)
            cb.blockSignals(False)
        self.reload()

    # ------------------------------------------------ 统计
    def refresh_stats(self) -> None:
        year = self.cb_stat_year.currentText()
        if not year or year == _ALL:
            year = str(date.today().year)
        label_map = {"文种": "doc_type", "发文机关": "org", "状态": "status",
                     "密级": "secret_level", "紧急程度": "urgency",
                     "拟稿人": "drafter", "签发人": "approver"}
        group_by = label_map.get(self.cb_group.currentText(), "doc_type")

        rows = dao.list_dispatch(year=year)
        summary = registry.summarize(rows)
        grouped = dao.dispatch_stats(group_by, year)
        monthly = dao.dispatch_monthly_counts(year)

        html = [f"<h3>{year} 年度发文统计</h3>",
                f"<p>发文 <b>{summary['total']}</b> 件　"
                f"累计 <b>{summary['pages']}</b> 页　"
                f"印制 <b>{summary['copies']}</b> 份</p>",
                f"<h4>按{self.cb_group.currentText()}分布</h4>"]
        if grouped:
            html.append("<table border='1' cellspacing='0' cellpadding='4'>"
                        "<tr><th>类别</th><th>件数</th><th>占比</th></tr>")
            total = max(1, sum(n for _k, n in grouped))
            for key, n in grouped:
                html.append(f"<tr><td>{_esc(key)}</td><td align='right'>{n}</td>"
                            f"<td align='right'>{n * 100.0 / total:.1f}%</td></tr>")
            html.append("</table>")
        else:
            html.append("<p>该年度暂无登记记录。</p>")

        html.append("<h4>逐月发文量</h4><table border='1' cellspacing='0' cellpadding='4'>"
                    "<tr>" + "".join(f"<th>{m}</th>" for m, _n in monthly) + "</tr>"
                    "<tr>" + "".join(f"<td align='right'>{n}</td>" for _m, n in monthly)
                    + "</tr></table>")

        if summary["by_status"]:
            html.append("<h4>办理状态</h4><p>" +
                        "　".join(f"{_esc(k)}：{v} 件"
                                  for k, v in summary["by_status"].items()) + "</p>")
        self.stats_view.setHtml("".join(html))


def _esc(s: str) -> str:
    return (str(s) or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
