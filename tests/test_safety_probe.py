# -*- coding: utf-8 -*-
"""安全备份与启动组（backup/security/paths/app 种子链路）逐模块深探：
真实 zip 包、真实加密（pyzipper）、真实 SQLite、真实脏数据记录，禁 mock。

覆盖此前零执行路径：备份排除预算与留痕、脏附件记录兜底（越界/损坏成目录/
包内同名去重）、.part 半成品清理与失败不落包、加密备份创建-列表-恢复回滚、
损坏包拒绝、旧格式包兼容、恢复后缺失核对带原因、口令锁 PBKDF2 往返与损坏
存储防御、数据目录推导回退、首启种子导入与置信度迁移幂等。
"""
from __future__ import annotations

import os
import sqlite3
import time
import zipfile
from pathlib import Path

import pytest

from gwtool import paths
from gwtool.core import backup, security
from gwtool.db import connection as dbconn
from gwtool.db import dao


def _raw_att(doc_id: int, file_name: str, stored_path: str, size: int = 1) -> None:
    """直接向库里插入一条附件记录（真实 SQL，构造正常 API 造不出的脏数据）。"""
    conn = dbconn.get_conn()
    conn.execute(
        "INSERT INTO attachments(doc_id,file_name,stored_path,size) VALUES(?,?,?,?)",
        (doc_id, file_name, stored_path, size))
    conn.commit()


# ---------------------------------------------------------------- security
class TestSecurityProbe:
    def test_password_roundtrip(self, tmp_db):
        assert security.has_password() is False
        security.set_password("口令甲123")
        assert security.has_password() is True
        assert security.verify_password("口令甲123") is True
        assert security.verify_password("口令乙456") is False
        security.clear_password()
        assert security.has_password() is False

    def test_verify_without_password_always_true(self, tmp_db):
        """未设口令：verify 任意输入都放行（锁未启用的语义）。"""
        assert security.verify_password("") is True
        assert security.verify_password("随便什么") is True

    def test_verify_corrupted_storage_fails_closed(self, tmp_db):
        """存储串损坏（缺 $ 分段）：一律拒绝而不是放行——fail closed。"""
        dao.set_setting(security.KEY, "garbage-no-delimiter")
        assert security.verify_password("任意") is False
        dao.set_setting(security.KEY, "120000$非十六进制盐$哈希")
        assert security.verify_password("任意") is False

    def test_two_passwords_different_salt(self, tmp_db):
        """同一口令两次设置产生不同盐：存储串必不同，但都能验过。"""
        security.set_password("同一口令")
        v1 = dao.get_setting(security.KEY)
        security.set_password("同一口令")
        v2 = dao.get_setting(security.KEY)
        assert v1 != v2
        assert security.verify_password("同一口令") is True


# ---------------------------------------------------------------- paths
class TestPathsProbe:
    def test_override_clear_falls_back_to_platform(self, tmp_db):
        """set_app_data_dir(None)：清除覆盖后回平台推导（目录末段 gwtool）。"""
        saved = paths._override
        try:
            paths.set_app_data_dir(None)
            d = paths.app_data_dir()
            assert d.name == "gwtool"
            assert d.exists()
        finally:
            paths.set_app_data_dir(saved)

    def test_sub_dirs_live_inside_data_dir(self, tmp_db):
        """备份/附件/日志/增强包目录都必须落在数据目录内（换机迁移的前提）。"""
        root = paths.app_data_dir()
        assert paths.backup_dir().parent == root
        assert paths.attachments_dir().parent == root
        assert paths.logs_dir().parent == root
        assert paths.enhance_dir().parent == root
        assert paths.db_path() == root / "gwtool.db"

    def test_portable_mode_switch(self, tmp_db):
        saved_flag = paths.is_portable()
        saved = paths._override
        try:
            paths.set_portable(True)
            paths.set_app_data_dir(None)  # 便携模式优先于覆盖
            d = paths.app_data_dir()
            assert d.name == "Data"
        finally:
            paths.set_portable(saved_flag)
            paths.set_app_data_dir(saved)

    def test_seed_db_bundled(self, tmp_db):
        """随包种子库必须真实存在（首启词典/纠错库的来源）。"""
        seed = paths.bundled_db_seed_path()
        assert seed.exists(), f"种子库缺失：{seed}"


# ---------------------------------------------------------------- backup 创建
class TestBackupCreateProbe:
    def test_zero_limit_excludes_all_and_logs(self, tmp_db):
        """附件上限设 0：全部附件被排除但记录在案，日志留痕，报告如实。"""
        did = dao.add_document(dao.Document(title="备份探针", content_text="c"))
        src = paths.attachments_dir() / "探针附件.bin"
        src.write_bytes(b"probe-content" * 100)
        dao.add_attachment(did, src.name, f"attachments/{src.name}",
                           src.stat().st_size)
        dao.set_setting(backup.SETTING_LIMIT_MB, "0")

        report = backup.create_backup_detailed(note="零上限探测")
        assert report.mode == backup.MODE_MANUAL
        assert report.limit_bytes == 0
        assert report.included == []
        assert report.truncated and report.excluded
        assert report.excluded[0]["reason"] == "超出备份附件体积上限"

        log = paths.logs_dir() / backup.LOG_NAME
        assert log.exists()
        assert "未随包" in log.read_text(encoding="utf-8")

    def test_invalid_record_excluded_with_reason(self, tmp_db):
        """越界脏记录（定位不到文件）：进 excluded，不崩、不静默。"""
        did = dao.add_document(dao.Document(title="脏记录", content_text="c"))
        _raw_att(did, "越界.txt", "../outside/evil.txt", size=123)
        report = backup.create_backup_detailed()
        names = [it["name"] for it in report.excluded]
        assert "越界.txt" in names
        hit = next(it for it in report.excluded if it["name"] == "越界.txt")
        assert "无效" in hit["reason"] or "定位不到" in hit["reason"]

    def test_dir_disguised_as_attachment_harmless(self, tmp_db):
        """附件位置被同名目录占据：zipfile 会静默写成目录条目（带尾 /），
        恢复端按目录条目忽略 —— 备份-恢复全链路无害化，不产生任何文件。"""
        did = dao.add_document(dao.Document(title="目录怪附件", content_text="c"))
        (paths.attachments_dir() / "怪附件.dat").mkdir()
        _raw_att(did, "怪附件.dat", "attachments/怪附件.dat", size=777)

        report = backup.create_backup_detailed()
        names = [it["name"] for it in report.included]
        assert "怪附件.dat" in names          # zipfile 对目录不抛错

        with zipfile.ZipFile(report.path) as zf:
            hit = [n for n in zf.namelist()
                   if n.startswith("attachments/") and "怪附件" in n]
            assert hit and hit[0].endswith("/")   # 确实是目录条目

        # 恢复端：目录条目被忽略，不落任何文件
        result = backup.restore_backup_detailed(report.path)
        assert result.ok is True
        assert not (paths.attachments_dir() / "怪附件.dat").is_file()

    def test_same_stored_path_deduplicated(self, tmp_db):
        """两条记录指向同一文件：包内只打一份，后写的那条按"包内同名"去重。
        去重条目的 name 取实际落盘路径的 basename。"""
        did = dao.add_document(dao.Document(title="同名去重", content_text="c"))
        real = paths.attachments_dir() / "真文件.txt"
        real.write_bytes(b"dup-content")
        dao.add_attachment(did, "真文件.txt", f"attachments/{real.name}",
                           real.stat().st_size)
        _raw_att(did, "别名.txt", f"attachments/{real.name}", size=11)

        report = backup.create_backup_detailed()
        assert len(report.included) == 1
        dup = next(it for it in report.excluded if "同名" in it["reason"])
        assert dup["name"] == "真文件.txt"    # name 来自实际路径 basename

    def test_stale_part_files_cleaned(self, tmp_db):
        """超过 1 小时的 .part 半成品被清掉；新鲜的（1 小时内）保留。"""
        bdir = paths.backup_dir()
        stale = bdir / "gwtool_backup_20200101_000000_000.zip.part"
        fresh = bdir / "gwtool_backup_20990101_000000_000.zip.part"
        stale.write_bytes(b"junk")
        fresh.write_bytes(b"junk")
        old = time.time() - 2 * 3600
        os.utime(stale, (old, old))

        backup.create_backup(note="半成品清理探测")
        assert not stale.exists()
        assert fresh.exists()

    def test_failed_backup_leaves_no_part_nor_package(self, tmp_db):
        """写包中途失败（数据库文件被替换成目录，库连接与模板导出必炸）：
        异常照实冒出，且不产生半成品 .part，也不产生看似完整的备份包。"""
        dbf = dbconn.current_db_file()
        dbconn.close_current_thread()   # 释放句柄
        if dbf.exists():                # 本测试无 DAO 调用，库文件可能尚未创建
            dbf.unlink()
        dbf.mkdir()                     # 数据库位置变成目录
        try:
            with pytest.raises(Exception):
                backup.create_backup(note="失败探测")
        finally:
            dbconn.close_current_thread()
            try:
                dbf.rmdir()
            except OSError:
                pass
        assert list(paths.backup_dir().glob("*.zip.part")) == []
        assert list(paths.backup_dir().glob("gwtool_backup_*.zip")) == []

    def test_encrypted_backup_full_lifecycle(self, tmp_db):
        """加密备份真实链路（pyzipper AES）：创建 → 列表标记加密 →
        改库 → 恢复回滚数据。"""
        dao.add_document(dao.Document(title="加密前文档", content_text="v1"))
        rep = backup.create_backup_detailed(note="加密探测", password="口令甲")
        assert "_加密" in rep.path
        listing = backup.list_backups()
        hit = next(x for x in listing if x["file"] == rep.path)
        assert hit["encrypted"] is True
        assert hit["note"] == "AES 加密备份"

        dao.update_document_content(1, "加密前文档", "v2")
        assert backup.restore_backup(rep.path, password="口令甲") is True
        assert dao.get_document(1).content_text == "v1"

    def test_restore_encrypted_wrong_password_rejected(self, tmp_db):
        dao.add_document(dao.Document(title="加密文档", content_text="v1"))
        rep = backup.create_backup_detailed(password="正确口令")
        with pytest.raises(Exception):
            backup.restore_backup(rep.path, password="错误口令")


# ---------------------------------------------------------------- backup 恢复
class TestBackupRestoreProbe:
    def _make_custom_zip(self, path: Path, entries: dict[str, bytes | str]) -> Path:
        """手工构造真实 zip 备份包（可注入任意畸形条目）。"""
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in entries.items():
                zf.writestr(name, data)
        return path

    def test_package_without_db_rejected(self, tmp_db):
        """包里没有 gwtool.db：明确报错，绝不把坏包当备份恢复。"""
        p = self._make_custom_zip(paths.backup_dir() / "坏包.zip",
                                  {backup.MANIFEST_NAME: "{}"})
        with pytest.raises(ValueError):
            backup.restore_backup(str(p))

    def _snapshot_db_bytes(self) -> bytes:
        """先 checkpoint（关闭当前线程连接）再读主库字节：
        WAL 模式下未 checkpoint 的数据躺在 -wal 里，主文件读不到。"""
        dbconn.close_current_thread()
        return dbconn.current_db_file().read_bytes()

    def test_legacy_package_treated_as_full(self, tmp_db):
        """旧格式包（无 manifest）：按全量恢复，legacy=True，不报错。"""
        dao.add_document(dao.Document(title="恢复前", content_text="old"))
        p = paths.backup_dir() / "旧包.zip"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("gwtool.db", self._snapshot_db_bytes())
        rep = backup.restore_backup_detailed(str(p))
        assert rep.ok is True and rep.legacy is True
        assert dao.get_document(1).content_text == "old"

    def test_broken_templates_json_swallowed(self, tmp_db):
        """包内 templates.json 损坏：吞掉该步，数据库恢复照常完成。"""
        dao.add_document(dao.Document(title="模板损坏包", content_text="x"))
        p = paths.backup_dir() / "模板损坏.zip"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("gwtool.db", self._snapshot_db_bytes())
            zf.writestr("templates.json", "not-json-at-all{{{")
        rep = backup.restore_backup_detailed(str(p))
        assert rep.ok is True
        assert dao.get_document(1) is not None

    def test_malformed_attachment_entries_skipped(self, tmp_db):
        """包内附件条目畸形：空名/点条目跳过、指向目录的写入失败跳过——不崩，
        也不把 "attachments/." 落成与目录同名的垃圾文件。"""
        dao.add_document(dao.Document(title="畸形条目", content_text="x"))
        (paths.attachments_dir() / "占位目录.txt").mkdir()
        p = paths.backup_dir() / "畸形.zip"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("gwtool.db", self._snapshot_db_bytes())
            zf.writestr("attachments/.", "点条目不得落盘")
            zf.writestr("attachments/占位目录.txt", "目标已是目录，写不进去")
            zf.writestr("attachments/正常.txt", "这个应该能写进去")
        rep = backup.restore_backup_detailed(str(p))
        assert rep.ok is True
        assert rep.restored_files == 1   # 只有"正常.txt"写成功
        assert (paths.attachments_dir() / "正常.txt").read_text(encoding="utf-8") \
            == "这个应该能写进去"
        assert not (paths.attachments_dir() / "attachments").exists()

    def test_missing_attachments_reported_with_reason(self, tmp_db):
        """恢复后核对：随包缺失的附件带原因报出（模拟异机恢复）。"""
        did = dao.add_document(dao.Document(title="缺失核对", content_text="c"))
        src = paths.attachments_dir() / "会丢的附件.bin"
        src.write_bytes(b"content-x")
        dao.add_attachment(did, src.name, f"attachments/{src.name}",
                           src.stat().st_size)
        dao.set_setting(backup.SETTING_LIMIT_MB, "0")   # 备份时全部排除
        rep = backup.create_backup_detailed(note="缺失核对")
        assert rep.truncated

        src.unlink()                     # 异机语义：磁盘上也没有
        result = backup.restore_backup_detailed(rep.path)
        assert result.ok is True
        names = [m["name"] for m in result.missing]
        assert "会丢的附件.bin" in names
        hit = next(m for m in result.missing if m["name"] == "会丢的附件.bin")
        assert hit["reason"] == "超出备份附件体积上限"

    def test_missing_check_survives_no_records(self, tmp_db):
        """库中无任何附件记录：缺失核对返回空列表，不报错。"""
        assert backup._missing_attachments([]) == []
        assert backup._missing_attachments([{"name": "不存在.bin",
                                             "size": 5, "doc_id": 1,
                                             "reason": "r"}]) == []


# ---------------------------------------------------------------- app 种子链路
class TestSeedingProbe:
    def test_seed_import_migration_idempotent(self, tmp_db):
        """首启链路三件事：种子库导入、generated 置信度迁移、默认模板落库；
        连跑两遍幂等（标记位防重入）。"""
        from gwtool import app

        conn = dbconn.get_conn()
        # 预置早期 generated 混淆对（confidence>=0.6 应被迁移到 0.55）
        conn.execute("INSERT INTO error_pairs(wrong,correct,category,"
                     "confidence,enabled,source) VALUES('探针错字','探针正字',"
                     "'probe',0.9,1,'generated')")
        conn.commit()

        app.ensure_database_seeded()
        app.ensure_database_seeded()     # 幂等

        row = conn.execute("SELECT confidence FROM error_pairs "
                           "WHERE wrong='探针错字'").fetchone()
        assert float(row[0]) == pytest.approx(0.55)
        # 种子库真实导入：词典与纠错对必须进来
        n_dict = conn.execute("SELECT count(*) FROM dictionary").fetchone()[0]
        n_pairs = conn.execute("SELECT count(*) FROM error_pairs").fetchone()[0]
        assert n_dict > 0 and n_pairs > 1
        assert conn.execute("SELECT value FROM settings "
                            "WHERE key='seeded_version'").fetchone() is not None
        assert conn.execute("SELECT value FROM settings WHERE "
                            "key='generated_conf_v2'").fetchone() is not None
        # 默认模板
        from gwtool.db import dao
        assert dao.list_templates(), "默认模板未落库"

    def test_follow_system_theme_reads_real_db(self, tmp_db):
        """_follow_system_theme 直接只读 gwtool.db（独立于 dbconn 连接）。"""
        from gwtool import app
        data_dir = paths.app_data_dir()
        dbf = data_dir / "gwtool.db"
        if dbf.exists():
            dbf.unlink()
        assert app._follow_system_theme() is False      # 文件不存在
        conn = sqlite3.connect(str(dbf))
        conn.execute("CREATE TABLE IF NOT EXISTS settings("
                     "key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        conn.close()
        assert app._follow_system_theme() is False      # 无该设置项
        conn = sqlite3.connect(str(dbf))
        conn.execute("INSERT INTO settings(key,value) "
                     "VALUES('follow_system_theme','1')")
        conn.commit()
        conn.close()
        assert app._follow_system_theme() is True
        dbf.unlink()
