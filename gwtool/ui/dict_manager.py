# -*- coding: utf-8 -*-
"""词典与用户词库管理：词典/错别字对/常用句式三个标签页。"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListWidget,
                               QMessageBox, QPushButton, QTabWidget,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..core import ruleset
from ..core.corrector import invalidate_cache
from ..db import dao
from ..db.connection import get_conn
from .widgets import ask, info, warn


class DictManager(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("词典与词库管理")
        self.resize(900, 600)
        v = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_dictionary(), "词典")
        self.tabs.addTab(self._tab_errors(), "错别字对")
        self.tabs.addTab(self._tab_phrases(), "常用句式")
        self.tabs.addTab(self._tab_ignore(), "忽略名单")
        v.addWidget(self.tabs, 1)
        self.lbl_stats = QLabel()
        v.addWidget(self.lbl_stats)
        self._refresh_stats()

    def _refresh_stats(self):
        n_dict = get_conn().execute("SELECT count(*) FROM dictionary").fetchone()[0]
        n_pairs = dao.count_error_pairs()
        n_phr = len(dao.list_phrases(limit=100000))
        self.lbl_stats.setText(f"词典 {n_dict} 条；错别字对 {n_pairs} 条；常用句式 {n_phr} 条。"
                               f"新增条目立即参与纠错与参考匹配。")

    # ================================================================ 词典
    def _tab_dictionary(self):
        w = QDialog()
        v = QVBoxLayout(w)
        bar = QHBoxLayout()
        self.dict_search = QLineEdit()
        self.dict_search.setPlaceholderText("搜索词条…")
        self.dict_search.returnPressed.connect(self._reload_dict)
        btn_add = QPushButton("添加词条")
        btn_add.clicked.connect(self._add_word)
        btn_import = QPushButton("导入词典文件(CSV/TSV)…")
        btn_import.clicked.connect(self._import_dict)
        btn_del = QPushButton("删除所选")
        btn_del.clicked.connect(self._del_dict_row)
        bar.addWidget(self.dict_search, 1)
        bar.addWidget(btn_add)
        bar.addWidget(btn_import)
        bar.addWidget(btn_del)
        v.addLayout(bar)

        self.dict_table = QTableWidget(0, 5)
        self.dict_table.setHorizontalHeaderLabels(["词", "拼音", "释义", "例句", "来源"])
        self.dict_table.setColumnWidth(0, 120)
        self.dict_table.setColumnWidth(1, 160)
        self.dict_table.setColumnWidth(2, 380)
        v.addWidget(self.dict_table, 1)
        self._reload_dict()
        return w

    def _reload_dict(self):
        kw = self.dict_search.text().strip()
        conn = get_conn()
        if kw:
            rows = conn.execute(
                "SELECT word,pinyin,definition,example,source FROM dictionary"
                " WHERE word LIKE ? ORDER BY id LIMIT 300", (f"%{kw}%",)).fetchall()
        else:
            rows = conn.execute(
                "SELECT word,pinyin,definition,example,source FROM dictionary"
                " WHERE source<>'cc-cedict' ORDER BY id DESC LIMIT 300").fetchall()
        self._fill_table(self.dict_table, rows)

    def _add_word(self):
        word, ok = QInputDialog.getText(self, "添加词条", "词语：")
        if not (ok and word.strip()):
            return
        pinyin, _ = QInputDialog.getText(self, "添加词条", "拼音（可空）：")
        definition, _ = QInputDialog.getText(self, "添加词条", "释义（可空）：")
        dao.add_dictionary_entry(word.strip(), pinyin.strip(), definition.strip())
        invalidate_cache()
        self._reload_dict()
        self._refresh_stats()

    def _import_dict(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入词典", "",
                                              "CSV/TSV (*.csv *.tsv *.txt)")
        if not path:
            return
        n = 0
        with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
            sample = f.read(4096)
            f.seek(0)
            first_line = sample.splitlines()[0] if sample else ""
            delim = "\t" if "\t" in first_line else ","
            reader = csv.reader(f, delimiter=delim)
            for row in reader:
                if not row or not row[0].strip():
                    continue
                word = row[0].strip()
                pinyin = row[1].strip() if len(row) > 1 else ""
                definition = row[2].strip() if len(row) > 2 else ""
                example = row[3].strip() if len(row) > 3 else ""
                dao.add_dictionary_entry(word, pinyin, definition, example,
                                         source="user")
                n += 1
        invalidate_cache()
        self._reload_dict()
        self._refresh_stats()
        info(self, f"已导入 {n} 条词条。")

    def _del_dict_row(self):
        rows = {i.row() for i in self.dict_table.selectedIndexes()}
        for r in sorted(rows, reverse=True):
            word = self.dict_table.item(r, 0).text()
            get_conn().execute("DELETE FROM dictionary WHERE word=?", (word,))
            get_conn().commit()
        self._reload_dict()

    # ================================================================ 错别字对
    def _tab_errors(self):
        w = QDialog()
        v = QVBoxLayout(w)
        bar = QHBoxLayout()
        self.err_search = QLineEdit()
        self.err_search.setPlaceholderText("搜索错别字对…")
        self.err_search.returnPressed.connect(self._reload_errors)
        btn_add = QPushButton("添加纠错对")
        btn_add.clicked.connect(self._add_pair)
        btn_import = QPushButton("批量导入(CSV: 错,对)…")
        btn_import.clicked.connect(self._import_pairs)
        btn_del = QPushButton("删除所选")
        btn_del.clicked.connect(self._del_pair_row)
        bar.addWidget(self.err_search, 1)
        bar.addWidget(btn_add)
        bar.addWidget(btn_import)
        bar.addWidget(btn_del)
        v.addLayout(bar)

        # 规则集工具条：按来源整体启停与导出
        rs = QHBoxLayout()
        rs.addWidget(QLabel("规则集"))
        self.err_source = QComboBox()
        self.err_source.setMinimumWidth(180)
        rs.addWidget(self.err_source)
        btn_on = QPushButton("启用该规则集")
        btn_on.clicked.connect(lambda: self._toggle_ruleset(True))
        btn_off = QPushButton("停用该规则集")
        btn_off.clicked.connect(lambda: self._toggle_ruleset(False))
        toggle_hint = ("只作用于词库中的纠错对。程序内置的人工精标对"
                       "（如「布署→部署」「截止→截至」）始终生效，无法停用。")
        btn_on.setToolTip(toggle_hint)
        btn_off.setToolTip(toggle_hint)
        btn_export = QPushButton("导出 CSV…")
        btn_export.clicked.connect(self._export_pairs)
        rs.addWidget(btn_on)
        rs.addWidget(btn_off)
        rs.addWidget(btn_export)
        rs.addStretch(1)
        self.err_stat = QLabel("")
        rs.addWidget(self.err_stat)
        v.addLayout(rs)

        self.err_table = QTableWidget(0, 3)
        self.err_table.setHorizontalHeaderLabels(["错误写法", "正确写法", "类别"])
        self.err_table.setColumnWidth(0, 220)
        self.err_table.setColumnWidth(1, 220)
        v.addWidget(self.err_table, 1)
        self._reload_errors()
        return w

    def _reload_rule_sets(self):
        """重填规则集下拉与统计标签，尽量保留当前选择。"""
        current = self.err_source.currentText()
        self.err_source.blockSignals(True)
        self.err_source.clear()
        self.err_source.addItem("全部规则集", "")
        for name, n in dao.error_pair_sources():
            self.err_source.addItem(f"{name}（{n} 条）", name)
        idx = self.err_source.findData(current)
        if idx >= 0:
            self.err_source.setCurrentIndex(idx)
        self.err_source.blockSignals(False)

        on = dao.count_error_pairs_by(enabled=True)
        off = dao.count_error_pairs_by(enabled=False)
        self.err_stat.setText(f"已启用 {on} 条　已停用 {off} 条")

    def _current_rule_set(self) -> str:
        return self.err_source.currentData() or ""

    def _toggle_ruleset(self, enable: bool):
        source = self._current_rule_set()
        scope = f"规则集「{source}」" if source else "全部纠错对"
        if not ask(self, f"确认{'启用' if enable else '停用'}{scope}？\n"
                         f"停用后这些纠错对立即不再参与文字纠错，但数据保留，可随时重新启用。"):
            return
        n = dao.set_error_pairs_enabled(enable, source=source)
        invalidate_cache()      # 纠错流水线缓存了词库，不失效则本次会话仍按旧规则纠
        self._reload_rule_sets()
        self._reload_errors()
        info(self, f"已{'启用' if enable else '停用'} {n} 条纠错对。")

    def _export_pairs(self):
        source = self._current_rule_set()
        total = dao.count_error_pairs_by(source=source)
        if not total:
            warn(self, "当前范围内没有可导出的纠错对。")
            return
        default = f"纠错规则集_{source or '全部'}_{date.today():%Y%m%d}.csv"
        path, _sel = QFileDialog.getSaveFileName(self, "导出纠错规则集",
                                                 default, "CSV 文件 (*.csv)")
        if not path:
            return
        try:
            n = ruleset.export_error_pairs(path, source=source)
        except OSError as exc:
            warn(self, f"导出失败：{exc}")
            return
        info(self, f"已导出 {n} 条纠错对到：\n{path}\n"
                   f"（UTF-8 BOM 编码，Excel 可直接打开；含来源与启用状态，可原样导回）")

    def _reload_errors(self):
        kw = self.err_search.text().strip()
        pairs = dao.list_error_pairs(limit=300, keyword=kw)
        self.err_table.setRowCount(0)
        for p in pairs:
            r = self.err_table.rowCount()
            self.err_table.insertRow(r)
            self.err_table.setItem(r, 0, QTableWidgetItem(p.wrong))
            self.err_table.setItem(r, 1, QTableWidgetItem(p.correct))
            self.err_table.setItem(r, 2, QTableWidgetItem(p.category))
        self._reload_rule_sets()

    def _add_pair(self):
        wrong, ok = QInputDialog.getText(self, "添加纠错对", "错误写法：")
        if not (ok and wrong.strip()):
            return
        correct, ok2 = QInputDialog.getText(self, "添加纠错对", "正确写法：")
        if not (ok2 and correct.strip()):
            return
        dao.add_error_pair(wrong.strip(), correct.strip())
        invalidate_cache()
        self._reload_errors()
        self._refresh_stats()

    def _import_pairs(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入纠错对", "",
                                              "CSV/TSV (*.csv *.tsv *.txt)")
        if not path:
            return
        n = 0
        with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
            sample = f.read(4096)
            f.seek(0)
            first = sample.splitlines()[0] if sample else ""
            delim = "\t" if "\t" in first else ","
            for row in csv.reader(f, delimiter=delim):
                if len(row) >= 2 and row[0].strip() and row[1].strip():
                    cat = row[2].strip() if len(row) > 2 else "用户导入"
                    dao.add_error_pair(row[0].strip(), row[1].strip(), cat,
                                       confidence=0.95)
                    n += 1
        invalidate_cache()
        self._reload_errors()
        self._refresh_stats()
        info(self, f"已导入 {n} 条纠错对。")

    def _del_pair_row(self):
        rows = {i.row() for i in self.err_table.selectedIndexes()}
        for r in sorted(rows, reverse=True):
            wrong = self.err_table.item(r, 0).text()
            correct = self.err_table.item(r, 1).text()
            get_conn().execute("DELETE FROM error_pairs WHERE wrong=? AND correct=?",
                               (wrong, correct))
            get_conn().commit()
        invalidate_cache()
        self._reload_errors()
        self._refresh_stats()

    # ================================================================ 常用句式
    def _tab_phrases(self):
        w = QDialog()
        v = QVBoxLayout(w)
        bar = QHBoxLayout()
        self.phr_search = QLineEdit()
        self.phr_search.setPlaceholderText("搜索句式…")
        self.phr_search.returnPressed.connect(self._reload_phrases)
        btn_add = QPushButton("添加句式")
        btn_add.clicked.connect(self._add_phrase)
        btn_import = QPushButton("批量导入(每行一条)…")
        btn_import.clicked.connect(self._import_phrases)
        btn_del = QPushButton("删除所选")
        btn_del.clicked.connect(self._del_phrase_row)
        bar.addWidget(self.phr_search, 1)
        bar.addWidget(btn_add)
        bar.addWidget(btn_import)
        bar.addWidget(btn_del)
        v.addLayout(bar)

        self.phr_table = QTableWidget(0, 3)
        self.phr_table.setHorizontalHeaderLabels(["句式/片段", "备注", "标签"])
        self.phr_table.setColumnWidth(0, 500)
        v.addWidget(self.phr_table, 1)
        self._reload_phrases()
        return w

    def _reload_phrases(self):
        kw = self.phr_search.text().strip()
        phrases = dao.list_phrases(limit=300, keyword=kw)
        self.phr_table.setRowCount(0)
        for p in phrases:
            r = self.phr_table.rowCount()
            self.phr_table.insertRow(r)
            self.phr_table.setItem(r, 0, QTableWidgetItem(p.phrase))
            self.phr_table.setItem(r, 1, QTableWidgetItem(p.context[:80]))
            self.phr_table.setItem(r, 2, QTableWidgetItem(p.tag))

    def _add_phrase(self):
        phrase, ok = QInputDialog.getText(self, "添加句式", "句式/片段：")
        if not (ok and phrase.strip()):
            return
        tag, _ = QInputDialog.getText(self, "添加句式", "标签（可空）：")
        dao.add_phrase(phrase.strip(), tag=tag.strip())
        invalidate_cache()
        self._reload_phrases()
        self._refresh_stats()

    def _import_phrases(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入句式", "",
                                              "文本 (*.txt *.md *.csv)")
        if not path:
            return
        n = 0
        for line in Path(path).read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = line.strip()
            if line:
                dao.add_phrase(line[:2000], source="导入", tag="批量导入")
                n += 1
        invalidate_cache()
        self._reload_phrases()
        self._refresh_stats()
        info(self, f"已导入 {n} 条句式。")

    def _del_phrase_row(self):
        rows = {i.row() for i in self.phr_table.selectedIndexes()}
        for r in sorted(rows, reverse=True):
            phrase = self.phr_table.item(r, 0).text()
            get_conn().execute(
                "DELETE FROM user_phrases WHERE id="
                "(SELECT id FROM user_phrases WHERE phrase=? LIMIT 1)",
                (phrase,))
            get_conn().commit()
        from ..db.connection import get_conn as gc
        conn = gc()
        # 同步 FTS：全量重建 phrases 索引
        conn.execute("DELETE FROM phrases_fts")
        for p in dao.list_phrases(limit=100000):
            from ..db.tokenize import tokenize
            conn.execute("INSERT INTO phrases_fts(phrase,tokenized,ref_id) VALUES(?,?,?)",
                         (tokenize(p.phrase), tokenize(p.phrase + " " + p.context), p.id))
        conn.commit()
        self._reload_phrases()
        self._refresh_stats()

    def _fill_table(self, table: QTableWidget, rows):
        table.setRowCount(0)
        for row in rows:
            r = table.rowCount()
            table.insertRow(r)
            for c, val in enumerate(row):
                table.setItem(r, c, QTableWidgetItem(str(val or "")))

    # ================================================================ 忽略名单
    def _tab_ignore(self):
        w = QDialog()
        v = QVBoxLayout(w)
        tip = QLabel("忽略名单中的词不再参与纠错提示（用于人名、地名、专有名词等）。")
        v.addWidget(tip)
        bar = QHBoxLayout()
        self.ig_search = QLineEdit()
        self.ig_search.setPlaceholderText("添加要忽略的词（回车添加）…")
        self.ig_search.returnPressed.connect(self._add_ignore)
        btn_del = QPushButton("删除所选")
        btn_del.clicked.connect(self._del_ignore)
        btn_clear = QPushButton("清空全部")
        btn_clear.clicked.connect(self._clear_ignore)
        bar.addWidget(self.ig_search, 1)
        bar.addWidget(btn_del)
        bar.addWidget(btn_clear)
        v.addLayout(bar)
        self.ig_list = QListWidget()
        v.addWidget(self.ig_list, 1)
        self._reload_ignore()
        return w

    def _reload_ignore(self):
        self.ig_list.clear()
        for word in sorted(dao.all_ignore_words()):
            self.ig_list.addItem(word)

    def _add_ignore(self):
        word = self.ig_search.text().strip()
        if word:
            dao.add_ignore_word(word)
            from ..core.corrector import invalidate_cache
            invalidate_cache()
            self.ig_search.clear()
            self._reload_ignore()

    def _del_ignore(self):
        for item in self.ig_list.selectedItems():
            dao.remove_ignore_word(item.text())
        from ..core.corrector import invalidate_cache
        invalidate_cache()
        self._reload_ignore()

    def _clear_ignore(self):
        if ask(self, "清空全部忽略名单？"):
            for word in list(dao.all_ignore_words()):
                dao.remove_ignore_word(word)
            from ..core.corrector import invalidate_cache
            invalidate_cache()
            self._reload_ignore()
