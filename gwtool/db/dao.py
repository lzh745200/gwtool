# -*- coding: utf-8 -*-
"""数据访问对象（DAO）：所有 SQLite 读写的唯一入口。

约定：
  - FTS 索引在本模块内同步维护（写入时同步写 FTS 行）；
  - 所有函数可被任意线程调用（各自线程连接）。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field

from . import connection as dbconn
from .tokenize import build_match_query, tokenize


def now() -> str:
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def text_hash(text: str) -> str:
    return hashlib.sha256("".join((text or "").split()).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- dataclasses
@dataclass
class Document:
    id: int = 0
    title: str = ""
    content_text: str = ""
    blocks_json: str = "[]"
    file_path: str = ""
    file_type: str = ""
    tags: str = ""
    category_id: int = 0
    text_hash: str = ""
    word_count: int = 0
    import_time: str = ""
    updated_time: str = ""
    simhash: int | None = None


@dataclass
class Category:
    id: int = 0
    parent_id: int = 0
    name: str = ""
    sort: int = 0


@dataclass
class ErrorPair:
    id: int = 0
    wrong: str = ""
    correct: str = ""
    category: str = ""
    confidence: float = 0.9
    enabled: int = 1
    source: str = "builtin"


@dataclass
class Phrase:
    id: int = 0
    phrase: str = ""
    context: str = ""
    source: str = "user"
    tag: str = ""


@dataclass
class SearchResult:
    table: str          # documents / dictionary / phrases
    ref_id: int
    title: str
    snippet: str
    rank: float


# ---------------------------------------------------------------- categories
def add_category(name: str, parent_id: int = 0) -> int:
    conn = dbconn.get_conn()
    cur = conn.execute(
        "INSERT INTO categories(parent_id,name) VALUES(?,?)", (parent_id, name))
    conn.commit()
    return int(cur.lastrowid)


def rename_category(cat_id: int, name: str) -> None:
    conn = dbconn.get_conn()
    conn.execute("UPDATE categories SET name=? WHERE id=?", (name, cat_id))
    conn.commit()


def delete_category(cat_id: int) -> int:
    """删除分类及其全部子分类；其下文档归入「未分类」(0)。返回受影响文档数。"""
    conn = dbconn.get_conn()
    # 收集整棵子树
    ids: list[int] = [cat_id]
    frontier = [cat_id]
    while frontier:
        placeholders = ",".join("?" * len(frontier))
        rows = conn.execute(
            f"SELECT id FROM categories WHERE parent_id IN ({placeholders})",
            frontier).fetchall()
        frontier = [int(r["id"]) for r in rows if int(r["id"]) not in ids]
        ids.extend(frontier)
    placeholders = ",".join("?" * len(ids))
    cur = conn.execute(
        f"UPDATE documents SET category_id=0 WHERE category_id IN ({placeholders})",
        ids)
    n = cur.rowcount
    conn.execute(f"DELETE FROM categories WHERE id IN ({placeholders})", ids)
    conn.commit()
    return n


def list_categories() -> list[Category]:
    conn = dbconn.get_conn()
    rows = conn.execute("SELECT id,parent_id,name,sort FROM categories ORDER BY sort,id").fetchall()
    return [Category(**dict(r)) for r in rows]


# ---------------------------------------------------------------- documents
def _fts_update_documents(doc_id: int, title: str, content: str) -> None:
    conn = dbconn.get_conn()
    conn.execute("DELETE FROM documents_fts WHERE ref_id=?", (doc_id,))
    conn.execute(
        "INSERT INTO documents_fts(title,tokenized,ref_id) VALUES(?,?,?)",
        (tokenize(title), tokenize(content), doc_id))


def add_document(doc: Document) -> int:
    """插入文档；重复内容（hash 相同）返回 -1。"""
    conn = dbconn.get_conn()
    if not doc.text_hash:
        doc.text_hash = text_hash(doc.content_text)
    dup = conn.execute(
        "SELECT id FROM documents WHERE text_hash=?", (doc.text_hash,)).fetchone()
    if dup:
        return -1
    if not doc.word_count:
        doc.word_count = len(doc.content_text)
    if doc.simhash is None:
        from ..core.simhash import simhash as _simhash, to_db
        doc.simhash = to_db(_simhash(doc.content_text))
    cur = conn.execute(
        "INSERT INTO documents(title,content_text,blocks_json,file_path,file_type,"
        "tags,category_id,text_hash,word_count,import_time,updated_time,simhash)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (doc.title, doc.content_text, doc.blocks_json, doc.file_path, doc.file_type,
         doc.tags, doc.category_id, doc.text_hash, doc.word_count, now(), now(),
         doc.simhash))
    doc_id = int(cur.lastrowid)
    _fts_update_documents(doc_id, doc.title, doc.content_text)
    conn.commit()
    return doc_id


def update_document_content(doc_id: int, title: str, content: str,
                            blocks_json: str | None = None) -> None:
    conn = dbconn.get_conn()
    from ..core.simhash import simhash as _simhash, to_db
    conn.execute(
        "UPDATE documents SET title=?,content_text=?,blocks_json=?,text_hash=?,"
        "word_count=?,updated_time=?,simhash=? WHERE id=?",
        (title, content, blocks_json if blocks_json is not None else "[]",
         text_hash(content), len(content), now(),
         to_db(_simhash(content)), doc_id))
    _fts_update_documents(doc_id, title, content)
    conn.commit()


def update_document_meta(doc_id: int, tags: str | None = None,
                         category_id: int | None = None, title: str | None = None) -> None:
    conn = dbconn.get_conn()
    if tags is not None:
        conn.execute("UPDATE documents SET tags=? WHERE id=?", (tags, doc_id))
    if category_id is not None:
        conn.execute("UPDATE documents SET category_id=? WHERE id=?", (category_id, doc_id))
    if title is not None:
        conn.execute("UPDATE documents SET title=? WHERE id=?", (title, doc_id))
        row = conn.execute("SELECT content_text FROM documents WHERE id=?", (doc_id,)).fetchone()
        if row:
            _fts_update_documents(doc_id, title, row["content_text"])
    conn.commit()


def delete_document(doc_id: int) -> None:
    conn = dbconn.get_conn()
    conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    conn.execute("DELETE FROM documents_fts WHERE ref_id=?", (doc_id,))
    # 级联清理历史快照，避免孤儿快照无限累积
    conn.execute("DELETE FROM snapshots WHERE doc_id=?", (doc_id,))
    conn.commit()


def get_document(doc_id: int) -> Document | None:
    row = dbconn.get_conn().execute(
        "SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    return Document(**dict(row)) if row else None


def list_documents(category_id: int | None = None) -> list[Document]:
    """category_id=None 返回全部（标题列表用，不含正文以省内存）。"""
    conn = dbconn.get_conn()
    cols = "id,title,tags,category_id,file_type,word_count,import_time,updated_time"
    if category_id is None:
        rows = conn.execute(
            f"SELECT {cols} FROM documents ORDER BY import_time DESC,id DESC").fetchall()
    else:
        rows = conn.execute(
            f"SELECT {cols} FROM documents WHERE category_id=?"
            " ORDER BY import_time DESC,id DESC", (category_id,)).fetchall()
    return [Document(**dict(r)) for r in rows]


def count_documents() -> int:
    return int(dbconn.get_conn().execute("SELECT count(*) FROM documents").fetchone()[0])


def all_simhashes() -> dict[int, int]:
    """全库已持久化的 SimHash（查重粗筛用，避免每次全库重算）。"""
    rows = dbconn.get_conn().execute(
        "SELECT id, simhash FROM documents WHERE simhash IS NOT NULL").fetchall()
    from ..core.simhash import from_db
    return {int(r["id"]): from_db(int(r["simhash"])) for r in rows}


def rebuild_fts() -> dict[str, int]:
    """全量重建 FTS 索引（documents/phrases）。

    FTS 行由 DAO 在写入时同步维护，任何绕过 DAO 的写入（如种子 ATTACH 直插、
    手工改库）都会造成索引静默失配；此函数是用户可见的自救入口。
    返回各源重建条数。
    """
    conn = dbconn.get_conn()
    from . import tokenize as tok
    counts: dict[str, int] = {}
    conn.execute("DELETE FROM documents_fts")
    rows = conn.execute("SELECT id, title, content_text FROM documents").fetchall()
    for r in rows:
        conn.execute(
            "INSERT INTO documents_fts(title,tokenized,ref_id) VALUES(?,?,?)",
            (tok.tokenize(r["title"]), tok.tokenize(r["content_text"]), int(r["id"])))
    counts["documents"] = len(rows)
    conn.execute("DELETE FROM phrases_fts")
    rows = conn.execute("SELECT id, phrase, context FROM user_phrases").fetchall()
    for r in rows:
        text = r["phrase"] + "\n" + (r["context"] or "")
        conn.execute(
            "INSERT INTO phrases_fts(phrase,tokenized,ref_id) VALUES(?,?,?)",
            (tok.tokenize(r["phrase"]), tok.tokenize(text), int(r["id"])))
    counts["phrases"] = len(rows)
    conn.commit()
    return counts


# ---------------------------------------------------------------- dictionary
def add_dictionary_entry(word: str, pinyin: str = "", definition: str = "",
                         example: str = "", source: str = "user") -> int:
    conn = dbconn.get_conn()
    cur = conn.execute(
        "INSERT INTO dictionary(word,pinyin,definition,example,source) VALUES(?,?,?,?,?)",
        (word, pinyin, definition, example, source))
    conn.commit()
    return int(cur.lastrowid)


def delete_dictionary_entry(entry_id: int) -> None:
    conn = dbconn.get_conn()
    conn.execute("DELETE FROM dictionary WHERE id=?", (entry_id,))
    conn.commit()


def lookup_dictionary(word: str) -> list[dict]:
    rows = dbconn.get_conn().execute(
        "SELECT * FROM dictionary WHERE word=? ORDER BY id", (word,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- error pairs
def add_error_pair(wrong: str, correct: str, category: str = "用户添加",
                   confidence: float = 0.99, source: str = "user") -> int:
    conn = dbconn.get_conn()
    cur = conn.execute(
        "INSERT OR REPLACE INTO error_pairs(wrong,correct,category,confidence,enabled,source)"
        " VALUES(?,?,?,?,1,?)",
        (wrong, correct, category, confidence, source))
    conn.commit()
    return int(cur.lastrowid)


def delete_error_pair(pair_id: int) -> None:
    conn = dbconn.get_conn()
    conn.execute("DELETE FROM error_pairs WHERE id=?", (pair_id,))
    conn.commit()


def all_error_pairs(only_enabled: bool = True) -> list[ErrorPair]:
    sql = "SELECT * FROM error_pairs"
    if only_enabled:
        sql += " WHERE enabled=1"
    rows = dbconn.get_conn().execute(sql).fetchall()
    return [ErrorPair(**dict(r)) for r in rows]


def list_error_pairs(limit: int = 500, offset: int = 0, keyword: str = "") -> list[ErrorPair]:
    conn = dbconn.get_conn()
    if keyword:
        rows = conn.execute(
            "SELECT * FROM error_pairs WHERE wrong LIKE ? OR correct LIKE ?"
            " ORDER BY id LIMIT ? OFFSET ?",
            (f"%{keyword}%", f"%{keyword}%", limit, offset)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM error_pairs ORDER BY id LIMIT ? OFFSET ?",
            (limit, offset)).fetchall()
    return [ErrorPair(**dict(r)) for r in rows]


def count_error_pairs() -> int:
    return int(dbconn.get_conn().execute("SELECT count(*) FROM error_pairs").fetchone()[0])


# ---------------------------------------------------------------- phrases
def add_phrase(phrase: str, context: str = "", tag: str = "", source: str = "user") -> int:
    conn = dbconn.get_conn()
    cur = conn.execute(
        "INSERT INTO user_phrases(phrase,context,source,tag) VALUES(?,?,?,?)",
        (phrase, context, source, tag))
    conn.execute(
        "INSERT INTO phrases_fts(phrase,tokenized,ref_id) VALUES(?,?,?)",
        (tokenize(phrase), tokenize(phrase + " " + context), int(cur.lastrowid)))
    conn.commit()
    return int(cur.lastrowid)


def delete_phrase(phrase_id: int) -> None:
    conn = dbconn.get_conn()
    conn.execute("DELETE FROM user_phrases WHERE id=?", (phrase_id,))
    conn.execute("DELETE FROM phrases_fts WHERE ref_id=?", (phrase_id,))
    conn.commit()


def list_phrases(keyword: str = "", limit: int = 500, offset: int = 0) -> list[Phrase]:
    conn = dbconn.get_conn()
    if keyword:
        rows = conn.execute(
            "SELECT * FROM user_phrases WHERE phrase LIKE ? OR context LIKE ?"
            " ORDER BY id DESC LIMIT ? OFFSET ?",
            (f"%{keyword}%", f"%{keyword}%", limit, offset)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM user_phrases ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset)).fetchall()
    return [Phrase(**dict(r)) for r in rows]


# ---------------------------------------------------------------- templates
def save_template(name: str, config_json: str, is_default: bool = False) -> int:
    conn = dbconn.get_conn()
    if is_default:
        conn.execute("UPDATE templates SET is_default=0")
    cur = conn.execute(
        "INSERT INTO templates(name,config_json,is_default,updated_time) VALUES(?,?,?,?)"
        " ON CONFLICT(name) DO UPDATE SET config_json=excluded.config_json,"
        " is_default=excluded.is_default, updated_time=excluded.updated_time",
        (name, config_json, 1 if is_default else 0, now()))
    conn.commit()
    return int(cur.lastrowid)


def delete_template(template_id: int) -> None:
    conn = dbconn.get_conn()
    conn.execute("DELETE FROM templates WHERE id=?", (template_id,))
    conn.commit()


def list_templates() -> list[dict]:
    rows = dbconn.get_conn().execute(
        "SELECT id,name,is_default,updated_time FROM templates ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def get_template_config(name: str) -> str | None:
    row = dbconn.get_conn().execute(
        "SELECT config_json FROM templates WHERE name=?", (name,)).fetchone()
    return row["config_json"] if row else None


def default_template_config() -> str:
    row = dbconn.get_conn().execute(
        "SELECT config_json FROM templates WHERE is_default=1 LIMIT 1").fetchone()
    if row:
        return row["config_json"]
    row = dbconn.get_conn().execute(
        "SELECT config_json FROM templates ORDER BY id LIMIT 1").fetchone()
    return row["config_json"] if row else ""


# ---------------------------------------------------------------- settings
def get_setting(key: str, default: str = "") -> str:
    row = dbconn.get_conn().execute(
        "SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    conn = dbconn.get_conn()
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()


# ---------------------------------------------------------------- snapshots
def add_snapshot(doc_id: int | None, title: str, content: str,
                 reason: str = "auto") -> int:
    conn = dbconn.get_conn()
    cur = conn.execute(
        "INSERT INTO snapshots(doc_id,title,content,reason) VALUES(?,?,?,?)",
        (doc_id, title, content, reason))
    _prune_snapshots(conn, doc_id)
    conn.commit()
    return int(cur.lastrowid)


SNAPSHOT_KEEP = 30  # 每文档保留快照数


def _prune_snapshots(conn, doc_id: int | None):
    if doc_id is None:
        return
    conn.execute(
        "DELETE FROM snapshots WHERE doc_id=? AND id NOT IN"
        " (SELECT id FROM snapshots WHERE doc_id=? ORDER BY id DESC LIMIT ?)",
        (doc_id, doc_id, SNAPSHOT_KEEP))


def list_snapshots(doc_id: int | None = None, limit: int = 200) -> list[dict]:
    conn = dbconn.get_conn()
    if doc_id is None:
        rows = conn.execute(
            "SELECT * FROM snapshots ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM snapshots WHERE doc_id=? ORDER BY id DESC LIMIT ?",
            (doc_id, limit)).fetchall()
    return [dict(r) for r in rows]


def get_snapshot(snapshot_id: int) -> dict | None:
    row = dbconn.get_conn().execute(
        "SELECT * FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------- ignore words
def add_ignore_word(word: str, note: str = "") -> None:
    conn = dbconn.get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO ignore_words(word,note) VALUES(?,?)",
        (word.strip(), note))
    conn.commit()


def remove_ignore_word(word: str) -> None:
    conn = dbconn.get_conn()
    conn.execute("DELETE FROM ignore_words WHERE word=?", (word.strip(),))
    conn.commit()


def all_ignore_words() -> set[str]:
    rows = dbconn.get_conn().execute("SELECT word FROM ignore_words").fetchall()
    return {r["word"] for r in rows}


# ---------------------------------------------------------------- search
def search_documents(query: str, limit: int = 50) -> list[SearchResult]:
    return _fts_search("documents_fts", "documents", query, limit)


def search_dictionary(query: str, limit: int = 50) -> list[SearchResult]:
    """词典检索走 word 列 LIKE（12万词条免建 FTS，命中即时）。

    优先精确/前缀命中，其次包含命中；拼音不参与（写作参考以词条为主）。
    """
    q = (query or "").strip()
    if not q:
        return []
    conn = dbconn.get_conn()
    out: list[SearchResult] = []
    seen: set[int] = set()
    for stage, (sql, params) in enumerate((
        ("SELECT id,word,definition FROM dictionary WHERE word=? LIMIT ?",
         (q, limit)),
        ("SELECT id,word,definition FROM dictionary WHERE word LIKE ? "
         "AND word<>? ORDER BY LENGTH(word) LIMIT ?", (q + "%", q, limit)),
        ("SELECT id,word,definition FROM dictionary WHERE word LIKE ? "
         "AND word<>? ORDER BY LENGTH(word) LIMIT ?", ("%" + q + "%", q, limit)),
    )):
        rank = (0.9, 0.8, 0.7)[stage]  # 精确 > 前缀 > 包含
        for row in conn.execute(sql, params):
            rid = int(row["id"])
            if rid in seen:
                continue
            seen.add(rid)
            snip = (row["definition"] or "")[:120]
            out.append(SearchResult("dictionary", rid, row["word"], snip, rank))
            if len(out) >= limit:
                return out
    return out


def search_phrases(query: str, limit: int = 50) -> list[SearchResult]:
    return _fts_search("phrases_fts", "user_phrases", query, limit)


def _fts_search(fts_table: str, src_table: str, query: str, limit: int) -> list[SearchResult]:
    match = build_match_query(query)
    if not match:
        return []
    conn = dbconn.get_conn()
    try:
        if src_table == "documents":
            sql = (f"SELECT f.ref_id AS ref_id, bm25({fts_table}) AS rank,"
                   f" d.title AS title,"
                   f" snippet({fts_table},1,'【','】','…',16) AS snip"
                   f" FROM {fts_table} f JOIN {src_table} d ON d.id=f.ref_id"
                   f" WHERE {fts_table} MATCH ? ORDER BY rank LIMIT ?")
        elif src_table == "dictionary":
            sql = (f"SELECT f.ref_id AS ref_id, bm25({fts_table}) AS rank,"
                   f" d.word AS title,"
                   f" snippet({fts_table},1,'【','】','…',16) AS snip"
                   f" FROM {fts_table} f JOIN {src_table} d ON d.id=f.ref_id"
                   f" WHERE {fts_table} MATCH ? ORDER BY rank LIMIT ?")
        else:
            sql = (f"SELECT f.ref_id AS ref_id, bm25({fts_table}) AS rank,"
                   f" d.phrase AS title,"
                   f" snippet({fts_table},1,'【','】','…',16) AS snip"
                   f" FROM {fts_table} f JOIN {src_table} d ON d.id=f.ref_id"
                   f" WHERE {fts_table} MATCH ? ORDER BY rank LIMIT ?")
        rows = conn.execute(sql, (match, limit)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [SearchResult(src_table, int(r["ref_id"]), r["title"], r["snip"], float(r["rank"]))
            for r in rows]
