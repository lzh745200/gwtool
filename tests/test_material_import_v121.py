# -*- coding: utf-8 -*-
"""v1.21：写作参考库「任意文档/任意文字」导入。"""
from __future__ import annotations

from pathlib import Path

import pytest

from gwtool import paths
from gwtool.core import material_import
from gwtool.core.material_pack import MaterialPack


@pytest.fixture()
def pack_dir(tmp_path):
    pack = tmp_path / "我的素材"
    material_import.ensure_pack(pack)
    return pack


def test_import_pasted_text_and_search(pack_dir):
    s = material_import.import_text(
        "测试讲话",
        "各单位要高度重视此项工作。因此，要加强协调配合。\n"
        "会议指出，必须抓好贯彻落实。",
        pack_dir=pack_dir)
    assert s.imported == 1 and not s.failed
    pack = MaterialPack(pack_dir)
    hits = pack.search("协调")
    assert hits and "要加强协调配合" in hits[0].sentence
    assert pack.stats()["docs"] == 1


def test_import_txt_with_gb18030_encoding(pack_dir):
    p = Path(pack_dir).parent / "gbk素材.txt"
    p.write_bytes("这是一段用国标编码保存的素材文字，可用于参考。".encode("gb18030"))
    s = material_import.import_paths([p], pack_dir)
    assert s.imported == 1, s.text()
    pack = MaterialPack(pack_dir)
    assert pack.search("国标编码")


def test_import_docx(pack_dir):
    docx = pytest.importorskip("docx")
    p = Path(pack_dir).parent / "通知.docx"
    d = docx.Document()
    d.add_paragraph("关于做好年度工作的通知")
    d.add_paragraph("各部门要结合实际，认真抓好落实。")
    d.save(str(p))
    s = material_import.import_paths([p], pack_dir)
    assert s.imported == 1, s.text()
    assert MaterialPack(pack_dir).search("抓好落实")


def test_duplicate_is_skipped(pack_dir):
    text = "同一段素材只应入库一次。"
    a = material_import.import_text("A", text, pack_dir=pack_dir)
    b = material_import.import_text("B", text, pack_dir=pack_dir)
    assert a.imported == 1 and b.imported == 0 and b.skipped_dup == 1
    assert MaterialPack(pack_dir).stats()["docs"] == 1


def test_title_collision_gets_suffix(pack_dir):
    text1 = "第一篇不同的正文内容，用于测试标题去重。"
    text2 = "第二篇不同的正文内容，用于测试标题去重。"
    material_import.import_text("同名素材", text1, pack_dir=pack_dir)
    material_import.import_text("同名素材", text2, pack_dir=pack_dir)
    index = __import__("json").loads(
        (Path(pack_dir) / "index.json").read_text(encoding="utf-8"))
    titles = sorted(d["title"] for d in index)
    assert titles == ["同名素材", "同名素材（2）"]


def test_unknown_extension_falls_back_to_text(pack_dir):
    p = Path(pack_dir).parent / "素材.unknownext"
    p.write_text("即便扩展名不认识，只要内容是文字也应能导入。", encoding="utf-8")
    s = material_import.import_paths([p], pack_dir)
    assert s.imported == 1, s.text()


def test_failed_file_reported_not_fatal(pack_dir, tmp_path):
    bad = tmp_path / "坏文件.docx"
    bad.write_bytes(b"")
    good = tmp_path / "好素材.txt"
    good.write_text("这一篇是好素材，应当照常入库。", encoding="utf-8")
    s = material_import.import_paths([bad, good], pack_dir)
    assert s.imported >= 1
    assert s.failed and "坏文件.docx" in s.failed[0][0]


def test_user_pack_dir_under_app_data(tmp_path):
    old = paths.app_data_dir()
    paths.set_app_data_dir(tmp_path)
    try:
        assert material_import.user_pack_dir() == tmp_path / "material_packs" / "我的素材"
        p = material_import.ensure_pack()
        assert (p / "index.json").is_file()
    finally:
        paths.set_app_data_dir(old)
