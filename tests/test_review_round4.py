# -*- coding: utf-8 -*-
"""第四轮代码审查（delegate 模式）修复项的回归测试。

每条断言都先在**修复前**的实现上确认过失败 —— 项目纪律：恒过的断言等于没写。

覆盖：
  R1 段落检索排序：FTS5 bm25 为负值且越负越相关，词频更高的段落必须排前
  R2 序号重复不得引发"假跳号"（「一、二、二、三」里的「三」不是跳号）
  R3 普通句不得被抽成 {org} 单位名槽位
  R4 彻底删除文档要级联清掉段落派生表（paragraphs / 段落 FTS / 索引状态）
  R5 超长号段不得把"已用最大序号"顶成天文数字
  R6 骨架文种下拉框必须带 userData（`addItems` 不设 data）
  R7 模板重建槽位时，参考原值（example）不得丢失
  R8 `skeleton_from_template` 保留示例，供"载入骨架"回填
  R9 段落参考菜单入口受三个能力开关控制
  R10 批量替换：预览期间条件变动 → 预览作废，不得按新条件改旧清单
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from gwtool.core import paragraph_ref as pr
from gwtool.core.model import PARAGRAPH, Block, DocTree
from gwtool.db import dao


def _add(title: str, paras: list[str]) -> int:
    tree = DocTree(title=title)
    tree.blocks = [Block(type=PARAGRAPH, text=p) for p in paras]
    content = "\n".join(paras)
    did = dao.add_document(dao.Document(title=title, content_text=content,
                                        blocks_json=tree.to_json()))
    assert did > 0
    return did


# ---------------------------------------------------------------- R1 检索排序
class TestRetrievalOrder:
    def test_denser_hit_ranks_first(self, tmp_db):
        """词频更高的段落必须排在前面。

        修复前实测：3 次词频的段 score=0.55 排第二，1 次词频的段
        score=1.00 排第一 —— 归一化写成了 1.0-(abs-lo)/span，而 FTS5
        的 bm25 是负值、越负越相关，等于把最相关的压到了末位。
        """
        _add("关于开展安全生产检查的通知",
             ["安全生产 安全生产 安全生产 工作要点。", "只提一次安全生产的段落。"])
        res, _relaxed = pr.search_paragraphs_ex("安全生产")
        assert len(res) >= 2, "至少应命中两段"
        counts = [r.text.count("安全生产") for r in res]
        assert counts[0] == max(counts), f"最相关段落未排首位：{counts}"


def _ref(text: str) -> pr.ParagraphRef:
    """构造一条最小可用的参考段落（序号检查只看草稿，refs 仅用于触发对齐）。"""
    return pr.ParagraphRef("library", 1, 1, "参考材料", 0, "heading", 1,
                           text, 0, "h")


# ---------------------------------------------------------------- R2 序号判定
class TestOrdinalIssues:
    def test_duplicate_ordinal_does_not_fake_skip(self):
        """「一、二、二、三」只该报第 3 段重复，不得再报「三」跳号。

        修复前期望值用 enumerate 下标（计入被跳过的重复项），于是后续每一项
        都被判成跳号；用户照提示把「三」改成「四」，反而把正确的序号改坏。
        """
        text = "一、总体要求\n二、主要任务\n二、主要任务\n三、保障措施\n"
        out = pr.align_to_draft(text, [_ref("一、总体要求")])
        hints = [a.hint for a in out if "序号" in (a.hint or "")]
        assert any("重复" in h for h in hints), hints
        assert not any("跳到" in h for h in hints), hints

    def test_real_skip_is_still_reported(self):
        """真跳号仍要报 —— 别把修复做成"永远不报"。"""
        text = "一、总体要求\n三、保障措施\n"
        out = pr.align_to_draft(text, [_ref("一、总体要求")])
        hints = [a.hint for a in out if "序号" in (a.hint or "")]
        assert any("跳到" in h for h in hints), hints


# ---------------------------------------------------------------- R3 槽位误抽
class TestOrgSlotFalsePositive:
    def test_plain_sentence_has_no_org_slot(self):
        """普通句不得被抽成单位名槽位。

        修复前实测：'本次工作全部完成。' → {org}='本次工作全部'、
        '到处都要注意安全。' → {org}='到处'、'好处很多。' → {org}='好处'
        —— 单字后缀（局/部/厅/委/办/处/科）命中大量常用词。
        """
        for s in ("本次工作全部完成。", "到处都要注意安全。", "好处很多。",
                  "各种干部的培养很重要。"):
            got = [x.name for x in pr.extract_slots(s) if x.name == "org"]
            assert not got, f"{s!r} 被误抽成 {got}"

    def test_real_org_still_extracted(self):
        """真单位名仍要抽到（别把修复做成"永不抽 org"）。"""
        slots = pr.extract_slots("××市教育局关于安全生产的通知。")
        assert any(x.name == "org" for x in slots), slots


# ---------------------------------------------------------------- R4 级联删除
class TestPurgeCascades:
    def test_purge_document_clears_paragraph_tables(self, tmp_db):
        """彻底删除必须级联清段落派生表。

        修复前实测：purge 后 paragraphs / paragraphs_fts / fts_index_state
        全部残留，只有下一次 rebuild 才会顺带回收。
        """
        from gwtool.db import connection

        did = _add("待彻底删除的通知", ["第一段内容。", "第二段内容。"])
        dao.rebuild_paragraphs()
        conn = connection.get_conn()
        assert conn.execute("SELECT COUNT(*) c FROM paragraphs WHERE doc_id=?",
                            (did,)).fetchone()["c"] > 0
        dao.purge_document(did)
        assert conn.execute("SELECT COUNT(*) c FROM paragraphs WHERE doc_id=?",
                            (did,)).fetchone()["c"] == 0
        assert conn.execute(
            "SELECT COUNT(*) c FROM fts_index_state WHERE kind='paragraphs'"
            " AND ref_id=?", (did,)).fetchone()["c"] == 0


# ---------------------------------------------------------------- R5 号段长度
class TestSerialLengthGuard:
    @pytest.fixture
    def conn(self, tmp_db):
        from gwtool.db.connection import get_conn
        return get_conn()

    def test_absurd_long_serial_is_ignored(self, conn):
        """超长号段不得把"已用最大序号"顶成天文数字。

        修复前实测：30 个 9 会被 int() 成功（`\\d+` 永不抛 ValueError，
        那里的 try/except 不可达），自动取号随即给出 10^30 量级的号。
        """
        conn.execute("INSERT INTO dispatch_register(doc_no,title,sign_date)"
                     " VALUES(?,?,?)", ("×政办发〔2025〕9号", "正常件", ""))
        conn.execute("INSERT INTO dispatch_register(doc_no,title,sign_date)"
                     " VALUES(?,?,?)",
                     ("×政办发〔2025〕" + "9" * 30 + "号", "脏数据", ""))
        conn.commit()
        assert dao.max_doc_no_serial("×政办发", "2025") == 9


# ------------------------------------------------- R6/R7/R8 骨架文种与示例
class TestSkeletonKindAndExamples:
    def test_kind_combo_carries_value(self, qapp, tmp_db):
        """文种下拉必须带 userData。

        修复前实测：`addItems` 只填文本、不设 userData，itemData(i) is None
        —— 用户明明选了文种，currentData() 仍返回 None，派生与落库的
        kind 静默变空串。
        """
        from gwtool.ui.feature_dialogs import SkeletonFromRefsDialog

        _add("××市教育局关于安全生产的通知", ["××市教育局关于安全生产的通知"])
        refs, _ = pr.search_paragraphs_ex("安全生产")
        dlg = SkeletonFromRefsDialog(refs)
        try:
            assert dlg.cmb_kind.count() > 1, "文种下拉应有可选项"
            for i in range(1, dlg.cmb_kind.count()):
                assert dlg.cmb_kind.itemData(i), f"第 {i} 项文种没有 userData"
            dlg.cmb_kind.setCurrentIndex(1)
            assert dlg.cmb_kind.currentData()
        finally:
            dlg.deleteLater()

    def test_slot_placeholder_keeps_reference_value(self, qapp, tmp_db):
        """改模板后重建槽位表单，参考原值必须还在（预填 / placeholder）。"""
        from gwtool.ui.feature_dialogs import SkeletonFromRefsDialog

        _add("××市教育局关于安全生产的通知", ["××市教育局关于安全生产的通知"])
        refs, _ = pr.search_paragraphs_ex("安全生产")
        dlg = SkeletonFromRefsDialog(refs)
        try:
            ph = [e.placeholderText() for e in dlg._slot_edits.values()]
            assert any("××市教育局" in p for p in ph), ph
            dlg._rebuild_slots()                # 模拟"改了模板再重建"
            ph2 = [e.placeholderText() for e in dlg._slot_edits.values()]
            assert any("××市教育局" in p for p in ph2), ph2
        finally:
            dlg.deleteLater()

    def test_skeleton_from_template_keeps_examples(self):
        """文本重建骨架时，示例应能按槽位名带回来。"""
        sk = pr.skeleton_from_template("{org}关于{matter}的通知",
                                       examples={"org": "××市教育局"})
        got = {s.name: s.example for s in sk.slots}
        assert got.get("org") == "××市教育局"
        assert got.get("matter") == ""

    def test_load_saved_restores_kind(self, qapp, tmp_db, monkeypatch):
        """载入已存骨架必须回填文种，否则"载入后微调再保存"会把 kind 清空。"""
        from gwtool.ui import feature_dialogs as fd

        monkeypatch.setattr(fd, "info", lambda *a, **k: None)
        monkeypatch.setattr(fd, "warn", lambda *a, **k: None)

        _add("××市教育局关于安全生产的通知", ["××市教育局关于安全生产的通知"])
        refs, _ = pr.search_paragraphs_ex("安全生产")
        dlg = fd.SkeletonFromRefsDialog(refs)
        try:
            dlg.cmb_kind.setCurrentIndex(1)
            kind = dlg.cmb_kind.currentData()
            assert kind, "文种必须带值（否则本用例无意义）"
            dlg.ed_name.setText("测试骨架")
            dlg._save()
            saved = dao.list_user_skeletons()
            assert saved and saved[0].kind == kind

            dlg.cmb_kind.setCurrentIndex(0)     # 界面回到「不限」
            dlg.cmb_saved.setCurrentIndex(0)
            dlg._load_saved()
            assert dlg.cmb_kind.currentData() == kind, "载入未回填文种"
        finally:
            dlg.deleteLater()


# ------------------------------------------------- R9 能力开关控制菜单入口
class TestParaFeatureSwitches:
    def test_menu_entries_follow_switches(self, qapp, tmp_db, monkeypatch):
        """关掉能力开关后，菜单入口必须一起隐藏（规格 §9.2 / 约束 C16）。

        修复前：开关只作用于右侧面板，菜单里的「内容对齐」「从参考段落生成
        草稿」始终可点 —— 关掉 para_ref_enabled 后段落清单已无从产生，
        点开只剩一句死路提示。
        """
        from gwtool import config
        from gwtool.ui import main_window as mw

        monkeypatch.setattr(config, "para_align_enabled", lambda: False)
        monkeypatch.setattr(config, "para_gen_enabled", lambda: False)
        w = mw.MainWindow()
        try:
            assert not w.act_align.isVisible()
            assert not w.act_generate.isVisible()
        finally:
            w.close()
            w.deleteLater()

    def test_menu_entries_visible_when_enabled(self, qapp, tmp_db, monkeypatch):
        """开关打开时入口可见 —— 防止"一律隐藏"式的假修复。"""
        from gwtool.ui import main_window as mw

        w = mw.MainWindow()
        try:
            assert w.act_align.isVisible()
            assert w.act_generate.isVisible()
        finally:
            w.close()
            w.deleteLater()


# ------------------------------------------------------ R10 预览条件快照
class TestReplacePreviewSnapshot:
    def test_preview_dropped_when_condition_changed(self, qapp, tmp_db, monkeypatch):
        """预览期间条件变动 → 本次预览作废。

        修复前：完成回调里读的是**此刻**的控件值，于是"用旧条件扫出的文档
        清单"会被配上"新条件"交给执行（预览所见 ≠ 实际所改）。
        """
        from gwtool.ui import feature_dialogs as fd

        monkeypatch.setattr(fd, "info", lambda *a, **k: None)
        monkeypatch.setattr(fd, "warn", lambda *a, **k: None)

        dlg = fd.BulkReplaceDialog(None)
        try:
            dlg.ed_find.setText("安全生产")
            dlg._preview_key = dlg._current_key()      # 模拟"预览已启动"
            dlg.ed_find.setText("安全生产责任")        # 期间用户改了条件
            dlg._on_preview_done(([(1, "某材料", 2, "…上下文…")], 2, None))
            assert dlg._plan_key is None, "条件漂移后不得留下可执行的计划"
            assert dlg.preview_list.count() == 0, "作废的预览不得把清单留在界面上"
        finally:
            dlg.deleteLater()
