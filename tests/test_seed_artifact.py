# -*- coding: utf-8 -*-
"""随包种子库（seed.db）的产物契约。

为什么需要这个文件
------------------
`seed.db` 是**构建产物**（`scripts/seed_data.py` 生成后提交进仓库）。它曾经
停在旧 schema 时代：`PRAGMA user_version = 0`、只有 20 张表（缺后加的 10 张业务表）、
还带着 220 条 `source='curated'` 的精标快照。后果不是"看起来不整齐"，而是：

  · 快照会让内置精标表的**删除与修订在真实安装上失效**（单测用空库看不到）；
  · 旧 schema 的产物一旦被当成"用户库"打开，会触发一轮无意义的版本迁移。

这些不变量靠人眼守不住（改 schema 时没人会记得重建一个 15MB 的二进制产物），
所以写成测试：**下次改 schema 忘了重建 seed.db，这里先红**。
"""
from __future__ import annotations

import re
import sqlite3

from gwtool.db.schema import FTS_TABLES, SCHEMA_VERSION, TABLES
from gwtool.paths import bundled_db_seed_path


def _seed_conn() -> sqlite3.Connection:
    path = bundled_db_seed_path()
    assert path.exists(), f"种子库缺失：{path}"
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _declared_tables() -> set[str]:
    names: set[str] = set()
    for ddl in list(TABLES) + list(FTS_TABLES):
        m = re.search(r"CREATE\s+(?:VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
                      r"[\"'`\[]?(\w+)", ddl, re.I)
        assert m, f"无法从 DDL 解析表名：{ddl[:60]}"
        names.add(m.group(1))
    return names


def test_seed_schema_matches_current_version():
    """种子库的 user_version 必须等于当前 schema 版本（否则首启会当老库迁移）。"""
    conn = _seed_conn()
    try:
        uv = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    assert uv == SCHEMA_VERSION, (
        f"seed.db user_version={uv}，当前 SCHEMA_VERSION={SCHEMA_VERSION}："
        "改过 schema 后必须重跑 scripts/seed_data.py 重建产物")


def test_seed_has_no_legacy_or_missing_tables():
    """不得残留旧 schema 的"遗留空表"，也不得缺当前 schema 声明的表。

    FTS5 虚表在库里会额外生成 ``_data/_idx/_content/_docsize/_config`` 影子表，
    它们是引擎实现细节，允许存在。
    """
    declared = _declared_tables()
    fts_names = {m.group(1) for m in re.finditer(
        r"CREATE\s+VIRTUAL\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`\[]?(\w+)",
        "\n".join(FTS_TABLES), re.I)}
    shadow = {f"{t}{suffix}" for t in fts_names
              for suffix in ("_data", "_idx", "_content", "_docsize", "_config")}
    conn = _seed_conn()
    try:
        actual = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name NOT LIKE 'sqlite_%'")}
    finally:
        conn.close()
    unexpected = actual - declared - shadow
    missing = declared - actual
    assert not unexpected, f"seed.db 有当前 schema 之外的遗留表：{sorted(unexpected)}"
    assert not missing, f"seed.db 缺当前 schema 的表：{sorted(missing)}"


def test_seed_carries_no_curated_snapshot():
    """种子库不得再带 `source='curated'` 的精标快照。

    精标表的唯一事实来源是 `corrector_data.CURATED_PAIRS`；库内快照会让
    "内置删掉的错对复活、降级的类别被旧值盖回"（v1.8 真实修复过的缺陷）。
    """
    conn = _seed_conn()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM error_pairs WHERE source='curated'").fetchone()[0]
    finally:
        conn.close()
    assert n == 0, f"seed.db 仍带 {n} 条 curated 快照（应只留生成对）"


def test_seed_error_pairs_are_deterministic_and_plentiful():
    """生成对：按错形唯一（同一错形只留一个正形），且总量满足 ≥3 万条承诺。"""
    conn = _seed_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM error_pairs").fetchone()[0]
        dup = conn.execute(
            "SELECT COUNT(*) FROM (SELECT wrong FROM error_pairs"
            " GROUP BY wrong HAVING COUNT(*) > 1)").fetchone()[0]
        sources = {r[0] for r in conn.execute(
            "SELECT DISTINCT source FROM error_pairs")}
    finally:
        conn.close()
    assert total >= 30000, f"错别字对仅 {total} 条"
    assert dup == 0, f"有 {dup} 个错形对应多个正形（建议哪条取决于库内行序）"
    assert sources == {"generated"}, f"种子库只应含生成对，实际来源：{sources}"


def test_seed_dictionary_complete():
    """词典：随包全量 CC-CEDICT（≥5 万条），且中文词占绝大多数。"""
    conn = _seed_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM dictionary").fetchone()[0]
        cjk = conn.execute(
            "SELECT COUNT(*) FROM dictionary WHERE word GLOB "
            "'*[一-龥]*'").fetchone()[0]
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    assert total >= 50000, f"词典仅 {total} 条"
    assert cjk >= total * 0.9, f"中文词占比异常：{cjk}/{total}"
    assert integrity == "ok", f"种子库完整性检查失败：{integrity}"
