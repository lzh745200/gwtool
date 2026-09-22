# -*- coding: utf-8 -*-
"""数据访问对象（DAO）：所有 SQLite 读写的唯一入口。

约定：
  - FTS 索引在本模块内同步维护（写入时同步写 FTS 行）；
  - 所有函数可被任意线程调用（各自线程连接）。
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass

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
    deleted_time: str = ""      # 非空 = 在回收站里（软删除时间）


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


@dataclass
class Receive:
    """一条收文登记记录。

    字段与 `receive_register` 表一一对应，新增字段必须两边同步
    （`_RECEIVE_COLUMNS` 是写库时的唯一列清单）。

    一次给全办理时限（due_date/done_date）与归档（archive_no/retention）
    字段：SQLite 的 ALTER 虽便宜，但每次版本迁移都要走"迁移前自动备份"，
    对用户是可见的开销，能一次到位的就一次到位。
    """
    id: int = 0
    reg_no: str = ""            # 收文登记号（本单位内部流水）
    incoming_no: str = ""       # 来文字号（外单位格式，宽松保存）
    title: str = ""
    doc_type: str = ""
    from_org: str = ""          # 来文机关
    main_send: str = ""         # 主送（本单位受文部门）
    cc: str = ""                # 抄送
    secret_level: str = "公开"
    urgency: str = ""
    receive_date: str = ""      # 收到日期
    doc_date: str = ""          # 来文成文日期
    pages: int = 0
    copies: int = 0
    propose: str = ""           # 拟办意见
    instruction: str = ""       # 领导批示
    handler_dept: str = ""      # 承办部门
    handler: str = ""           # 承办人
    due_date: str = ""          # 应办结日期
    done_date: str = ""         # 实际办结日期
    result: str = ""            # 办理结果
    status: str = "签收"         # 签收/拟办/批办/承办/已办结/已归档
    archive_no: str = ""        # 档号
    retention: str = ""         # 保管期限：永久/30年/10年
    archive_date: str = ""      # 归档日期
    doc_id: int = 0             # 关联资料库文档（0=未关联）
    remark: str = ""
    created_time: str = ""
    updated_time: str = ""


@dataclass
class Attachment:
    """一条文档附件记录。

    stored_path 存数据目录内的相对路径（如 attachments/xxx.pdf）而非用户选的
    原始绝对路径：附件本体已复制进数据目录，备份/恢复与便携模式换机器后仍能定位。
    """
    id: int = 0
    doc_id: int = 0
    file_name: str = ""         # 原始文件名（列表里给用户看的名字）
    stored_path: str = ""       # 数据目录内相对路径（兼容历史绝对路径）
    size: int = 0               # 字节
    added_time: str = ""


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
    """插入文档；重复内容（hash 相同）返回 -1。

    特例：重复的那篇正在回收站里时，视为用户重新导入同一份材料 —— 直接把它
    恢复出来并返回其 id。text_hash 上有唯一索引，不恢复就再也无法入库，
    用户会看到"提示重复却在资料库里找不到"的怪现象。
    """
    conn = dbconn.get_conn()
    if not doc.text_hash:
        doc.text_hash = text_hash(doc.content_text)
    dup = conn.execute(
        "SELECT id,deleted_time FROM documents WHERE text_hash=?",
        (doc.text_hash,)).fetchone()
    if dup:
        if not dup["deleted_time"]:
            return -1
        return _restore_deleted(conn, int(dup["id"]), category_id=doc.category_id)
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
    """软删除：移入回收站（写 deleted_time），并摘掉 FTS 行使其不再被检索命中。

    正文、历史快照与附件都原样保留，restore_document 可完整恢复；
    真正删行见 purge_document（附件磁盘文件由 core.attachments 清理）。
    """
    conn = dbconn.get_conn()
    conn.execute(
        "UPDATE documents SET deleted_time=? WHERE id=? AND deleted_time=''",
        (now(), doc_id))
    conn.execute("DELETE FROM documents_fts WHERE ref_id=?", (doc_id,))
    conn.commit()


def _restore_deleted(conn, doc_id: int, category_id: int = 0) -> int:
    """把回收站里的文档恢复出来（调用方持有连接，负责语义上的分类归属）。"""
    conn.execute("UPDATE documents SET deleted_time='', updated_time=? WHERE id=?",
                 (now(), doc_id))
    if category_id:
        conn.execute("UPDATE documents SET category_id=? WHERE id=?",
                     (category_id, doc_id))
    row = conn.execute(
        "SELECT title,content_text FROM documents WHERE id=?", (doc_id,)).fetchone()
    if row:
        # 软删除时摘掉了 FTS 行，恢复必须补回，否则"恢复后搜不到"
        _fts_update_documents(doc_id, row["title"], row["content_text"])
    conn.commit()
    return doc_id


def restore_document(doc_id: int) -> bool:
    """从回收站恢复一篇文档；返回是否确有恢复（未在回收站/不存在则 False）。"""
    conn = dbconn.get_conn()
    row = conn.execute(
        "SELECT deleted_time FROM documents WHERE id=?", (doc_id,)).fetchone()
    if row is None or not row["deleted_time"]:
        return False
    _restore_deleted(conn, doc_id)
    return True


def purge_document(doc_id: int) -> None:
    """彻底删除（不可恢复）：真删文档行、FTS 行、历史快照与附件记录。

    附件的磁盘文件不在这里删（DAO 不做文件 IO）——UI 一律走
    core.attachments.purge_document，它先删文件再调本函数。
    """
    conn = dbconn.get_conn()
    conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    conn.execute("DELETE FROM documents_fts WHERE ref_id=?", (doc_id,))
    # 级联清理历史快照与附件记录，避免孤儿数据无限累积
    conn.execute("DELETE FROM snapshots WHERE doc_id=?", (doc_id,))
    conn.execute("DELETE FROM attachments WHERE doc_id=?", (doc_id,))
    conn.commit()


def list_deleted_documents(limit: int = 5000) -> list[Document]:
    """回收站列表：按删除时间倒序（走 idx_documents_deleted）。"""
    conn = dbconn.get_conn()
    cols = ("id,title,tags,category_id,file_type,word_count,import_time,"
            "updated_time,deleted_time")
    rows = conn.execute(
        f"SELECT {cols} FROM documents WHERE deleted_time<>''"
        " ORDER BY deleted_time DESC,id DESC LIMIT ?", (limit,)).fetchall()
    return [Document(**dict(r)) for r in rows]


def count_deleted_documents() -> int:
    return int(dbconn.get_conn().execute(
        "SELECT count(*) FROM documents WHERE deleted_time<>''").fetchone()[0])


def deleted_document_ids() -> list[int]:
    """回收站内全部文档 id（清空回收站时先取 id 再逐个彻底删除）。"""
    rows = dbconn.get_conn().execute(
        "SELECT id FROM documents WHERE deleted_time<>''").fetchall()
    return [int(r["id"]) for r in rows]


def get_document(doc_id: int) -> Document | None:
    """按 id 取单篇（含回收站里的：恢复/彻底删除/附件管理都要能取到）。

    需要"只看未删除"的列表请用 list_documents。
    """
    row = dbconn.get_conn().execute(
        "SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    return Document(**dict(row)) if row else None


def list_documents(category_id: int | None = None,
                   include_content: bool = False) -> list[Document]:
    """category_id=None 返回全部（标题列表用，不含正文以省内存）。

    include_content=True 时额外带出 content_text —— 供画像/统计等需要
    逐篇正文的场景；列表展示类调用保持默认 False，避免整库正文进内存。

    回收站里的文档（deleted_time 非空）一律不返回 —— 这是软删除语义的关键，
    资料库列表、汇编选料、查重、批量替换都走本函数。
    """
    conn = dbconn.get_conn()
    # file_path 必须带出：批量导出/移交包要按它复制用户的**原始文件**，
    # 缺了它只能退化成导出的纯文本（原格式与批注全丢）。
    cols = ("id,title,tags,category_id,file_type,file_path,word_count,"
            "import_time,updated_time,deleted_time")
    if include_content:
        cols += ",content_text"
    if category_id is None:
        rows = conn.execute(
            f"SELECT {cols} FROM documents WHERE deleted_time=''"
            " ORDER BY import_time DESC,id DESC").fetchall()
    else:
        rows = conn.execute(
            f"SELECT {cols} FROM documents WHERE deleted_time='' AND category_id=?"
            " ORDER BY import_time DESC,id DESC", (category_id,)).fetchall()
    return [Document(**dict(r)) for r in rows]


def bulk_update_tags(doc_ids: list[int], add: tuple[str, ...] = (),
                     remove: tuple[str, ...] = ()) -> int:
    """批量增删多篇文档的标签，返回实际发生变化的文档数。

    tags 是逗号分隔串，历史数据里中英文逗号混用，解析时两种都认、
    写回统一为中文逗号；去重且保持原有顺序，避免反复批量操作后
    标签越堆越乱。
    """
    add_clean = [t.strip() for t in add if t and t.strip()]
    remove_clean = {t.strip() for t in remove if t and t.strip()}
    if not doc_ids or (not add_clean and not remove_clean):
        return 0

    conn = dbconn.get_conn()
    changed = 0
    for doc_id in doc_ids:
        row = conn.execute("SELECT tags FROM documents WHERE id=?",
                           (doc_id,)).fetchone()
        if row is None:
            continue
        raw = (row["tags"] or "").replace("，", ",")
        current = [t.strip() for t in raw.split(",") if t.strip()]
        merged = [t for t in current if t not in remove_clean]
        for t in add_clean:
            if t not in merged:
                merged.append(t)
        new_value = "，".join(merged)
        if new_value != (row["tags"] or ""):
            conn.execute(
                "UPDATE documents SET tags=?, updated_time=datetime('now','localtime')"
                " WHERE id=?", (new_value, doc_id))
            changed += 1
    if changed:
        conn.commit()
    return changed


def count_documents(category_id: int | None = None) -> int:
    """在库文档数（不含回收站）。category_id 非 None 时只数该分类。"""
    conn = dbconn.get_conn()
    if category_id is None:
        return int(conn.execute(
            "SELECT count(*) FROM documents WHERE deleted_time=''").fetchone()[0])
    return int(conn.execute(
        "SELECT count(*) FROM documents WHERE deleted_time='' AND category_id=?",
        (category_id,)).fetchone()[0])


def all_simhashes() -> dict[int, int]:
    """全库已持久化的 SimHash（查重粗筛用，避免每次全库重算）。

    回收站里的文档不参与查重。
    """
    rows = dbconn.get_conn().execute(
        "SELECT id, simhash FROM documents"
        " WHERE simhash IS NOT NULL AND deleted_time=''").fetchall()
    from ..core.simhash import from_db
    return {int(r["id"]): from_db(int(r["simhash"])) for r in rows}


def iter_documents_content(category_id: int | None = None,
                           doc_ids: list[int] | None = None,
                           chunk: int = 200):
    """惰性产出未删除文档的 {id,title,content_text,blocks_json}。

    批量处理（批量纠错、批量替换）用它逐篇取正文：既不把全库正文一次性读进
    内存，也不必对每篇再发一次 get_document（N+1 查询）。
    category_id 走 idx_documents_cat，doc_ids 按主键分片参数化查询。
    """
    conn = dbconn.get_conn()
    cols = "id,title,content_text,blocks_json"
    if doc_ids is not None:
        ids = [int(i) for i in doc_ids]
        for i in range(0, len(ids), max(1, chunk)):
            part = ids[i:i + max(1, chunk)]
            marks = ",".join("?" * len(part))
            for row in conn.execute(
                    f"SELECT {cols} FROM documents WHERE deleted_time=''"
                    f" AND id IN ({marks})", part):
                yield dict(row)
        return
    if category_id is None:
        sql = f"SELECT {cols} FROM documents WHERE deleted_time='' ORDER BY id"
        params: tuple = ()
    else:
        sql = (f"SELECT {cols} FROM documents WHERE deleted_time=''"
               " AND category_id=? ORDER BY id")
        params = (category_id,)
    for row in conn.execute(sql, params):
        yield dict(row)


def rebuild_fts(progress_cb=None, incremental: bool = True) -> dict[str, int]:
    """重建 FTS 索引（documents/phrases）；默认**增量**。

    FTS 行由 DAO 在写入时同步维护，任何绕过 DAO 的写入（如种子 ATTACH 直插、
    手工改库）都会造成索引静默失配；此函数是用户可见的自救入口。
    返回各源重建条数（`documents` 为索引内文档总数）。

    `progress_cb(text)` 为可选阶段进度回调：万篇库重建要逐条分词，放在 UI
    线程上会整窗冻结。回调**只作观测**——它自己抛错绝不能中断重建
    （重建失败才是真故障，进度显示失败不是）。

    为什么是增量（P4）：全量重建要**整库重新分词**，而绝大多数文档自上次
    重建以来并没有改动。判据用 `documents.text_hash` 与 `fts_index_state`
    里记的上次分词依据比对：只有"新增/内容变了/索引里缺行"的文档才重新分词。

    ⚠ 为什么必须比对**内容哈希**、而不是"FTS 行是否存在"：文档改过之后 FTS
    行依然在（DAO 会就地替换），只查存在性会把改过的文档当成"已索引"——
    检索命中的仍是旧词，而这正是 rebuild 本该修好的问题。

    `incremental=False` 退化为全量重建（先清空两张索引表与状态表）：状态表被
    外部改坏时用它兜底 —— 自助入口必须能真的自救，不能只会增量。
    """
    def _tick(msg: str) -> None:
        if progress_cb is None:
            return
        try:
            progress_cb(msg)
        except Exception:
            pass

    conn = dbconn.get_conn()
    from . import tokenize as tok
    counts: dict[str, int] = {}
    # 回收站里的文档不进索引：否则重建一次就把软删除的材料全部变回可检索
    rows = conn.execute(
        "SELECT id, title, content_text, text_hash FROM documents"
        " WHERE deleted_time=''"
    ).fetchall()
    total = len(rows)

    if incremental:
        indexed = {int(r["ref_id"]) for r in conn.execute(
            "SELECT ref_id FROM documents_fts").fetchall()}
        state = {int(r["ref_id"]): (r["text_hash"] or "") for r in conn.execute(
            "SELECT ref_id, text_hash FROM fts_index_state"
            " WHERE kind='documents'").fetchall()}
    else:
        _tick("正在清空旧索引…")
        indexed, state = set(), {}
        conn.execute("DELETE FROM documents_fts")
        conn.execute("DELETE FROM fts_index_state WHERE kind='documents'")

    keep: set[int] = set()
    reused = 0
    _tick(f"正在重建资料索引（0/{total}）…")
    for i, r in enumerate(rows, 1):
        did = int(r["id"])
        keep.add(did)
        digest = r["text_hash"] or ""
        if digest and did in indexed and state.get(did) == digest:
            reused += 1          # 内容未变且索引在位：跳过——这就是"第二次很快"的来源
        else:
            conn.execute("DELETE FROM documents_fts WHERE ref_id=?", (did,))
            conn.execute(
                "INSERT INTO documents_fts(title,tokenized,ref_id) VALUES(?,?,?)",
                (tok.tokenize(r["title"]), tok.tokenize(r["content_text"]), did))
            conn.execute(
                "INSERT INTO fts_index_state(kind,ref_id,text_hash)"
                " VALUES('documents',?,?)"
                " ON CONFLICT(kind,ref_id) DO UPDATE SET text_hash=excluded.text_hash",
                (did, digest))
        if i % 50 == 0 or i == total:
            _tick(f"正在重建资料索引（{i}/{total}）…")
    # 清掉"已不在库中"（含被移入回收站）的文档残留行与状态
    for did in (indexed | set(state)) - keep:
        conn.execute("DELETE FROM documents_fts WHERE ref_id=?", (did,))
        conn.execute("DELETE FROM fts_index_state WHERE kind='documents' AND ref_id=?",
                     (did,))
    counts["documents"] = len(keep)
    counts["rescanned"] = total - reused

    # 句式索引条数少（人工维护），仍然整体重建，不必为它引入第二套状态
    _tick("正在重建句式索引…")
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


def all_dictionary_words() -> list[str]:
    """词典全部唯一词条（供重复字检测运行期派生词集合与叠字白名单）。

    只读查询；词典为空（如未导入种子的临时库）时返回空列表，
    调用方据此退化为"不报"（宁少报不误报）。

    ⚠️ 保留"全部词"的原语义：`scripts/e2e_check.py` 与
    `scripts/eval_corrector.py` 依赖它做全量断言。
    **纠错证据集请改用 `builtin_dictionary_words()`** —— 见其文档。
    """
    rows = dbconn.get_conn().execute(
        "SELECT DISTINCT word FROM dictionary").fetchall()
    return [r["word"] for r in rows if r["word"]]


# ------------------------------------------------- 词表来源分离（v5）
def user_wordlist_sources() -> set:
    """用户导入词表占用的全部 source 名（可能为空集）。

    这是"证据集 / 用户词集"分离的唯一判据来源，被 `repeat_rules` 在
    **每次派生缓存时**调用一次，不在热路径上。
    """
    try:
        rows = dbconn.get_conn().execute(
            "SELECT source FROM wordlist_sources").fetchall()
    except Exception:
        # 库还是 v4 结构（未迁移）时退化为"没有用户来源"：
        # 等价于旧行为，绝不能因此让重复字检测整个挂掉。
        return set()
    return {r["source"] for r in rows if r["source"]}


def builtin_dictionary_words() -> list[str]:
    """**证据集**：不含用户导入来源的词条，供 repeat_rules 作判词依据。

    为什么必须把用户来源排除在外：`repeat_rules` 拿这个集合回答
    "这段字是不是汉语里的真词"，而用户导入的行业术语属于**领域词汇**，
    不是关于汉语的证据。混进去会产生双向污染：
      · ②④⑦ 拿它放行 → 漏报增加；
      · ⑤b/⑤c 的左右证据与 cXc 的 `deletable` 也拿它判 → 导入常见二字词
        （如"会在"）会把**原本放行的正常语句翻成 0.85/0.5 误报**。
    用户词的保护交由 `user_dictionary_words()` 的"整段豁免"承担。
    """
    srcs = user_wordlist_sources()
    conn = dbconn.get_conn()
    if not srcs:
        rows = conn.execute("SELECT DISTINCT word FROM dictionary").fetchall()
    else:
        ph = ",".join("?" * len(srcs))
        rows = conn.execute(
            "SELECT DISTINCT word FROM dictionary "
            f"WHERE COALESCE(source,'') NOT IN ({ph})", tuple(srcs)).fetchall()
    return [r["word"] for r in rows if r["word"]]


def user_dictionary_words() -> list[str]:
    """**豁免集**：用户导入的词，**只**用于"整个同字连被该词覆盖 → 放行"。

    刻意不参与 ②④⑤⑦ 的通用放行与 cXc 的 `deletable` ——
    那些判据代表"汉语是否真词"，用户词不能作证。
    """
    srcs = user_wordlist_sources()
    if not srcs:
        return []
    ph = ",".join("?" * len(srcs))
    rows = dbconn.get_conn().execute(
        "SELECT DISTINCT word FROM dictionary "
        f"WHERE COALESCE(source,'') IN ({ph})", tuple(srcs)).fetchall()
    return [r["word"] for r in rows if r["word"]]


def list_wordlist_sources() -> list:
    """用户导入词表的登记清单（供 UI 与预检）。"""
    try:
        rows = dbconn.get_conn().execute(
            "SELECT * FROM wordlist_sources ORDER BY id").fetchall()
    except Exception:
        return []
    return [dict(r) for r in rows]


def upsert_wordlist_source(source: str, role: str = "pairs", label: str = "",
                           fmt: str = "", entry_count: int = 0) -> None:
    """登记/更新一个用户导入来源（同 source 覆盖，不产生重复登记）。"""
    from datetime import datetime
    conn = dbconn.get_conn()
    conn.execute(
        "INSERT INTO wordlist_sources(source,role,label,fmt,entry_count,"
        "imported_at,enabled) VALUES(?,?,?,?,?,?,1) "
        "ON CONFLICT(source) DO UPDATE SET role=excluded.role, "
        "label=excluded.label, fmt=excluded.fmt, "
        "entry_count=excluded.entry_count, imported_at=excluded.imported_at",
        (source, role, label, fmt, int(entry_count),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()


def set_wordlist_source_enabled(source: str, enabled: bool) -> int:
    """按来源整体启停：同步改登记表 + 该来源的纠错对 enabled。返回受影响对数。"""
    conn = dbconn.get_conn()
    conn.execute("UPDATE wordlist_sources SET enabled=? WHERE source=?",
                 (1 if enabled else 0, source))
    cur = conn.execute("UPDATE error_pairs SET enabled=? WHERE source=?",
                       (1 if enabled else 0, source))
    conn.commit()
    return int(cur.rowcount or 0)


def delete_wordlist_source(source: str) -> dict:
    """整体移除一个用户导入来源：同时清掉它的词条、纠错对与登记。"""
    conn = dbconn.get_conn()
    n_words = conn.execute("DELETE FROM dictionary WHERE source=?",
                           (source,)).rowcount or 0
    n_pairs = conn.execute("DELETE FROM error_pairs WHERE source=?",
                           (source,)).rowcount or 0
    conn.execute("DELETE FROM wordlist_sources WHERE source=?", (source,))
    conn.commit()
    return {"words": int(n_words), "pairs": int(n_pairs)}


def count_dictionary_by_source(source: str) -> int:
    row = dbconn.get_conn().execute(
        "SELECT count(*) AS n FROM dictionary WHERE COALESCE(source,'')=?",
        (source,)).fetchone()
    return int(row["n"]) if row else 0


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
            # 追加 deleted_time='' 是道保险：软删除时已摘掉 FTS 行，但外部改库
            # 或恢复旧备份可能留下残行，不能让回收站里的材料被检索命中
            sql = (f"SELECT f.ref_id AS ref_id, bm25({fts_table}) AS rank,"
                   f" d.title AS title,"
                   f" snippet({fts_table},1,'【','】','…',16) AS snip"
                   f" FROM {fts_table} f JOIN {src_table} d ON d.id=f.ref_id"
                   f" WHERE {fts_table} MATCH ? AND d.deleted_time=''"
                   f" ORDER BY rank LIMIT ?")
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


# ---------------------------------------------------------------- attachments
# 只管数据库行；附件文件的复制/删除/定位在 core.attachments（DAO 不做文件 IO）。
def add_attachment(doc_id: int, file_name: str, stored_path: str,
                   size: int = 0) -> int:
    """登记一条附件记录，返回附件 id。

    stored_path 传数据目录内的相对路径（core.attachments.add 已把文件复制进去）。
    """
    conn = dbconn.get_conn()
    cur = conn.execute(
        "INSERT INTO attachments(doc_id,file_name,stored_path,size,added_time)"
        " VALUES(?,?,?,?,?)",
        (int(doc_id), file_name, stored_path, int(size), now()))
    conn.commit()
    return int(cur.lastrowid)


def get_attachment(attachment_id: int) -> Attachment | None:
    row = dbconn.get_conn().execute(
        "SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
    return Attachment(**dict(row)) if row else None


def list_attachments(doc_id: int) -> list[Attachment]:
    """某文档的附件，按添加先后（走 idx_attachments_doc）。"""
    rows = dbconn.get_conn().execute(
        "SELECT * FROM attachments WHERE doc_id=? ORDER BY id", (doc_id,)).fetchall()
    return [Attachment(**dict(r)) for r in rows]


def list_all_attachments(limit: int = 100000) -> list[Attachment]:
    """全库附件记录（维护用：清理无人引用的孤儿文件）。"""
    rows = dbconn.get_conn().execute(
        "SELECT * FROM attachments ORDER BY id LIMIT ?", (limit,)).fetchall()
    return [Attachment(**dict(r)) for r in rows]


def delete_attachment(attachment_id: int) -> None:
    """删除单条附件记录（磁盘文件由 core.attachments.remove 先删）。"""
    conn = dbconn.get_conn()
    conn.execute("DELETE FROM attachments WHERE id=?", (attachment_id,))
    conn.commit()


def delete_attachments_of(doc_id: int) -> int:
    """删除某文档的全部附件记录，返回条数。"""
    conn = dbconn.get_conn()
    cur = conn.execute("DELETE FROM attachments WHERE doc_id=?", (doc_id,))
    conn.commit()
    return int(cur.rowcount)


def count_attachments(doc_id: int | None = None) -> int:
    conn = dbconn.get_conn()
    if doc_id is None:
        return int(conn.execute("SELECT count(*) FROM attachments").fetchone()[0])
    return int(conn.execute(
        "SELECT count(*) FROM attachments WHERE doc_id=?", (doc_id,)).fetchone()[0])


def attachment_counts(doc_ids: list[int]) -> dict[int, int]:
    """多篇文档各自的附件数：单条 IN 查询搞定，避免逐篇 count 的 N+1。"""
    ids = [int(i) for i in doc_ids]
    if not ids:
        return {}
    conn = dbconn.get_conn()
    out: dict[int, int] = {}
    chunk = 400
    for i in range(0, len(ids), chunk):
        part = ids[i:i + chunk]
        marks = ",".join("?" * len(part))
        rows = conn.execute(
            f"SELECT doc_id, count(*) AS n FROM attachments"
            f" WHERE doc_id IN ({marks}) GROUP BY doc_id", part).fetchall()
        for r in rows:
            out[int(r["doc_id"])] = int(r["n"])
    return out


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


# ==================================================== 收文登记台账（receive）
# 命名与 dispatch 一组保持对称：add/update/delete/get/list/count/stats/monthly。
_RECEIVE_COLUMNS = (
    "reg_no", "incoming_no", "title", "doc_type", "from_org", "main_send",
    "cc", "secret_level", "urgency", "receive_date", "doc_date", "pages",
    "copies", "propose", "instruction", "handler_dept", "handler",
    "due_date", "done_date", "result", "status", "archive_no", "retention",
    "archive_date", "doc_id", "remark",
)
# 统计分组列白名单：与 dispatch 同理，绝不把调用方传入的字符串直接拼进 SQL
_RECEIVE_GROUPABLE = ("doc_type", "from_org", "status", "secret_level",
                      "urgency", "handler_dept", "handler", "retention")


def add_receive(r: Receive) -> int:
    conn = dbconn.get_conn()
    cols = ",".join(_RECEIVE_COLUMNS)
    marks = ",".join("?" * len(_RECEIVE_COLUMNS))
    cur = conn.execute(
        f"INSERT INTO receive_register({cols}) VALUES({marks})",
        [getattr(r, c) for c in _RECEIVE_COLUMNS])
    conn.commit()
    return int(cur.lastrowid)


def update_receive(r: Receive) -> None:
    conn = dbconn.get_conn()
    assigns = ",".join(f"{c}=?" for c in _RECEIVE_COLUMNS)
    conn.execute(
        f"UPDATE receive_register SET {assigns},"
        f"updated_time=datetime('now','localtime') WHERE id=?",
        [getattr(r, c) for c in _RECEIVE_COLUMNS] + [r.id])
    conn.commit()


def update_receive_many(rows: list[Receive]) -> int:
    """批量更新收文登记：**单事务**提交，返回更新条数。

    与 `update_receive` 的差别只在提交粒度。逐行 commit 有两个实际问题：

      1. 千件级别退化成千次 fsync（归档动辄几百上千件）；
      2. 中途失败会留下"一半已归档"的台账 —— 而归档是**整批成立**的语义，
         移交清单上写着 50 件、台账却只落了 30 件，比整批失败难收拾得多。

    失败时显式回滚后再抛：调用方拿到异常就知道"一件都没归档"，而不是
    "不知道落了多少"。注意本函数只在**同一线程**的连接上工作（与
    `dbconn.get_conn()` 的 thread-local 语义一致）。
    """
    if not rows:
        return 0
    conn = dbconn.get_conn()
    assigns = ",".join(f"{c}=?" for c in _RECEIVE_COLUMNS)
    sql = (f"UPDATE receive_register SET {assigns},"
           f"updated_time=datetime('now','localtime') WHERE id=?")
    params = [[getattr(r, c) for c in _RECEIVE_COLUMNS] + [r.id] for r in rows]
    try:
        conn.executemany(sql, params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(rows)


def delete_receive(receive_id: int) -> None:
    conn = dbconn.get_conn()
    conn.execute("DELETE FROM receive_register WHERE id=?", (receive_id,))
    conn.commit()


def get_receive(receive_id: int) -> Receive | None:
    conn = dbconn.get_conn()
    row = conn.execute("SELECT * FROM receive_register WHERE id=?",
                       (receive_id,)).fetchone()
    return Receive(**dict(row)) if row else None


def list_receive(keyword: str = "", from_org: str = "", doc_type: str = "",
                 status: str = "", year: str = "", date_from: str = "",
                 date_to: str = "", limit: int = 5000) -> list[Receive]:
    """按条件筛选收文登记，收到日期倒序（同日按 id 倒序）。"""
    where: list[str] = []
    params: list = []
    if keyword.strip():
        # 收文检索最常用的是来文机关与来文字号，必须纳入关键字匹配
        kw_cols = ("title", "incoming_no", "reg_no", "from_org", "main_send",
                   "cc", "propose", "instruction", "handler", "handler_dept",
                   "remark")
        where.append("(" + " OR ".join(f"{c} LIKE ?" for c in kw_cols) + ")")
        like = f"%{keyword.strip()}%"
        params += [like] * len(kw_cols)
    if from_org:
        where.append("from_org=?")
        params.append(from_org)
    if doc_type:
        where.append("doc_type=?")
        params.append(doc_type)
    if status:
        where.append("status=?")
        params.append(status)
    if year:
        # 年份既可能来自收到日期，也可能来自来文字号里的〔2026〕
        where.append("(receive_date LIKE ? OR incoming_no LIKE ?)")
        params += [f"{year}%", f"%〔{year}〕%"]
    if date_from:
        where.append("receive_date>=?")
        params.append(date_from)
    if date_to:
        where.append("receive_date<=?")
        params.append(date_to)
    sql = "SELECT * FROM receive_register"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY receive_date DESC, id DESC LIMIT ?"
    params.append(limit)
    conn = dbconn.get_conn()
    rows = conn.execute(sql, params).fetchall()
    return [Receive(**dict(r)) for r in rows]


def count_receive() -> int:
    conn = dbconn.get_conn()
    return int(conn.execute("SELECT count(*) FROM receive_register").fetchone()[0])


def receive_stats(group_by: str = "from_org", year: str = "") -> list[tuple[str, int]]:
    """按指定列聚合计数，返回 [(取值, 件数)]，件数倒序。空值归入「未填写」。"""
    if group_by not in _RECEIVE_GROUPABLE:
        raise ValueError(f"不支持的分组列：{group_by}")
    conn = dbconn.get_conn()
    sql = (f"SELECT COALESCE(NULLIF({group_by},''),'未填写') AS k, count(*) AS n"
           f" FROM receive_register")
    params: list = []
    if year:
        sql += " WHERE receive_date LIKE ? OR incoming_no LIKE ?"
        params += [f"{year}%", f"%〔{year}〕%"]
    sql += " GROUP BY k ORDER BY n DESC, k"
    return [(r["k"], int(r["n"])) for r in conn.execute(sql, params).fetchall()]


def receive_monthly_counts(year: str) -> list[tuple[str, int]]:
    """某年 1-12 月各月收文件数（按收到日期）。"""
    conn = dbconn.get_conn()
    rows = conn.execute(
        "SELECT substr(receive_date,6,2) AS m, count(*) AS n FROM receive_register"
        " WHERE receive_date LIKE ? GROUP BY m", (f"{year}%",)).fetchall()
    got = {r["m"]: int(r["n"]) for r in rows if r["m"]}
    return [(f"{int(m):02d}月", got.get(m, 0)) for m in
            [f"{i:02d}" for i in range(1, 13)]]


def receive_years() -> list[str]:
    """台账中出现过的年度（由收到日期推导），倒序。"""
    conn = dbconn.get_conn()
    rows = conn.execute(
        "SELECT DISTINCT substr(receive_date,1,4) AS y FROM receive_register"
        " WHERE length(receive_date)>=4 ORDER BY y DESC").fetchall()
    return [r["y"] for r in rows if r["y"] and str(r["y"]).isdigit()]


def max_reg_no_serial(prefix: str, year: str) -> int:
    """取某前缀某年度已用的最大收文登记号序号，无记录返回 0。

    与 max_doc_no_serial 对称，但**独立计数**——收文与发文是两本账。
    """
    conn = dbconn.get_conn()
    rows = conn.execute(
        "SELECT reg_no FROM receive_register WHERE reg_no LIKE ?",
        (f"{prefix}%〔{year}〕%",)).fetchall()
    best = 0
    for r in rows:
        m = re.search(r"〕\s*(\d+)\s*号", r["reg_no"] or "")
        if m:
            best = max(best, int(m.group(1)))
    return best


def pending_receive(due_within_days: int = 2, today: str = "") -> list[Receive]:
    """未办结且即将到期或已逾期的收文（供 A2 督办提醒）。

    只认"填了应办结日期、且尚未办结"的记录——没有期限的收文不该被算法
    替用户判定为"该催办了"，那只会制造噪音。按应办结日期升序（最急在前）。
    """
    import datetime as _dt

    base = today or _dt.date.today().isoformat()
    try:
        edge = (_dt.date.fromisoformat(base)
                + _dt.timedelta(days=int(due_within_days))).isoformat()
    except (TypeError, ValueError):
        return []
    conn = dbconn.get_conn()
    rows = conn.execute(
        "SELECT * FROM receive_register"
        " WHERE due_date <> '' AND done_date = ''"
        "   AND status NOT IN ('已办结','已归档')"
        "   AND due_date <= ?"
        " ORDER BY due_date ASC, id ASC", (edge,)).fetchall()
    return [Receive(**dict(r)) for r in rows]
