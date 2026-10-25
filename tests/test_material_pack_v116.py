# -*- coding: utf-8 -*-
"""写作参考库核心：语料包加载、检索、模板、错误路径（v1.16）。"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from gwtool.core.material_pack import (MaterialPack, MaterialPackError,
                                       find_default_packs)


def _build_pack(root: Path) -> Path:
    pack = root / "pack"
    (pack / "corpus").mkdir(parents=True)
    (pack / "annotations").mkdir()
    index = [
        {"id": "d1", "title": "论衡·书虚", "source": "classic", "work": "论衡",
         "han_chars": 120},
        {"id": "d2", "title": "国务院关于扩大消费的通知", "source": "gov",
         "work": "通知", "han_chars": 90},
    ]
    (pack / "index.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8")
    tpl = [{"role": "过渡", "work": "论衡", "pattern": "故{主体}之事 不可不察也",
            "count": 3, "examples": ["故圣人之事，不可不察也。"]}]
    (pack / "templates.json").write_text(
        json.dumps(tpl, ensure_ascii=False), encoding="utf-8")
    ann1 = {"doc": {"id": "d1"}, "annotated_paragraphs": [
        {"role": "support", "text": "故圣人之事不可不察也。",
         "sentences": [
             {"text": "故圣人之事不可不察也。", "role": "过渡",
              "rule": "transition-led", "conf": 0.85},
             {"text": "夫论者所以辨是非也。", "role": "论点",
              "rule": "claim-classical", "conf": 0.62},
         ]}]}
    ann2 = {"doc": {"id": "d2"}, "annotated_paragraphs": [
        {"role": "support", "text": "各地要抓好落实。",
         "sentences": [
             {"text": "各地要抓好落实。", "role": "论点",
              "rule": "claim-deontic", "conf": 0.6},
             {"text": "据统计 消费增长百分之五。", "role": "论据",
              "rule": "evidence-example", "conf": 0.7},
         ]}]}
    (pack / "annotations" / "d1.json").write_text(
        json.dumps(ann1, ensure_ascii=False), encoding="utf-8")
    (pack / "annotations" / "d2.json").write_text(
        json.dumps(ann2, ensure_ascii=False), encoding="utf-8")
    (pack / "corpus" / "d1.md").write_text("正文", encoding="utf-8")
    return pack


def test_stats_and_search(tmp_path):
    pack = MaterialPack(_build_pack(tmp_path))
    st = pack.stats()
    assert st["docs"] == 2
    assert st["templates"] == 1
    hits = pack.search("不可不察")
    assert hits and hits[0].role == "过渡"
    assert hits[0].title == "论衡·书虚"
    # 角色过滤
    only_claim = pack.search("要", role="论点")
    assert only_claim and all(h.role == "论点" for h in only_claim)
    # 篇目命中
    title_hits = pack.search("消费")
    assert any("篇目命中" in h.sentence for h in title_hits)
    pack.close()


def test_templates_filter(tmp_path):
    pack = MaterialPack(_build_pack(tmp_path))
    assert pack.templates(role="过渡")
    assert not pack.templates(role="结尾")
    t = pack.templates(work="论衡")[0]
    assert t.pattern.startswith("故")
    assert t.count == 3
    pack.close()


def test_zip_pack(tmp_path):
    pack_dir = _build_pack(tmp_path)
    zip_path = tmp_path / "pack.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        for p in pack_dir.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(pack_dir).as_posix())
    with MaterialPack(zip_path) as pack:
        assert pack.stats()["docs"] == 2
        assert pack.search("论者")[0].role == "论点"


def test_errors(tmp_path):
    with pytest.raises(MaterialPackError):
        MaterialPack(tmp_path / "missing")
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(MaterialPackError):
        MaterialPack(bad)
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "index.json").write_text("[]", encoding="utf-8")
    with pytest.raises(MaterialPackError):
        MaterialPack(empty)


def test_find_default_packs(tmp_path):
    (tmp_path / "写作语料库_公版_20260101").mkdir()
    (tmp_path / "写作语料库_公版_20260101" / "index.json").write_text(
        "[]", encoding="utf-8")
    (tmp_path / "写作语料库_公版_20260101.zip").write_bytes(b"PK")
    (tmp_path / "other").mkdir()
    found = find_default_packs(tmp_path)
    names = {p.name for p in found}
    assert "写作语料库_公版_20260101" in names
    assert "写作语料库_公版_20260101.zip" in names


def test_empty_query_returns_empty(tmp_path):
    pack = MaterialPack(_build_pack(tmp_path))
    assert pack.search("") == []
    pack.close()
