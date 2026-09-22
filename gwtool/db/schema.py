# -*- coding: utf-8 -*-
"""SQLite 表结构与迁移。

设计要点：
  - 全部业务数据存于单库 gwtool.db；
  - 中文全文检索使用 FTS5，中文文本先经 jieba 预分词、以空格连接后写入
    （FTS5 默认 unicode61 分词器按空格切词，预分词即可命中中文）；
  - FTS 表由 DAO 在 Python 侧同步维护（分词无法在触发器内完成）。
"""
from __future__ import annotations

import re
import sqlite3

from .. import logs

# 版本 4：新增 receive_register（收文登记台账）。
# 该表由 TABLES 里的 CREATE TABLE IF NOT EXISTS 建立（老库同样会补上），
# 故 MIGRATIONS[4] 无需任何语句——版本号只用于记录结构代数。
#
# 版本 5：新增 wordlist_sources（用户导入词表的来源登记）。
# 同样是**整张新表**，由 TABLES 的 CREATE TABLE IF NOT EXISTS 覆盖老库，
# 故 MIGRATIONS[5] 同样无需语句。新增它要解决的是一个真实缺陷：
#   `dictionary` 表被 repeat_rules 当作"这段字是不是汉语真词"的**证据集**，
#   而用户导入的行业术语属于"领域词汇"、不是关于汉语的证据。两者混在一起
#   会让常见二字词（如"会在"）成为 ⑤/cXc 的证据，把**原本放行的正常语句
#   翻成 0.85/0.5 误报**。wordlist_sources 记录"哪些 source 是用户导入的"，
#   供 repeat_rules 把证据集与用户词集**分开取用**。
#
# 版本 6：新增 fts_index_state（增量重建全文索引的状态表，P4）。
# 同样由 TABLES 建立（老库自动补上），故 MIGRATIONS[6] 也为空。
SCHEMA_VERSION = 6

# 版本化迁移：键 = 目标版本号。老库按 user_version 逐版本升级。
#
# 只放"补列/改结构"的 ALTER——**不要把 CREATE INDEX 写在这里**：
# 全部二级索引由 init_schema 在迁移之后统一跑一遍 INDEXES（见该列表的注释），
# 迁移里再写一遍就是同一索引定义两处维护，改动时极易漏掉一处。
# （历史遗留：MIGRATIONS[2]/[3] 曾各自重复定义了一次
#  idx_documents_simhash / idx_documents_deleted，已于 2026-09 清理。）
#
# 语句要求幂等容错：新库建表已含新列时 ALTER 会报 duplicate column，
# 由调用方（init_schema）忽略。
MIGRATIONS = {
    2: [
        "ALTER TABLE documents ADD COLUMN simhash INTEGER",
    ],
    # v3：回收站（软删除）。新列两处都要写 —— TABLES 的 CREATE TABLE 供全新库，
    # 这里供老库升级（TABLES 循环不容错，老库缺列会当场崩启动）。
    3: [
        "ALTER TABLE documents ADD COLUMN deleted_time TEXT NOT NULL DEFAULT ''",
    ],
}

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
        updated_time TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        simhash INTEGER,
        deleted_time TEXT NOT NULL DEFAULT ''
    )""",
    # 注意：二级索引一律放 INDEXES（依赖 text_hash/category_id/deleted_time 等列）。
    # 深探确认（2026-09-13）：TABLES 在迁移之前执行且**不容错**，老库此时还没有
    # 新列，在这里建索引会抛 "no such column: deleted_time"，用户升级即启动崩溃。
    # 全部二级索引已统一挪到 INDEXES、在迁移之后执行——本列表只留 CREATE TABLE。
    # 词典（词条、拼音、释义、例句）
    """CREATE TABLE IF NOT EXISTS dictionary(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        word TEXT NOT NULL,
        pinyin TEXT DEFAULT '',
        definition TEXT DEFAULT '',
        example TEXT DEFAULT '',
        source TEXT DEFAULT 'builtin'
    )""",
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
    # 用户句式/常用语/范文片段
    """CREATE TABLE IF NOT EXISTS user_phrases(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phrase TEXT NOT NULL,
        context TEXT DEFAULT '',
        source TEXT DEFAULT 'user',
        tag TEXT DEFAULT ''
    )""",
    # 用户导入词表的来源登记（v5）
    #
    # 为什么单独一张表、而不是给 dictionary 加一列：同一份词表会**同时**落到
    # dictionary（词）与 error_pairs（纠错对/术语），需要一处统一的"这批东西
    # 是用户导入的、角色是什么、来自哪个文件"的登记；且 repeat_rules 必须能
    # 一次问出"哪些 source 属于用户导入"，避免每行都去判断。
    #
    # role 取值：
    #   pairs   纠错对（错→对）
    #   terms   行业术语（异名→规范名，展平后同样落 error_pairs）
    #   protect 保护词（防误纠 + 检索 + 重复字豁免）
    """CREATE TABLE IF NOT EXISTS wordlist_sources(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL UNIQUE,
        role TEXT NOT NULL DEFAULT 'pairs',
        label TEXT DEFAULT '',
        fmt TEXT DEFAULT '',
        entry_count INTEGER NOT NULL DEFAULT 0,
        imported_at TEXT DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1
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
    # 纠错持久忽略名单
    """CREATE TABLE IF NOT EXISTS ignore_words(
        word TEXT PRIMARY KEY,
        note TEXT DEFAULT ''
    )""",
    # 发文登记台账：公文发出后的登记、查询与统计
    """CREATE TABLE IF NOT EXISTS dispatch_register(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_no TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        doc_type TEXT DEFAULT '',
        org TEXT DEFAULT '',
        main_send TEXT DEFAULT '',
        cc TEXT DEFAULT '',
        secret_level TEXT DEFAULT '公开',
        urgency TEXT DEFAULT '',
        sign_date TEXT DEFAULT '',
        print_date TEXT DEFAULT '',
        pages INTEGER NOT NULL DEFAULT 0,
        copies INTEGER NOT NULL DEFAULT 0,
        drafter TEXT DEFAULT '',
        reviewer TEXT DEFAULT '',
        approver TEXT DEFAULT '',
        status TEXT DEFAULT '拟稿',
        doc_id INTEGER NOT NULL DEFAULT 0,
        remark TEXT DEFAULT '',
        created_time TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        updated_time TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )""",
    # 文档附件：文件本体复制进数据目录 attachments/ 子目录，库里只存相对路径
    # （便携模式与备份恢复后仍能按数据目录重新定位，不依赖用户原始绝对路径）
    """CREATE TABLE IF NOT EXISTS attachments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_id INTEGER NOT NULL DEFAULT 0,
        file_name TEXT NOT NULL DEFAULT '',
        stored_path TEXT NOT NULL DEFAULT '',
        size INTEGER NOT NULL DEFAULT 0,
        added_time TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )""",
    # 收文登记台账：来文的签收、拟办、批办、承办、办结与归档。
    # 此前只有 dispatch_register（发文），公文实务的"进"这一半完全缺失。
    #
    # 字段一次给全（含办理时限与归档所需的列），不留给后续版本逐个 ALTER：
    # 每次版本迁移都要走"迁移前自动备份"，对用户是可见的开销与风险，
    # 能一次到位的就一次到位。
    """CREATE TABLE IF NOT EXISTS receive_register(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reg_no TEXT NOT NULL DEFAULT '',
        incoming_no TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        doc_type TEXT DEFAULT '',
        from_org TEXT DEFAULT '',
        main_send TEXT DEFAULT '',
        cc TEXT DEFAULT '',
        secret_level TEXT DEFAULT '公开',
        urgency TEXT DEFAULT '',
        receive_date TEXT DEFAULT '',
        doc_date TEXT DEFAULT '',
        pages INTEGER NOT NULL DEFAULT 0,
        copies INTEGER NOT NULL DEFAULT 0,
        propose TEXT DEFAULT '',
        instruction TEXT DEFAULT '',
        handler_dept TEXT DEFAULT '',
        handler TEXT DEFAULT '',
        due_date TEXT DEFAULT '',
        done_date TEXT DEFAULT '',
        result TEXT DEFAULT '',
        status TEXT DEFAULT '签收',
        archive_no TEXT DEFAULT '',
        retention TEXT DEFAULT '',
        archive_date TEXT DEFAULT '',
        doc_id INTEGER NOT NULL DEFAULT 0,
        remark TEXT DEFAULT '',
        created_time TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        updated_time TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )""",
    # v6：增量重建索引用的状态表（P4）。记"某个 ref_id 上次是按哪份内容
    # （documents.text_hash）分词的"，重建时据此跳过未改动的文档。
    # 只由 dao.rebuild_fts 维护；DAO 的日常写入不更新它 —— 那正是"改过的文档
    # 哈希对不上、必须重分词"的判据来源。
    """CREATE TABLE IF NOT EXISTS fts_index_state(
        kind TEXT NOT NULL DEFAULT 'documents',
        ref_id INTEGER NOT NULL,
        text_hash TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(kind, ref_id)
    )""",
]

# 二级索引：统一在"建表+迁移"之后创建。
# 索引依赖的列可能来自新表结构，也可能来自 MIGRATIONS 补的列——放在迁移之后
# 两种情况都已就位，杜绝老库升级时 "no such column" 直接崩启动。
INDEXES = [
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_hash ON documents(text_hash)""",
    """CREATE INDEX IF NOT EXISTS idx_documents_cat ON documents(category_id)""",
    """CREATE INDEX IF NOT EXISTS idx_documents_simhash ON documents(simhash)""",
    """CREATE INDEX IF NOT EXISTS idx_documents_deleted ON documents(deleted_time)""",
    """CREATE INDEX IF NOT EXISTS idx_dictionary_word ON dictionary(word)""",
    # 按来源取词（user_terms / 证据集分离、按来源启停）需要它。
    # ⚠️ 刻意**不**给 dictionary(word) 加唯一索引：老库里可能已有重复行
    # （旧版 UI 导入不去重），加唯一索引会当场建索引失败、升级即崩。
    # 去重放在应用层（core/wordlist.py 的预检阶段）。
    """CREATE INDEX IF NOT EXISTS idx_dictionary_word_source ON dictionary(word, source)""",
    """CREATE INDEX IF NOT EXISTS idx_wordlist_role ON wordlist_sources(role, enabled)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_error_pairs_key ON error_pairs(wrong, correct)""",
    """CREATE INDEX IF NOT EXISTS idx_snapshots_doc ON snapshots(doc_id, id DESC)""",
    """CREATE INDEX IF NOT EXISTS idx_dispatch_no ON dispatch_register(doc_no)""",
    """CREATE INDEX IF NOT EXISTS idx_dispatch_sign_date ON dispatch_register(sign_date)""",
    """CREATE INDEX IF NOT EXISTS idx_dispatch_org ON dispatch_register(org)""",
    """CREATE INDEX IF NOT EXISTS idx_attachments_doc ON attachments(doc_id, id)""",
    """CREATE INDEX IF NOT EXISTS idx_receive_no ON receive_register(incoming_no)""",
    """CREATE INDEX IF NOT EXISTS idx_receive_date ON receive_register(receive_date)""",
    """CREATE INDEX IF NOT EXISTS idx_receive_org ON receive_register(from_org)""",
    """CREATE INDEX IF NOT EXISTS idx_receive_due ON receive_register(due_date)""",
]

# 核心业务表：这四张缺任意一张，就判定"不是本程序创建的库"。
#
# 与 `required_table_names()`（由 TABLES 推导的**全部**业务表）的区别至关重要：
# 备份恢复时，**只有核心表缺失才该拒绝**。其余表是后续版本陆续新增的，
# 老备份缺它们属正常版本差异——若一并拒绝，用户升级后就**打不开自己的旧备份**。
# 这正是"新增一张表"最容易踩到的向后兼容陷阱。
CORE_TABLES = ("documents", "settings", "dictionary", "error_pairs")

# FTS5 虚拟表：ref_id 指向源表 id；tokenized 为 jieba 分词后文本
FTS_TABLES = [
    """CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
        title, tokenized, ref_id UNINDEXED)""",
    """CREATE VIRTUAL TABLE IF NOT EXISTS dictionary_fts USING fts5(
        word, tokenized, ref_id UNINDEXED)""",
    """CREATE VIRTUAL TABLE IF NOT EXISTS phrases_fts USING fts5(
        phrase, tokenized, ref_id UNINDEXED)""",
]

# 必需业务表清单：从 TABLES 的 DDL 里解析，避免与建表语句两处维护、日久漂移。
# 备份恢复校验按它判断"包里的库是不是本程序的库"（见 core/backup._validate_restored_db）。
_TABLE_NAME_RE = re.compile(
    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE)


def required_table_names() -> set[str]:
    """本程序必需的业务表名集合（由 TABLES 声明推导）。"""
    names: set[str] = set()
    for ddl in TABLES:
        m = _TABLE_NAME_RE.search(ddl)
        if m:
            names.add(m.group(1))
    return names


def core_table_names() -> tuple[str, ...]:
    """核心业务表名（备份恢复时判定"是不是本程序的库"就靠这几张）。"""
    return CORE_TABLES


class SchemaTooNewError(RuntimeError):
    """库由**更新版本**的程序创建（`user_version > SCHEMA_VERSION`）。

    为什么必须明确拒绝、而不是"顺手把版本号改回自己能识别的值"：那个库可能
    含本版本不认识的列、索引与触发器，降版本号只是把事实掩盖起来 —— 之后
    任何按老结构写回的操作都可能**破坏用户数据**，而用户看到的却是一切正常。

    宁可拒绝启动：用户至少知道该用哪个版本的程序打开，数据完好无损。
    """

    def __init__(self, db_version: int, app_version: int):
        self.db_version = db_version
        self.app_version = app_version
        super().__init__(
            f"数据库由更新版本的程序创建（库结构 v{db_version}，"
            f"本程序支持到 v{app_version}）。\n"
            "为避免损坏数据，本程序不会打开或改写它。\n\n"
            "怎么办（任选其一）：\n"
            "  1) 改用创建该库的那个程序（更新版本）打开；\n"
            "  2) 用本版本程序打开另一个数据目录（便携模式 --portable），"
            "再从备份包还原一份本版本可用的数据；\n"
            "  3) 确认这份库不需要后，把它移走再启动本程序（会新建空库）。")


class MigrationFailedError(RuntimeError):
    """迁移语句执行失败，本次初始化已**整批回滚**。

    关键点：`user_version` 只在全部迁移成功后写入。老实现会把失败语句
    记一条 warning 后继续，最后仍把版本号写成 `SCHEMA_VERSION` —— 于是
    "迁移失败的库"与"迁移成功的库"对程序完全一样，下一版迁移不会再跑，
    缺失的列会一直缺下去。
    """

    def __init__(self, version: int, stmt: str, cause: BaseException):
        self.version = version
        self.statement = stmt
        self.cause = cause
        super().__init__(
            f"数据库迁移 v{version} 执行失败，已回滚到迁移前状态"
            f"（版本号保持原值）。\n失败语句：{stmt}\n原因：{cause}")


def init_schema(conn: sqlite3.Connection) -> None:
    """建库建表并执行版本化迁移；设置用户版本号以支持后续迁移。

    执行顺序（深探修复后固化，勿再调换）：
      1. TABLES   —— 纯建表（新库建新结构，老库整表跳过）；
      2. MIGRATIONS —— 老库逐版本补列；
      3. INDEXES  —— 全部二级索引（此时新列两种来源都已就位）；
      4. FTS_TABLES —— 全文检索虚表。

    事务边界（D5）：
      · 整段初始化放在**单个事务**里（含建表、迁移、索引、虚表），
        任一真故障即整批回滚 —— 不留"一半迁移完"的库；
      · `user_version` **只在全部成功后**写入，失败时保持原值，
        下次启动会重新尝试；
      · 库版本高于本程序时直接拒绝（SchemaTooNewError），不降版本号。
    """
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    old_ver = cur.execute("PRAGMA user_version").fetchone()[0]
    if old_ver > SCHEMA_VERSION:
        raise SchemaTooNewError(old_ver, SCHEMA_VERSION)
    # 显式开启事务：Python sqlite3 的 legacy 模式**只为 DML 隐式开事务**，
    # 而这里全是 DDL —— 不显式 BEGIN 的话，每条 DDL 各自 autocommit，
    # "整批回滚"就无从谈起（SQLite 本身支持事务化 DDL，是驱动层没开）。
    owns_txn = not conn.in_transaction
    if owns_txn:
        conn.execute("BEGIN")
    try:
        for ddl in TABLES:
            cur.execute(ddl)
        # 老库逐版本迁移；新库建表已含新列时 ALTER 重复报错属预期，忽略
        for v in range(old_ver + 1, SCHEMA_VERSION + 1):
            for stmt in MIGRATIONS.get(v, []):
                try:
                    cur.execute(stmt)
                except sqlite3.OperationalError as exc:
                    # "duplicate column name" 是预期分支（新库建表时已含该列）。
                    # 其他 OperationalError（语法错、库损坏、锁冲突）是真故障：
                    # 必须抛 —— 老实现只记一条 warning 就继续，最后仍把版本号
                    # 顶格写成最新，那个库从此再也补不上缺的列。
                    if "duplicate column" in str(exc).lower():
                        continue
                    logs.get_logger("db").error(
                        "迁移 v%d 语句失败，整批回滚：%s", v, exc)
                    raise MigrationFailedError(v, stmt, exc) from exc
        for ddl in INDEXES:
            cur.execute(ddl)
        for ddl in FTS_TABLES:
            cur.execute(ddl)
        cur.execute("PRAGMA user_version=%d" % SCHEMA_VERSION)
    except Exception:
        if owns_txn:
            try:
                conn.rollback()
            except sqlite3.Error:      # 回滚自身失败不能顶替原始异常
                pass
        raise
    conn.commit()
