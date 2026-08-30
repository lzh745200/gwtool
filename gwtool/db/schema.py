# -*- coding: utf-8 -*-
"""SQLite 表结构与迁移。

设计要点：
  - 全部业务数据存于单库 gwtool.db；
  - 中文全文检索使用 FTS5，中文文本先经 jieba 预分词、以空格连接后写入
    （FTS5 默认 unicode61 分词器按空格切词，预分词即可命中中文）；
  - FTS 表由 DAO 在 Python 侧同步维护（分词无法在触发器内完成）。
"""
from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

TABLES = [
    # 分类树（资料归档）
    """CREATE TABLE IF NOT EXISTS categories(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        parent_id INTEGER NOT NULL DEFAULT 0,
        name TEXT NOT NULL,
        sort INTEGER NOT NULL DEFAULT 0,
        created_time TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )""",
    # 资料文档
    """CREATE TABLE IF NOT EXISTS documents(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        content_text TEXT NOT NULL DEFAULT '',
        blocks_json TEXT NOT NULL DEFAULT '[]',
        file_path TEXT DEFAULT '',
        file_type TEXT DEFAULT '',
        tags TEXT DEFAULT '',
        category_id INTEGER NOT NULL DEFAULT 0,
        text_hash TEXT NOT NULL DEFAULT '',
        word_count INTEGER NOT NULL DEFAULT 0,
        import_time TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        updated_time TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_hash ON documents(text_hash)""",
    """CREATE INDEX IF NOT EXISTS idx_documents_cat ON documents(category_id)""",
    # 词典（词条、拼音、释义、例句）
    """CREATE TABLE IF NOT EXISTS dictionary(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        word TEXT NOT NULL,
        pinyin TEXT DEFAULT '',
        definition TEXT DEFAULT '',
        example TEXT DEFAULT '',
        source TEXT DEFAULT 'builtin'
    )""",
    """CREATE INDEX IF NOT EXISTS idx_dictionary_word ON dictionary(word)""",
    # 错别字/纠错对
    """CREATE TABLE IF NOT EXISTS error_pairs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        wrong TEXT NOT NULL,
        correct TEXT NOT NULL,
        category TEXT DEFAULT '',
        confidence REAL NOT NULL DEFAULT 0.9,
        enabled INTEGER NOT NULL DEFAULT 1,
        source TEXT DEFAULT 'builtin'
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_error_pairs_key ON error_pairs(wrong, correct)""",
    # 用户句式/常用语/范文片段
    """CREATE TABLE IF NOT EXISTS user_phrases(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phrase TEXT NOT NULL,
        context TEXT DEFAULT '',
        source TEXT DEFAULT 'user',
        tag TEXT DEFAULT ''
    )""",
    # 公文模板
    """CREATE TABLE IF NOT EXISTS templates(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        config_json TEXT NOT NULL,
        is_default INTEGER NOT NULL DEFAULT 0,
        updated_time TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )""",
    # 键值设置
    """CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY,
        value TEXT
    )""",
    # 版本快照（自动保存/历史版本）
    """CREATE TABLE IF NOT EXISTS snapshots(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_id INTEGER,
        title TEXT DEFAULT '',
        content TEXT NOT NULL,
        reason TEXT DEFAULT 'auto',
        created_time TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )""",
    """CREATE INDEX IF NOT EXISTS idx_snapshots_doc ON snapshots(doc_id, id DESC)""",
    # 纠错持久忽略名单
    """CREATE TABLE IF NOT EXISTS ignore_words(
        word TEXT PRIMARY KEY,
        note TEXT DEFAULT ''
    )""",
]

# FTS5 虚拟表：ref_id 指向源表 id；tokenized 为 jieba 分词后文本
FTS_TABLES = [
    """CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
        title, tokenized, ref_id UNINDEXED)""",
    """CREATE VIRTUAL TABLE IF NOT EXISTS dictionary_fts USING fts5(
        word, tokenized, ref_id UNINDEXED)""",
    """CREATE VIRTUAL TABLE IF NOT EXISTS phrases_fts USING fts5(
        phrase, tokenized, ref_id UNINDEXED)""",
]


def init_schema(conn: sqlite3.Connection) -> None:
    """建库建表；设置用户版本号以支持后续迁移。"""
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    for ddl in TABLES:
        cur.execute(ddl)
    for ddl in FTS_TABLES:
        cur.execute(ddl)
    cur.execute("PRAGMA user_version=%d" % SCHEMA_VERSION)
    conn.commit()
