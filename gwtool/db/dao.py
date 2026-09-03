# -*- coding: utf-8 -*-
"""数据访问对象（DAO）：所有 SQLite 读写的唯一入口。

约定：
  - FTS 索引在本模块内同步维护（写入时同步写 FTS 行）；
  - 所有函数可被任意线程调用（各自线程连接）。
"""
from __future__ import annotations

import hashlib
import json
import re
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


@dataclass
class Dispatch:
    """一条发文登记记录。"""
    id: int = 0
    doc_no: str = ""            # 发文字号，如 ×政办发〔2026〕12号
    title: str = ""
    doc_type: str = ""          # 文种：通知/报告/请示/批复/函/纪要…
    org: str = ""               # 发文机关
    main_send: str = ""         # 主送
    cc: str = ""                # 抄送
    secret_level: str = "公开"   # 密级
    urgency: str = ""           # 紧急程度
    sign_date: str = ""         # 成文日期
    print_date: str = ""        # 印发日期
    pages: int = 0
    copies: int = 0             # 印数
    drafter: str = ""           # 拟稿人
    reviewer: str = ""          # 核稿人
    approver: str = ""          # 签发人
    status: str = "拟稿"         # 拟稿/核稿/签发/已印发/已归档
    doc_id: int = 0             # 关联资料库文档（0=未关联）
    remark: str = ""
    created_time: str = ""
    updated_time: str = ""


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
                   confidence: float = 0.99, source: str = "user",
                   enabled: bool = True) -> int:
    conn = dbconn.get_conn()
    cur = conn.execute(
        "INSERT OR REPLACE INTO error_pairs(wrong,correct,category,confidence,enabled,source)"
        " VALUES(?,?,?,?,?,?)",
        (wrong, correct, category, confidence, 1 if enabled else 0, source))
    conn.commit()
    return int(cur.lastrowid)


def delete_error_pair(pair_id: int) -> None:
    conn = dbconn.get_conn()
    conn.execute("DELETE FROM error_pairs WHERE id=?", (pair_id,))
    conn.commit()


def error_pair_sources() -> list[tuple[str, int]]:
    """全部纠错对来源及其条数，按条数倒序。用于「规则集」下拉。"""
    rows = dbconn.get_conn().execute(
        "SELECT COALESCE(NULLIF(source,''),'未标注') AS s, count(*) AS n"
        " FROM error_pairs GROUP BY s ORDER BY n DESC, s").fetchall()
    return [(r["s"], int(r["n"])) for r in rows]


def error_pair_categories(source: str = "") -> list[tuple[str, int]]:
    """某来源下的分类及条数（source 为空表示不限来源）。"""
    conn = dbconn.get_conn()
    sql = ("SELECT COALESCE(NULLIF(category,''),'未分类') AS c, count(*) AS n"
           " FROM error_pairs")
    params: list = []
    if source:
        sql += " WHERE source=?"
        params.append(source)
    sql += " GROUP BY c ORDER BY n DESC, c"
    return [(r["c"], int(r["n"])) for r in conn.execute(sql, params).fetchall()]


def set_error_pairs_enabled(enabled: bool, source: str = "",
                            category: str = "") -> int:
    """按来源/分类批量启用或停用纠错对，返回受影响行数。

    这是「规则集」的开关：停用某个来源后，该来源下的全部纠错对立即不再参与
    纠错（all_error_pairs(only_enabled=True) 会过滤掉），但数据仍保留在库里，
    可随时重新启用，不必删了再导。
    """
    where: list[str] = []
    params: list = []
    if source:
        where.append("COALESCE(NULLIF(source,''),'未标注')=?")
        params.append(source)
    if category:
        where.append("COALESCE(NULLIF(category,''),'未分类')=?")
        params.append(category)
    sql = f"UPDATE error_pairs SET enabled={1 if enabled else 0}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    conn = dbconn.get_conn()
    cur = conn.execute(sql, params)
    conn.commit()
    return int(cur.rowcount)


def count_error_pairs_by(source: str = "", category: str = "",
                         enabled: bool | None = None) -> int:
    """按来源/分类/启用状态统计纠错对条数。"""
    where: list[str] = []
    params: list = []
    if source:
        where.append("COALESCE(NULLIF(source,''),'未标注')=?")
        params.append(source)
    if category:
        where.append("COALESCE(NULLIF(category,''),'未分类')=?")
        params.append(category)
    if enabled is not None:
        where.append("enabled=?")
        params.append(1 if enabled else 0)
    sql = "SELECT count(*) FROM error_pairs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return int(dbconn.get_conn().execute(sql, params).fetchone()[0])


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


# ------------------------------------------------------- dispatch_register
_DISPATCH_COLUMNS = (
    "doc_no", "title", "doc_type", "org", "main_send", "cc", "secret_level",
    "urgency", "sign_date", "print_date", "pages", "copies", "drafter",
    "reviewer", "approver", "status", "doc_id", "remark",
)
# 统计分组列白名单：绝不能把调用方传入的字符串直接拼进 SQL
_DISPATCH_GROUPABLE = ("doc_type", "org", "status", "secret_level", "urgency",
                       "drafter", "approver")


def add_dispatch(d: Dispatch) -> int:
    conn = dbconn.get_conn()
    cols = ",".join(_DISPATCH_COLUMNS)
    marks = ",".join("?" * len(_DISPATCH_COLUMNS))
    cur = conn.execute(
        f"INSERT INTO dispatch_register({cols}) VALUES({marks})",
        [getattr(d, c) for c in _DISPATCH_COLUMNS])
    conn.commit()
    return int(cur.lastrowid)


def update_dispatch(d: Dispatch) -> None:
    conn = dbconn.get_conn()
    assigns = ",".join(f"{c}=?" for c in _DISPATCH_COLUMNS)
    conn.execute(
        f"UPDATE dispatch_register SET {assigns},"
        f"updated_time=datetime('now','localtime') WHERE id=?",
        [getattr(d, c) for c in _DISPATCH_COLUMNS] + [d.id])
    conn.commit()


def delete_dispatch(dispatch_id: int) -> None:
    conn = dbconn.get_conn()
    conn.execute("DELETE FROM dispatch_register WHERE id=?", (dispatch_id,))
    conn.commit()


def get_dispatch(dispatch_id: int) -> Dispatch | None:
    conn = dbconn.get_conn()
    row = conn.execute("SELECT * FROM dispatch_register WHERE id=?",
                       (dispatch_id,)).fetchone()
    return Dispatch(**dict(row)) if row else None


def list_dispatch(keyword: str = "", org: str = "", doc_type: str = "",
                  status: str = "", year: str = "", date_from: str = "",
                  date_to: str = "", limit: int = 5000) -> list[Dispatch]:
    """按条件筛选发文登记，成文日期倒序（同日按 id 倒序）。"""
    where: list[str] = []
    params: list = []
    if keyword.strip():
        # 台账最常见的检索就是按机关名/拟稿人查，这些列必须纳入关键字匹配
        kw_cols = ("title", "doc_no", "main_send", "cc", "remark", "org",
                   "doc_type", "drafter", "reviewer", "approver")
        where.append("(" + " OR ".join(f"{c} LIKE ?" for c in kw_cols) + ")")
        like = f"%{keyword.strip()}%"
        params += [like] * len(kw_cols)
    if org:
        where.append("org=?")
        params.append(org)
    if doc_type:
        where.append("doc_type=?")
        params.append(doc_type)
    if status:
        where.append("status=?")
        params.append(status)
    if year:
        # 年份既可能来自成文日期，也可能来自发文字号里的〔2026〕
        where.append("(sign_date LIKE ? OR doc_no LIKE ?)")
        params += [f"{year}%", f"%〔{year}〕%"]
    if date_from:
        where.append("sign_date>=?")
        params.append(date_from)
    if date_to:
        where.append("sign_date<=?")
        params.append(date_to)
    sql = "SELECT * FROM dispatch_register"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY sign_date DESC, id DESC LIMIT ?"
    params.append(limit)
    conn = dbconn.get_conn()
    rows = conn.execute(sql, params).fetchall()
    return [Dispatch(**dict(r)) for r in rows]


def count_dispatch() -> int:
    conn = dbconn.get_conn()
    return int(conn.execute("SELECT count(*) FROM dispatch_register").fetchone()[0])


def dispatch_stats(group_by: str = "doc_type", year: str = "") -> list[tuple[str, int]]:
    """按指定列聚合计数，返回 [(取值, 件数)]，件数倒序。空值归入「未填写」。"""
    if group_by not in _DISPATCH_GROUPABLE:
        raise ValueError(f"不支持的分组列：{group_by}")
    conn = dbconn.get_conn()
    sql = (f"SELECT COALESCE(NULLIF({group_by},''),'未填写') AS k, count(*) AS n"
           f" FROM dispatch_register")
    params: list = []
    if year:
        sql += " WHERE sign_date LIKE ? OR doc_no LIKE ?"
        params += [f"{year}%", f"%〔{year}〕%"]
    sql += " GROUP BY k ORDER BY n DESC, k"
    return [(r["k"], int(r["n"])) for r in conn.execute(sql, params).fetchall()]


def dispatch_monthly_counts(year: str) -> list[tuple[str, int]]:
    """某年 1-12 月各月发文件数（按成文日期）。"""
    conn = dbconn.get_conn()
    rows = conn.execute(
        "SELECT substr(sign_date,6,2) AS m, count(*) AS n FROM dispatch_register"
        " WHERE sign_date LIKE ? GROUP BY m", (f"{year}%",)).fetchall()
    got = {r["m"]: int(r["n"]) for r in rows if r["m"]}
    return [(f"{int(m):02d}月", got.get(m, 0)) for m in
            [f"{i:02d}" for i in range(1, 13)]]


def max_doc_no_serial(prefix: str, year: str) -> int:
    """取某机关代字某年度已用的最大发文序号，无记录返回 0。

    prefix 形如「×政办发」，匹配「×政办发〔2026〕12号」中的 12。
    """
    conn = dbconn.get_conn()
    rows = conn.execute(
        "SELECT doc_no FROM dispatch_register WHERE doc_no LIKE ?",
        (f"{prefix}%〔{year}〕%",)).fetchall()
    best = 0
    for r in rows:
        m = re.search(r"〕\s*(\d+)\s*号", r["doc_no"] or "")
        if m:
            best = max(best, int(m.group(1)))
    return best
