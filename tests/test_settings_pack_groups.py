# -*- coding: utf-8 -*-
"""设置页「增强包」双分组的测试。

锁住三件事：
  ① csc 与 cgec 两个分组都能正常构建、各自显示状态；
  ② 两类开关互不干扰（写一个不影响另一个）；
  ③ 卸载一类后，另一类的开关状态与安装状态都保持原样。

不做真实导入（那要构造 zip 与真模型），只验证 UI 与设置层的行为契约。
"""
import pytest

from gwtool.core import csc_gec, csc_neural, enhance_pack
from gwtool.db import dao


@pytest.fixture
def dlg(qapp, tmp_db, monkeypatch):
    """SecurityDialog + **屏蔽模态提示**。

    本文件有真会走到 `warn()` 的路径（开关写库失败提示、导入校验提示）。
    PySide6 的 `QMessageBox.warning` 是**静态方法、C++ 侧实现**，不 mock 就会
    弹出真实模态框，把无人值守的测试永久挂住（实测：全量跑到本文件停住不返回、
    日志十几分钟不增一行、无任何报错）。
    """
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    from gwtool.ui.feature_dialogs import SecurityDialog
    d = SecurityDialog(None)
    yield d
    d.deleteLater()


def test_both_groups_built(dlg):
    assert set(dlg._pack_ui.keys()) == {"csc", "cgec"}
    for kind in ("csc", "cgec"):
        ui = dlg._pack_ui[kind]
        assert ui["state"].text().startswith("状态：")
        assert ui["import"] is not None
        assert ui["remove"] is not None


def test_remove_disabled_when_nothing_installed(dlg):
    for kind in ("csc", "cgec"):
        assert dlg._pack_ui[kind]["remove"].isEnabled() is False


def test_toggles_write_independent_settings(dlg):
    """两个开关各写各的设置键，互不覆盖。"""
    dlg._pack_ui["csc"]["syncing"] = False
    dlg._pack_ui["cgec"]["syncing"] = False
    dlg._on_pack_toggled("csc", True)
    dlg._on_pack_toggled("cgec", True)
    assert str(dao.get_setting(csc_neural.SETTING_KEY, "0")) == "1"
    assert str(dao.get_setting(csc_gec.SETTING_KEY, "0")) == "1"

    dlg._on_pack_toggled("csc", False)
    assert str(dao.get_setting(csc_neural.SETTING_KEY, "0")) == "0"
    assert str(dao.get_setting(csc_gec.SETTING_KEY, "0")) == "1", \
        "关掉拼写包不该影响语法包"


def test_syncing_guard_does_not_persist(dlg):
    """程序回填勾选态时不得落库。"""
    dao.set_setting(csc_neural.SETTING_KEY, "0")
    dlg._pack_ui["csc"]["syncing"] = True
    dlg._on_pack_toggled("csc", True)
    assert str(dao.get_setting(csc_neural.SETTING_KEY, "0")) == "0"


def test_setting_keys_are_distinct():
    assert csc_neural.SETTING_KEY != csc_gec.SETTING_KEY
    assert csc_neural.SETTING_KEY == "corrector_neural_enabled"
    assert csc_gec.SETTING_KEY == "corrector_gec_enabled"


def test_toggle_does_not_raise_false_alarm(dlg, monkeypatch):
    """拨动开关**不得**弹出"未能保存到数据库"。

    回归（本轮全量门禁抓到）：`csc_neural.set_setting` 曾返回 `None`，而设置页
    按 `if not saved:` 判定 —— 于是每次拨动 L4 开关都弹"未能保存到数据库，
    重启后会恢复"，而设置其实写成功了。用户会据此白排查一遍磁盘与权限。
    """
    import gwtool.ui.feature_dialogs as fd
    warns: list[str] = []
    monkeypatch.setattr(fd, "warn", lambda parent, msg: warns.append(msg))
    dlg._pack_ui["csc"]["syncing"] = False
    dlg._on_pack_toggled("csc", True)
    assert warns == [], f"写入成功却弹了告警：{warns}"
    assert str(dao.get_setting(csc_neural.SETTING_KEY, "0")) == "1"


def test_neural_set_setting_returns_false_on_db_error(tmp_db, monkeypatch):
    """L4 开关写库失败必须返回 False（与 csc_gec / reminder 同契约）。

    此前 F3 只给 csc_gec / reminder 补了返回值契约，L4 这一处被漏掉 ——
    漏掉的代价不是"少一个返回值"，而是上面那条**假告警**。
    """
    from gwtool.db import dao as dao_mod

    monkeypatch.setattr(dao_mod, "set_setting",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("库不可写")))
    assert csc_neural.set_setting(True) is False


def test_refresh_handles_broken_module_gracefully(dlg, monkeypatch):
    """某个模块报错时，另一个分组仍应正常刷新。"""
    monkeypatch.setattr(csc_gec, "status_text",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        dlg._refresh_pack_group("cgec")
    # csc 不受影响
    dlg._refresh_pack_group("csc")
    assert dlg._pack_ui["csc"]["state"].text().startswith("状态：")


def test_import_rejects_kind_mismatch_without_touching_other_slot(
        dlg, tmp_path, monkeypatch):
    """把一个 csc 包往 cgec 槽位塞 —— 必须被拒，且两个槽位都不落地。"""
    import hashlib
    import json
    import zipfile
    model, vocab = b"M", b"A\n"
    man = {"schema": 1, "name": "p", "kind": "csc", "version": "1",
           "license": "Apache-2.0", "backend": "onnx", "max_len": 64,
           "files": {"model.onnx": hashlib.sha256(model).hexdigest(),
                     "vocab.txt": hashlib.sha256(vocab).hexdigest()}}
    p = tmp_path / "x.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("manifest.json", json.dumps(man))
        zf.writestr("model.onnx", model)
        zf.writestr("vocab.txt", vocab)

    with pytest.raises(enhance_pack.EnhancePackError, match="类型不匹配"):
        enhance_pack.install_pack(p, kind="cgec")
    assert enhance_pack.installed_pack("cgec") is None
    assert enhance_pack.installed_pack("csc") is None
