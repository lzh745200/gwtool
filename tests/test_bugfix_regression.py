# -*- coding: utf-8 -*-
"""针对本轮集中修复的缺陷所加的回归测试。

每个用例对应一处真实 BUG，文件名/类名注明当时的表现，防止日后改回去：
  1. A3 小册子 worker 从未 start() —— 勾选后永远无输出、界面卡死；
  2. 批量纠错漂移重定位盲改 —— 文本变动后可能改到语义无关的另一处；
  3. jieba 运行期异常时纠错门控反向失效 —— 低置信命中被全部吞掉；
  4. 标点重复规则吞掉混合标点串 —— "。，" 被改成 "，"；
  5. 格式体检日期 break 无条件首轮执行 —— 第二处起的日期漏报；
  6. 中文 Word 列表样式「列表段落」漏判 —— 列表项降级为正文；
  7. 导入对话框拖放被列表控件截获 —— 目录展开与扩展名过滤失效；
  8. 工作线程读 Qt 控件 + SQLite 连接随线程泄漏。
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from unittest import mock

import pytest


# ------------------------------------------------------------------ 3. 纠错门控
class TestCorrectorTokenizeFallback:
    """jieba 分词不可用时，门控必须**降级为不过滤**，而不是全灭。"""

    def test_low_conf_hits_survive_when_tokenize_fails(self):
        from gwtool.core import corrector
        raw = [(0, 2, "布署", "部署", "错别字", 0.55),
               (2, 4, "截止", "截至", "错别字", 0.99)]
        with mock.patch("jieba.tokenize",
                        side_effect=RuntimeError("jieba 运行期错误")):
            out = corrector._suppress_inside_common_words("布署工作截止日期", raw)
        assert len(out) == 2, "分词不可用时应保留全部命中而非清空"

    def test_structural_false_positive_still_suppressed(self):
        """即便分词不可用，逐字高频的『结构性误报』仍要拦住。"""
        from gwtool.core import corrector
        raw = [(0, 2, "这是", "这时", "错别字", 0.55)]
        with mock.patch("jieba.tokenize",
                        side_effect=RuntimeError("jieba 运行期错误")):
            out = corrector._suppress_inside_common_words("这是正确的", raw)
        assert out == []


# ------------------------------------------------------------------ 4. 标点规则
class TestPunctRepeatRule:
    def test_same_punct_collapses(self):
        from gwtool.core.corrector import check_text
        res = [(c.wrong, c.suggestion) for c in check_text("句子结束。。。")
               if c.category == "标点"]
        assert ("。。。", "。") in res

    def test_mixed_punct_not_merged(self):
        """『。，』是两个不同标点相邻，不应被错并成一个（原实现会吞掉句号）。"""
        from gwtool.core.corrector import check_text
        bad = [(c.wrong, c.suggestion) for c in check_text("甲。，乙")
               if c.category == "标点"]
        assert ("。，", "，") not in bad


# ------------------------------------------------------------------ 5. 体检日期
class TestInspectorDateAllOccurrences:
    def test_every_date_reported(self):
        from gwtool.core.inspector import inspect_text
        txt = "第一处 2026年08月30日，第二处 2026年09月01日，第三处 2026/8/30。"
        details = [f.detail for f in inspect_text(txt) if f.item == "成文日期"]
        joined = "".join(details)
        assert "2026年08月30日" in joined
        assert "2026年09月01日" in joined, "第二处日期不应因 break 被漏报"
        assert "2026/8/30" in joined, "斜杠日期不应只报第一处"


# ------------------------------------------------------------------ 6. 中文列表
class TestDocxListStyle:
    def test_chinese_list_style_recognized(self, tmp_db, tmp_path):
        from docx import Document as DX
        from gwtool.core.parsers.docx_parser import parse_docx
        p = tmp_path / "列表.docx"
        d = DX()
        d.add_paragraph("第一项", style="List Paragraph")
        d.save(str(p))
        tree = parse_docx(str(p))
        types = [b.type for b in tree.blocks]
        assert "list_item" in types

    def test_cjk_list_style_name_recognized(self, tmp_db, tmp_path):
        """用中文样式名「列表段落」的 docx 也要判成列表项。

        python-docx 没有内置中文样式名，这里直接改样式元素的 name 值，
        模拟中文版 Word 导出的文档（样式名本地化）。
        """
        from docx import Document as DX
        from gwtool.core.parsers.docx_parser import parse_docx
        W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        p = tmp_path / "中文列表.docx"
        d = DX()
        para = d.add_paragraph("第一项", style="List Paragraph")
        # 把样式显示名改成中文版 Word 的本地化名「列表段落」
        name_el = para.style.element.find(W + "name")
        if name_el is not None:
            name_el.set(W + "val", "列表段落")
        d.save(str(p))
        tree = parse_docx(str(p))
        assert "list_item" in [b.type for b in tree.blocks]


# ------------------------------------------------------------------ 2. 漂移重定位
class TestBatchRelocate:
    def test_no_blind_rewrite_when_text_changed(self):
        """文本已变、上下文对不上时必须跳过，不能盲改到别处。"""
        from gwtool.core.batch import CorrectHit, _apply_hits, _context
        t0 = "已在前期落实。随后实施计划另发。"
        h = CorrectHit(start=2, end=4, wrong="实施", suggestion="实行",
                       context=_context(t0, 2, 4))
        t1 = "已在前期落实。补充说明一句。随后实施计划另发。"
        out, n, skipped, _ = _apply_hits(t1, [h])
        assert out == t1 and n == 0 and skipped == 1

    def test_normal_replace_still_works(self):
        from gwtool.core.batch import CorrectHit, _apply_hits, _context
        t0 = "已在前期落实。随后实施计划另发。"
        h = CorrectHit(start=2, end=4, wrong="实施", suggestion="实行",
                       context=_context(t0, 2, 4))
        out, n, _, _ = _apply_hits(t0, [h])
        assert out == t0.replace("实施", "实行") and n == 1

    def test_relocate_uses_context_among_duplicates(self):
        """同词多处、文本整体位移时，应按上下文定位到原来那一处。"""
        from gwtool.core.batch import CorrectHit, _apply_hits, _context
        t3 = "甲处实施工作。乙处实施计划。"
        h = CorrectHit(start=2, end=4, wrong="实施", suggestion="实行",
                       context=_context(t3, 2, 4))
        out, n, _, _ = _apply_hits("前置说明插入。" + t3, [h])
        assert n == 1
        assert "甲处实行工作" in out and "乙处实施计划" in out


# ------------------------------------------------------------------ 7. 导入拖放
class TestImportDialogDrop:
    def test_list_widget_accepts_drops(self, tmp_db, qapp):
        from gwtool.ui.import_dialog import ImportDialog
        dlg = ImportDialog(0)
        assert dlg.file_list.acceptDrops(), "列表控件必须自己接收拖放"
        # 宿主对话框具备展开目录的处理入口
        assert hasattr(dlg, "_drop_urls")

    def test_drop_urls_expands_directory(self, tmp_db, qapp, tmp_path):
        from PySide6.QtCore import QUrl
        from gwtool.ui.import_dialog import ImportDialog
        sub = tmp_path / "材料"
        sub.mkdir()
        (sub / "a.txt").write_text("正文", encoding="utf-8")
        (sub / "b.exe").write_bytes(b"x")        # 非支持格式，应被过滤

        dlg = ImportDialog(0)
        dlg._drop_urls([QUrl.fromLocalFile(str(sub))])
        items = [dlg.file_list.item(i).text() for i in range(dlg.file_list.count())]
        assert any(x.endswith("a.txt") for x in items)
        assert not any(x.endswith("b.exe") for x in items), "不支持的类型不应入列"


# ------------------------------------------------------------------ 1. 小册子 worker
class TestBookletWorkerStarted:
    def test_after_pdf_starts_booklet_worker(self):
        """勾选小册子时 _after_pdf 必须真正启动 worker（原实现漏了 start）。"""
        src = (Path(__file__).resolve().parent.parent
               / "gwtool" / "ui" / "compile_wizard.py").read_text(encoding="utf-8")
        seg = src[src.index("def _after_pdf"):src.index("def _finish")]
        assert "BookletWorker(" in seg
        assert re.search(r"_booklet_worker\.start\(\)", seg), \
            "_after_pdf 里必须调用 _booklet_worker.start()"


# ------------------------------------------------------------------ 8. 线程连接
class TestWorkerConnectionHygiene:
    def test_workers_close_thread_connection(self):
        src = (Path(__file__).resolve().parent.parent
               / "gwtool" / "ui" / "workers.py").read_text(encoding="utf-8")
        assert "_close_thread_conn" in src
        # 六个 worker 的 run 都应带 finally 收尾
        assert src.count("finally:") >= 6

    def test_fn_worker_closes_on_exception(self, tmp_db):
        """worker 抛异常时也要关连接（finally 生效），且 failed 正常发出。"""
        from gwtool.ui.workers import FnWorker
        got = {}

        def boom():
            from gwtool.db import connection as dbconn
            dbconn.get_conn()          # 本线程真开一条连接
            raise RuntimeError("boom")

        w = FnWorker(boom)
        w.failed.connect(lambda m: got.__setitem__("err", m))
        w.run()                        # 同步直接跑，避免 QThread 时序问题
        assert "boom" in got.get("err", "")
        from gwtool.db import connection as dbconn
        assert getattr(dbconn._local, "conn", None) is None, "线程连接应已关闭"
