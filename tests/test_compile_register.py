# -*- coding: utf-8 -*-
"""P0-1：汇编产物落库 + 汇编→发文台账回填（幂等、失败降级、悬空关联）。

背景：`compile_wizard` 全文件**从不调用** `dao.add_document` —— 用户花力气
汇编出来的成品只落在导出目录里，资料库里查不到、台账里也关不上号。本文件
按"红→绿"纪律逐条钉住修复后的行为：
  · 产物入库（标题/标签/分类/文件路径/可检索）
  · 汇编元数据回填台账（`dispatch_register.doc_id` 指回资料条目）
  · 同产物重复登记**幂等**（按 doc_id 更新，不新增行）
  · 同发文字号**应用层查重**（`idx_dispatch_no` 非唯一索引，库层拦不住）
  · 入库/登记失败**不得**把"生成成功"变成"整体失败"
  · `doc_id` 悬空时「打开原文」置灰
"""
from __future__ import annotations

import sqlite3

import pytest

from gwtool.core import registry
from gwtool.db import dao


# ------------------------------------------------------------ 测试专用构装
class _FakeCover:
    """最小封面信息（与 template.CoverInfo 同形，避免测试依赖 UI）。"""

    def __init__(self, **kw):
        self.title = kw.get("title", "")
        self.subtitle = kw.get("subtitle", "")
        self.org = kw.get("org", "")
        self.date = kw.get("date", "")
        self.doc_type = kw.get("doc_type", "")
        self.secret_level = kw.get("secret_level", "")
        self.urgency = kw.get("urgency", "")
        self.doc_no = kw.get("doc_no", "")
        self.extra_lines = kw.get("extra_lines", [])


def _make_material(title="材料一", text=""):
    """建一篇材料。text 缺省时按标题派生 —— `dao.add_document` 按内容 hash
    查重，两篇默认文本相同的材料第二篇会被拒收（返回 -1），
    于是"建 2 篇"实际只有 1 篇，测试会以令人困惑的方式失败。"""
    return dao.add_document(dao.Document(title=title,
                                        content_text=text or f"{title}的正文内容"))


# ------------------------------------------------------------ dao 侧
def test_find_dispatch_by_document_returns_none_when_absent(tmp_db):
    assert dao.find_dispatch_by_document(123) is None


def test_find_dispatch_by_document_finds_row(tmp_db):
    doc_id = _make_material()
    rid = dao.add_dispatch(dao.Dispatch(title="关于加强安全生产工作的通知",
                                        doc_no="×政办发〔2026〕12号",
                                        doc_id=doc_id))
    got = dao.find_dispatch_by_document(doc_id)
    assert got is not None and got.id == rid
    assert got.doc_id == doc_id


def test_find_dispatch_by_document_ignores_unlinked(tmp_db):
    """doc_id=0 的"未关联"记录不能被任何查询误命中（0 是哨兵值）。"""
    _make_material()
    dao.add_dispatch(dao.Dispatch(title="手工登记的件"))
    assert dao.find_dispatch_by_document(0) is None


# ------------------------------------------------------------ registry.from_compile
def test_from_compile_maps_cover_metadata():
    """封面元数据 → Dispatch：标题、机关、成文日期、文种、密级、字号全部就位。"""
    d = registry.from_compile(
        "关于汇编年度要点的通知",
        cover=_FakeCover(title="关于汇编年度要点的通知", org="××市人民政府办公室",
                         date="2026年8月", doc_type="通知",
                         secret_level="内部", urgency="急件",
                         doc_no="×政办发〔2026〕7号"),
        doc_id=42)
    assert d.title == "关于汇编年度要点的通知"
    assert d.org == "××市人民政府办公室"
    assert d.doc_type == "通知"
    assert d.secret_level == "内部"
    assert d.urgency == "急件"
    assert d.doc_no == "×政办发〔2026〕7号"
    assert d.doc_id == 42
    # 中文日期要归一化成 YYYY-MM-DD 才过得了 validate
    assert d.sign_date == "2026-08-01"

    assert registry.validate(d) == []


@pytest.mark.parametrize("raw,expected", [
    ("2026年8月", "2026-08-01"),
    ("2026年8月15日", "2026-08-15"),
    ("2026-08-15", "2026-08-15"),
    ("2026.8.5", "2026-08-05"),
    ("2026年", "2026-01-01"),
    ("", ""),
    ("不是日期", ""),
])
def test_from_compile_normalizes_dates(raw, expected):
    d = registry.from_compile("某通知", cover=_FakeCover(date=raw))
    assert d.sign_date == expected


def test_from_compile_defaults_doc_type_from_title():
    """文种留空时从标题尾部推断（"…的通知" → 通知），推不出就留空不硬猜。"""
    assert registry.from_compile("关于开展安全检查的通知").doc_type == "通知"
    assert registry.from_compile("关于XX情况的报告").doc_type == "报告"
    assert registry.from_compile("没有文种后缀的标题").doc_type == ""


def test_from_compile_status_is_draft():
    """刚汇编出来的成品尚未核稿签发，默认落在"拟稿"。"""
    assert registry.from_compile("某通知").status == "拟稿"


def test_from_compile_defaults_title_when_blank():
    d = registry.from_compile("", cover=_FakeCover())
    assert d.title, "标题不能为空，否则 validate 过不了"


def test_from_compile_rejects_bad_doc_no_by_leaving_it_empty():
    """格式不合规的字号宁可丢弃也不写进台账 —— 否则 validate 必挂。"""
    d = registry.from_compile("某通知", cover=_FakeCover(doc_no="格式不对"))
    assert d.doc_no == ""
    assert registry.validate(d) == []


# ============================================================ 向导侧（P0-1）
# 下面这组用例直接驱动 `CompileWizard` 的真实生成链路。为了不依赖 Word/PDF
# 渲染（那是 test_compile_pdf 的职责），统一把三个后台 Worker 换成"立即回调
# 一个假产物路径"的替身，只验证**向导在拿到产物之后做了什么**。
import os

from PySide6.QtWidgets import QDialog


@pytest.fixture()
def wizard(tmp_db, qapp, monkeypatch):
    """构造向导并屏蔽全部模态弹窗，同时把弹窗调用**记账**下来。

    记账是必要的：B5.6 要求批量模式"每份材料不许各弹一次框"，
    只能靠数调用次数来验证。
    """
    from gwtool import app
    app.ensure_database_seeded()
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("", False)))
    monkeypatch.setattr(QInputDialog, "getInt", staticmethod(lambda *a, **k: (0, False)))

    from gwtool.ui.compile_wizard import CompileWizard

    # 默认就把导出目录指到临时目录：默认值是不隔离的，一旦有测试跑到真实
    # 生成路径就会在用户的「文档」文件夹里留东西（实测踩过一次）。
    _empty_export_dir(monkeypatch)

    w = CompileWizard()
    calls: list[str] = []
    monkeypatch.setattr("gwtool.ui.compile_wizard.info",
                        lambda *a, **k: calls.append(str(a[-1]) if a else ""))
    w.modal_calls = calls
    yield w
    w.close()


def _empty_export_dir(monkeypatch):
    """把导出目录指到测试临时目录。

    必须隔离：默认导出目录是**用户的「文档」文件夹**，测试若直接生成，
    会在开发者机器上堆一堆 `汇编成果.docx`（实测过一次）。
    """
    import tempfile
    from pathlib import Path as _P
    d = _P(tempfile.mkdtemp(prefix="gwtool_export_"))
    monkeypatch.setattr("gwtool.ui.compile_wizard.export_dir", lambda: d)
    return d


def _stub_workers(monkeypatch, produce: bool = True, exc: Exception | None = None):
    """把 CompileWorker/PdfRenderWorker/BookletWorker 换成同步替身。

    `produce=True` 时用 `_touch` 真的写一个产物文件 —— 入库要读文件算 hash，
    路径不存在会被降级逻辑拦下，测不到"成功入库"这条正路。
    """
    import gwtool.ui.compile_wizard as cw

    def _touch(path: str) -> str:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            # 内容里带上路径：`dao.add_document` 按**内容 hash** 查重，
            # 两份产物内容一样时第二份会被判重复（-1），测不到"两份都入库"。
            fh.write(f"汇编产物内容 {path}")
        return path

    class _Stub:
        # 输出路径是**第 4 个位置参数**（CompileWorker/PdfRenderWorker/
        # BookletWorker 的签名都是 (*args, out, ...)），不是关键字参数 ——
        # 只读 kwargs 会拿到空串，产物写不出来、后续入库全部落空。
        _OUT_INDEX = {
            "CompileWorker": 3, "PdfRenderWorker": 3, "BookletWorker": 1,
        }

        class _Sig:
            def __init__(self):
                self._slots = []

            def connect(self, fn):
                self._slots.append(fn)

            def emit(self, *a):
                for fn in self._slots:
                    fn(*a)

        def __init__(self, *a, **k):
            self._kind = k.pop("_kind", "CompileWorker")
            self.done = _Stub._Sig()
            self.error = _Stub._Sig()
            self.progress = _Stub._Sig()
            idx = _Stub._OUT_INDEX.get(self._kind, 3)
            self._out = a[idx] if len(a) > idx else (
                k.get("out_docx") or k.get("out_pdf") or k.get("out_bk") or "")

        def start(self):
            if exc is not None:
                self.error.emit(str(exc))
                return
            self.done.emit(_touch(self._out) if produce else self._out)

        def isRunning(self):
            return False

        def wait(self, *_a):
            return True

    def _factory(kind):
        def _make(*a, **k):
            k["_kind"] = kind
            return _Stub(*a, **k)
        return _make

    monkeypatch.setattr(cw, "CompileWorker", _factory("CompileWorker"))
    monkeypatch.setattr(cw, "PdfRenderWorker", _factory("PdfRenderWorker"))
    monkeypatch.setattr(cw, "BookletWorker", _factory("BookletWorker"))
    return _Stub


def _select_all(w) -> None:
    """第 1 步：全勾 + 清掉「附来源清单」以免产物文本随材料数变化。"""
    w.chk_titles.setChecked(True)
    w.chk_sources.setChecked(False)


def test_compile_saves_product_to_library(wizard, monkeypatch):
    """P0-1 核心：汇编成功后资料库里**应当**多出一条产物条目。"""
    _stub_workers(monkeypatch)
    _make_material("材料甲", "甲的内容")
    _make_material("材料乙", "乙的内容")
    wizard._order = [d.id for d in dao.list_documents()]
    wizard._checked = set(wizard._order)
    _select_all(wizard)
    wizard.ed_title.setText("关于汇编年度要点的通知")
    wizard.chk_docx.setChecked(True)
    wizard.chk_pdf.setChecked(False)
    wizard.chk_booklet.setChecked(False)

    wizard._start()

    saved = [d for d in dao.list_documents() if d.title == "关于汇编年度要点的通知"]
    assert len(saved) == 1, "汇编产物没有落进资料库"
    assert "汇编成果" in saved[0].tags
    assert saved[0].file_path.lower().endswith(".docx")
    assert os.path.exists(saved[0].file_path), "条目指向的产物文件不存在"


def test_compile_saves_pdf_product_too(wizard, monkeypatch):
    """同时勾选 docx + pdf 时两条产物都要入库（各一条，不互相覆盖）。"""
    _stub_workers(monkeypatch)
    _make_material()
    wizard._order = [d.id for d in dao.list_documents()]
    wizard._checked = set(wizard._order)
    _select_all(wizard)
    wizard.ed_title.setText("含两份产物的汇编")
    wizard.chk_docx.setChecked(True)
    wizard.chk_pdf.setChecked(True)
    wizard.chk_booklet.setChecked(False)

    wizard._start()

    saved = [d for d in dao.list_documents() if d.title.startswith("含两份产物的汇编")]
    exts = sorted(os.path.splitext(d.file_path)[1].lower() for d in saved)
    assert exts == [".docx", ".pdf"], f"产物入库不完整：{exts}"


def test_compile_reports_products_in_non_modal_panel(wizard, monkeypatch):
    """B4：完成提示必须走**非模态面板**，不能每生成一次就弹一个模态框。

    "同名输出已存在，本次输出将追加时间戳"这条**前置**提示不算：它发生在
    生成**之前**，用户还没进入"等结果"的状态，拦一下让他知道文件名变了是
    合理的。本用例只钉住"生成完成后不再弹框"。
    """
    _stub_workers(monkeypatch)
    _make_material()
    wizard._order = [d.id for d in dao.list_documents()]
    wizard._checked = set(wizard._order)
    _select_all(wizard)
    wizard.ed_title.setText("非模态完成面板")
    wizard.chk_docx.setChecked(True)
    wizard.chk_pdf.setChecked(False)

    # 让输出目录不存在同名文件，排除掉"追加时间戳"那条前置提示
    monkeypatch.setattr("gwtool.ui.compile_wizard.export_dir",
                        lambda: _empty_export_dir(monkeypatch))
    wizard.modal_calls.clear()
    wizard._start()

    assert wizard.modal_calls == [], f"完成时仍弹了模态框：{wizard.modal_calls}"
    assert "生成完成" in wizard.lbl_result.text()
    # 面板上要有可点的"登记到台账"入口
    assert wizard.btn_register_products.isEnabled()


def test_compile_failure_does_not_touch_library(wizard, monkeypatch):
    """生成失败时不许留下半截条目（产物不存在，入库也无从谈起）。"""
    _stub_workers(monkeypatch, exc=RuntimeError("模拟渲染失败"))
    _make_material()
    wizard._order = [d.id for d in dao.list_documents()]
    wizard._checked = set(wizard._order)
    _select_all(wizard)
    wizard.ed_title.setText("注定失败的汇编")
    wizard.chk_docx.setChecked(True)

    before = len(dao.list_documents())
    wizard._start()
    assert len(dao.list_documents()) == before, "生成失败却写入了资料库"
    assert "失败" in wizard.lbl_result.text()


def test_library_write_failure_keeps_generation_success(wizard, monkeypatch):
    """B5.4：入库失败**不得**把"生成成功"变成"整体失败"。

    产物已经躺在磁盘上了，用户能拿它去用 —— 此时报"生成失败"是误导。
    正确行为是照常报成功，另附一句"可在台账中手工补登记"的提示。
    """
    _stub_workers(monkeypatch)
    _make_material()
    wizard._order = [d.id for d in dao.list_documents()]
    wizard._checked = set(wizard._order)
    _select_all(wizard)
    wizard.ed_title.setText("入库会失败的汇编")
    wizard.chk_docx.setChecked(True)

    def _boom(_doc):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("gwtool.db.dao.add_document", _boom)
    wizard._start()

    assert "生成完成" in wizard.lbl_result.text(), "入库失败被误报成生成失败"
    assert "手工补登记" in wizard.lbl_result.text()


def test_register_from_compile_writes_dispatch(wizard, monkeypatch):
    """B5.1：确认登记后台账里出现一条记录，且 doc_id 指回资料条目。"""
    _stub_workers(monkeypatch)
    _make_material()
    wizard._order = [d.id for d in dao.list_documents()]
    wizard._checked = set(wizard._order)
    _select_all(wizard)
    wizard.ed_title.setText("关于登记回填的通知")
    wizard.chk_docx.setChecked(True)

    # 屏蔽"确认登记"的询问框 → 直接放行
    monkeypatch.setattr("gwtool.ui.compile_wizard.ask", lambda *a, **k: True)
    # 屏蔽登记表单的模态执行 → 直接接受（表单内容由另一组用例覆盖）
    monkeypatch.setattr(QDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)

    wizard._start()

    rows = dao.list_dispatch()
    assert len(rows) == 1, f"台账应恰好 1 条，实得 {len(rows)}"
    saved = next(d for d in dao.list_documents()
                 if d.title == "关于登记回填的通知")
    assert rows[0].doc_id == saved.id, "台账未回填资料条目 id"
    assert rows[0].title == "关于登记回填的通知"


def test_register_is_idempotent_by_doc_id(wizard, monkeypatch):
    """B5.1：同一产物重复登记 → 更新既有行，行数不变。

    注意"同一产物"的判据是**资料条目 doc_id**，不是"标题相同"：
    第二次生成同名汇编会因防覆盖逻辑追加时间戳而成为**另一份产物**，
    那是新条目、新记录，本就该多一行。所以本用例直接对同一份已入库
    产物连点两次「登记到台账」。
    """
    _stub_workers(monkeypatch)
    _make_material()
    wizard._order = [d.id for d in dao.list_documents()]
    wizard._checked = set(wizard._order)
    monkeypatch.setattr("gwtool.ui.compile_wizard.ask", lambda *a, **k: True)
    monkeypatch.setattr(QDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)

    _select_all(wizard)
    wizard.ed_title.setText("会重复登记的汇编")
    wizard.chk_docx.setChecked(True)
    wizard._start()
    assert dao.count_dispatch() == 1

    # 同一产物再登记一次：产物列表没变，doc_id 也没变
    wizard._register_from_compile(ask_first=True)
    assert dao.count_dispatch() == 1, "重复登记同一产物新增了行（未按 doc_id 幂等）"


def test_register_asks_by_default_and_never_auto_writes(wizard, monkeypatch):
    """B5.2：默认**只询问、不自动登记**。用户答"否"时台账必须为空。"""
    _stub_workers(monkeypatch)
    _make_material()
    wizard._order = [d.id for d in dao.list_documents()]
    wizard._checked = set(wizard._order)
    _select_all(wizard)
    wizard.ed_title.setText("不自动登记的汇编")
    wizard.chk_docx.setChecked(True)

    monkeypatch.setattr("gwtool.ui.compile_wizard.ask", lambda *a, **k: False)
    wizard._start()
    assert dao.count_dispatch() == 0, "用户拒绝后仍被自动登记"


def test_duplicate_doc_no_is_rejected_at_app_layer(wizard, monkeypatch):
    """B5.2：`idx_dispatch_no` 是**普通索引**，库层拦不住重复字号 → 应用层查重。"""
    dao.add_dispatch(dao.Dispatch(title="已存在的一件",
                                  doc_no="×政办发〔2026〕3号",
                                  sign_date="2026-01-05"))
    assert dao.count_dispatch() == 1
    # 库层确实允许写入同号记录（这正是必须在应用层拦的原因）
    dao.add_dispatch(dao.Dispatch(title="同号的一件",
                                  doc_no="×政办发〔2026〕3号",
                                  sign_date="2026-01-06"))
    assert dao.count_dispatch() == 2

    _stub_workers(monkeypatch)
    _make_material()
    wizard._order = [d.id for d in dao.list_documents()]
    wizard._checked = set(wizard._order)
    _select_all(wizard)
    wizard.ed_title.setText("撞号的汇编")
    wizard.chk_docx.setChecked(True)
    wizard._doc_no_override = "×政办发〔2026〕3号"

    monkeypatch.setattr("gwtool.ui.compile_wizard.ask", lambda *a, **k: True)
    monkeypatch.setattr(QDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)

    wizard._start()
    assert dao.count_dispatch() == 2, "同字号被重复登记进台账"


def test_register_failure_keeps_product_and_library_entry(wizard, monkeypatch):
    """B5.3：登记失败不影响产物与资料条目，只提示可手工补登记。"""
    _stub_workers(monkeypatch)
    _make_material()
    wizard._order = [d.id for d in dao.list_documents()]
    wizard._checked = set(wizard._order)
    _select_all(wizard)
    wizard.ed_title.setText("登记会失败的汇编")
    wizard.chk_docx.setChecked(True)

    monkeypatch.setattr("gwtool.ui.compile_wizard.ask", lambda *a, **k: True)
    monkeypatch.setattr(QDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)

    def _boom(_d):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("gwtool.db.dao.add_dispatch", _boom)
    wizard._start()

    assert dao.count_dispatch() == 0
    saved = [d for d in dao.list_documents()
             if d.title == "登记会失败的汇编"]
    assert len(saved) == 1, "登记失败把资料条目也弄丢了"
    assert os.path.exists(saved[0].file_path)
    assert "手工补登记" in wizard.lbl_result.text()


def test_register_failure_does_not_raise(wizard, monkeypatch):
    """登记链路的任何异常都必须被咽掉 —— 它只是"锦上添花"，不该崩掉整个向导。"""
    _stub_workers(monkeypatch)
    _make_material()
    wizard._order = [d.id for d in dao.list_documents()]
    wizard._checked = set(wizard._order)
    _select_all(wizard)
    wizard.ed_title.setText("登记炸了的汇编")
    wizard.chk_docx.setChecked(True)

    monkeypatch.setattr("gwtool.ui.compile_wizard.ask", lambda *a, **k: True)
    monkeypatch.setattr(QDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)

    def _boom(*_a, **_k):
        raise RuntimeError("未预期的错误")

    monkeypatch.setattr("gwtool.core.registry.from_compile", _boom)
    wizard._start()          # 不应抛异常
    assert "生成完成" in wizard.lbl_result.text()


def test_batch_compile_does_not_pop_modal_per_item(wizard, monkeypatch):
    """B5.6：批量模式每份材料一个弹框会把用户逼疯 → 全流程至多 1 个模态框。"""
    import gwtool.ui.compile_wizard as cw
    from gwtool.core import batch

    def _fake_batch(ids, tpl, outdir, **k):
        os.makedirs(outdir, exist_ok=True)
        paths = []
        for i, _did in enumerate(ids):
            p = os.path.join(outdir, f"批量件{i + 1}.docx")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("批量产物")
            paths.append(p)
        return paths, []

    monkeypatch.setattr(batch, "batch_compile_each", _fake_batch)

    for _ in range(3):
        _make_material()
    wizard._order = [d.id for d in dao.list_documents()]
    wizard._checked = set(wizard._order)
    wizard.chk_titles.setChecked(False)
    wizard.ed_title.setText("批量汇编")
    wizard.chk_batch.setChecked(True)

    wizard._start()
    # _BatchThread 是内联 QThread：等它收工并把队列里的槽跑完
    worker = getattr(wizard, "_batch_worker", None)
    assert worker is not None
    worker.wait(20000)
    for _ in range(20):
        wizard.parent().processEvents() if wizard.parent() else None
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        if not worker.isRunning():
            break
    from PySide6.QtWidgets import QApplication
    for _ in range(5):
        QApplication.processEvents()

    assert len(wizard.modal_calls) <= 1, (
        f"批量模式弹了 {len(wizard.modal_calls)} 个模态框（应 ≤1）")
    assert "批量生成完成" in wizard.lbl_result.text()
    assert cw is not None


def test_batch_products_are_saved_to_library(wizard, monkeypatch):
    """批量产物同样要入库 —— 否则用户从资料库角度看到的仍是"什么都没生成"。"""
    from gwtool.core import batch

    seen_ids: list[int] = []

    def _fake_batch(ids, tpl, outdir, **k):
        seen_ids.extend(ids)
        os.makedirs(outdir, exist_ok=True)
        paths = []
        for i, _did in enumerate(ids):
            p = os.path.join(outdir, f"批量件{i + 1}.docx")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(f"批量产物{i}")
            paths.append(p)
        return paths, []

    monkeypatch.setattr(batch, "batch_compile_each", _fake_batch)
    _make_material("材料甲")
    _make_material("材料乙")
    wizard._order = [d.id for d in dao.list_documents()]
    wizard._checked = set(wizard._order)
    wizard.chk_titles.setChecked(False)
    wizard.ed_title.setText("批量入库")
    wizard.chk_batch.setChecked(True)
    assert len(wizard.selected_doc_ids()) == 2, "前置条件：应勾到 2 份材料"

    wizard._start()
    worker = wizard._batch_worker
    from PySide6.QtWidgets import QApplication
    for _ in range(200):
        QApplication.processEvents()
        worker.wait(20)
        if not worker.isRunning():
            break
    for _ in range(5):
        QApplication.processEvents()

    assert len(seen_ids) == 2, f"批量线程只拿到 {len(seen_ids)} 份材料"
    saved = [d for d in dao.list_documents() if d.title.startswith("批量件")]
    assert len(saved) == 2, f"批量产物入库不全：{[d.title for d in saved]}"


# ============================================================ 悬空关联
def test_dangling_doc_id_disables_open(wizard, monkeypatch):
    """B5.5：台账里 doc_id 指向的条目被删后，「打开原文」必须置灰并说明原因。"""
    from gwtool.ui.registry_dialog import RegistryDialog

    doc_id = _make_material("会被删掉的材料")
    dao.add_dispatch(dao.Dispatch(title="关联了一个要删的条目",
                                  doc_id=doc_id))
    dao.delete_document(doc_id)

    dlg = RegistryDialog()
    dlg.reload()
    dlg.table.selectRow(0)
    assert dlg.btn_open_source.isEnabled() is False, "悬空关联仍可点开原文"

    # 关联完好时应可点
    ok_id = _make_material("正常的材料")
    dao.add_dispatch(dao.Dispatch(title="关联正常", doc_id=ok_id))
    dlg.reload()
    dlg.table.selectRow(0)
    assert dlg.btn_open_source.isEnabled() is True
    dlg.close()


def test_negative_doc_id_is_treated_as_unlinked(wizard, monkeypatch):
    """doc_id 为负数（如 -1）时必须按「未关联」处理，不能放行去查库。

    背景：`doc_id=-1` 不是凭空构造的形态 —— 它是**真实会落库**的值。
    `dao.add_document` 命中内容重复时返回 -1，调用方若未判负就把 -1 写进
    台账（E2E 自检里就实测留下过这种记录），于是台账里存在指向不存在条目的行。

    守卫写法 `not int(rec.doc_id or 0)` 只挡得住 0 和 None：`int(-1)` 是 -1，
    **真值为 True**，不会在这里短路。真正兜住负数的是下一层——
    `dao.get_document(-1)` 查不到行返回 None，被 `if doc is None: return 0` 拦下。

    也就是说：**负数情形靠的是"查库查不到"这一层，而不是首道判空**。
    本用例把最终行为钉死（无论哪一层救回来），避免日后有人把 `doc is None`
    那一支删掉或加宽 `get_document` 的容错，让 -1 变成"能点开但打开空白页"。
    """
    from gwtool.ui.registry_dialog import RegistryDialog

    dao.add_dispatch(dao.Dispatch(title="doc_id 为负数的脏数据", doc_id=-1))

    dlg = RegistryDialog()
    dlg.reload()
    dlg.table.selectRow(0)
    assert dlg.btn_open_source.isEnabled() is False, (
        "doc_id=-1 被当成有效关联，点开后会是空页或报错")
    # 直接验守卫返回值：负数必须归 0
    rec = dao.get_dispatch(dlg._selected_record().id)
    assert dlg._live_document_id(rec) == 0, "负数 doc_id 未归一为 0"
    dlg.close()


def test_open_source_emits_document_id(wizard, monkeypatch):
    """「打开原文」把 doc_id 通过信号抛出，由主窗口承接跳转。"""
    from gwtool.ui.registry_dialog import RegistryDialog

    doc_id = _make_material("要打开的材料")
    dao.add_dispatch(dao.Dispatch(title="打开原文", doc_id=doc_id))

    dlg = RegistryDialog()
    seen: list[int] = []
    dlg.open_document.connect(seen.append)
    dlg.reload()
    dlg.table.selectRow(0)
    dlg.open_selected_source()
    assert seen == [doc_id]
    dlg.close()


def test_open_source_warns_when_nothing_selected(wizard):
    from gwtool.ui.registry_dialog import RegistryDialog

    dlg = RegistryDialog()
    dlg.open_selected_source()      # 不应抛异常
    assert dlg.btn_open_source.isEnabled() is False
    dlg.close()


# ============================================================ 设置项
def test_compile_settings_defaults(tmp_db):
    """两个新设置项的缺省值：产物不分类、登记只询问。"""
    from gwtool.core import registry  # noqa: F401  (确保模块可导入)
    from gwtool import config

    assert config.compile_result_category() == 0
    assert config.compile_ask_register() is True


def test_compile_settings_roundtrip(tmp_db):
    from gwtool import config

    config.set_compile_result_category(3)
    config.set_compile_ask_register(False)
    assert config.compile_result_category() == 3
    assert config.compile_ask_register() is False

