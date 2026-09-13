# -*- coding: utf-8 -*-
"""增强包「按 kind 分槽」的测试。

背景
----
改造前 `current_dir()` 全局唯一，`install_pack()` 会 `rmtree` 掉整个 current ——
装了语法包就把拼写包冲掉，两类包物理无法共存。本模块锁住改造后的契约：

  ① 两类包可以同时安装、互不干扰；
  ② 卸载一类不影响另一类；
  ③ 同类重复安装仍是「替换」语义（保留原有原子性）；
  ④ 历史布局（文件直接躺在 current/ 下）能自动迁移到 current/csc；
  ⑤ 清单 kind 与安装槽位不一致时拒绝（防串槽）。

沿用 test_corrector_l4 的做法：**不依赖 numpy、不依赖真实模型**。
"""
import hashlib
import json
import zipfile

import pytest

from gwtool import paths
from gwtool.core import enhance_pack


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _make_pack(path, *, name="p", kind="csc", lic="Apache-2.0", schema=1,
               backend="onnx", model=b"MODEL", vocab=b"A\nB\n",
               tokenizer=b'{"version":"1.0"}'):
    """构造一个结构合法的增强包（sha256 与内容一致）。

    必填文件随 kind 变化：csc 要 vocab.txt，cgec 要 tokenizer.json。
    """
    files = {"model.onnx": _sha(model)}
    if kind == "cgec":
        files["tokenizer.json"] = _sha(tokenizer)
    else:
        files["vocab.txt"] = _sha(vocab)
    man = {"schema": schema, "name": name, "kind": kind, "version": "1.0.0",
           "license": lic, "source": "https://example.invalid",
           "backend": backend, "max_len": 64, "files": files}
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(man, ensure_ascii=False))
        zf.writestr("model.onnx", model)
        if kind == "cgec":
            zf.writestr("tokenizer.json", tokenizer)
        else:
            zf.writestr("vocab.txt", vocab)
    return path


# ------------------------------------------------------- 槽位路径
def test_slot_dirs_are_distinct(tmp_db):
    csc = enhance_pack.current_dir("csc")
    cgec = enhance_pack.current_dir("cgec")
    assert csc != cgec
    assert csc.name == "csc" and cgec.name == "cgec"
    assert csc.parent == cgec.parent


def test_default_kind_is_csc(tmp_db):
    """不传 kind 时维持历史语义：默认落 csc 槽位。"""
    assert enhance_pack.current_dir() == enhance_pack.current_dir("csc")


def test_unknown_kind_falls_back_to_csc(tmp_db):
    assert enhance_pack.normalize_kind("BART") == "csc"
    assert enhance_pack.normalize_kind("") == "csc"
    assert enhance_pack.normalize_kind(None) == "csc"
    assert enhance_pack.normalize_kind("cgec") == "cgec"


# ------------------------------------------------------- 共存
def test_two_kinds_coexist(tmp_db, tmp_path):
    """核心回归：装完 cgec 不能把 csc 冲掉。"""
    csc_zip = _make_pack(tmp_path / "csc.zip", name="拼写包", kind="csc")
    gec_zip = _make_pack(tmp_path / "gec.zip", name="语法包", kind="cgec",
                         model=b"GECMODEL", vocab=b"C\nD\n")

    enhance_pack.install_pack(csc_zip)
    enhance_pack.install_pack(gec_zip)

    a = enhance_pack.installed_pack("csc")
    b = enhance_pack.installed_pack("cgec")
    assert a is not None and a.name == "拼写包"
    assert b is not None and b.name == "语法包"
    assert enhance_pack.verify_installed("csc")
    assert enhance_pack.verify_installed("cgec")
    assert a.kind == "csc" and b.kind == "cgec"

    # 物理上也确实分开
    base = paths.enhance_dir() / "current"
    assert (base / "csc" / "model.onnx").exists()
    assert (base / "cgec" / "model.onnx").exists()
    assert (base / "csc" / "vocab.txt").exists()
    assert (base / "cgec" / "tokenizer.json").exists()
    assert (base / "csc" / "model.onnx").read_bytes() == b"MODEL"
    assert (base / "cgec" / "model.onnx").read_bytes() == b"GECMODEL"


def test_remove_one_kind_keeps_the_other(tmp_db, tmp_path):
    enhance_pack.install_pack(_make_pack(tmp_path / "c.zip", kind="csc"))
    enhance_pack.install_pack(_make_pack(tmp_path / "g.zip", kind="cgec"))

    assert enhance_pack.remove_pack("csc") is True
    assert enhance_pack.installed_pack("csc") is None
    assert enhance_pack.installed_pack("cgec") is not None
    assert enhance_pack.verify_installed("cgec")

    assert enhance_pack.remove_pack("csc") is False      # 已删过
    assert enhance_pack.remove_pack("cgec") is True
    assert enhance_pack.installed_pack("cgec") is None


def test_same_kind_reinstall_replaces(tmp_db, tmp_path):
    """同类重复安装仍是替换语义，且不留残渣。"""
    p1 = _make_pack(tmp_path / "v1.zip", name="旧版", kind="csc")
    p2 = _make_pack(tmp_path / "v2.zip", name="新版", kind="csc",
                    model=b"NEWMODEL")
    enhance_pack.install_pack(p1)
    enhance_pack.install_pack(p2)

    info = enhance_pack.installed_pack("csc")
    assert info is not None and info.name == "新版"
    assert (enhance_pack.current_dir("csc") / "model.onnx").read_bytes() == b"NEWMODEL"


# ------------------------------------------------------- kind 一致性
def test_kind_mismatch_is_rejected(tmp_db, tmp_path):
    """清单声明 csc，却要塞进 cgec 槽位 —— 拒绝，防止串槽。"""
    p = _make_pack(tmp_path / "csc.zip", kind="csc")
    with pytest.raises(enhance_pack.EnhancePackError, match="类型不匹配"):
        enhance_pack.install_pack(p, kind="cgec")
    assert enhance_pack.installed_pack("cgec") is None


def test_explicit_kind_must_match_manifest(tmp_db, tmp_path):
    p = _make_pack(tmp_path / "g.zip", kind="cgec")
    info = enhance_pack.install_pack(p, kind="cgec")
    assert info.kind == "cgec"
    assert enhance_pack.installed_pack("cgec") is not None


# ------------------------------------------------------- 旧布局迁移
def test_migrate_legacy_layout(tmp_db, tmp_path):
    """历史布局：文件直接躺在 current/ 下（无子目录）→ 自动迁到 current/csc。"""
    base = paths.enhance_dir() / "current"
    base.mkdir(parents=True, exist_ok=True)
    man = {"schema": 1, "name": "老包", "kind": "csc", "version": "0.9",
           "license": "Apache-2.0", "backend": "onnx", "max_len": 64,
           "files": {"model.onnx": _sha(b"OLD"), "vocab.txt": _sha(b"X\n")}}
    (base / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    (base / "model.onnx").write_bytes(b"OLD")
    (base / "vocab.txt").write_bytes(b"X\n")

    enhance_pack.migrate_layout()

    assert not (base / "manifest.json").exists(), "旧位置的文件应已挪走"
    assert (base / "csc" / "manifest.json").exists()
    assert (base / "csc" / "model.onnx").read_bytes() == b"OLD"
    info = enhance_pack.installed_pack("csc")
    assert info is not None and info.name == "老包"


def test_migrate_is_noop_when_already_new_layout(tmp_db, tmp_path):
    enhance_pack.install_pack(_make_pack(tmp_path / "c.zip", kind="csc"))
    before = (enhance_pack.current_dir("csc") / "model.onnx").read_bytes()
    enhance_pack.migrate_layout()
    after = (enhance_pack.current_dir("csc") / "model.onnx").read_bytes()
    assert before == after


def test_migrate_never_raises_on_garbage(tmp_db):
    """迁移本身坏掉也绝不能把主流程带崩。"""
    base = paths.enhance_dir() / "current"
    base.mkdir(parents=True, exist_ok=True)
    (base / "manifest.json").write_text("{ not json", encoding="utf-8")
    enhance_pack.migrate_layout()      # 不抛异常即通过


def test_migrate_does_not_clobber_existing_slot(tmp_db, tmp_path):
    """新槽位已有包时，迁移不得覆盖它。"""
    enhance_pack.install_pack(_make_pack(tmp_path / "n.zip", name="新版",
                                         kind="csc", model=b"NEW"))
    base = paths.enhance_dir() / "current"
    # 人为在旧位置留一个残留清单
    (base / "manifest.json").write_text(
        json.dumps({"schema": 1, "name": "野包", "kind": "csc",
                    "license": "L", "backend": "onnx",
                    "files": {"model.onnx": "0" * 64, "vocab.txt": "0" * 64}}),
        encoding="utf-8")
    enhance_pack.migrate_layout()
    info = enhance_pack.installed_pack("csc")
    assert info is not None and info.name == "新版"
