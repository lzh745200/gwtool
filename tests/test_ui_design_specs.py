# -*- coding: utf-8 -*-
"""UI 设计方案条目落地的护栏测试（§3.1 / §6.x / §7.x / §8.x / §11.3）。

与 `test_ui_components.py` 的分工：那份守"三个新组件本身"，这份守
**方案各条目的落地形态**——即"方案写了什么，界面上就必须有什么"。

断言一律用 `isVisibleTo(宿主)`：顶层窗口未 show() 时 `isVisible()` 恒为
False，用它断言会得到"永远通过"的假绿。
"""
import pytest

from gwtool.core import corrector
from gwtool.db import dao
from gwtool.ui import widgets as W


def _add_doc(title: str, text: str = "正文内容" * 10, cat: int = 0) -> int:
    return dao.add_document(dao.Document(title=title, content_text=text,
                                         category_id=cat))


# ---------------------------------------------------------------- §8.3
def test_apply_all_asks_before_batch_write(tmp_db, qapp, monkeypatch):
    """全部替换必须先确认，并如实列出"改多少、跳过多少"。"""
    from gwtool.ui.reference_panel import ReferencePanel
    text = "关于布署工作的通知，大约需要五天左右完成。"
    panel = ReferencePanel(editor_getter=lambda: text)
    panel._checked_text = text          # 模拟"检查已完成"（run_check 负责设置）
    panel._on_check_done(corrector.check_text(text))
    seen: list[str] = []
    monkeypatch.setattr(W, "ask", lambda parent, msg, *a, **k: (seen.append(msg), False)[1])
    emitted: list = []
    panel.apply_edit.connect(lambda *a: emitted.append(a))
    panel._apply_all()
    assert seen, "批量改写前没有确认"
    assert "将改动正文" in seen[0] and "提示类" in seen[0], seen[0]
    assert emitted == [], "用户取消后仍然改动了正文"
    panel.deleteLater()


def test_apply_all_proceeds_after_confirm(tmp_db, qapp, monkeypatch):
    from gwtool.ui.reference_panel import ReferencePanel
    text = "工作布署已完成。"
    panel = ReferencePanel(editor_getter=lambda: text)
    panel._checked_text = text
    panel._on_check_done(corrector.check_text(text))
    monkeypatch.setattr(W, "ask", lambda *a, **k: True)
    emitted: list = []
    panel.apply_edit.connect(lambda s, e, r: emitted.append(r))
    panel._apply_all()
    assert emitted and "部署" in emitted[0]
    panel.deleteLater()


def test_apply_all_reports_nothing_to_apply(tmp_db, qapp, monkeypatch):
    from gwtool.ui.reference_panel import ReferencePanel
    panel = ReferencePanel(editor_getter=lambda: "一点问题都没有。")
    panel._checked_text = "一点问题都没有。"
    panel._on_check_done([])
    msgs: list[str] = []
    monkeypatch.setattr(W, "info", lambda parent, msg, *a, **k: msgs.append(msg))
    panel._apply_all()
    assert msgs and "没有可自动替换" in msgs[0]
    panel.deleteLater()


# ---------------------------------------------------------------- §11.3
def test_wizard_shows_product_cards(tmp_db, qapp, tmp_path):
    """产物卡片：有产物则显示、清空则收起。

    用 `isHidden()` 而不是 `isVisibleTo(wizard)`：QWizard 会把**非当前页**
    显式隐藏，而输出页不是当前页，isVisibleTo 恒为 False（误判成"卡片没出"）。
    isHidden 只反映"自身是否被显式隐藏"，正是这里要的语义。
    """
    from gwtool.ui.compile_wizard import CompileWizard
    wiz = CompileWizard()
    assert wiz.products_area.isHidden(), "未生成时卡片区应是收起的"
    p1 = tmp_path / "a.docx"
    p1.write_text("x", encoding="utf-8")
    wiz._remember_product(str(p1))
    assert wiz.products_box.count() == 1
    assert not wiz.products_area.isHidden(), "有产物时卡片区应显示"
    p2 = tmp_path / "b.pdf"
    p2.write_text("x", encoding="utf-8")
    wiz._remember_product(str(p2))
    assert wiz.products_box.count() == 2
    wiz._clear_product_cards()
    assert wiz.products_box.count() == 0
    assert wiz.products_area.isHidden(), "清空后卡片区应收起"
    wiz.close()


def test_wizard_card_shows_title_and_path(tmp_db, qapp, tmp_path):
    from PySide6.QtWidgets import QLabel
    from gwtool.ui.compile_wizard import CompileWizard
    wiz = CompileWizard()
    p = tmp_path / "关于测试的汇编.docx"
    p.write_text("x", encoding="utf-8")
    wiz._remember_product(str(p))
    card = wiz.products_box.itemAt(0).widget()
    texts = [lb.text() for lb in card.findChildren(QLabel)]
    assert any("关于测试的汇编" in t for t in texts)
    assert any(str(p) in t for t in texts)
    wiz.close()


# ---------------------------------------------------------------- §6.8
def test_ask_custom_button_text_reaches_impl(monkeypatch):
    """ask(ok_text=...) 必须走自定义按钮分支，并原样传递后果文案。"""
    got: list[tuple] = []
    monkeypatch.setattr(W, "_ask_custom",
                        lambda parent, text, title, ok: (got.append(ok), True)[1])
    assert W.ask(None, "覆盖当前资料？", ok_text="恢复并覆盖当前资料") is True
    assert got == ["恢复并覆盖当前资料"]


def test_ask_default_path_unchanged(monkeypatch):
    """不传 ok_text 时仍走既有 Yes/No 询问（既有调用方行为不变）。"""
    from PySide6.QtWidgets import QMessageBox
    called: list = []
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: called.append(1) or QMessageBox.Yes))
    assert W.ask(None, "继续？") is True
    assert called == [1]


def test_security_dialog_has_danger_zone(tmp_db, qapp):
    """设置页危险区：独立分组 + 两个入口按钮（回调可注入）。"""
    from PySide6.QtWidgets import QGroupBox, QPushButton
    from gwtool.ui.feature_dialogs import SecurityDialog
    hits: list[str] = []
    dlg = SecurityDialog(None, on_restore=lambda: hits.append("restore"),
                         on_recycle=lambda: hits.append("recycle"))
    boxes = [g for g in dlg.findChildren(QGroupBox) if "危险" in g.title()]
    assert boxes, "缺少危险操作分组"
    labels = {b.text() for b in boxes[0].findChildren(QPushButton)}
    assert "从备份恢复…" in labels and "打开回收站…" in labels
    for b in boxes[0].findChildren(QPushButton):
        if b.text() == "从备份恢复…":
            b.click()
    assert hits == ["restore"]
    dlg.deleteLater()


def test_recycle_bin_danger_buttons(tmp_db, qapp):
    """回收站的不可逆操作用 DANGER 描边（§6.8）。"""
    from PySide6.QtWidgets import QPushButton
    from gwtool.ui import theme
    from gwtool.ui.material_dialogs import RecycleBinDialog
    dlg = RecycleBinDialog()
    styled = [b for b in dlg.findChildren(QPushButton)
              if theme.DANGER in b.styleSheet()]
    assert len(styled) >= 2, "彻底删除/清空回收站应为 DANGER 描边"
    dlg.deleteLater()


# ---------------------------------------------------------------- §7.3
def test_registry_form_marks_required_and_invalid(tmp_db, qapp):
    from gwtool.ui.registry_dialog import DispatchForm
    form = DispatchForm(record=dao.Dispatch())
    form._show_form_problems(["标题不能为空"])
    assert form.lbl_form_error.isVisibleTo(form)
    assert "标题不能为空" in form.lbl_form_error.text()
    assert "#" in form._fields["title"].styleSheet(), "问题字段应标红"
    form.deleteLater()


# ---------------------------------------------------------------- §6.6
def test_receive_table_status_chip(tmp_db, qapp):
    from gwtool.ui.receive_dialog import ReceiveDialog, _status_chip_kind
    _add_doc("来文材料")
    dlg = ReceiveDialog()
    dlg._rows = [dao.Receive(id=1, reg_no="R1", title="来文", status="承办"),
                 dao.Receive(id=2, reg_no="R2", title="已办", status="已办结")]
    dlg._render_table()
    assert _status_chip_kind("承办") == "primary"
    assert _status_chip_kind("已办结") == "success"
    assert _status_chip_kind("已归档") == "info"
    assert dlg.tbl.cellWidget(0, 6) is not None, "状态列应有徽标"
    dlg.deleteLater()


def test_registry_table_status_chip(tmp_db, qapp):
    from gwtool.ui.registry_dialog import RegistryDialog, _status_chip_kind
    assert _status_chip_kind("拟稿") == "primary"
    assert _status_chip_kind("已印发") == "success"
    dlg = RegistryDialog()
    dlg._rows = [dao.Dispatch(id=1, title="测试", status="已印发")]
    dlg._render_table()
    assert dlg.table.cellWidget(0, 6) is not None
    dlg.deleteLater()


# ---------------------------------------------------------------- §7.5 / §8.1
def test_import_cancel_banner(tmp_db, qapp):
    from gwtool.ui.import_dialog import ImportDialog
    dlg = ImportDialog()
    dlg._cancel_requested = True
    dlg._finish_common(2, 1, [("坏件.docx", "解析失败")])
    assert "已取消，已完成部分保留" in dlg.banner._text.text()
    assert dlg.banner.isVisibleTo(dlg)
    assert dlg.fail_view.isVisibleTo(dlg), "失败明细应就地可见"
    dlg.deleteLater()


def test_import_done_banner_and_fail_list(tmp_db, qapp):
    from gwtool.ui.import_dialog import ImportDialog
    dlg = ImportDialog()
    dlg._finish_common(3, 0, [("坏件.docx", "文件损坏")])
    assert "导入完成" in dlg.banner._text.text()
    assert "坏件.docx" in dlg.fail_view.toPlainText()
    dlg.deleteLater()


# ---------------------------------------------------------------- §6.4
def test_correction_rows_have_inline_widgets(tmp_db, qapp):
    from gwtool.ui.reference_panel import ReferencePanel
    text = "工作布署已完成。"
    panel = ReferencePanel(editor_getter=lambda: text)
    panel._on_check_done(corrector.check_text(text))
    item = panel.corr_list.item(0)
    assert item is not None
    holder = panel.corr_list.itemWidget(item)
    assert holder is not None, "纠错条目缺少行内视图（色条/双列/按钮）"
    labels = [lb.text() for lb in holder.findChildren(type(panel.lbl_count))]
    assert any("布署" in t for t in labels) and any("部署" in t for t in labels)
    panel.deleteLater()


def test_reference_paragraph_cards(tmp_db, qapp):
    from gwtool.ui.reference_panel import ReferencePanel
    _add_doc("安全生产调研报告",
             "为深入贯彻安全生产工作要求，我们对全市安全生产情况进行了专题调研。")
    panel = ReferencePanel(editor_getter=lambda: "")
    panel.ref_input.setText("安全生产")
    panel.run_reference()
    assert panel.ref_list.count() > 0
    item = panel.ref_list.item(0)
    card = panel.ref_list.itemWidget(item)
    assert card is not None, "段落结果缺少卡片（徽标/摘要/溯源/插入）"
    panel.deleteLater()


# ---------------------------------------------------------------- §3.1 / §6.2
def test_editor_dirty_dot(tmp_db, qapp):
    from gwtool.ui.editor_panel import EditorPanel
    panel = EditorPanel()
    assert not panel.dirty_dot.isVisibleTo(panel)
    panel._set_dirty(True)
    assert panel.dirty_dot.isVisibleTo(panel), "有未保存改动时应显示圆点"
    panel._set_dirty(False)
    assert not panel.dirty_dot.isVisibleTo(panel)
    panel.deleteLater()


def test_editor_line_spacing_applied(tmp_db, qapp):
    from gwtool.ui.editor_panel import EditorPanel
    panel = EditorPanel()
    panel.editor.setPlainText("第一段\n第二段")
    panel._apply_line_spacing()
    block = panel.editor.document().firstBlock()
    assert block.blockFormat().lineHeightType() == 1      # ProportionalHeight
    assert block.blockFormat().lineHeight() == 150.0
    panel.deleteLater()


# ---------------------------------------------------------------- §6.3
def test_category_tree_counts(tmp_db, qapp):
    cat = dao.add_category("测试分类")
    _add_doc("分类里的材料", cat=cat)
    from gwtool.ui.library_panel import LibraryPanel
    panel = LibraryPanel()
    panel._fill_categories()
    root = panel.cat_tree.topLevelItem(0)
    assert root.text(0) == "全部文档"
    assert root.text(1) == "1", "根节点应显示总篇数"
    node = root.child(0)
    assert node.text(0) == "测试分类" and node.text(1) == "1"
    panel.deleteLater()


def test_dao_count_by_category(tmp_db):
    cat = dao.add_category("C1")
    _add_doc("甲材料", text="甲" * 30, cat=cat)
    _add_doc("乙材料", text="乙" * 30, cat=cat)
    _add_doc("丙材料", text="丙" * 30)
    counts = dao.count_documents_by_category()
    assert counts.get(cat) == 2
    assert 0 not in counts, "未分类不计入分类计数"


def test_library_search_esc_clears(tmp_db, qapp):
    from gwtool.ui.library_panel import LibraryPanel
    _add_doc("检索目标材料")
    panel = LibraryPanel()
    panel.search_box.setText("检索目标")
    panel._on_search()
    panel._clear_search()
    assert panel.search_box.text() == ""
    assert panel.doc_list.count() >= 1, "清空后应回到完整列表"
    panel.deleteLater()


# ---------------------------------------------------------------- 全量用例自证
def test_no_hardcoded_colors_still_holds(qapp):
    """本轮新增的样式同样必须是 token（避免新代码把护栏踩坏）。"""
    import ast
    import re
    from pathlib import Path
    ui_dir = Path(__file__).resolve().parent.parent / "gwtool" / "ui"
    hex_pat = re.compile(r"#[0-9a-fA-F]{6}\b")
    bad = []
    for path in sorted(ui_dir.glob("*.py")):
        if path.name == "theme.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and hex_pat.search(node.value):
                bad.append(f"{path.name}:{node.lineno}")
    assert not bad, f"新增代码出现硬编码色值：{bad}"


@pytest.mark.parametrize("kind", ["success", "warn", "danger", "info", "primary"])
def test_chip_kinds_render(qapp, kind):
    from gwtool.ui.widgets import make_chip, make_chip_cell
    chip = make_chip("x", kind)
    assert chip.text() == "x" and "border-radius" in chip.styleSheet()
    assert make_chip_cell("x", kind) is not None


# ---------------------------------------------------------------- §6 页面规格的数值落地
def test_search_box_uses_scoped_qss(qapp):
    """§6.3：检索框圆角 6px + SURFACE 底，且**不**写死色值（走对象名 QSS）。"""
    from gwtool.ui import theme
    from gwtool.ui.library_panel import LibraryPanel

    panel = LibraryPanel()
    assert panel.search_box.objectName() == "search_box"
    qss = theme.build_qss()
    assert "QLineEdit#search_box" in qss, "缺少检索框的定向样式"
    block = qss.split("QLineEdit#search_box", 1)[1].split("}", 1)[0]
    assert "6px" in block, "检索框圆角应为 6px"
    assert theme.SURFACE in block, "检索框底色应取 SURFACE token"
    panel.deleteLater()


def test_doc_list_row_height_floor(qapp):
    """§6.3：文档列表行高 32px（按对象名施加，不影响其它列表）。"""
    from gwtool.ui import theme
    from gwtool.ui.library_panel import LibraryPanel

    panel = LibraryPanel()
    assert panel.doc_list.objectName() == "doc_list"
    qss = theme.build_qss()
    assert "QListWidget#doc_list::item" in qss
    block = qss.split("QListWidget#doc_list::item", 1)[1].split("}", 1)[0]
    assert "32px" in block
    panel.deleteLater()


def test_library_bottom_row_counts_multi_selection(tmp_db, qapp):
    """§6.3：多选计数显示在列表底部工具行（清空选中后回到总数）。"""
    from gwtool.ui.library_panel import LibraryPanel

    # 两篇必须给不同正文：内容 hash 相同会被 dao 判重并返回 -1（既有语义）
    _add_doc("甲材料", "甲" + "正文内容" * 10)
    _add_doc("乙材料", "乙" + "正文内容" * 10)
    panel = LibraryPanel()
    panel.reload()
    assert panel.doc_list.count() == 2
    assert panel.count_label.text() == "2 篇", "未选中时显示总数"

    panel.doc_list.item(0).setSelected(True)
    panel.doc_list.item(1).setSelected(True)
    assert "已选 2" in panel.count_label.text()

    panel.doc_list.clearSelection()
    assert panel.count_label.text() == "2 篇", "清空选中后必须回到总数"
    panel.deleteLater()


@pytest.mark.parametrize("mod_name,cls_name,attr", [
    ("gwtool.ui.registry_dialog", "RegistryDialog", "table"),
    ("gwtool.ui.receive_dialog", "ReceiveDialog", "tbl"),
])
def test_ledger_tables_row_height(qapp, mod_name, cls_name, attr):
    """§6.6：台账表格行高 30px（发文与收文口径一致）。"""
    import importlib

    mod = importlib.import_module(mod_name)
    dlg = getattr(mod, cls_name)()
    assert getattr(dlg, attr).verticalHeader().defaultSectionSize() == 30
    dlg.deleteLater()


def test_editor_success_flash_dot(qapp):
    """§6.2：保存后状态栏闪现 SUCCESS 圆点；非成功态不得带圆点。"""
    from gwtool.ui.editor_panel import EditorPanel

    ed = EditorPanel()
    ed.editor.setPlainText("关于××事项的通知")
    ed._update_status("已保存", kind="success")
    assert ed.lbl_status.text().startswith("● "), "成功后应闪现圆点"

    ed._update_status("已打开：某某材料")
    assert not ed.lbl_status.text().startswith("● "), "普通状态不应带圆点"
    ed.deleteLater()


def test_ref_split_handle_carries_count(qapp):
    """§6.4：结果计数落在两区之间的折叠把手（tooltip）与常驻标签上。"""
    from gwtool.ui.reference_panel import ReferencePanel

    panel = ReferencePanel(editor_getter=lambda: "")
    panel._set_ref_count_text("相关结果 3 条")
    assert panel.lbl_ref.text() == "相关结果 3 条"
    assert panel._split.count() > 1
    assert panel._split.handle(1).toolTip() == "相关结果 3 条"
    panel.deleteLater()


def test_outline_tree_uses_scoped_qss(qapp):
    """§6.2：大纲树 200px 吸左 + SURFACE 底、无边框（走对象名 QSS）。"""
    from gwtool.ui import theme
    from gwtool.ui.editor_panel import EditorPanel

    ed = EditorPanel()
    assert ed.outline.objectName() == "outline"
    assert ed.outline.maximumWidth() == 200, "大纲树应固定 200px"
    qss = theme.build_qss()
    assert "QTreeWidget#outline" in qss
    block = qss.split("QTreeWidget#outline", 1)[1].split("}", 1)[0]
    assert theme.SURFACE in block, "大纲树底色应取 SURFACE"
    assert "border: none" in block, "大纲树应无边框"
    ed.deleteLater()


def test_wizard_success_banner(tmp_db, qapp, tmp_path):
    """§6.5：汇编完成后出成功横幅；新一轮开始（清产物）时收起。"""
    from gwtool.ui.compile_wizard import CompileWizard

    wiz = CompileWizard()
    assert wiz.banner.isHidden(), "未生成产物时不应显示横幅"

    p = tmp_path / "a.docx"
    p.write_text("x", encoding="utf-8")
    wiz._remember_product(str(p))
    assert not wiz.banner.isHidden(), "生成产物后应出现成功横幅"
    assert "1 份" in wiz.banner._text.text()

    wiz._clear_product_cards()
    assert wiz.banner.isHidden(), "新一轮必须收起上一轮横幅"
    wiz.close()


def test_ask_default_branch_is_stubbed(qapp):
    """`ask()` 默认分支不得真弹框（否则全量会话会挂死）。

    这是"全量卡在 87%"那次事故的回归护栏：conftest 的 autouse 夹具必须屏蔽
    QMessageBox 静态方法。若该屏蔽被移除，本用例会**挂起**并被 pytest-timeout
    捕获 —— 挂起本身即是失败信号，不会安静地假绿。
    """
    from gwtool.ui.widgets import ask

    assert ask(None, "确认继续？") is True
