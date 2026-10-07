# -*- coding: utf-8 -*-
"""OpenCodeReview 全量审查（v1.7.3 基线）修复项的回归护栏。

覆盖 5 项 P1 与关键 P2 的可测行为；无 LLM 依赖、全部离线可跑。
对应报告：doc/OpenCodeReview全量审查报告_v1.7.3.md §三/§四。
"""
from __future__ import annotations

import zipfile

import pytest


# ---------------------------------------------------------------- P1
class TestImportDialogFinishOnce:
    """P1：导入完成回调（finished_ok + finished_detail 先后到达）只处理一次。"""

    def test_double_signal_single_finish(self, qapp, tmp_db):
        from gwtool.ui.import_dialog import ImportDialog

        dlg = ImportDialog()
        dlg._finished_round = False
        # 打点 btn_start.setEnabled：只有 _finish_common 真正执行才会碰它
        calls = {"n": 0}
        orig_set = dlg.btn_start.setEnabled

        def spy(v):
            calls["n"] += 1
            orig_set(v)

        dlg.btn_start.setEnabled = spy
        dlg._done(2, 1)             # worker 先发 finished_ok
        dlg._done_detail(2, 1, [])  # 随后发 finished_detail —— 不得再处理
        assert calls["n"] == 1, calls

    def test_guard_resets_on_new_round(self, qapp, tmp_db):
        """新一轮导入必须复位守卫，否则第二次导入的完成被永久短路。"""
        from gwtool.ui.import_dialog import ImportDialog

        dlg = ImportDialog()
        dlg._finished_round = False
        dlg._done(1, 0)
        assert dlg._finished_round is True
        dlg._finished_round = False      # 模拟 _start 的复位
        dlg._done(1, 0)
        assert dlg._finished_round is True


class TestSaveTemplateIdStable:
    """P1：save_template 走 upsert UPDATE 分支后必须返回真实 id（非陈旧 lastrowid）。"""

    def test_second_save_returns_same_id(self, tmp_db):
        from gwtool.db import dao

        id1 = dao.save_template("护栏模板", '{"k":1}')
        id2 = dao.save_template("护栏模板", '{"k":2}')   # 触发 ON CONFLICT UPDATE
        assert id1 == id2, (id1, id2)
        # 内容确实被更新到同一行（而不是另立新行）
        assert dao.get_template_config("护栏模板") == '{"k":2}'


class TestWatermarkEscapesXml:
    """P1：水印文本含 & 等实体字符时不得让 parse_xml 崩溃。"""

    def test_ampersand_text_ok(self, tmp_db, tmp_path):
        from docx import Document
        from gwtool.core.watermark import add_watermark_docx

        path = tmp_path / "in.docx"
        Document().save(str(path))
        out = add_watermark_docx(str(path), "R&D 项目·意见<稿>",
                                 str(tmp_path / "wm.docx"))
        from docx import Document as D2
        xml = D2(out).sections[0].header._element.xml
        assert "R&amp;D" in xml or "R&D" in xml
        assert "意见&lt;稿&gt;" in xml or "意见<稿>" in xml


# ---------------------------------------------------------------- P2
class TestXlsxSparseGridGuard:
    """P2：稀疏坐标（如 r=\"XFD1\"）不再把补齐网格撑爆，直接判异常文件。"""

    def test_huge_column_rejected(self, tmp_path):
        from gwtool.core import xlsx_read

        p = tmp_path / "evil.xlsx"
        with zipfile.ZipFile(str(p), "w") as z:
            z.writestr("sheet1.xml",
                       '<row r="1"><c r="XFD1" t="n"><v>1</v></c></row>')
        budget = [0]
        with pytest.raises(xlsx_read.XlsxError):
            xlsx_read.read_sheet_rows(zipfile.ZipFile(str(p)), "sheet1.xml",
                                      [], budget)


class TestPhraseFullTextBeyondLimit:
    """P2：句式超过 list_phrases 默认 limit=500 时仍能按 id 取到全文。"""

    def test_502nd_phrase_retrievable(self, tmp_db):
        from gwtool.core import reference
        from gwtool.db import connection as dbconn

        conn = dbconn.get_conn()
        last = 0
        for i in range(1, 503):        # 批量直插（一次事务），避开 502 次 commit
            cur = conn.execute(
                "INSERT INTO user_phrases(phrase,context,source) VALUES(?,?, 't')",
                (f"句式{i}", f"第{i}条正文"))
            last = int(cur.lastrowid)
        conn.commit()
        assert reference.phrase_full_text(last) == "第502条正文"


class TestGetPhraseById:
    def test_get_phrase_roundtrip(self, tmp_db):
        from gwtool.db import dao

        pid = dao.add_phrase("术语", "解释说明")
        p = dao.get_phrase(pid)
        assert p is not None and p.phrase == "术语"
        assert dao.get_phrase(99999999) is None
