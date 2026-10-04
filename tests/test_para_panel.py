# -*- coding: utf-8 -*-
"""段落参考面板（L1 UI）的行为测试。

面板是"段落参考"的唯一入口，它的状态机（粒度切换 / 参考清单 / 按钮可用性）
出错会让整条链路静默失效：用户看不到清单、按钮永远是灰的——而那不报错，
只是"点了没反应"。所以这些"看起来只是 UI"的状态必须被测试钉住。
"""
from __future__ import annotations

from gwtool.core.model import PARAGRAPH, Block, DocTree
from gwtool.db import dao
from gwtool.ui.reference_panel import (MODE_DOCUMENT, MODE_PARAGRAPH,
                                       ReferencePanel)


def _seed_doc(title: str, paras: list[str]) -> int:
    tree = DocTree(title=title)
    tree.blocks = [Block(type=PARAGRAPH, text=p) for p in paras]
    content = "\n".join(paras)
    did = dao.add_document(dao.Document(title=title, content_text=content,
                                        blocks_json=tree.to_json()))
    assert did > 0
    return did


def _panel(qapp, tmp_db, text: str = "") -> ReferencePanel:
    return ReferencePanel(lambda: text)


class TestModeSwitch:
    def test_default_mode_is_paragraph(self, qapp, tmp_db):
        p = _panel(qapp, tmp_db)
        assert p.mode() == MODE_PARAGRAPH

    def test_switch_to_document_mode_hides_pick_widgets(self, qapp, tmp_db):
        p = _panel(qapp, tmp_db)
        idx = p.cmb_mode.findData(MODE_DOCUMENT)
        p.cmb_mode.setCurrentIndex(idx)
        assert p.mode() == MODE_DOCUMENT
        # 用 isVisibleTo(parent)：顶层没 show() 时 isVisible() 恒为 False，
        # 那种断言无论实现对错都会通过（假绿）。
        assert not p.pick_list.isVisibleTo(p)
        assert not p.btn_pick.isVisibleTo(p)

    def test_switch_back_shows_pick_widgets(self, qapp, tmp_db):
        p = _panel(qapp, tmp_db)
        p.cmb_mode.setCurrentIndex(p.cmb_mode.findData(MODE_DOCUMENT))
        p.cmb_mode.setCurrentIndex(p.cmb_mode.findData(MODE_PARAGRAPH))
        assert p.mode() == MODE_PARAGRAPH
        assert p.btn_pick.isVisibleTo(p)

    def test_changing_mode_clears_previous_results_and_picks(self, qapp, tmp_db):
        """两套结果的 id 语义不同（段落 id vs 文档 id），混在一起会让
        "插入"取到错误的实体 —— 换粒度必须清空。"""
        _seed_doc("某通知", ["安全生产责任要落实。"])
        p = _panel(qapp, tmp_db)
        p.ref_input.setText("安全生产")
        p.run_reference()
        assert p.ref_list.count() > 0
        p.ref_list.setCurrentRow(0)
        p._add_picks()
        assert p.picks()

        p.cmb_mode.setCurrentIndex(p.cmb_mode.findData(MODE_DOCUMENT))
        assert p.picks() == []
        assert p.ref_list.count() == 0


class TestPickList:
    def test_add_pick_from_result(self, qapp, tmp_db):
        _seed_doc("某通知", ["安全生产责任要落实到每个岗位。"])
        p = _panel(qapp, tmp_db)
        p.ref_input.setText("安全生产")
        p.run_reference()
        p.ref_list.setCurrentRow(0)
        p._add_picks()
        assert len(p.picks()) == 1
        assert p.pick_list.count() == 1
        assert "1 条" in p.lbl_picks.text()

    def test_same_paragraph_added_twice_is_deduped(self, qapp, tmp_db):
        _seed_doc("某通知", ["安全生产责任要落实到每个岗位。"])
        p = _panel(qapp, tmp_db)
        p.ref_input.setText("安全生产")
        p.run_reference()
        p.ref_list.setCurrentRow(0)
        p._add_picks()
        p.ref_list.setCurrentRow(0)
        p._add_picks()
        assert len(p.picks()) == 1

    def test_clear_picks(self, qapp, tmp_db):
        _seed_doc("某通知", ["安全生产责任要落实。"])
        p = _panel(qapp, tmp_db)
        p.ref_input.setText("安全生产")
        p.run_reference()
        p.ref_list.setCurrentRow(0)
        p._add_picks()
        p._clear_picks()
        assert p.picks() == []
        assert p.pick_list.count() == 0

    def test_remove_selected_pick(self, qapp, tmp_db):
        _seed_doc("某通知", ["安全生产第一段内容。", "安全生产第二段内容。"])
        p = _panel(qapp, tmp_db)
        p.ref_input.setText("安全生产")
        p.run_reference()
        for i in range(p.ref_list.count()):
            p.ref_list.setCurrentRow(i)
            p._add_picks()
        assert len(p.picks()) == 2
        p.pick_list.setCurrentRow(0)
        p._remove_picks()
        assert len(p.picks()) == 1


class TestActionEnablement:
    def test_buttons_disabled_when_no_picks(self, qapp, tmp_db):
        p = _panel(qapp, tmp_db)
        assert not p.btn_align.isEnabled()
        assert not p.btn_generate.isEnabled()
        assert not p.btn_unpick.isEnabled()
        assert not p.btn_clear_picks.isEnabled()

    def test_buttons_enabled_after_pick(self, qapp, tmp_db):
        _seed_doc("某通知", ["安全生产责任要落实。"])
        p = _panel(qapp, tmp_db)
        p.ref_input.setText("安全生产")
        p.run_reference()
        p.ref_list.setCurrentRow(0)
        p._add_picks()
        assert p.btn_align.isEnabled()
        assert p.btn_generate.isEnabled()
        assert p.btn_clear_picks.isEnabled()

    def test_pick_button_needs_selection(self, qapp, tmp_db):
        _seed_doc("某通知", ["安全生产责任要落实。"])
        p = _panel(qapp, tmp_db)
        p.ref_input.setText("安全生产")
        p.run_reference()
        p.ref_list.clearSelection()
        p._update_ref_actions()
        assert not p.btn_pick.isEnabled()

    def test_switching_off_para_ref_hides_paragraph_controls(self, qapp, tmp_db):
        """关掉开关后必须回到"改动前的那条路径"：只剩整篇。"""
        from gwtool import config
        config.set_para_ref_enabled(False)
        try:
            p = _panel(qapp, tmp_db)
            assert p.mode() == MODE_DOCUMENT
            assert not p.cmb_mode.isVisibleTo(p)
            assert not p.btn_align.isVisibleTo(p)
            assert not p.btn_generate.isVisibleTo(p)
        finally:
            config.set_para_ref_enabled(True)

    def test_align_and_gen_switches_are_independent(self, qapp, tmp_db):
        from gwtool import config
        config.set_para_align_enabled(False)
        try:
            p = _panel(qapp, tmp_db)
            assert not p.btn_align.isVisibleTo(p)
            assert p.btn_generate.isVisibleTo(p)
        finally:
            config.set_para_align_enabled(True)

    def test_gen_switch_off_hides_only_generate(self, qapp, tmp_db):
        from gwtool import config
        config.set_para_gen_enabled(False)
        try:
            p = _panel(qapp, tmp_db)
            assert p.btn_align.isVisibleTo(p)
            assert not p.btn_generate.isVisibleTo(p)
        finally:
            config.set_para_gen_enabled(True)


class TestInsertAndJump:
    def test_insert_paragraph_emits_text(self, qapp, tmp_db):
        _seed_doc("某通知", ["安全生产责任要落实。"])
        p = _panel(qapp, tmp_db)
        got: list[str] = []
        p.insert_text.connect(got.append)
        p.ref_input.setText("安全生产")
        p.run_reference()
        p.ref_list.setCurrentRow(0)
        p._insert_ref()
        assert got and "安全生产" in got[0]

    def test_open_source_emits_the_ref(self, qapp, tmp_db):
        _seed_doc("某通知", ["安全生产责任要落实。"])
        p = _panel(qapp, tmp_db)
        got: list = []
        p.open_source.connect(got.append)
        p.ref_input.setText("安全生产")
        p.run_reference()
        p.ref_list.setCurrentRow(0)
        p._add_picks()
        p.pick_list.setCurrentRow(0)
        p._open_pick_source()
        assert got and got[0].text == "安全生产责任要落实。"

    def test_align_signal_carries_picks(self, qapp, tmp_db):
        _seed_doc("某通知", ["安全生产责任要落实。"])
        p = _panel(qapp, tmp_db)
        got: list = []
        p.align_requested.connect(got.append)
        p.ref_input.setText("安全生产")
        p.run_reference()
        p.ref_list.setCurrentRow(0)
        p._add_picks()
        p._request_align()
        assert got and len(got[0]) == 1

    def test_generate_signal_carries_picks(self, qapp, tmp_db):
        _seed_doc("某通知", ["安全生产责任要落实。"])
        p = _panel(qapp, tmp_db)
        got: list = []
        p.generate_requested.connect(got.append)
        p.ref_input.setText("安全生产")
        p.run_reference()
        p.ref_list.setCurrentRow(0)
        p._add_picks()
        p._request_generate()
        assert got and len(got[0]) == 1

    def test_no_signal_when_picks_empty(self, qapp, tmp_db):
        p = _panel(qapp, tmp_db)
        got: list = []
        p.align_requested.connect(got.append)
        p.generate_requested.connect(got.append)
        p._request_align()
        p._request_generate()
        assert got == []

    def test_save_as_phrase_from_paragraph(self, qapp, tmp_db):
        _seed_doc("某通知", ["安全生产责任要落实到每个岗位。"])
        p = _panel(qapp, tmp_db)
        p.ref_input.setText("安全生产")
        p.run_reference()
        p.ref_list.setCurrentRow(0)
        p._save_as_phrase()
        phrases = dao.list_phrases()
        assert phrases and "安全生产" in phrases[0].phrase

    def test_empty_query_clears_results(self, qapp, tmp_db):
        _seed_doc("某通知", ["安全生产责任要落实。"])
        p = _panel(qapp, tmp_db)
        p.ref_input.setText("安全生产")
        p.run_reference()
        assert p.ref_list.count() > 0
        p.ref_input.setText("   ")
        p.run_reference()
        assert p.ref_list.count() == 0
