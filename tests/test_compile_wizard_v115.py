# -*- coding: utf-8 -*-
"""v1.15 一键汇编向导增强（UI 层）回归测试。

覆盖：
1. 新增控件与默认值：封面（默认开/日期今天）、目录/页码（默认开/目录标题可改/
   正文页码从 1 说明）、字体方案展示、输出目录/文件名可见；
2. 输出钩子：DOCX / A4 PDF / A3 小册子，勾 A3 自动勾 A4；
3. 选项组合 → CompileWorker.req 与模板副本正确（不改 CompileRequest 结构）；
4. 健壮性：空选择拦截、未选输出拦截、封面全留空不炸、失败只写状态不弹模态；
5. 材料识别结果（标题/一级/二级/正文块数）+ 坏 blocks_json 兜底不崩；
6. 字体方案 standard/military/custom 与模板下拉启用逻辑。

全部离屏（qapp 夹具）+ 临时库；不启动真实生成线程，只检查请求/状态。
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest
from PySide6.QtWidgets import QDialog

import gwtool.ui.compile_wizard as cw
from gwtool.db import dao


def _add_doc(title="材料一", text="正文内容", blocks_json="[]"):
    return dao.add_document(dao.Document(
        title=title, content_text=text, blocks_json=blocks_json,
        file_type="txt"))


def _block(level=0, btype="paragraph", text="正文"):
    return {"type": btype, "level": level, "text": text,
            "align": "left", "rows": None}


@pytest.fixture()
def wizard(tmp_db, qapp, monkeypatch, tmp_path):
    from gwtool import app
    app.ensure_database_seeded()
    monkeypatch.setattr(QDialog, "exec",
                        lambda self: QDialog.DialogCode.Rejected)
    out = tmp_path / "export"
    out.mkdir()
    monkeypatch.setattr(cw, "export_dir", lambda: out)
    # 不在测试里起真实生成线程：只验证向导装配出的请求与交互状态
    monkeypatch.setattr(cw.CompileWorker, "start", lambda self: None)
    monkeypatch.setattr(cw.PdfRenderWorker, "start", lambda self: None)
    monkeypatch.setattr(cw.BookletWorker, "start", lambda self: None)
    w = cw.CompileWizard()
    yield w
    w.close()


def _pick_docs(w, count=1) -> list[int]:
    docs = dao.list_documents()
    assert len(docs) >= count
    w._order = [d.id for d in docs]
    w._checked = {d.id for d in docs[:count]}
    w._render_materials()
    return sorted(w._checked)


# ---------------------------------------------------------------- 默认可用
def test_new_controls_and_defaults(wizard):
    w = wizard
    # 输出钩子
    assert w.chk_docx.isChecked() is True
    assert w.chk_pdf.isChecked() is False
    assert w.chk_booklet.isChecked() is False
    assert w.ed_out_dir.text().strip(), "输出目录必须可见"
    assert w.ed_out_name is not None, "文件名控件必须存在"
    assert w.lbl_out_preview.text().startswith("将输出到：")
    # 封面
    assert w.chk_cover.isChecked() is True
    today = datetime.now()
    assert str(today.year) in w.ed_date.text() and "年" in w.ed_date.text()
    # 目录/页码
    assert w.chk_toc.isChecked() is True
    assert w.chk_page_numbers.isChecked() is True
    assert w.ed_toc_title.text() == "目  录"
    assert "正文页码从 1 开始" in w.lbl_page_start.text()
    # 字体方案：三种选择 + 当前模板字体展示
    schemes = [w.cmb_scheme.itemData(i)
               for i in range(w.cmb_scheme.count())]
    assert schemes == ["standard", "military", "custom"]
    assert "字体方案" in w.lbl_fonts.text()
    assert "正文" in w.lbl_fonts.text()


def test_a3_auto_checks_a4(wizard):
    w = wizard
    w.chk_pdf.setChecked(False)
    w.chk_booklet.setChecked(True)
    assert w.chk_pdf.isChecked() is True, "勾 A3 必须自动勾上 A4（拼版依赖）"
    # A4/A3 可独立勾选：A4 本身不依赖 A3
    w.chk_booklet.setChecked(False)
    w.chk_pdf.setChecked(True)
    assert w.chk_booklet.isChecked() is False


def test_font_scheme_controls_template_choice(wizard):
    w = wizard
    assert w.load_template().name  # 默认方案能取到模板
    w.cmb_scheme.setCurrentIndex(w.cmb_scheme.findData("military"))
    assert w.load_template().name == "军队公文"
    assert w.tpl_combo.isEnabled() is True, "模板选择保持既有交互（始终可用）"
    w.cmb_scheme.setCurrentIndex(w.cmb_scheme.findData("custom"))
    assert w.tpl_combo.isEnabled() is True


# ---------------------------------------------------------------- 空选择/无输出
def test_empty_selection_blocked_non_modal(wizard, monkeypatch):
    w = wizard
    calls: list[str] = []
    monkeypatch.setattr(cw, "info", lambda *a, **k: calls.append(str(a[-1])))
    w._checked.clear()
    w._render_materials()
    w._start()
    assert "勾选材料" in w.lbl_result.text()
    assert calls == [], "空选择不应弹模态框"
    assert w._worker is None
    assert w.progress.isVisibleTo(w) is False


def test_no_output_blocked(wizard, monkeypatch):
    w = wizard
    _add_doc()
    _pick_docs(w)
    monkeypatch.setattr(cw, "info", lambda *a, **k: None)
    w.chk_docx.setChecked(False)
    w.chk_pdf.setChecked(False)
    w.chk_booklet.setChecked(False)
    w._start()
    assert "至少勾选一种输出" in w.lbl_result.text()
    assert w._worker is None


def test_failure_writes_status_not_modal(wizard, monkeypatch):
    w = wizard
    _add_doc()
    _pick_docs(w)
    calls: list[str] = []
    monkeypatch.setattr(cw, "info", lambda *a, **k: calls.append(str(a[-1])))

    def _fail_start(self):
        self.error.emit("模拟渲染失败")

    monkeypatch.setattr(cw.CompileWorker, "start", _fail_start)
    w.chk_docx.setChecked(True)
    w._start()
    assert "生成失败" in w.lbl_result.text()
    assert "模拟渲染失败" in w.lbl_result.text()
    assert calls == [], "生成失败不应弹模态框"
    assert w.btn_start.isEnabled() is True
    assert w.progress.isHidden(), "失败后进度条应隐藏"


# ---------------------------------------------------------------- 选项 → req
def test_a3_only_starts_a4_pdf_worker(wizard, monkeypatch, tmp_path):
    """只勾 A3：自动补 A4 后应走 PDF 渲染 worker（而不是 DOCX）。"""
    w = wizard
    _add_doc()
    _pick_docs(w)
    w.chk_docx.setChecked(False)
    w.chk_pdf.setChecked(False)
    w.chk_booklet.setChecked(True)          # 自动补勾 A4
    w.ed_out_name.setText("a3样本")
    w._start()
    assert w._worker is None, "只勾 A3 时不应启动 DOCX worker"
    assert w._pdf_worker is not None, "A3 依赖 A4：应启动 PDF 渲染 worker"
    assert w._pdf_worker.out_pdf == str(tmp_path / "export" / "a3样本.pdf")
    assert w.chk_pdf.isChecked() is True


def test_options_map_to_compile_request(wizard, monkeypatch, tmp_path):
    w = wizard
    did = _add_doc("材料甲", "甲的内容")
    _pick_docs(w)
    outdir = tmp_path / "自选目录"
    outdir.mkdir()
    w._out_dir = outdir
    w.ed_out_dir.setText(str(outdir))
    w.ed_out_name.setText("自定义文件名")
    w.ed_title.setText("汇编标题")
    w.ed_subtitle.setText("副标题")
    w.ed_org.setText("某某单位")
    w.ed_date.setText("2026年10月")
    w.chk_cover.setChecked(True)
    w.chk_titles.setChecked(True)
    w.chk_toc.setChecked(False)
    w.ed_toc_title.setText("自定义目录")
    w.chk_page_numbers.setChecked(False)
    w.chk_sources.setChecked(True)
    w.chk_docx.setChecked(True)
    w.chk_pdf.setChecked(False)
    w.chk_booklet.setChecked(False)

    w._start()
    worker = w._worker
    assert worker is not None, "应启动 DOCX 生成 worker"
    req = worker.req
    # CompileRequest 字段以 core/compiler.py 为准（未新增字段）
    assert req.doc_ids == [did]
    assert req.extra_paths == []
    assert req.out_docx == str(outdir / "自定义文件名.docx")
    assert req.include_sources is True
    tpl = req.template
    assert tpl is not None
    assert tpl.cover.enabled is True
    assert tpl.cover.title == "汇编标题"
    assert tpl.cover.subtitle == "副标题"
    assert tpl.cover.org == "某某单位"
    assert tpl.cover.date == "2026年10月"
    assert tpl.insert_material_titles is True
    assert tpl.toc_enabled is False
    assert tpl.toc_title == "自定义目录"
    assert tpl.page_number_enabled is False
    # 生成中：按钮禁用 + 进度可见（真实 worker 由 stub start 挂起）
    assert w.btn_start.isEnabled() is False
    assert not w.progress.isHidden(), "生成中进度条应可见"


def test_cover_all_blank_does_not_crash(wizard, monkeypatch):
    w = wizard
    _add_doc()
    _pick_docs(w)
    for field in (w.ed_title, w.ed_subtitle, w.ed_org, w.ed_date):
        field.setText("")
    w.ed_out_name.setText("")
    w.chk_cover.setChecked(True)
    w.chk_docx.setChecked(True)
    w.chk_pdf.setChecked(False)
    w.chk_booklet.setChecked(False)

    w._start()
    worker = w._worker
    assert worker is not None
    tpl = worker.req.template
    assert tpl.cover.enabled is True
    assert (tpl.cover.title, tpl.cover.subtitle, tpl.cover.org,
            tpl.cover.date) == ("", "", "", "")
    # 标题全空时文件名回落到「汇编成果」，旧行为一致且不崩
    assert worker.req.out_docx.endswith("汇编成果.docx")


def test_batch_mode_keeps_original_path(wizard, monkeypatch):
    w = wizard
    _add_doc()
    ids = _pick_docs(w)
    seen: list[list[int]] = []
    monkeypatch.setattr(w, "_run_batch", lambda _ids: seen.append(list(_ids)))
    w.chk_batch.setChecked(True)
    w._start()
    assert seen == [ids], "批量模式应原样走 _run_batch"


# ---------------------------------------------------------------- 材料识别
def test_material_analysis_display_and_bad_json(tmp_db, qapp, monkeypatch,
                                                tmp_path):
    from gwtool import app
    app.ensure_database_seeded()
    good_blocks = [
        _block(0, "heading", "一、总体要求"),
        _block(2, "heading", "（一）具体安排"),
        _block(0, "paragraph", "正文一"),
        _block(0, "paragraph", "正文二"),
    ]
    did_good = _add_doc("结构材料", "正文", json.dumps(good_blocks,
                                                        ensure_ascii=False))
    did_bad = _add_doc("坏结构材料", "纯文本", "{not-json")
    out = tmp_path / "export"
    out.mkdir()
    monkeypatch.setattr(cw, "export_dir", lambda: out)
    w = cw.CompileWizard()
    try:
        stats = w.material_analysis(did_good)
        assert stats.get("h1") == 1
        assert stats.get("h2") == 1
        # T2 规范化的落点可能把连续正文行合并（merged_lines），因此只断言
        # "正文块数被识别出来"；接口未就绪时本地兜底会给 2。
        assert stats.get("body") >= 1
        assert stats.get("title") == 1
        assert stats.get("source") in ("compile_norm.normalize_tree_ex",
                                       "compile_norm.analyze_tree",
                                       "blocks_json")
        bad = w.material_analysis(did_bad)
        assert set(bad) >= {"title", "h1", "h2", "body"}
        assert all(isinstance(bad[k], int) for k in ("title", "h1", "h2", "body"))
        assert w.material_analysis(999999) == {}
        # 列表项文案必须展示识别结果
        labels = [w.material_list.item(i).text()
                  for i in range(w.material_list.count())]
        assert any("识别：" in s and "正文" in s for s in labels)
    finally:
        w.close()
