# -*- coding: utf-8 -*-
"""写作参考库对话框：离屏冒烟（加载/检索/模板/预览/插入回调）。"""
from __future__ import annotations

import json
from pathlib import Path



def _build_pack(root: Path) -> Path:
    pack = root / "pack"
    (pack / "corpus").mkdir(parents=True)
    (pack / "annotations").mkdir()
    (pack / "index.json").write_text(json.dumps([
        {"id": "d1", "title": "论衡·书虚", "source": "classic",
         "work": "论衡", "han_chars": 120},
    ], ensure_ascii=False), encoding="utf-8")
    (pack / "templates.json").write_text(json.dumps([
        {"role": "过渡", "work": "论衡", "pattern": "故{主体}之事 不可不察也",
         "count": 3, "examples": ["故圣人之事，不可不察也。"]},
    ], ensure_ascii=False), encoding="utf-8")
    (pack / "annotations" / "d1.json").write_text(json.dumps({
        "doc": {"id": "d1"},
        "annotated_paragraphs": [{"role": "support", "text": "…", "sentences": [
            {"text": "故圣人之事不可不察也。", "role": "过渡",
             "rule": "transition-led", "conf": 0.85},
            {"text": "夫论者所以辨是非也。", "role": "论点",
             "rule": "claim-classical", "conf": 0.62},
        ]}],
    }, ensure_ascii=False), encoding="utf-8")
    return pack


def test_dialog_search_templates_insert(tmp_path, qapp, monkeypatch):
    from gwtool.ui import material_dialog as mod

    pack_dir = _build_pack(tmp_path)
    monkeypatch.setattr(mod, "find_default_packs", lambda root=None: [pack_dir])
    inserted = []
    dlg = mod.MaterialDialog(insert_cb=inserted.append)
    try:
        assert "文档 1 篇" in dlg.lbl_stats.text()
        assert dlg.lst.count() == 0

        dlg.ed_search.setText("不可不察")
        dlg._do_search()
        assert dlg.lst.count() >= 1
        assert dlg.lst.currentRow() == 0
        assert "不可不察" in dlg._current_text()
        assert "过渡" in dlg.preview.toHtml()

        dlg._insert()
        assert inserted and "不可不察" in inserted[0]

        dlg.cmb_role.setCurrentText("模板")
        dlg._do_search()
        assert dlg.lst.count() == 1
        assert dlg._current_text().startswith("故")
        assert "3 次" in dlg.lst.item(0).text()
    finally:
        dlg.close()


def test_dialog_empty_pack_does_not_crash(tmp_path, qapp, monkeypatch):
    from gwtool.ui import material_dialog as mod

    monkeypatch.setattr(mod, "find_default_packs", lambda root=None: [])
    dlg = mod.MaterialDialog()
    try:
        assert dlg.lst.count() == 0
        dlg.ed_search.setText("x")
        dlg._do_search()      # 无包时静默返回，不抛
    finally:
        dlg.close()
