# -*- coding: utf-8 -*-
"""模板编辑器：可视化设置全部排版参数，右侧实时预览（首页 PDF 渲染）。"""
from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap, QPainter
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QPushButton, QScrollArea,
                               QSpinBox, QTabWidget, QTextEdit, QVBoxLayout,
                               QWidget)

from ..core.model import Block, DocTree
from ..core.template import DocTemplate, default_template
from ..db import dao
from .widgets import (ask, font_families_official_first, info,
                      make_font_combo, missing_official_fonts)


class TemplateEditor(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("模板管理")
        self.resize(1000, 640)
        self._build_ui()
        self._fill_template_list()
        self._load_template()

    def _build_ui(self):
        root = QHBoxLayout(self)
        # 左：模板列表
        left = QVBoxLayout()
        self.tpl_list = QListWidget()
        self.tpl_list.currentRowChanged.connect(self._on_select_template)
        left.addWidget(self.tpl_list, 1)
        btn_new = QPushButton("新建模板")
        btn_new.clicked.connect(self._new_template)
        btn_del = QPushButton("删除所选模板")
        btn_del.clicked.connect(self._delete_template)
        left.addWidget(btn_new)
        left.addWidget(btn_del)
        lw = QWidget()
        lw.setLayout(left)
        root.addWidget(lw, 1)

        # 中：参数表单
        self.tabs = QTabWidget()
        self.tabs.addTab(self._page_page(), "页面与正文")
        self.tabs.addTab(self._page_headings(), "标题样式")
        self.tabs.addTab(self._page_parts(), "红头/封面/页码/版记")
        root.addWidget(self.tabs, 2)

        # 右：预览 + 保存
        right = QVBoxLayout()
        self.preview_area = QScrollArea()
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignHCenter)
        self.preview_area.setWidget(self.preview_label)
        right.addWidget(self.preview_area, 1)
        btn_preview = QPushButton("刷新预览")
        btn_preview.clicked.connect(self.refresh_preview)
        right.addWidget(btn_preview)
        btn_save = QPushButton("保存模板")
        btn_save.clicked.connect(self._save)
        btn_save_default = QPushButton("保存并设为默认")
        btn_save_default.clicked.connect(lambda: self._save(set_default=True))
        right.addWidget(btn_save)
        right.addWidget(btn_save_default)
        rw = QWidget()
        rw.setLayout(right)
        root.addWidget(rw, 2)

    # ------------------------------------------------ 参数页
    def _page_page(self):
        w = QWidget()
        form = QFormLayout(w)
        self.sp_w = QDoubleSpinBox(); self.sp_w.setRange(100, 500); self.sp_w.setSuffix(" mm")
        self.sp_h = QDoubleSpinBox(); self.sp_h.setRange(100, 600); self.sp_h.setSuffix(" mm")
        form.addRow("页面宽/高：", _pair(self.sp_w, self.sp_h))
        self.sp_mt = QDoubleSpinBox(); self.sp_mb = QDoubleSpinBox()
        for s in (self.sp_mt, self.sp_mb):
            s.setRange(0, 150); s.setSuffix(" mm")
        form.addRow("上/下边距：", _pair(self.sp_mt, self.sp_mb))
        self.sp_ml = QDoubleSpinBox(); self.sp_mr = QDoubleSpinBox()
        for s in (self.sp_ml, self.sp_mr):
            s.setRange(0, 150); s.setSuffix(" mm")
        form.addRow("左/右边距：", _pair(self.sp_ml, self.sp_mr))
        self.cb_body_font = make_font_combo()
        form.addRow("正文字体：", self.cb_body_font)
        self.sp_body_size = QDoubleSpinBox(); self.sp_body_size.setRange(8, 48); self.sp_body_size.setSuffix(" pt")
        form.addRow("正文字号：", self.sp_body_size)
        self.sp_line = QDoubleSpinBox(); self.sp_line.setRange(12, 80); self.sp_line.setSuffix(" 磅")
        form.addRow("固定行距：", self.sp_line)
        self.sp_indent = QDoubleSpinBox(); self.sp_indent.setRange(0, 8)
        form.addRow("首行缩进（字符）：", self.sp_indent)
        self.cb_align = QComboBox()
        self.cb_align.addItems(["justify", "left", "center"])
        form.addRow("对齐方式：", self.cb_align)
        return w

    def _page_headings(self):
        w = QWidget()
        form = QFormLayout(w)
        self.h_fonts, self.h_sizes, self.h_bold = [], [], []
        for name in ("一级标题（如 一、）", "二级标题（如 （一））", "三级标题（如 1.）"):
            cb = make_font_combo()
            sp = QDoubleSpinBox(); sp.setRange(8, 48); sp.setSuffix(" pt")
            cbx = QCheckBox("加粗")
            self.h_fonts.append(cb)
            self.h_sizes.append(sp)
            self.h_bold.append(cbx)
            form.addRow(name, _pair(cb, sp))
            form.addRow("", cbx)
        tip = QLabel("提示：TOC 目录抓取 1-3 级标题。")
        form.addRow(tip)
        return w

    def _page_parts(self):
        w = QWidget()
        form = QFormLayout(w)
        # 红头
        self.chk_red = QCheckBox("启用红头")
        form.addRow(self.chk_red)
        self.ed_org = QLineEdit()
        form.addRow("发文机关标志：", self.ed_org)
        self.cb_org_font = make_font_combo()
        form.addRow("红头字体：", self.cb_org_font)
        self.sp_org_size = QDoubleSpinBox(); self.sp_org_size.setRange(16, 72); self.sp_org_size.setSuffix(" pt")
        form.addRow("红头字号：", self.sp_org_size)
        self.ed_docno = QLineEdit()
        form.addRow("发文字号：", self.ed_docno)
        # 页码
        self.chk_pageno = QCheckBox("启用页码（奇右偶左=外侧，宋体四号）")
        form.addRow(self.chk_pageno)
        self.ed_pageno_fmt = QLineEdit()
        self.ed_pageno_fmt.setToolTip("{page} 为页码占位")
        form.addRow("页码格式：", self.ed_pageno_fmt)
        self.sp_pageno_size = QDoubleSpinBox(); self.sp_pageno_size.setRange(8, 24); self.sp_pageno_size.setSuffix(" pt")
        form.addRow("页码字号：", self.sp_pageno_size)
        # 目录
        self.chk_toc = QCheckBox("生成目录")
        form.addRow(self.chk_toc)
        # 版记
        self.chk_colophon = QCheckBox("启用版记")
        form.addRow(self.chk_colophon)
        self.ed_colophon = QTextEdit()
        self.ed_colophon.setMaximumHeight(80)
        self.ed_colophon.setPlaceholderText("每行一条，如：\n抄送：有关单位。\n××办公室  2026年8月30日印发")
        form.addRow("版记内容：", self.ed_colophon)
        # 水印/密级
        self.chk_wm = QCheckBox("启用文字水印/密级标注")
        form.addRow(self.chk_wm)
        self.ed_wm = QLineEdit()
        self.ed_wm.setPlaceholderText("如：征求意见稿 / 秘密★1年 / 仅供参考")
        form.addRow("水印文字：", self.ed_wm)
        self.sp_wm_opacity = QDoubleSpinBox()
        self.sp_wm_opacity.setRange(0.03, 0.5)
        self.sp_wm_opacity.setSingleStep(0.01)
        form.addRow("水印透明度：", self.sp_wm_opacity)
        self.sp_wm_angle = QDoubleSpinBox()
        self.sp_wm_angle.setRange(-90, 90)
        self.sp_wm_angle.setValue(45)
        self.sp_wm_angle.setSuffix("°")
        form.addRow("水印角度：", self.sp_wm_angle)
        return w

    # ------------------------------------------------ 模板列表
    def _fill_template_list(self):
        self.tpl_list.clear()
        for t in dao.list_templates():
            self.tpl_list.addItem(t["name"] + ("（默认）" if t["is_default"] else ""))

    def _on_select_template(self, row):
        self._load_template()

    def _current_template_name(self) -> str:
        item = self.tpl_list.currentItem()
        if not item:
            return ""
        return item.text().replace("（默认）", "")

    def _load_template(self):
        name = self._current_template_name()
        cfg = dao.get_template_config(name) if name else ""
        tpl = DocTemplate.from_json(cfg) if cfg else default_template()
        self._tpl = tpl
        self._template_to_form(tpl)
        self.refresh_preview()

    def _template_to_form(self, t: DocTemplate):
        self.sp_w.setValue(t.page_width_mm)
        self.sp_h.setValue(t.page_height_mm)
        self.sp_mt.setValue(t.margin_top_mm)
        self.sp_mb.setValue(t.margin_bottom_mm)
        self.sp_ml.setValue(t.margin_left_mm)
        self.sp_mr.setValue(t.margin_right_mm)
        _set_combo_text(self.cb_body_font, t.body_font)
        self.sp_body_size.setValue(t.body_size_pt)
        self.sp_line.setValue(t.line_spacing_pt)
        self.sp_indent.setValue(t.first_line_indent_chars)
        self.cb_align.setCurrentText(t.align)
        for i, hs in enumerate((t.h1, t.h2, t.h3)):
            _set_combo_text(self.h_fonts[i], hs.font)
            self.h_sizes[i].setValue(hs.size_pt)
            self.h_bold[i].setChecked(hs.bold)
        self.chk_red.setChecked(t.red_header.enabled)
        self.ed_org.setText(t.red_header.org)
        _set_combo_text(self.cb_org_font, t.red_header.org_font)
        self.sp_org_size.setValue(t.red_header.org_size_pt)
        self.ed_docno.setText(t.red_header.doc_number)
        self.chk_pageno.setChecked(t.page_number_enabled)
        self.ed_pageno_fmt.setText(t.page_number_format)
        self.sp_pageno_size.setValue(t.page_number_size_pt)
        self.chk_toc.setChecked(t.toc_enabled)
        self.chk_colophon.setChecked(t.colophon.enabled)
        self.ed_colophon.setPlainText("\n".join(t.colophon.lines))
        self.chk_wm.setChecked(bool(t.watermark_text))
        self.ed_wm.setText(t.watermark_text)
        self.sp_wm_opacity.setValue(t.watermark_opacity)
        self.sp_wm_angle.setValue(t.watermark_angle)

    def _form_to_template(self) -> DocTemplate:
        t = self._tpl
        t.page_width_mm = self.sp_w.value()
        t.page_height_mm = self.sp_h.value()
        t.margin_top_mm = self.sp_mt.value()
        t.margin_bottom_mm = self.sp_mb.value()
        t.margin_left_mm = self.sp_ml.value()
        t.margin_right_mm = self.sp_mr.value()
        t.body_font = self.cb_body_font.currentText()
        t.body_size_pt = self.sp_body_size.value()
        t.line_spacing_pt = self.sp_line.value()
        t.first_line_indent_chars = self.sp_indent.value()
        t.align = self.cb_align.currentText()
        from ..core.template import HeadingStyle
        t.h1 = HeadingStyle(self.h_fonts[0].currentText(), self.h_sizes[0].value(),
                            self.h_bold[0].isChecked())
        t.h2 = HeadingStyle(self.h_fonts[1].currentText(), self.h_sizes[1].value(),
                            self.h_bold[1].isChecked())
        t.h3 = HeadingStyle(self.h_fonts[2].currentText(), self.h_sizes[2].value(),
                            self.h_bold[2].isChecked())
        t.red_header.enabled = self.chk_red.isChecked()
        t.red_header.org = self.ed_org.text()
        t.red_header.org_font = self.cb_org_font.currentText()
        t.red_header.org_size_pt = self.sp_org_size.value()
        t.red_header.doc_number = self.ed_docno.text()
        t.page_number_enabled = self.chk_pageno.isChecked()
        t.page_number_format = self.ed_pageno_fmt.text() or "— {page} —"
        t.page_number_size_pt = self.sp_pageno_size.value()
        t.toc_enabled = self.chk_toc.isChecked()
        t.colophon.enabled = self.chk_colophon.isChecked()
        t.colophon.lines = [ln for ln in self.ed_colophon.toPlainText().splitlines() if ln.strip()]
        t.watermark_text = self.ed_wm.text().strip() if self.chk_wm.isChecked() else ""
        t.watermark_opacity = self.sp_wm_opacity.value()
        t.watermark_angle = self.sp_wm_angle.value()
        return t

    # ------------------------------------------------ 操作
    def _new_template(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "新建模板", "模板名称：",
                                        text=f"我的模板{dao.list_templates().__len__() + 1}")
        if ok and name.strip():
            base = self._form_to_template()
            clone = base.clone(name.strip())
            dao.save_template(clone.name, clone.to_json())
            self._fill_template_list()
            rows = self.tpl_list.count()
            for i in range(rows):
                if self.tpl_list.item(i).text().startswith(name.strip()):
                    self.tpl_list.setCurrentRow(i)
                    break

    def _delete_template(self):
        name = self._current_template_name()
        if not name:
            return
        if ask(self, f"删除模板「{name}」？"):
            from ..db.connection import get_conn
            get_conn().execute("DELETE FROM templates WHERE name=?", (name,))
            get_conn().commit()
            self._fill_template_list()
            self._load_template()

    def _save(self, set_default: bool = False):
        t = self._form_to_template()
        name = self._current_template_name() or t.name
        t.name = name
        dao.save_template(name, t.to_json(), is_default=set_default)
        self._fill_template_list()
        info(self, f"模板「{name}」已保存" + ("，并设为默认。" if set_default else "。"))
        self.refresh_preview()

    def refresh_preview(self):
        """渲染首页预览（红头+标题样式近似效果）。"""
        t = self._form_to_template()
        try:
            from ..core.pdfrender import trees_to_html
            tree = DocTree(title="关于××××的报告")
            tree.blocks = [
                Block(type="heading", level=1, text="一、总体情况"),
                Block(type="paragraph", text="今年以来，在上级部门的坚强领导下，我们坚持以高质量发展为主线，各项工作稳步推进，取得了阶段性成效。现将有关情况报告如下。"),
                Block(type="heading", level=2, text="（一）主要成效"),
                Block(type="paragraph", text="一是重点任务全面完成；二是制度建设持续加强；三是队伍建设成效明显。"),
            ]
            demo = t.clone("preview")
            demo.cover.enabled = False
            demo.toc_enabled = False
            demo.page_number_enabled = False
            html = trees_to_html([tree], demo, toc_pages=[])
            # 用 QTextDocument 离屏渲染为图片
            from PySide6.QtGui import QTextDocument
            from PySide6.QtCore import QSizeF, QSize
            doc = QTextDocument()
            doc.setHtml(html)
            doc.setTextWidth(460)
            size = doc.size().toSize()
            from PySide6.QtGui import QPainter, QColor
            img = QImage(max(size.width(), 100) + 20, max(size.height(), 100) + 20,
                         QImage.Format_RGB888)
            img.fill(0xffffff)
            painter = QPainter(img)
            painter.translate(10, 10)
            doc.drawContents(painter)
            painter.end()
            self.preview_label.setPixmap(QPixmap.fromImage(img))
            self.preview_label.resize(img.size())
        except Exception as exc:  # noqa: BLE001
            self.preview_label.setText(f"预览失败：{exc}")


def _pair(a: QWidget, b: QWidget) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(a)
    h.addWidget(b)
    return w


def _set_combo_text(cb: QComboBox, text: str):
    idx = cb.findText(text)
    if idx >= 0:
        cb.setCurrentIndex(idx)
    else:
        cb.addItem(text)
        cb.setCurrentIndex(cb.count() - 1)
