# -*- coding: utf-8 -*-
"""深审回归：2026-09-23 对全项目做规则级深度审查后修复的四项缺陷。

四项都由真实链路复现后定位（不是理论担忧），每项都补了先前**没有覆盖**的
那一档边界 —— 它们能潜伏下来，正是因为既有用例恰好绕过了这些取值：

1. **金额大写跨段漏零**（`toolbox.amount_to_cn`）
   段间补零的旧判据是"本段是否小于 1000"，只覆盖了"下段不满四位"，
   漏掉"上段不满四位"这一半。1005000 因此读成「壹佰万伍仟元整」——
   按《正确填写票据和结算凭证的基本规定》"阿拉伯数字中间有 0 时，
   汉字大写金额要写零字"，应为「壹佰万零伍仟元整」。金额大写是票据要素，
   少一个"零"等于少一个数量级的歧义。

2. **相似度短串判定失真**（`simhash.jaccard`）
   旧实现让每一边按自身长度定阶：不足 3 字的串整个充当唯一元素，
   于是 "甲乙" 与 "甲乙丙"（一个明显是另一个的前缀）交集恒为空 →
   相似度 0.0，短句查重直接失效。

3. **台账日期不校验真实日历**（`registry.normalize_date`）
   旧判据只查 `1<=月<=12` 与 `1<=日<=31`，放行 2026年2月30日、
   2026-02-31 这类不存在的日期；入库后它们会以"合法日期"身份参与
   年度分组与排序，错误一直藏到按日期对账时才暴露。

3b. **同模块两条日期入口口径不一**（`registry.validate`）
   上一条修好后，`validate` 仍只查 `YYYY-MM-DD` 的形状正则：
   走汇编封面进来的「2026年2月30日」会被拒，而用户在台账里手工敲
   `2026-02-30`（甚至 `2026-13-01`）照样存得进去 —— 坏日期从另一扇门
   进来，`normalize_date` 的修复被绕过。两条入口必须共用同一把尺子。

4. **非一级同名首块导致正文整体丢失**（`model.DocTree.effective_blocks`）
   跳过条件未校验 level：一个 level=2 的同名首块也会被当作"已渲染过"
   删掉，而它其实没被渲染过；`blocks[1:]` 在只有该块时把列表清空。

5. **批量替换的预览与执行条件不一致**（`feature_dialogs.BulkReplaceDialog`）
   `_apply` 读实时控件值，而执行按钮的启用只取决于上次预览：预览后改
   查找内容再点执行，就用新条件改旧清单。最坏档是「按正则」+ 查找留空
   ——`re.subn("", x, text)` 在每个字符间插入 x，整篇正文被破坏。

6. **PDF 输出预览渲染不保证关闭文档**（`editor_panel._render_pdf_images`）
   `doc.close()` 是裸调用：某页渲染抛异常就被跳过，PyMuPDF 文档与文件
   句柄一起泄漏。同仓 `booklet.py`/`pdfrender.py` 都已用 try/finally。
"""
from __future__ import annotations

import pytest

from gwtool.core import registry as rg
from gwtool.core import simhash as sh
from gwtool.core import toolbox as tb
from gwtool.core.model import HEADING, PARAGRAPH, Block, DocTree


@pytest.fixture(autouse=True)
def _silence_modal_dialogs(monkeypatch):
    """屏蔽本文件里 UI 类用例可能弹出的模态框。

    `widgets.warn/info/ask` 走的是 `QMessageBox` 的**静态**方法，它们是 C++ 侧
    实现、不经 Python 层的 `QDialog.exec`，因此 `monkeypatch.setattr(QDialog,
    "exec", ...)` 拦不住 —— 会真的弹框，在无头环境把测试**永久挂死**
    （实测：无此夹具时 test_builtin_entry_survives_delete_attempt 卡在
    QMessageBox.warning 上直到 pytest-timeout 介入）。这是本仓反复出现的坑，
    凡触发 UI 提示的用例都必须先屏蔽。
    """
    from PySide6.QtWidgets import QMessageBox
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes))


# ============================================================ 1. 金额大写补零
class TestAmountToChineseCrossChunkZero:
    """跨段补零：上段不满四位时，两段之间的零必须写出来。"""

    @pytest.mark.parametrize("value,expected", [
        # ↓ 本轮修复的核心：上段 0100（最低有效位在百位），下段千位起
        ("1005000", "人民币壹佰万零伍仟元整"),
        # 上段 0001（最低有效位在万位）与下段千位相邻 → 不补零
        ("16409", "人民币壹万陆仟肆佰零玖元整"),
        ("1234567", "人民币壹佰贰拾叁万肆仟伍佰陆拾柒元整"),
        # 上段 1（亿位）与下段 5（万位）相距 4 位 → 补零
        ("100050000", "人民币壹亿零伍万元整"),
        # 两段都满四位：规范明确"可以只写一个零字，也可以不写"，取不写
        ("50001000", "人民币伍仟万壹仟元整"),
        # 三位段接四位段且上段尾部有零
        ("100500000", "人民币壹亿零伍拾万元整"),
        ("105000000", "人民币壹亿零伍佰万元整"),
    ])
    def test_cross_chunk_zero_placement(self, value, expected):
        assert tb.amount_to_cn(value) == expected

    def test_regression_pair_differing_only_by_one_zero(self):
        """1005000 与 10050000 只差一个零，读数只该差一个"零"字。

        旧实现把两者读成「壹佰万伍仟元整」/「壹仟零伍万元整」——
        前者漏零、后者正确，同一函数内自相矛盾，正是判据只覆盖一半的表现。
        """
        small = tb.amount_to_cn("1005000")
        big = tb.amount_to_cn("10050000")
        assert small == "人民币壹佰万零伍仟元整"
        assert big == "人民币壹仟零伍万元整"
        assert "零" in small and "零" in big

    def test_no_duplicate_zero_ever(self):
        """连续零只写一个：任何金额都不应出现"零零"。"""
        for v in ("10000005", "100000005", "1000000005", "10000000005"):
            assert "零零" not in tb.amount_to_cn(v)


# ============================================================ 2. 相似度短串
class TestShortTextSimilarity:
    """短串（含长短混合）的相似度必须反映真实的包含关系。"""

    def test_short_is_prefix_of_long(self):
        """本轮修复的核心：前缀关系不能再被判成 0。"""
        got = sh.jaccard("甲乙", "甲乙丙")
        assert got > 0, "短串是长串前缀却被判为 0 相似——短句查重失效"

    def test_both_sides_use_the_same_ngram_order(self):
        """两边必须同阶，否则集合永远无法相交。

        这是上一档失效的机制本身：不同阶的 n-gram 集合无交集。
        用两个长度不同但内容完全相同的串验证 —— 相同内容必须相似度 1.0。
        """
        assert sh.jaccard("乡村振兴", "乡村振兴") == 1.0
        assert sh.jaccard("乡村", "乡村") == 1.0
        assert sh.jaccard("乡村振", "乡村振") == 1.0

    @pytest.mark.parametrize("a,b", [
        ("甲乙丙丁", "甲乙丙丁戊"),
        ("通知", "关于开展工作的通知"),
    ])
    def test_containment_yields_positive_score(self, a, b):
        assert sh.jaccard(a, b) > 0

    @pytest.mark.parametrize("a,b", [
        ("甲乙", "丙丁"),
        ("短", "完全不同的长文本内容"),
    ])
    def test_unrelated_still_zero(self, a, b):
        """反向验证：无关的短串仍必须是 0，不能为了修前缀档把噪声一起放过。"""
        assert sh.jaccard(a, b) == 0.0

    def test_long_text_behaviour_unchanged(self):
        """长文本（≥3 字）行为必须与修复前完全一致（仍是 3-gram）。"""
        a = ("乡村振兴要坚持农业农村优先发展，巩固拓展脱贫攻坚成果，"
             "扎实推进共同富裕各项工作。")
        b = ("乡村振兴要坚持农业农村优先发展，巩固拓展脱贫攻坚成果，"
             "扎实推进共同富裕各项任务。")
        c = ("今天天气晴朗，适合外出郊游，公园里的花朵都开放了，"
             "孩子们在草地上奔跑嬉戏。")
        assert sh.similarity(a, b) > 0.8
        assert sh.similarity(a, c) < 0.3

    def test_empty_handling_unchanged(self):
        assert sh.jaccard("", "") == 1.0
        assert sh.jaccard("", "非空") == 0.0
        assert sh.jaccard("非空", "") == 0.0


# ============================================================ 3. 台账日期日历校验
class TestRegistryDateCalendarValidation:
    """归一化后的日期必须是**真实存在**的日历日。"""

    @pytest.mark.parametrize("raw,expected", [
        ("2026年8月30日", "2026-08-30"),
        ("2026年8月", "2026-08-01"),
        ("2026-08-15", "2026-08-15"),
        ("2026.8.5", "2026-08-05"),
        ("2026年", "2026-01-01"),
        # 闰年：2024 是闰年，2月29日成立
        ("2024年2月29日", "2024-02-29"),
    ])
    def test_valid_dates_normalized(self, raw, expected):
        assert rg.normalize_date(raw) == expected

    @pytest.mark.parametrize("raw", [
        "2026年2月30日",        # 二月没有 30 日 —— 旧实现放行
        "2026-02-31",          # 二月没有 31 日 —— 旧实现放行
        "2026年4月31日",        # 四月没有 31 日
        "2026年13月1日",        # 月份越界
        "2026年0月1日",         # 月份为 0
        "2026年2月29日",        # 平年没有 2月29日
        "99年3月1日",           # 年份不足四位
        "2026年1月0日",         # 日为 0
        "",
    ])
    def test_impossible_dates_rejected(self, raw):
        assert rg.normalize_date(raw) == "", f"{raw!r} 应被拒绝"

    def test_rejected_date_does_not_poison_year_grouping(self):
        """被拒绝的日期不得进入台账 → 年度分组里不能出现幽灵年份。"""
        d = rg.from_compile("关于XX工作的通知")
        d.sign_date = rg.normalize_date("2026年2月30日")
        assert d.sign_date == ""
        assert rg.year_of(d) in ("", "2026")   # 仅可能从字号推断，不得来自脏日期


# ==================================================== 3b. validate 日期同口径
class TestValidateDateCalendarConsistency:
    """`validate` 与 `normalize_date` 必须用**同一把尺子**。

    `normalize_date` 修好之后，`validate` 仍是旧的正则判据（只查 YYYY-MM-DD
    形状）：同一条「2026 年 2 月 30 日」，走汇编封面进来会被拒，而用户在台账
    里手工敲 `2026-02-30` 却能存进库。两条入口一条严一条松，坏日期照样入库，
    照样污染 `year_of` / 逐月统计 —— 等于 normalize_date 的修复被绕过。
    """

    @staticmethod
    def _only(value: str) -> list:
        """只有这一个入口可测：`rg.validate`。

        此前还挂着一个 `lenient_ok` 形参，但函数体从未读它 —— 调用处照样按
        True/False 传值，制造出"存在松/严两条口径"的错觉，后来者据此改断言
        就会与实现脱节（死参数本身就是误导）。
        """
        from gwtool.db import dao
        return rg.validate(dao.Dispatch(title="测试标题", sign_date=value))

    @pytest.mark.parametrize("value", [
        "2026-02-30",     # 二月没有 30 日
        "2026-02-31",     # 二月没有 31 日
        "2026-04-31",     # 四月没有 31 日
        "2026-13-01",     # 月份越界（形状完全合规，正则拦不住）
        "2026-00-10",     # 月份为 0
        "2026-05-00",     # 日为 0
        "2026-02-29",     # 平年没有 2 月 29 日
    ])
    def test_impossible_calendar_dates_rejected(self, value):
        problems = self._only(value)
        assert any("不是一个存在的日期" in p for p in problems), (
            f"{value} 是不存在的日期，validate 必须拒绝，实际返回 {problems}")

    @pytest.mark.parametrize("value", [
        "2026-01-01", "2026-02-28", "2026-12-31",
        "2024-02-29",     # 闰年 2 月 29 日成立
        "2000-02-29",     # 世纪闰年（能被 400 整除）
    ])
    def test_real_dates_accepted(self, value):
        problems = self._only(value)
        assert not [p for p in problems if "日期" in p], f"{value} 是合法日期"

    @pytest.mark.parametrize("value", ["2026-ab-cd", "2026/05/10", "20260510"])
    def test_wrong_shape_still_caught_by_format_rule(self, value):
        problems = self._only(value)
        assert any("应为 YYYY-MM-DD 格式" in p for p in problems)

    def test_empty_date_still_allowed(self):
        """成文/印发日期允许为空（旧行为不可回退）。"""
        assert not [p for p in self._only("") if "日期" in p]

    def test_both_date_fields_share_the_same_rule(self):
        """印发日期走同一把尺子，不能只严成文日期。"""
        from gwtool.db import dao
        d = dao.Dispatch(title="T", print_date="2026-13-01")
        assert any("印发日期不是一个存在的日期" in p for p in rg.validate(d))

    def test_print_before_sign_still_rejected(self):
        """新增日历校验不得顶掉「印发早于成文」这条既有规则。"""
        from gwtool.db import dao
        d = dao.Dispatch(title="T", sign_date="2026-05-10", print_date="2026-05-09")
        assert "印发日期早于成文日期" in rg.validate(d)


# ============================================================ 4. DocTree 去重层级
class TestEffectiveBlocksLevelGuard:
    """只有**一级**的同名首块才能被当作"已渲染过"跳过。"""

    def test_level1_same_title_is_skipped(self):
        tree = DocTree(title="报告", blocks=[
            Block(type=HEADING, level=1, text="报告"),
            Block(type=PARAGRAPH, text="正文"),
        ])
        assert [b.text for b in tree.effective_blocks(True)] == ["正文"]

    def test_level2_same_title_keeps_body(self):
        """本轮修复的核心：不得因层级不符把整篇正文清空。"""
        tree = DocTree(title="报告", blocks=[
            Block(type=HEADING, level=2, text="报告"),
            Block(type=PARAGRAPH, text="正文"),
        ])
        got = [b.text for b in tree.effective_blocks(True)]
        assert got == ["报告", "正文"], "level=2 首块被误删，正文一起丢了"

    def test_does_not_return_empty_when_only_block_is_level2(self):
        """极端档：整篇只有一个 level=2 同名块时，不能返回空列表。"""
        tree = DocTree(title="报告", blocks=[
            Block(type=HEADING, level=2, text="报告"),
        ])
        assert len(tree.effective_blocks(True)) == 1

    def test_different_title_untouched(self):
        tree = DocTree(title="报告", blocks=[
            Block(type=HEADING, level=1, text="其他标题"),
            Block(type=PARAGRAPH, text="正文"),
        ])
        assert len(tree.effective_blocks(True)) == 2

    def test_insert_titles_false_keeps_everything(self):
        tree = DocTree(title="报告", blocks=[
            Block(type=HEADING, level=1, text="报告"),
            Block(type=PARAGRAPH, text="正文"),
        ])
        assert len(tree.effective_blocks(False)) == 2


# ============================================ 5. 批量替换：预览与执行必须同条件
class TestBulkReplacePlanConsistency:
    """预览—执行不一致（`feature_dialogs.BulkReplaceDialog` / `_BulkReplaceWorker`）。

    老实现里 `_apply()` 读的是**实时控件值**（`self.ed_find.text()` 等），
    而「执行替换」按钮的启用状态只取决于**上一次预览**的 total>0：

      · 预览命中若干处 → 按钮变亮；
      · 用户随即改查找框（或清空、或切换「按正则」）→ 按钮**依然亮着**；
      · 点执行 → 用**新条件**改**旧清单**里的文档，用户看过的预览彻底失效。

    最坏档是「按正则」+ 查找留空：`re.subn("", x, text)` 会在每个字符之间
    插入 x，实测「某市人民政府办公室文件」→「【空】某【空】市【空】…」，
    整篇正文被破坏，而用户主观上是"什么都没输入就点了执行"。

    对照 `core/batch.py` 的既有契约——那里明确要求"执行时必须带上预览得到的
    plans"，即预览与执行共用同一份计划。本对话框漏了这一条。
    """

    @staticmethod
    def _worker(find, repl, use_regex=False, doc_ids=(1,)):
        from gwtool.ui.feature_dialogs import _BulkReplaceWorker
        return _BulkReplaceWorker(list(doc_ids), find, repl, use_regex)

    def test_empty_find_is_rejected_with_a_clear_reason(self):
        """空查找串必须整批拒绝，且说明后果 —— 它不是"无操作"。"""
        w = self._worker("", "【空】", use_regex=True)
        seen: list = []
        w.failed.connect(lambda m: seen.append(m))
        done: list = []
        w.done.connect(done.append)
        w._run()
        assert seen, "空查找串必须触发 failed，绝不能静默把正文改烂"
        assert "不能为空" in seen[0]
        assert not done, "拒绝时不得同时发出 done"
        assert "破坏" in seen[0], "文案要讲清后果，否则用户不知道为何被拦"

    def test_empty_find_rejected_in_plain_mode_too(self):
        """非正则模式同样危险：str.replace('', x) 也逐字符插入。"""
        w = self._worker("", "X", use_regex=False)
        seen: list = []
        w.failed.connect(lambda m: seen.append(m))
        w._run()
        assert seen, "非正则模式的空查找同样必须拒绝"

    def test_plan_key_captures_all_three_conditions(self):
        """快照必须覆盖查找、替换、正则开关这三项 —— 漏任一项都能绕过校验。"""
        from PySide6.QtWidgets import QApplication

        from gwtool.ui.feature_dialogs import BulkReplaceDialog

        app = QApplication.instance() or QApplication([])
        dlg = BulkReplaceDialog(category_id=None)
        try:
            dlg.ed_find.setText("办公室")
            dlg.ed_repl.setText("办公厅")
            dlg.chk_regex.setChecked(False)
            key0 = dlg._current_key()
            assert key0 == ("办公室", "办公厅", False)

            dlg.ed_repl.setText("办公厅室")
            assert dlg._current_key() != key0, "替换内容变化必须反映在快照里"
            dlg.ed_repl.setText("办公厅")

            dlg.chk_regex.setChecked(True)
            assert dlg._current_key() != key0, "「按正则」开关变化必须反映在快照里"
            dlg.chk_regex.setChecked(False)

            dlg.ed_find.setText("公文")
            assert dlg._current_key() != key0, "查找内容变化必须反映在快照里"
        finally:
            dlg.deleteLater()
            app.processEvents()

    def test_apply_refuses_when_never_previewed(self):
        """未预览过就点执行（如通过代码直调）必须被拦下。"""
        from PySide6.QtWidgets import QApplication

        from gwtool.ui.feature_dialogs import BulkReplaceDialog

        app = QApplication.instance() or QApplication([])
        dlg = BulkReplaceDialog(category_id=None)
        try:
            dlg.ed_find.setText("办公室")
            assert dlg._plan_key is None
            assert not dlg.btn_apply.isEnabled(), "未预览时执行按钮应保持禁用"
        finally:
            dlg.deleteLater()
            app.processEvents()

    def test_changing_condition_after_preview_disables_apply(self):
        """预览后改条件 → 按钮立刻置灰、快照作废（不必等用户点执行）。"""
        from PySide6.QtWidgets import QApplication

        from gwtool.ui.feature_dialogs import BulkReplaceDialog

        app = QApplication.instance() or QApplication([])
        dlg = BulkReplaceDialog(category_id=None)
        try:
            dlg.ed_find.setText("办公室")
            dlg.ed_repl.setText("办公厅")
            # 模拟"预览完成且有命中"的状态
            dlg._plan_key = dlg._current_key()
            dlg.btn_apply.setEnabled(True)

            dlg.ed_find.setText("办公")          # 用户改了条件
            assert dlg._plan_key is None, "条件漂移后快照必须作废"
            assert not dlg.btn_apply.isEnabled(), \
                "条件漂移后必须禁用执行，否则会用新条件改旧清单"
        finally:
            dlg.deleteLater()
            app.processEvents()

    def test_keeping_condition_keeps_plan_alive(self):
        """反向验证：不改条件时快照必须存活（不能误伤正常流程）。"""
        from PySide6.QtWidgets import QApplication

        from gwtool.ui.feature_dialogs import BulkReplaceDialog

        app = QApplication.instance() or QApplication([])
        dlg = BulkReplaceDialog(category_id=None)
        try:
            dlg.ed_find.setText("办公室")
            plan = dlg._current_key()
            dlg._plan_key = plan
            dlg.btn_apply.setEnabled(True)

            dlg.ed_find.setText("办公室")        # 写成相同的值
            assert dlg._plan_key == plan, "条件未变，快照不该被作废"
            assert dlg.btn_apply.isEnabled()
        finally:
            dlg.deleteLater()
            app.processEvents()


# ==================================== 6. PDF 输出预览渲染：文档必须成对关闭
class TestPdfPreviewAlwaysClosesDocument:
    """`editor_panel._render_pdf_images` 的 `doc.close()` 必须在 finally 里。

    老实现是裸调用：只要 `get_pixmap()` 在某页抛异常（该页内容损坏、
    渲染时内存不足），close 就被跳过 → PyMuPDF 文档与底层文件句柄一起泄漏。
    本函数跑在"每次生成后都调"的输出预览通路上，长会话反复预览会累积到
    句柄耗尽（麒麟上尤其容易撞 too many open files）。

    同一仓库 `core/booklet.py` / `core/pdfrender._page_count` 都已按
    try/finally 写，本用例把这条纪律锁进门禁。
    """

    @staticmethod
    def _pdf(tmp_path, pages: int) -> str:
        import pymupdf
        d = pymupdf.open()
        for _ in range(pages):
            d.new_page()
        p = str(tmp_path / "render_probe.pdf")
        d.save(p)
        d.close()
        return p

    def test_normal_path_returns_requested_pages(self, tmp_path):
        pytest.importorskip("PySide6")
        pytest.importorskip("pymupdf")
        from PySide6.QtGui import QGuiApplication

        from gwtool.ui.editor_panel import _render_pdf_images

        app = QGuiApplication.instance() or QGuiApplication([])
        assert app is not None
        path = self._pdf(tmp_path, 3)
        images, n, out = _render_pdf_images(path, max_pages=8)
        assert n == 3 and len(images) == 3 and out == path
        assert all(im.width() > 0 and im.height() > 0 for im in images)

    def test_max_pages_caps_the_work(self, tmp_path):
        pytest.importorskip("PySide6")
        from PySide6.QtGui import QGuiApplication

        from gwtool.ui.editor_panel import _render_pdf_images

        app = QGuiApplication.instance() or QGuiApplication([])
        assert app is not None
        _, n, _ = _render_pdf_images(self._pdf(tmp_path, 5), max_pages=2)
        assert n == 2, "max_pages 必须生效（长文档不能全量渲染卡住 UI）"

    def test_document_is_closed_when_a_page_render_fails(self, tmp_path):
        """核心回归：渲染中途失败时 close() 仍必须被调用（不得泄漏句柄）。"""
        pytest.importorskip("PySide6")
        import pymupdf
        from PySide6.QtGui import QGuiApplication

        from gwtool.ui import editor_panel as ep

        app = QGuiApplication.instance() or QGuiApplication([])
        assert app is not None
        path = self._pdf(tmp_path, 3)

        closed = {"n": 0}
        real_open = pymupdf.open

        class _SpyDoc:
            def __init__(self, real):
                self._r = real

            def __getattr__(self, k):
                return getattr(self._r, k)

            def close(self):
                closed["n"] += 1
                return self._r.close()

            def __getitem__(self, i):
                if i == 1:
                    class _BadPage:
                        @staticmethod
                        def get_pixmap(**_kw):
                            raise RuntimeError("模拟该页渲染失败")
                    return _BadPage()
                return self._r[i]

        pymupdf.open = lambda p: _SpyDoc(real_open(p))
        try:
            with pytest.raises(RuntimeError):
                ep._render_pdf_images(path, max_pages=8)
        finally:
            pymupdf.open = real_open

        assert closed["n"] == 1, (
            "渲染中途失败时文档对象未被关闭 → PyMuPDF 句柄泄漏")

# ====================================== 7. 内置词典不可被界面删除
class TestBuiltinDictionaryCannotBeDeleted:
    """词典页删除必须拦住内置词条（cc-cedict）。

    为什么是真缺陷（而不是"用户别乱点"）：词典页**检索态会把内置词典一并列出**
    （用户搜词就是要看释义），而 `_del_dict_row` 旧实现直接
    `DELETE FROM dictionary WHERE word=?`、不区分来源。内置 12.3 万条由
    `scripts/seed_data.py` 一次性灌入，**界面无任何重建入口**；更严重的是
    `dao.builtin_dictionary_words()` 把非用户来源的词条当作重复字检测的
    **判词证据集** —— 批量误删会让"这段字是不是汉语真词"的仲裁失灵，
    原本靠词典放行的正常语句（"进行行。"、"直接接时间"）会被翻成误报。
    """

    @pytest.fixture()
    def dlg(self, tmp_db, qapp):
        from gwtool.ui.dict_manager import DictManager
        d = DictManager()
        yield d
        d.close()

    def test_source_column_is_visible_so_guard_can_read_it(self, dlg):
        """守卫依赖第 5 列（来源）：表头与 SELECT 列序必须对齐。"""
        assert dlg.dict_table.columnCount() >= 5, (
            "词典表需保留「来源」列，否则删除守卫读不到来源判据")
        assert dlg.dict_table.horizontalHeaderItem(4).text() == "来源"

    def test_builtin_entry_survives_delete_attempt(self, dlg):
        """核心回归：对内置词条执行删除 → 词条仍在；自建词条正常删除。"""
        from gwtool.db.connection import get_conn
        conn = get_conn()
        conn.execute(
            "INSERT INTO dictionary(word,pinyin,definition,example,source)"
            " VALUES(?,?,?,?,?)", ("测试内置词", "csnzc", "内置", "", "cc-cedict"))
        conn.execute(
            "INSERT INTO dictionary(word,pinyin,definition,example,source)"
            " VALUES(?,?,?,?,?)", ("测试自建词", "csjzc", "自建", "", "我的词表"))
        conn.commit()

        dlg.dict_search.setText("测试")
        dlg._reload_dict()
        assert dlg.dict_table.rowCount() == 2, "检索态应同时列出内置与自建词条"

        dlg.dict_table.selectAll()
        dlg._del_dict_row()

        remain = {r[0] for r in conn.execute(
            "SELECT word FROM dictionary WHERE word LIKE '测试%'").fetchall()}
        assert "测试内置词" in remain, "内置词条被删掉了 —— 守卫失效"
        assert "测试自建词" not in remain, "自建词条应被正常删除"

    def test_delete_with_no_selection_is_noop(self, dlg):
        """未选中任何行时不得抛异常（rows 为空集）。"""
        dlg.dict_table.clearSelection()
        dlg._del_dict_row()

    def test_delete_with_missing_row_items_does_not_crash(self, dlg):
        """防御性：整行 item 缺失时不得 AttributeError（旧实现直接 item(r,0).text()）。"""
        from gwtool.db.connection import get_conn
        conn = get_conn()
        conn.execute(
            "INSERT INTO dictionary(word,pinyin,definition,example,source)"
            " VALUES(?,?,?,?,?)", ("测试空行", "cskh", "x", "", "我的词表"))
        conn.commit()
        dlg.dict_search.setText("测试空行")
        dlg._reload_dict()
        for c in range(dlg.dict_table.columnCount()):
            dlg.dict_table.setItem(0, c, None)     # 模拟脏表
        dlg.dict_table.selectAll()
        dlg._del_dict_row()                        # 不应抛异常


# ============================ 8. 文号序号提取（台账自动取号）
class TestSerialExtractionTakesMax:
    """`max_doc_no_serial` / `max_reg_no_serial` 必须取该行**全部**号段的最大值。

    为什么是真缺陷：这两个函数的契约是「已用序号的**最大值**」——台账的
    "自动取号"直接拿它 +1。旧实现用 `re.search` 只取第一个 `〕N号` 片段，
    于是当同一行文号列里出现多个号段（真实场景：「×政办发〔2025〕8号并入
    〔2025〕12号」这类合并件/补正件的写法）时，得到的是 8 而非 12 →
    自动取号给出**已经用过的 9**，台账出现重号。旧 docstring 写的是
    "取最大"，代码却只取首个，属实现与契约不符。
    """

    @pytest.fixture()
    def conn(self, tmp_db):
        from gwtool.db.connection import get_conn
        return get_conn()

    def test_doc_serial_uses_max_of_all_number_segments(self, conn):
        from gwtool.db import dao
        conn.execute(
            "INSERT INTO dispatch_register(doc_no,title,sign_date) VALUES(?,?,?)",
            ("×政办发〔2025〕8号并入〔2025〕12号", "合并件", ""))
        conn.execute(
            "INSERT INTO dispatch_register(doc_no,title,sign_date) VALUES(?,?,?)",
            ("×政办发〔2025〕3号", "普通件", "2025-01-05"))
        conn.commit()
        got = dao.max_doc_no_serial("×政办发", "2025")
        assert got == 12, f"应取号段最大值 12，实得 {got}（自动取号会重号）"

    def test_doc_serial_still_zero_when_no_match(self, conn):
        from gwtool.db import dao
        assert dao.max_doc_no_serial("不存在代字", "2025") == 0

    def test_reg_serial_uses_max_of_all_number_segments(self, conn):
        from gwtool.db import dao
        conn.execute(
            "INSERT INTO receive_register(reg_no,title,receive_date) VALUES(?,?,?)",
            ("收〔2025〕20号(原〔2025〕7号)", "更正件", "2025-02-01"))
        conn.commit()
        assert dao.max_reg_no_serial("收", "2025") == 20

    def test_serial_ignores_unreasonably_long_digits(self, conn):
        """号段里混进异常长数字时不得把"最大序号"顶成天文数字。

        ⚠️ 旧断言写的是 `got >= 9` —— 那**无论是否忽略长号段都会通过**
        （30 个 9 会被 int() 成功，`\\d+` 永不抛 ValueError），等于给了假绿。
        现在断言等值：既要求脏数据被跳过，也要求正常的 9 仍被取到。
        """
        from gwtool.db import dao
        conn.execute(
            "INSERT INTO dispatch_register(doc_no,title,sign_date) VALUES(?,?,?)",
            ("×政办发〔2025〕9号", "正常件", ""))
        conn.execute(
            "INSERT INTO dispatch_register(doc_no,title,sign_date) VALUES(?,?,?)",
            ("×政办发〔2025〕" + "9" * 30 + "号", "脏数据", ""))
        conn.commit()
        assert dao.max_doc_no_serial("×政办发", "2025") == 9, (
            "超长号段必须被跳过，且正常的 9 仍要取到（旧断言 >= 9 恒真）")


# ============================ 9. 年度统计的分组过滤（护栏，非缺陷修复）
class TestYearlyStatsBracket:
    """`dispatch_stats` / `receive_stats` 的年度条件加括号。

    ⚠️ 如实说明：这**不是**缺陷修复。实测（直接拿两条 SQL 在 SQLite 上对跑）
    证明"无括号"版本结果与加括号版完全一致 —— `GROUP BY` 之前只有 WHERE
    子句，`OR` 并未跨出 WHERE 的范围，旧写法语义本来就是对的。

    加括号的理由是**可读性与防退化**：旧写法 `WHERE a LIKE ? OR b LIKE ?` 与
    后面要追加的 `AND xxx` 天然容易踩"OR 与 AND 优先级"的坑（一旦将来多一个
    条件，`a OR b AND c` 就等于 `a OR (b AND c)`，年度过滤会真的失效）。
    这几条用例作为**护栏**存在：把当前正确的聚合语义钉住，改动时若把括号去掉
    或加错位置，立刻能看出来。
    """

    @pytest.fixture()
    def conn(self, tmp_db):
        from gwtool.db.connection import get_conn
        return get_conn()

    def test_dispatch_stats_year_filter_holds(self, conn):
        from gwtool.db import dao
        for dt, sd in (("通知", "2026-03-01"), ("通知", "2026-05-01"),
                       ("通知", "2025-03-01"), ("报告", "2025-06-01")):
            conn.execute(
                "INSERT INTO dispatch_register(title,doc_type,sign_date,status)"
                " VALUES(?,?,?,'拟稿')", ("t", dt, sd))
        conn.commit()
        got = dict(dao.dispatch_stats("doc_type", "2026"))
        assert got.get("通知") == 2, f"2026 年应只有 2 件通知，实得 {got}"
        assert "报告" not in got, "2025 年的报告混进了 2026 年度统计"

    def test_year_filter_also_matches_doc_no(self, conn):
        """年度过滤的另一半：成文日期缺失、年份只写在文号里也要能命中。"""
        from gwtool.db import dao
        conn.execute(
            "INSERT INTO dispatch_register(title,doc_type,sign_date,doc_no,status)"
            " VALUES('t','函','', '×政办发〔2026〕9号', '拟稿')")
        conn.execute(
            "INSERT INTO dispatch_register(title,doc_type,sign_date,status)"
            " VALUES('t','函','2025-01-01','拟稿')")
        conn.commit()
        got = dict(dao.dispatch_stats("doc_type", "2026"))
        assert got.get("函") == 1, f"文号含 2026 的那件应被计入，实得 {got}"

    def test_dispatch_stats_without_year_counts_all(self, conn):
        from gwtool.db import dao
        for sd in ("2026-03-01", "2025-03-01", "2024-03-01"):
            conn.execute(
                "INSERT INTO dispatch_register(title,doc_type,sign_date,status)"
                " VALUES('t','通知',?,'拟稿')", (sd,))
        conn.commit()
        assert dict(dao.dispatch_stats("doc_type")).get("通知") == 3

    def test_receive_stats_year_filter_holds(self, conn):
        from gwtool.db import dao
        for org, rd in (("甲局", "2026-01-10"), ("乙局", "2025-01-10")):
            conn.execute(
                "INSERT INTO receive_register(title,from_org,receive_date,status)"
                " VALUES('t',?,?,'签收')", (org, rd))
        conn.commit()
        got = dict(dao.receive_stats("from_org", "2026"))
        assert got == {"甲局": 1}, f"2026 年收文应只有甲局 1 件，实得 {got}"


# ================== 10. 批量汇编的产物必须能被登记到台账
class TestBatchProductsReachRegister:
    """`_save_products_to_library(products=...)` 必须回填 `self._products`。

    为什么是真缺陷：批量模式在 `_run_batch` 里把 `self._products` 清空，随后
    用临时列表 `[(p, stem) for p in paths]` 调本函数。旧实现在"显式传参"分支
    不回填，于是紧接着的 `_register_from_compile()` 从**空的** `self._products`
    出发 —— `_register_impl` 开头就因 docs 为空而 `return False`，**批量模式下
    产物永远进不了台账**。而且全程静默：`saved` 非空（确实入库了）不报错，
    用户只会觉得"这功能在批量模式下没用"。
    """

    def test_explicit_products_are_remembered(self, tmp_db, qapp):
        from gwtool.ui.compile_wizard import CompileWizard

        wiz = CompileWizard()
        # 造一份真实产物文件，并让入库真的走通（不 mock dao）
        out = tmp_db.parent / "产出一.docx"
        out.write_bytes(b"PK\x03\x04fake")

        wiz._products = []                  # 模拟 _run_batch 刚清空的状态
        saved, _note = wiz._save_products_to_library(
            [(str(out), "产出一")])
        assert saved, "产物应已入库（前提条件不满足，测试无意义）"
        assert wiz._products, (
            "显式传入的产物清单未回填 self._products → "
            "紧接着的 _register_from_compile 会因清单为空而放弃登记")


# ================== 11. 快照被裁剪后预览/回滚不得崩
class TestSnapshotPrunedDoesNotCrash:
    """`dao.get_snapshot` 声明返回 `dict | None`，调用处必须判空。

    为什么是真缺陷：`_fill()` 列出快照后才取详情，而 `dao.add_snapshot` 每次
    都 `_prune_snapshots`（每文档只留 30 条）。编辑器的自动快照定时器在本
    对话框打开期间照常触发，用户点的是"刚被裁掉的那一行"时 `get_snapshot`
    返回 None，`s["content"]` 抛 TypeError、预览与回滚双双中断。
    """

    def test_preview_with_pruned_snapshot_is_graceful(self, tmp_db, qapp):
        from gwtool.ui import feature_dialogs as fd
        from gwtool.db import dao

        doc_id = dao.add_document(dao.Document(
            title="快照源", content_text="第一版正文", category_id=0))
        dao.add_snapshot(doc_id, "快照源", "第一版正文")
        snaps = dao.list_snapshots(doc_id)
        assert snaps, "应有快照"
        gone_id = int(snaps[0]["id"])

        dlg = fd.SnapshotsDialog(doc_id=doc_id, current_text="当前",
                                 apply_callback=lambda t: None)
        dlg._fill()
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QListWidgetItem
        from gwtool.db.connection import get_conn
        # 模拟"列表里还留着这一行，但库里已被裁掉"
        it = QListWidgetItem("已失效快照")
        it.setData(Qt.UserRole, gone_id)
        dlg.list_w.addItem(it)
        get_conn().execute("DELETE FROM snapshots WHERE id=?", (gone_id,))
        get_conn().commit()

        dlg._preview(dlg.list_w.count() - 1)      # 旧实现：TypeError
        dlg.list_w.setCurrentRow(dlg.list_w.count() - 1)
        dlg._restore()                           # 旧实现：TypeError


# ================== 12. 体检未完成/进行中不得导出空报告
class TestInspectExportRequiresResult:
    """`_export_report` 的守卫不能靠 `lbl_stat.text()` 非空。

    为什么是真缺陷：`_run` 一开始写"体检中…"、`_run_failed` 写"体检未完成"，
    两者都非空 —— 于是"体检失败后点导出"会带着空 findings 进后台，产出一份
    **结论为空**的报告，用户看到的是"导出成功"却什么结论都没有。
    """

    def test_export_blocked_when_no_result(self, tmp_db, qapp, monkeypatch):
        from gwtool.ui import feature_dialogs as fd
        from PySide6.QtWidgets import QMessageBox

        warned = []
        monkeypatch.setattr(QMessageBox, "warning",
                            staticmethod(lambda *a, **k: warned.append(a)))
        # 连保存对话框都不该弹到：守卫必须先行拦住
        from PySide6.QtWidgets import QFileDialog
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError("不该弹保存对话框"))))

        dlg = fd.InspectorDialog(editor_text_getter=lambda: "正文")
        assert dlg._has_result is False
        dlg.lbl_stat.setText("体检未完成")        # 失败路径的文案：非空
        dlg._export_report()
        assert warned, "体检失败后导出未被拦住 → 会产出结论为空的报告"

        dlg.lbl_stat.setText("体检中…")           # 进行中的文案：同样非空
        dlg._export_report()
        assert len(warned) == 2


