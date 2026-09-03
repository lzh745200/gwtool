# -*- coding: utf-8 -*-
"""备份的附件体积上限：手动/自动分档、超限不静默丢数据、旧格式包仍可恢复。

背景：附件功能让备份包从「几百 KB 的库」变成「库 + 全部附件」，而退出时的自动
备份无条件触发、轮转又保留 20 份 → 关程序越来越卡、备份目录 20 × 全量附件。
这里守住三条底线：
  1. 有上限（可配置），退出路径的自动备份档更小；
  2. 超限的附件必须被记录（包内清单 + 恢复时明确告知 + 自动备份写日志）；
  3. 旧格式备份包（没有附件清单，甚至没有 manifest.json）照样能恢复。
体积断言一律用临时目录里的真实大文件（随机内容，压不动），不用 mock 糊过去。
"""
import json
import os
import zipfile
from pathlib import Path

import pytest

from gwtool import paths
from gwtool.core import attachments, backup
from gwtool.db import connection as dbconn
from gwtool.db import dao

MB = 1024 * 1024


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """把数据目录指到临时目录：备份/附件/日志都不能碰真实的 %APPDATA%/gwtool。"""
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paths, "app_data_dir", lambda: d)
    return d


@pytest.fixture()
def doc(tmp_db):
    return dao.add_document(dao.Document(title="关于季度工作的报告",
                                         content_text="一季度各项工作平稳推进。"))


def _make_big(tmp_path, name: str, mb: int) -> Path:
    """造一个 mb MB 的真实文件；随机内容压不动，zip 体积≈原体积，便于做体积断言。"""
    p = tmp_path / name
    p.write_bytes(os.urandom(mb * MB))
    return p


def _add_attachment(doc, tmp_path, name: str, mb: int):
    att = attachments.add(doc, str(_make_big(tmp_path, name, mb)))
    return att, attachments.resolve(att)


def _manifest(zip_path: str) -> dict:
    with zipfile.ZipFile(zip_path) as zf:
        return json.loads(zf.read(backup.MANIFEST_NAME).decode("utf-8"))


def _packed_attachments(zip_path: str) -> list[str]:
    with zipfile.ZipFile(zip_path) as zf:
        return [n for n in zf.namelist()
                if n.startswith("attachments/") and not n.endswith("/")]


# ------------------------------------------------------------------ 上限内：全带
def test_attachments_within_limit_all_packed_and_restored(tmp_db, data_dir, doc,
                                                          tmp_path):
    """附件总体积在上限内：包里带全部附件，恢复后一个不少、内容一致。"""
    dao.set_setting(backup.SETTING_LIMIT_MB, "50")
    made = [_add_attachment(doc, tmp_path, n, 1)
            for n in ("附件甲.pdf", "附件乙.pdf")]

    rep = backup.create_backup_detailed(note="上限内", mode=backup.MODE_MANUAL)
    assert rep.mode == backup.MODE_MANUAL
    assert rep.excluded == [], "上限内不该排除任何附件"
    assert len(rep.included) == 2
    assert rep.included_bytes >= 2 * MB
    assert rep.truncated is False
    assert sorted(_packed_attachments(rep.path)) == \
        sorted(f"attachments/{p.name}" for _, p in made)
    assert backup.EXCLUDED_LIST_NAME not in zipfile.ZipFile(rep.path).namelist(), \
        "没有排除附件时不该写缺失清单"
    assert _manifest(rep.path)["attachments"]["excluded"] == []

    # 模拟换机器：附件全没了，从备份恢复
    for _, p in made:
        p.unlink()
    rrep = backup.restore_backup_detailed(rep.path)
    assert rrep.ok is True and rrep.legacy is False
    assert rrep.restored_files == 2
    assert rrep.missing == [], "上限内的备份恢复后不该报缺附件"
    for att, p in made:
        back = attachments.resolve(dao.get_attachment(att.id))
        assert back.exists() and back.stat().st_size == MB


# ------------------------------------------------------------------ 超限：记录+告知
def test_over_limit_excluded_recorded_and_reported_on_restore(tmp_db, data_dir, doc,
                                                              tmp_path):
    """超上限：装不下的记进包内清单，恢复时明确报出缺哪些、为什么。"""
    dao.set_setting(backup.SETTING_LIMIT_MB, "5")        # 预算 5 MB
    small, p_small = _add_attachment(doc, tmp_path, "小附件.pdf", 1)
    mid, p_mid = _add_attachment(doc, tmp_path, "中附件.pdf", 2)
    big, p_big = _add_attachment(doc, tmp_path, "大附件.pdf", 4)

    rep = backup.create_backup_detailed(note="超限", mode=backup.MODE_MANUAL)
    # 体积升序装入：1 + 2 = 3 MB 进包，再加 4 MB 就超 5 MB → 大附件被排除
    assert [i["name"] for i in rep.included] == [p_small.name, p_mid.name]
    assert [e["name"] for e in rep.excluded] == [p_big.name]
    assert rep.excluded[0]["size"] == 4 * MB
    assert "上限" in rep.excluded[0]["reason"], "必须写明排除原因，不能只留个名字"
    assert rep.excluded[0]["doc_id"] == doc
    assert rep.truncated is True
    assert rep.included_bytes <= 5 * MB

    # 包里真的只有装得下的那两个（体积断言，不看清单看实物）
    assert sorted(_packed_attachments(rep.path)) == \
        sorted(f"attachments/{p.name}" for p in (p_small, p_mid))
    assert Path(rep.path).stat().st_size < 4 * MB, "被排除的 4 MB 附件不应出现在包里"

    meta = _manifest(rep.path)["attachments"]
    assert meta["limit_mb"] == 5 and meta["mode"] == backup.MODE_MANUAL
    assert [e["name"] for e in meta["excluded"]] == [p_big.name]
    with zipfile.ZipFile(rep.path) as zf:
        text = zf.read(backup.EXCLUDED_LIST_NAME).decode("utf-8")
    assert p_big.name in text and "attachments" in text, \
        "包内人眼可读的缺失清单应列出被排除的附件与补救办法"

    # 换机器恢复：小/中附件回来了，大附件必须被明确报缺（带原因与补救目录）
    for p in (p_small, p_mid, p_big):
        p.unlink()
    rrep = backup.restore_backup_detailed(rep.path)
    assert rrep.legacy is False
    assert rrep.restored_files == 2
    assert [m["name"] for m in rrep.missing] == [big.file_name]
    assert "上限" in rrep.missing[0]["reason"]
    assert rrep.missing[0]["size"] == 4 * MB
    assert rrep.attachments_dir == str(paths.attachments_dir())
    assert attachments.resolve(dao.get_attachment(small.id)).exists()
    assert attachments.resolve(dao.get_attachment(mid.id)).exists()
    assert not attachments.resolve(dao.get_attachment(big.id)).exists()


def test_attachment_missing_on_disk_is_recorded_not_skipped(tmp_db, data_dir, doc,
                                                            tmp_path):
    """备份时文件已不在磁盘上：也要进清单（旧实现是静默 continue）。"""
    att, p = _add_attachment(doc, tmp_path, "已被挪走的.pdf", 1)
    p.unlink()

    rep = backup.create_backup_detailed(note="缺文件", mode=backup.MODE_MANUAL)
    assert [e["name"] for e in rep.excluded] == [att.file_name]
    assert "不在磁盘上" in rep.excluded[0]["reason"]
    assert rep.included == []


def test_encrypted_backup_also_carries_exclusion_manifest(tmp_db, data_dir, doc,
                                                          tmp_path):
    """加密备份走的是另一条写包分支：清单同样必须在包里，恢复同样要报缺。"""
    pytest.importorskip("pyzipper")
    dao.set_setting(backup.SETTING_LIMIT_MB, "1")
    small, p_small = _add_attachment(doc, tmp_path, "小附件.pdf", 1)
    big, p_big = _add_attachment(doc, tmp_path, "大附件.pdf", 3)

    rep = backup.create_backup_detailed(note="加密超限", password="pass1234",
                                        mode=backup.MODE_MANUAL)
    assert "_加密" in Path(rep.path).name
    assert [i["name"] for i in rep.included] == [p_small.name]
    assert [e["name"] for e in rep.excluded] == [p_big.name]

    import pyzipper
    with pyzipper.AESZipFile(rep.path) as zf:
        zf.setpassword(b"pass1234")
        names = zf.namelist()
        assert backup.MANIFEST_NAME in names and backup.EXCLUDED_LIST_NAME in names
        meta = json.loads(zf.read(backup.MANIFEST_NAME).decode("utf-8"))
        assert meta["encrypted"] is True
        assert [e["name"] for e in meta["attachments"]["excluded"]] == [p_big.name]
        assert p_big.name in zf.read(backup.EXCLUDED_LIST_NAME).decode("utf-8")
        assert f"attachments/{p_small.name}" in names
        assert f"attachments/{p_big.name}" not in names

    for p in (p_small, p_big):
        p.unlink()
    with pytest.raises(Exception):
        backup.restore_backup_detailed(rep.path, password="wrongpass")
    rrep = backup.restore_backup_detailed(rep.path, password="pass1234")
    assert rrep.ok is True and rrep.legacy is False and rrep.restored_files == 1
    assert [m["name"] for m in rrep.missing] == [big.file_name]
    assert attachments.resolve(dao.get_attachment(small.id)).exists()


# ------------------------------------------------------------------ 手动 vs 自动
def test_manual_and_auto_backups_use_different_budgets(tmp_db, data_dir, doc,
                                                       tmp_path):
    """同一批附件：手动备份带得走，退出/定时的自动备份按小预算排除并写日志。"""
    dao.set_setting(backup.SETTING_LIMIT_MB, "50")        # 手动：装得下
    dao.set_setting(backup.SETTING_AUTO_LIMIT_MB, "0")    # 自动：一个都不带
    _, p1 = _add_attachment(doc, tmp_path, "甲.pdf", 1)
    _, p2 = _add_attachment(doc, tmp_path, "乙.pdf", 2)

    manual = backup.create_backup_detailed(note="手动备份", mode=backup.MODE_MANUAL)
    auto = backup.create_backup_detailed(note="退出自动备份", mode=backup.MODE_AUTO)

    assert manual.mode == backup.MODE_MANUAL and auto.mode == backup.MODE_AUTO
    assert len(manual.included) == 2 and manual.excluded == []
    assert auto.included == [] and len(auto.excluded) == 2
    assert _packed_attachments(manual.path) != []
    assert _packed_attachments(auto.path) == [], "自动档预算 0 时不该打包附件"
    assert Path(auto.path).stat().st_size < Path(manual.path).stat().st_size

    # 自动备份不弹窗（退出路径不能拦着用户），但必须留痕
    log = paths.logs_dir() / backup.LOG_NAME
    assert log.exists(), "自动备份排除附件时应写日志"
    text = log.read_text(encoding="utf-8")
    assert "自动备份" in text and p1.name in text and p2.name in text
    # 数据库与模板在两种档下都必须完整（自动档省的只是附件）
    for rep in (manual, auto):
        with zipfile.ZipFile(rep.path) as zf:
            names = zf.namelist()
            assert "gwtool.db" in names and "templates.json" in names
            assert backup.MANIFEST_NAME in names


def test_auto_backup_is_cheap_when_attachments_are_huge(tmp_db, data_dir, doc,
                                                        tmp_path):
    """退出路径的体积上限：附件远超自动档预算时，自动包体积仍被预算压住。"""
    dao.set_setting(backup.SETTING_AUTO_LIMIT_MB, str(backup.DEFAULT_AUTO_ATTACHMENT_LIMIT_MB))
    _add_attachment(doc, tmp_path, "超大附件.pdf", 12)     # 12 MB > 默认 8 MB 预算

    auto = backup.create_backup_detailed(note="退出自动备份", mode=backup.MODE_AUTO)
    assert auto.excluded and auto.included == []
    assert Path(auto.path).stat().st_size < 2 * MB, \
        "自动备份包不该随附件体积膨胀（退出要快、别吃磁盘）"

    # 兼容旧调用：create_backup 仍返回路径字符串
    plain = backup.create_backup(note="退出自动备份", mode=backup.MODE_AUTO)
    assert isinstance(plain, str) and Path(plain).exists()


# ------------------------------------------------------------------ 上限可配置
def test_limit_is_configurable_via_settings(tmp_db, data_dir, doc, tmp_path):
    """上限走 settings 表：默认值、改小、不限制、坏值回落都要对。"""
    assert backup.attachment_limit_mb(backup.MODE_MANUAL) == \
        backup.DEFAULT_ATTACHMENT_LIMIT_MB
    assert backup.attachment_limit_mb(backup.MODE_AUTO) == \
        backup.DEFAULT_AUTO_ATTACHMENT_LIMIT_MB
    assert backup.attachment_limit_bytes(backup.MODE_AUTO) == \
        backup.DEFAULT_AUTO_ATTACHMENT_LIMIT_MB * MB
    assert backup.attachment_limit_bytes(backup.MODE_MANUAL) == \
        backup.DEFAULT_ATTACHMENT_LIMIT_MB * MB

    _, p = _add_attachment(doc, tmp_path, "两兆.pdf", 2)

    dao.set_setting(backup.SETTING_LIMIT_MB, "1")           # 调小到 1 MB
    assert backup.attachment_limit_mb(backup.MODE_MANUAL) == 1
    rep = backup.create_backup_detailed(note="小上限", mode=backup.MODE_MANUAL)
    assert [e["name"] for e in rep.excluded] == [p.name]

    dao.set_setting(backup.SETTING_LIMIT_MB, "-1")          # 不限制
    assert backup.attachment_limit_mb(backup.MODE_MANUAL) == backup.NO_LIMIT
    assert backup.attachment_limit_bytes(backup.MODE_MANUAL) == backup.NO_LIMIT
    rep = backup.create_backup_detailed(note="不限制", mode=backup.MODE_MANUAL)
    assert rep.excluded == [] and len(rep.included) == 1
    assert _manifest(rep.path)["attachments"]["limit_mb"] == backup.NO_LIMIT

    dao.set_setting(backup.SETTING_LIMIT_MB, "不是数字")     # 坏值不能崩
    assert backup.attachment_limit_mb(backup.MODE_MANUAL) == \
        backup.DEFAULT_ATTACHMENT_LIMIT_MB


def test_settings_dialog_exposes_both_limits(tmp_db, qapp, data_dir, monkeypatch):
    """设置界面能读写两个上限（手动/自动），改完立刻落到 settings。"""
    from PySide6.QtWidgets import QDialog
    from gwtool.ui.feature_dialogs import SecurityDialog

    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    dao.set_setting(backup.SETTING_LIMIT_MB, "77")
    dao.set_setting(backup.SETTING_AUTO_LIMIT_MB, "3")
    dlg = SecurityDialog()
    try:
        assert dlg.sp_backup_limit.value() == 77
        assert dlg.sp_auto_backup_limit.value() == 3
        assert "当前附件共 0 个" in dlg.lbl_backup_limit.text()
        dlg.sp_backup_limit.setValue(11)
        dlg.sp_auto_backup_limit.setValue(0)
        assert dao.get_setting(backup.SETTING_LIMIT_MB) == "11"
        assert dao.get_setting(backup.SETTING_AUTO_LIMIT_MB) == "0"
        assert backup.attachment_limit_mb(backup.MODE_MANUAL) == 11
        assert backup.attachment_limit_mb(backup.MODE_AUTO) == 0
    finally:
        dlg.close()


# ------------------------------------------------------------------ 旧格式兼容
@pytest.mark.parametrize("with_manifest", [True, False])
def test_legacy_backup_without_attachment_manifest_still_restores(
        tmp_db, data_dir, doc, tmp_path, with_manifest):
    """旧格式备份包（无附件清单，甚至无 manifest.json）必须照常恢复。

    用户机器上已经存在这种包：上一版把全部附件塞进去、清单文件根本不存在。
    恢复端不能因为读不到清单就报错，只能按「旧格式/全量」处理。
    """
    att, stored = _add_attachment(doc, tmp_path, "旧包里的附件.pdf", 1)
    payload = stored.read_bytes()
    legacy = tmp_path / "legacy_backup.zip"
    dbconn.close_current_thread()                  # 落盘后再打包，模拟旧版写出的包
    with zipfile.ZipFile(legacy, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(dbconn.current_db_file(), "gwtool.db")
        zf.write(stored, f"attachments/{stored.name}")
        if with_manifest:
            # 上一版的 manifest：只有 created/version/note，没有 attachments 段
            zf.writestr(backup.MANIFEST_NAME, json.dumps(
                {"created": "20250101_120000_000", "version": "1.0.0",
                 "note": "退出自动备份"}, ensure_ascii=False))

    stored.unlink()                                # 模拟换机器/数据目录丢失
    assert not stored.exists()
    assert backup.restore_backup(str(legacy)) is True     # 兼容旧返回值
    rep = backup.restore_backup_detailed(str(legacy))
    assert rep.ok is True
    assert rep.legacy is True, "缺少附件清单时应按旧格式处理"
    assert rep.restored_files == 1
    back = attachments.resolve(dao.get_attachment(att.id))
    assert back.exists() and back.read_bytes() == payload
    assert rep.missing == []


def test_legacy_backup_reports_attachments_it_never_contained(tmp_db, data_dir, doc,
                                                              tmp_path):
    """旧包里没有附件目录（更早的版本）：恢复不报错，但仍要报出缺哪些附件。"""
    att, stored = _add_attachment(doc, tmp_path, "没进包的附件.pdf", 1)
    legacy = tmp_path / "legacy_no_attachments.zip"
    dbconn.close_current_thread()
    with zipfile.ZipFile(legacy, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(dbconn.current_db_file(), "gwtool.db")

    stored.unlink()
    rep = backup.restore_backup_detailed(str(legacy))
    assert rep.ok is True and rep.legacy is True and rep.restored_files == 0
    assert [m["name"] for m in rep.missing] == [att.file_name]
    assert rep.missing[0]["reason"], "缺附件必须给原因，不能只给个空字符串"


def test_restore_keeps_local_attachment_files(tmp_db, data_dir, doc, tmp_path):
    """恢复只写不删：本机已有的附件不会因为「包里没它」而被删掉。

    这条决定了「超限排除」在同机恢复时其实无损 —— 提示里也是这么告诉用户的。
    """
    dao.set_setting(backup.SETTING_LIMIT_MB, "1")
    _, kept = _add_attachment(doc, tmp_path, "留在本机的.pdf", 2)
    rep = backup.create_backup_detailed(note="超限", mode=backup.MODE_MANUAL)
    assert [e["name"] for e in rep.excluded] == [kept.name]

    assert backup.restore_backup_detailed(rep.path).ok is True
    assert kept.exists(), "恢复不该删掉本机已有的附件文件"


# ------------------------------------------------------------------ 落盘安全
def test_backup_leaves_no_partial_file(tmp_db, data_dir, doc, tmp_path):
    """先写 .part 再原子改名：备份目录里不该留半成品，残留的旧 .part 要清掉。"""
    stale = paths.backup_dir() / "gwtool_backup_20200101_000000_000.zip.part"
    stale.write_bytes(b"half written")
    os.utime(stale, (0, 0))                        # 造一个"很久以前"的残留

    rep = backup.create_backup_detailed(note="原子落名")
    assert Path(rep.path).exists()
    assert rep.path.endswith(".zip")
    assert not list(paths.backup_dir().glob("*.part")), "半成品应被改名或清理"
    assert [Path(x["file"]).name for x in backup.list_backups()] == \
        [Path(rep.path).name], "轮转与备份列表只认完整包"


def test_list_backups_exposes_attachment_counts(tmp_db, data_dir, doc, tmp_path):
    """备份列表要能看出哪份包缺附件（事后可查，不只在备份当下提示一次）。"""
    dao.set_setting(backup.SETTING_LIMIT_MB, "1")
    _, p = _add_attachment(doc, tmp_path, "两兆.pdf", 2)
    backup.create_backup_detailed(note="手动备份", mode=backup.MODE_MANUAL)

    items = backup.list_backups()
    assert items and items[0]["note"] == "手动备份"
    assert items[0]["attachments_included"] == 0
    assert items[0]["attachments_excluded"] == 1
    assert items[0]["created"]


# ------------------------------------------------------------------ UI 提示
@pytest.fixture()
def ui(tmp_db, qapp, data_dir, monkeypatch):
    """借用 MainWindow 的备份/恢复回调（不构造整窗，避免整库种子导入的开销），
    并把弹窗换成收集器，用来断言用户真正看到的文字。"""
    import gwtool.ui.main_window as mw
    from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: ("", "")))
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(mw, "info", lambda parent, text: shown.append(("info", text)))
    monkeypatch.setattr(mw, "warn", lambda parent, text: shown.append(("warn", text)))
    monkeypatch.setattr(mw, "ask", lambda *a, **k: True)

    class _Win:
        """只借用主窗口的备份/恢复回调，不构造整窗（PySide6 不允许空壳实例，
        且真窗口要先跑一遍整库种子导入，与本文件要验的提示文字无关）。"""
        _do_backup = mw.MainWindow._do_backup
        _do_restore = mw.MainWindow._do_restore
        _format_backup_items = staticmethod(mw.MainWindow._format_backup_items)
        _guarded = staticmethod(mw.MainWindow._guarded)

    return _Win(), shown


def test_manual_backup_ui_warns_listing_excluded_attachments(ui, doc, tmp_path,
                                                             monkeypatch):
    """手动备份超限时必须当场弹提示，列出被排除的附件与去哪儿改上限。"""
    win, shown = ui
    dao.set_setting(backup.SETTING_LIMIT_MB, "1")
    _, p = _add_attachment(doc, tmp_path, "四兆附件.pdf", 4)

    win._do_backup()
    assert len(shown) == 1 and shown[0][0] == "warn", "超限却只给了普通成功提示"
    text = shown[0][1]
    assert p.name in text and "未随本备份包备份" in text
    assert "上限" in text and "设置 → 系统与安全" in text
    assert "备份成功" in text and "attachments" in text


def test_manual_backup_ui_plain_success_when_within_limit(ui, doc, tmp_path):
    """上限内不打扰用户：普通成功提示里说明附件已随包。"""
    win, shown = ui
    dao.set_setting(backup.SETTING_LIMIT_MB, "50")
    _add_attachment(doc, tmp_path, "小附件.pdf", 1)

    win._do_backup()
    assert [k for k, _ in shown] == ["info"]
    assert "附件 1 个已随包备份" in shown[0][1]


def test_backup_failure_warns_and_restores_cursor(ui, monkeypatch):
    """备份出错（磁盘满等）只提示不裸奔，且等待光标必须复位。"""
    import gwtool.ui.main_window as mw
    from PySide6.QtWidgets import QApplication

    win, shown = ui

    def boom(**kwargs):
        raise OSError("磁盘空间不足")

    monkeypatch.setattr(mw, "create_backup_detailed", boom)
    win._do_backup()                            # 不许抛异常到 Qt 事件循环
    assert [k for k, _ in shown] == ["warn"]
    assert "磁盘空间不足" in shown[0][1]
    assert QApplication.overrideCursor() is None, "等待光标没有复位，界面会一直是沙漏"


def test_restore_ui_reports_missing_attachments(ui, doc, tmp_path, monkeypatch):
    """恢复时要明确告知哪些附件不在此备份中、从哪里补（不能只说"恢复成功"）。"""
    import gwtool.ui.main_window as mw
    from PySide6.QtWidgets import QFileDialog

    win, shown = ui
    dao.set_setting(backup.SETTING_LIMIT_MB, "1")
    _, big = _add_attachment(doc, tmp_path, "四兆附件.pdf", 4)
    rep = backup.create_backup_detailed(note="手动备份", mode=backup.MODE_MANUAL)
    big.unlink()                                # 换机器：本机也没有这个附件了

    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (rep.path, "")))
    win._do_restore()
    warns = [t for k, t in shown if k == "warn"]
    assert len(warns) == 1, f"恢复后应给出一次缺附件提醒，实际：{shown}"
    text = warns[0]
    assert "不在此备份包内" in text and big.name in text
    assert str(paths.attachments_dir()) in text, "要告诉用户去哪儿补附件"
    assert "重启程序" in text


# ------------------------------------------------------------------ 退出路径
class _CloseEvent:
    """closeEvent 只需要 accept()/ignore()，不必真造 QCloseEvent。"""

    def __init__(self):
        self.accepted = None

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


def _close_window(monkeypatch, auto_backup: str = "1"):
    """跑一遍主窗口的 closeEvent，返回 (记录到的备份调用, 事件是否被接受)。"""
    import gwtool.ui.main_window as mw

    dao.set_setting("auto_backup", auto_backup)
    calls: list[dict] = []
    monkeypatch.setattr(
        mw, "create_backup_detailed",
        lambda **kw: calls.append(kw) or backup.BackupReport(path="x.zip"))

    class _Editor:
        def confirm_discard_changes(self):
            return True

    class _Win:
        editor = _Editor()
        _tts_worker = None
        closeEvent = mw.MainWindow.closeEvent

    ev = _CloseEvent()
    _Win().closeEvent(ev)
    return calls, ev.accepted


def test_exit_auto_backup_uses_auto_budget(tmp_db, data_dir, monkeypatch):
    """退出路径必须走自动档：用手动档的话，关程序会随附件增多越来越卡。"""
    calls, accepted = _close_window(monkeypatch)
    assert accepted is True, "备份不该拦住退出"
    assert len(calls) == 1
    assert calls[0].get("mode") == backup.MODE_AUTO
    assert calls[0].get("note") == "退出自动备份"


def test_exit_auto_backup_can_be_turned_off(tmp_db, data_dir, monkeypatch):
    """设置里关掉「退出自动备份」后，退出路径一次备份都不做。"""
    calls, accepted = _close_window(monkeypatch, auto_backup="0")
    assert calls == []
    assert accepted is True
