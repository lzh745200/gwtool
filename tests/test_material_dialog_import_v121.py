# -*- coding: utf-8 -*-
"""v1.21：写作参考库对话框的「导入素材」接线（离屏）。"""
from __future__ import annotations

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


def test_dialog_import_txt_creates_user_pack(tmp_path, qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from gwtool import paths
    from gwtool.core import material_import
    from gwtool.ui import material_dialog as mod

    old = paths.app_data_dir()
    paths.set_app_data_dir(tmp_path / "data")
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(mod, "find_default_packs", lambda root=None: [])
    try:
        src = tmp_path / "讲话素材.txt"
        src.write_text("同志们，要高度重视这项工作。因此，必须抓好落实。",
                       encoding="utf-8")
        dlg = mod.MaterialDialog()
        try:
            dlg._start_import([str(src)])
            assert _pump(qapp, dlg._import_worker), "导入线程未完成"
            dlg._refresh_packs(select=str(material_import.user_pack_dir()))
            dlg._load_selected()
            assert "文档 1 篇" in dlg.lbl_stats.text()
            dlg.ed_search.setText("抓好落实")
            dlg._do_search()
            assert dlg.lst.count() >= 1
            assert "抓好落实" in dlg._current_text()
        finally:
            dlg.close()
    finally:
        paths.set_app_data_dir(old)


def test_dialog_paste_text(tmp_path, qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from gwtool import paths
    from gwtool.core import material_import
    from gwtool.ui import material_dialog as mod

    old = paths.app_data_dir()
    paths.set_app_data_dir(tmp_path / "data2")
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(mod, "find_default_packs", lambda root=None: [])

    class _FakePaste:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            return mod.QDialog.DialogCode.Accepted

        def values(self):
            return "粘贴的讲话", "会议强调，必须抓好贯彻落实。"

    monkeypatch.setattr(mod, "_PasteDialog", _FakePaste)
    try:
        dlg = mod.MaterialDialog()
        try:
            dlg._paste_text()
            dlg._refresh_packs(select=str(material_import.user_pack_dir()))
            dlg._load_selected()
            assert "文档 1 篇" in dlg.lbl_stats.text()
        finally:
            dlg.close()
    finally:
        paths.set_app_data_dir(old)
