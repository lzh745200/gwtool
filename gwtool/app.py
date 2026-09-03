# -*- coding: utf-8 -*-
"""应用装配：数据库初始化 + 种子数据导入 + 主窗口启动。"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QApplication, QDialog

from . import APP_NAME, __version__
# 顶层导入：这两个模块平时只在 UI 回调里延迟导入（report 用于体检报告导出、
# classify 用于分类建议），冻结包若漏收它们，用户点按钮时才会崩。放到启动路径上，
# CI 每次构建的启动冒烟即可覆盖。
from .core import classify, report  # noqa: F401
from .paths import bundled_db_seed_path, db_path
from .ui.feature_dialogs import LockDialog  # noqa: F401  口令锁解锁框（顶层导入防路径拼写错误）

# 麒麟/Ubuntu 下补齐 Qt6 xcb 平台插件所需的系统运行库
XCB_HINT = ("sudo apt-get install -y libxcb-cursor0 libxcb-icccm4 "
            "libxcb-image0 libxcb-keysyms1 libxcb-render-util0 "
            "libxcb-xinerama0 libxkbcommon-x11-0 libgl1 libegl1")


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


def _follow_system_theme() -> bool:
    """直接只读查询设置（QApplication 构造前需确定 darkmode 参数）。"""
    try:
        import sqlite3
        from .paths import db_path
        p = db_path()
        if not p.exists():
            return False
        conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=3)
        row = conn.execute(
            "SELECT value FROM settings WHERE key='follow_system_theme'").fetchone()
        conn.close()
        return row is not None and row[0] == "1"
    except Exception:
        return False


def _launch_qapp(argv: list[str]) -> "QApplication | None":
    """构造 QApplication；失败（平台插件初始化异常等）时输出可操作的诊断。"""
    try:
        return QApplication(argv)
    except Exception as exc:  # 打包版双击启动时错误不可见，落到日志
        lines = ["启动失败：Qt 图形环境初始化异常。", f"原始错误：{exc}"]
        if "xcb" in str(exc).lower():
            lines += ["通常是缺少系统运行库，麒麟/Ubuntu 下执行：", "  " + XCB_HINT]
        text = "\n".join(lines)
        print(text, file=sys.stderr)
        try:
            (Path.home() / "gwtool_启动诊断.log").write_text(
                text + "\n", encoding="utf-8")
        except OSError:
            pass
        return None


def _pass_lock() -> bool:
    """启动口令锁：未启用、未设口令或解锁成功时返回 True。"""
    from .db import dao
    if dao.get_setting("lock_enabled") != "1":
        return True
    from .core.security import has_password
    if not has_password():
        return True
    dlg = LockDialog()
    return dlg.exec() == QDialog.DialogCode.Accepted


def run(import_path: str = "") -> int:
    # 高分屏适配：默认强制浅色；设置「跟随系统深浅色」后交由系统决定
    if sys.platform == "win32" and not _follow_system_theme():
        sys.argv += ["-platform", "windows:darkmode=0"]
    QApplication.setApplicationName(APP_NAME)
    app = QApplication.instance() or _launch_qapp(sys.argv)
    if app is None:
        return 1

    # 零中文字体的机器上，界面与 PDF 的中文都会渲染成空白：启动时注入兜底字体
    from .core.pdfrender import ensure_cjk_font
    ensure_cjk_font()

    from .db import connection as dbconn
    dbconn.configure(db_path())
    ensure_database_seeded()

    from .ui.main_window import MainWindow
    # 口令锁（启用后启动先解锁）
    if not _pass_lock():
        return 0

    win = MainWindow()
    win.show()
    if import_path:
        win.import_external_file(import_path)
    return app.exec()
