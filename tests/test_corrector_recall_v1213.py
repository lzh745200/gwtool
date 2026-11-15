# -*- coding: utf-8 -*-
"""v1.21.3：错别字召回提升的两条判据 + E7 那次误改的回归。

钉死三件事：
  1. 两字精标对在被分词切开时，若**两侧都不成词** -> 必须报（原规则一律丢弃）；
  2. 后一字能与命中末字成词（按装+机=装机）-> 不报（跨词相接）；
  3. 前一字能与命中首字成词（各位+莅临 里的"位临"）-> 不报。
    第 3 条是第一版遗漏的：只查后字时 E7 掉到 99.7%，把"莅临"判成误改。
"""
from __future__ import annotations

import itertools

import pytest

from gwtool import paths
from gwtool.core import corrector
from gwtool.db import connection as dbconn


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    """带种子库的独立数据目录（词典/词频决定分词边界，必须真实）。"""
    import shutil
    from pathlib import Path

    data = tmp_path / "data"
    data.mkdir()
    src = Path(__file__).resolve().parents[1] / "gwtool" / "resources" / "data" / "seed.db"
    shutil.copy2(src, data / "gwtool.db")
    monkeypatch.setattr(paths, "app_data_dir", lambda: data)
    dbconn.configure(data / "gwtool.db")
    corrector.invalidate_cache()
    yield data
    corrector.invalidate_cache()


def _pairs(text: str) -> set[tuple[str, str]]:
    return {(c.wrong, c.suggestion) for c in corrector.check_text(text)}


def test_two_char_pair_reported_when_no_neighbor_word(seeded):
    """已径 + 报备：径/报不成词，前一字也不成词 -> 真笔误，必须报。"""
    assert ("已径", "已经") in _pairs("已径完成整改并报备。")


def test_two_char_pair_suppressed_when_next_char_forms_word(seeded):
    """按装 + 机容量：装/机=装机 成词 -> 跨词相接，不报。"""
    assert ("按装", "安装") not in _pairs("按装机容量核定投资。")


def test_two_char_pair_suppressed_when_prev_char_forms_word(seeded):
    """各位 + 莅临：位/临 落在"各位莅临"中间 -> 不报（E7 回归）。"""
    got = _pairs("请各位莅临指导。")
    assert not any(w == "位临" for w, _s in got)


def test_curated_idioms_are_caught(seeded):
    """v1.21.3 补录的成语错形要能报出来（抽查 4 条，覆盖四类错法）。"""
    cases = [("一丝不拘", "一丝不苟"), ("实事述是", "实事求是"),
             ("病入膏盲", "病入膏肓"), ("饮鸠止渴", "饮鸩止渴")]
    text = "；".join(w for w, _ in cases)
    got = _pairs(text)
    missing = [(w, c) for w, c in cases if (w, c) not in got]
    assert not missing, f"未命中：{missing}；实得 {sorted(got)}"


def test_correct_forms_are_not_flagged(seeded):
    """正确写法不得被判错（防止召回提升用误报换）。"""
    clean = ["一丝不苟", "实事求是", "按装机容量", "各位莅临指导", "已经完成整改"]
    hit = list(itertools.chain.from_iterable(
        [(c.wrong, c.suggestion) for c in corrector.check_text(s)] for s in clean))
    assert not hit, f"正确写法被误报：{hit}"
