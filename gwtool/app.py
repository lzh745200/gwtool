# -*- coding: utf-8 -*-
"""应用装配：数据库初始化 + 种子数据导入 + 主窗口启动。"""
from __future__ import annotations

import sys

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QApplication

from . import APP_NAME, __version__
from .paths import bundled_db_seed_path, db_path


def ensure_database_seeded() -> None:
    """首次启动：从随包 seed.db 导入词典与纠错库，并写入默认模板。

    使用当前线程的数据库连接（run() 已 configure；测试可指向临时库）。
    词典不做 FTS 预分词（12万条会拖慢首启动），检索走 word 列 LIKE。
    """
    from .db import connection as dbconn
    from .db.schema import init_schema

    conn = dbconn.get_conn()
    init_schema(conn)

    seeded = conn.execute(
        "SELECT value FROM settings WHERE key='seeded_version'").fetchone()
    seed_file = bundled_db_seed_path()
    if seeded is None and seed_file.exists():
        cur = conn.cursor()
        cur.execute("ATTACH DATABASE ? AS seed", (str(seed_file),))
        cur.execute("INSERT OR IGNORE INTO error_pairs(wrong,correct,category,"
                    "confidence,enabled,source)"
                    " SELECT wrong,correct,category,confidence,enabled,source"
                    " FROM seed.error_pairs")
        cur.execute("INSERT OR IGNORE INTO dictionary(word,pinyin,definition,"
                    "example,source)"
                    " SELECT word,pinyin,definition,example,source"
                    " FROM seed.dictionary")
        conn.commit()          # 事务内不能 DETACH，先提交
        cur.execute("DETACH DATABASE seed")

    # 迁移：早期生成的混淆对置信度统一为 0.55（配合词边界保护，降低误报干扰）
    if conn.execute("SELECT value FROM settings WHERE key='generated_conf_v2'").fetchone() is None:
        conn.execute("UPDATE error_pairs SET confidence=0.55 "
                     "WHERE source='generated' AND confidence>=0.6")
        conn.execute("INSERT OR REPLACE INTO settings(key,value) "
                     "VALUES('generated_conf_v2','1')")
        conn.commit()

    # 默认模板：绑定"首启未完成"标记而非上面的迁移标记——
    # 若模板写入失败，seeded_version 未落库，下次启动可重试
    if conn.execute("SELECT value FROM settings WHERE key='seeded_version'").fetchone() is None:
        from .core.template import default_template
        from .db import dao
        tpl = default_template()
        dao.save_template(tpl.name, tpl.to_json(), is_default=True)
        dao.set_setting("seeded_version", __version__)


def run(import_path: str = "") -> int:
    # 高分屏适配
    sys.argv += ["-platform", "windows:darkmode=0"] if sys.platform == "win32" else []
    QApplication.setApplicationName(APP_NAME)
    app = QApplication.instance() or QApplication(sys.argv)

    from .db import connection as dbconn
    dbconn.configure(db_path())
    ensure_database_seeded()

    from .ui.main_window import MainWindow
    # 口令锁（启用后启动先解锁）
    from .db import dao
    if dao.get_setting("lock_enabled") == "1":
        from .core.security import has_password
        if has_password():
            from .ui.lock_dialog import LockDialog
            dlg = LockDialog()
            if dlg.exec() != dlg.Accepted:
                return 0

    win = MainWindow()
    win.show()
    if import_path:
        win.import_external_file(import_path)
    return app.exec()
