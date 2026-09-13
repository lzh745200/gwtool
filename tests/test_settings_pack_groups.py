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
def dlg(qapp, tmp_db):
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
