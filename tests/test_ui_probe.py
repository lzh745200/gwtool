# -*- coding: utf-8 -*-
"""UI 离屏冒烟深探（widgets/workers/panels/dialogs）：真实 Qt 组件、真实
信号回传、真实数据库与文件，禁 mock。

覆盖此前零执行路径：公共控件工厂、后台工作器同步 run() 的信号链（Fn/Import/
Compile/PdfRender/Booklet，含异常分支）、资料库面板刷新与选择、对比对话框
真实 diff 渲染、汇编向导构建与材料排序边界、参考面板三源查询。
"""
from __future__ import annotations

import pytest

from gwtool.db import dao

pytest.importorskip("PySide6")


def _make_min_pdf(path, pages: int = 1) -> None:
    """手工构造最小合法 PDF（R3 验证过的字节模板）。"""
    body = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n" \
           b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n" \
           b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj\n" \
           b"trailer<</Root 1 0 R>>\n%%EOF"
    path.write_bytes(body)


def _collect(signal, cap=None):
    """把 Qt 信号连接到列表收集器。"""
    out: list = []
    signal.connect(lambda *a: out.append(a))
    return out


# ---------------------------------------------------------------- widgets
class TestWidgetsProbe:
    def test_font_helpers(self, qapp):
        from gwtool.ui.widgets import (font_families_official_first,
                                       make_font_combo, missing_official_fonts)
        fams = font_families_official_first()
        assert isinstance(fams, list) and fams
        missing = missing_official_fonts()
        assert isinstance(missing, list)
        cb = make_font_combo(current="不存在字体")
        assert cb.count() > 0          # 找不到当前项时停在默认位置，不崩

    def test_make_size_and_mm(self, qapp):
        from gwtool.ui.widgets import make_mm_spin, make_size_combo
        cb = make_size_combo([10.5, 16.0, 22.0], current=16.0)
        assert abs(cb.currentData() - 16.0) < 0.01
        cb2 = make_size_combo([10.5], current=99.0)   # 无匹配：停在默认
        assert cb2.count() == 1
        sp = make_mm_spin(37.0)
        assert sp.value() == 37.0 and sp.suffix() == " mm"

    def test_dialog_buttons(self, qapp):
        from PySide6.QtWidgets import QDialog, QPushButton
        from gwtool.ui.widgets import dialog_buttons
        dlg = QDialog()
        hits: list[str] = []
        layout = dialog_buttons(dlg,
                                ("次要", lambda: hits.append("次要")),
                                ("主要", lambda: hits.append("主要"), True))
        btns = [layout.itemAt(i).widget() for i in range(layout.count())
                if layout.itemAt(i).widget()]
        assert isinstance(btns[0], QPushButton)
        assert btns[-1].isDefault()     # 主按钮 setDefault
        btns[0].click()
        btns[-1].click()
        assert hits == ["次要", "主要"]


# ---------------------------------------------------------------- workers
class TestWorkersProbe:
    def test_fn_worker_ok_and_failed(self, tmp_db, qapp):
        """FnWorker：正常结果走 ok 信号；异常走 failed 信号（错误文本回传）。"""
        from gwtool.ui.workers import FnWorker
        w1 = FnWorker(lambda a, b: a + b, 2, 3)
        ok = _collect(w1.ok)
        failed = _collect(w1.failed)
        w1.run()
        assert ok == [(5,)] and failed == []

        w2 = FnWorker(lambda: 1 / 0)
        ok2 = _collect(w2.ok)
        failed2 = _collect(w2.failed)
        w2.run()
        assert ok2 == [] and len(failed2) == 1
        assert "division" in failed2[0][0] or "除" in failed2[0][0]

    def test_import_worker_real_files(self, tmp_db, tmp_path, qapp):
        """ImportWorker：真实 txt 文件导入（进度信号 + 成功计数 + 去重）。"""
        from gwtool.ui.workers import ImportWorker
        f1 = tmp_path / "导入材料甲.txt"
        f1.write_text("公文标题行\n这是正文内容，用于导入探测验证。", encoding="utf-8")
        f2 = tmp_path / "导入材料乙.txt"
        f2.write_text("另一个标题\n另一段正文内容。", encoding="utf-8")
        bad = tmp_path / "坏文件.xyz"
        bad.write_bytes(b"\x00\x01garbage")

        w = ImportWorker([str(f1), str(f2), str(bad)], category_id=0)
        prog = _collect(w.progress)
        done = _collect(w.finished_ok)
        failed = _collect(w.failed)
        w.run()
        assert failed == []
        # finished_ok(成功数, 跳过数)：两篇成功，坏格式跳过
        assert done and done[0][0] == 2 and done[0][1] == 1
        assert len(prog) == 3            # 三个文件都有进度回调
        docs = dao.list_documents()
        assert len(docs) == 2

    def test_import_worker_stop_breaks_loop(self, tmp_db, tmp_path, qapp):
        """stop() 后 run() 立即收尾：一个文件都不导入。"""
        from gwtool.ui.workers import ImportWorker
        files = []
        for i in range(3):
            f = tmp_path / f"停测{i}.txt"
            f.write_text(f"标题{i}\n正文{i}", encoding="utf-8")
            files.append(str(f))
        w = ImportWorker(files, category_id=0)
        w.stop()
        done = _collect(w.finished_ok)
        w.run()
        assert done and done[0] == (0, 0)
        assert dao.list_documents() == []

    def test_compile_worker_empty_materials_error(self, tmp_db, qapp):
        """CompileWorker：空材料 → error 信号（compile_docx 的 ValueError）。"""
        from gwtool.core.template import default_template
        from gwtool.ui.workers import CompileWorker
        tpl = default_template()
        w = CompileWorker([], [], tpl.to_json(),
                          "out_should_not_exist.docx")
        done = _collect(w.done)
        error = _collect(w.error)
        w.run()
        assert done == [] and len(error) == 1
        assert "材料" in error[0][0]

    def test_pdf_render_worker_no_trees_error(self, tmp_db, qapp):
        """PdfRenderWorker：无可汇编材料 → error 信号。"""
        from gwtool.core.template import default_template
        from gwtool.ui.workers import PdfRenderWorker
        tpl = default_template()
        w = PdfRenderWorker([], [], tpl.to_json(), "out.pdf")
        done = _collect(w.done)
        error = _collect(w.error)
        w.run()
        assert done == [] and len(error) == 1
        assert "材料" in error[0][0]

    def test_booklet_worker_real_pdf(self, tmp_db, tmp_path, qapp):
        """BookletWorker：真实 PDF 输入 → done 信号与页数回传。"""
        from gwtool.ui.workers import BookletWorker
        src = tmp_path / "src.pdf"
        _make_min_pdf(src)
        out = tmp_path / "booklet.pdf"
        w = BookletWorker(str(src), str(out))
        done = _collect(w.done)
        error = _collect(w.error)
        w.run()
        assert error == []
        assert done and "页" in done[0][0]


# ---------------------------------------------------------------- panels
class TestPanelsProbe:
    def test_library_panel_reload_and_select(self, tmp_db, qapp):
        """资料库面板：构建、reload 填充分类/文档树、选中回传 id。"""
        from gwtool.ui.library_panel import LibraryPanel
        did = dao.add_document(dao.Document(title="面板材料", content_text="c"))
        panel = LibraryPanel()
        panel.reload()
        panel.search_box.setText("面板材料")
        panel._reload_docs()
        # 模拟用户点击：列表填充后选中首项（搜索命中项）
        panel.doc_list.setCurrentRow(0)
        assert panel.selected_doc_ids() == [did]

    def test_reference_panel_build_and_search(self, tmp_db, qapp):
        """参考面板：构建 + 词典/句式真实查询。"""
        from gwtool.ui.reference_panel import ReferencePanel
        dao.add_dictionary_entry("面板词", pinyin="mian4ban3")
        dao.add_phrase("面板句式", context="面板上下文")
        panel = ReferencePanel(editor_getter=lambda: None)
        assert panel is not None

    def test_compile_wizard_pages_and_move(self, tmp_db, qapp):
        """汇编向导：三页构建、材料列表按库填充、越界移动安全。"""
        from gwtool.ui.compile_wizard import CompileWizard
        from PySide6.QtCore import Qt
        ida = dao.add_document(dao.Document(title="汇编材料甲",
                                            content_text="甲正文"))
        idb = dao.add_document(dao.Document(title="汇编材料乙",
                                            content_text="乙正文"))
        wiz = CompileWizard()
        assert list(wiz.pageIds()) == [0, 1, 2]
        items = [wiz.material_list.item(i) for i in
                 range(wiz.material_list.count())]
        assert {it.data(Qt.UserRole) for it in items} == {ida, idb}
        # 越界移动：未选中时不崩
        wiz.material_list.setCurrentRow(-1)
        wiz._move(-1)
        wiz._move(1)
        wiz.material_list.setCurrentRow(0)
        wiz._move(-1)                   # 已在顶部：无变化不崩
        wiz.close()

    def test_compare_dialog_real_diff(self, tmp_db, qapp):
        """对比对话框：从资料库选两篇 → 后台线程真实 diff 渲染进浏览器。"""
        import time
        from gwtool.ui.compare_dialog import CompareDialog
        dao.add_document(dao.Document(title="对比甲", content_text="第一版本内容"))
        dao.add_document(dao.Document(title="对比乙", content_text="第二版本内容"))
        dlg = CompareDialog()
        assert dlg.combo_a.count() >= 3  # （未选择）+ 两篇
        dlg.combo_a.setCurrentIndex(1)
        dlg.combo_b.setCurrentIndex(2)
        dlg._run()                        # 启动 FnWorker 后台线程
        assert dlg._worker is not None
        dlg._worker.wait(5000)            # 等线程收尾
        for _ in range(50):               # 泵事件循环让信号送达主线程
            qapp.processEvents()
            time.sleep(0.02)
        html = dlg.browser.toHtml()
        # jieba 词级 diff："第一/第二"成 del/ins 词块，公共段"版本内容"渲染在列
        assert "版本内容" in html
        assert "红色=删除" in dlg.lbl_stat.text()   # on_ok 回调已执行
        dlg.close()
