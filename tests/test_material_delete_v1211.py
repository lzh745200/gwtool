# -*- coding: utf-8 -*-
"""v1.21.1：「我的素材」删除链路（material_import.remove_doc 接入对话框）。

背景：v1.21 的任意素材导入只做了「进」，没做「出」——用户误导入一篇后
在界面里删不掉（remove_doc 全仓零引用）。这里把删除接到对话框，并锁住
两条底线：只对可写的「我的素材」包启用；删除后索引与列表同步收敛。
"""
from __future__ import annotations

import json
import time


def _pump(qapp, worker, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        if worker is None or worker.isFinished():
            qapp.processEvents()
            return True
        time.sleep(0.03)
    return False


def _prepare(tmp_path, monkeypatch):
    """建好「我的素材」并导入一篇，返回 (dlg, material_import, paths, old)。"""
    from PySide6.QtWidgets import QMessageBox

    from gwtool import paths
    from gwtool.core import material_import
    from gwtool.ui import material_dialog as mod

    old = paths.app_data_dir()
    paths.set_app_data_dir(tmp_path / "data")
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(mod, "find_default_packs", lambda root=None: [])

    src = tmp_path / "讲话素材.txt"
    src.write_text("因此，必须抓好落实。\n", encoding="utf-8")
    dlg = mod.MaterialDialog()
    dlg._start_import([str(src)])
    return dlg, material_import, paths, old


def _search_user_pack(dlg, material_import, keyword="抓好落实"):
    dlg._refresh_packs(select=str(material_import.user_pack_dir()))
    dlg._load_selected()
    dlg.ed_search.setText(keyword)
    dlg._do_search()


def test_delete_removes_imported_material(tmp_path, qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    dlg, material_import, paths, old = _prepare(tmp_path, monkeypatch)
    try:
        assert _pump(qapp, dlg._import_worker), "导入线程未结束"
        _search_user_pack(dlg, material_import)
        assert dlg.lst.count() >= 1
        assert dlg.btn_delete.isEnabled(), "「我的素材」的命中应允许删除"
        monkeypatch.setattr(
            QMessageBox, "question",
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
        dlg._delete_current()
        pack = material_import.user_pack_dir()
        index = json.loads((pack / "index.json").read_text(encoding="utf-8"))
        assert index == [], "删除后索引里该篇必须消失"
        assert dlg.lst.count() == 0, "删除后检索列表应同步收敛"
        assert not dlg.btn_delete.isEnabled()
    finally:
        dlg.close()
        paths.set_app_data_dir(old)


def test_delete_disabled_for_readonly_pack(tmp_path, qapp, monkeypatch):
    dlg, material_import, paths, old = _prepare(tmp_path, monkeypatch)
    try:
        assert _pump(qapp, dlg._import_worker), "导入线程未结束"
        _search_user_pack(dlg, material_import)
        assert dlg.lst.count() >= 1
        # 把「我的素材」目录指到别处：当前包即被判定为只读，必须禁用删除
        monkeypatch.setattr(material_import, "user_pack_dir",
                            lambda: tmp_path / "另一个目录")
        dlg._preview(0)
        assert not dlg.btn_delete.isEnabled()
        dlg._delete_current()          # 不得抛错、不得改动索引
        index_path = paths.app_data_dir() / "material_packs" / "我的素材" / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        assert len(index) == 1, "只读包上的删除请求必须被拒绝"
    finally:
        dlg.close()
        paths.set_app_data_dir(old)
