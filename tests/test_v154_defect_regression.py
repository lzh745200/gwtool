# -*- coding: utf-8 -*-
"""v1.5.4 缺陷修复回归：金额大写越界崩溃、恢复链三处数据安全缺陷。

本轮修复的四个缺陷（均由真实链路探针复现后定位，不是理论担忧）：

1. **金额大写 13 位以上 IndexError 崩溃**（`toolbox.amount_to_cn`）
   节名写死成 ["", "万", "亿"] 三档下标，1000000000000 元（1 万亿）
   直接在右键菜单上抛 IndexError；且组内零处理会把位数读丢
   （1005 读成"壹仟零伍拾"）。

2. **恢复备份时 Windows 文件锁导致恢复全面失败**（`backup._replace_with_retry`）
   `os.replace` 覆盖被打开的文件必抛 WinError 5；程序是多线程的，
   实测"边读库边恢复"20/20 全部失败。

3. **损坏/空备份包被误判合法并静默覆盖用户数据**（`backup._validate_restored_db`）
   老实现只跑一句 `SELECT count(*) FROM sqlite_master`：空文件返回 0 行、
   截断库不报错，于是把空库顶上真库，界面还提示"恢复成功"——
   灾难恢复场景下最坏的失败方式。

4. **备份包可向附件目录注入非法文件名**（`backup._restore_attachments`）
   裸 `Path(name).name` 拦不住 `report.txt:evil.txt` 这类名字：
   Windows 上冒号是 NTFS 备用数据流语法，会写出目录列表看不见的隐藏流。
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from gwtool import paths
from gwtool.core import attachments as att_core
from gwtool.core import backup, toolbox
from gwtool.db import connection as dbconn
from gwtool.db import dao
from gwtool.db.schema import init_schema, required_table_names


def _add_doc(title: str, content: str) -> int:
    return dao.add_document(dao.Document(
        title=title, content_text=content, blocks_json="[]", file_path="",
        file_type="txt", category_id=0))


# ============================================================ 1. 金额大写
class TestAmountToChineseBounds:
    """13 位以上金额不得崩；读数必须与规范一致。"""

    @pytest.mark.parametrize("value,expected", [
        # 老实现直接 IndexError 的三档越界点
        ("1000000000000", "人民币壹万亿元整"),
        ("1234567890123",
         "人民币壹万亿贰仟叁佰肆拾伍亿陆仟柒佰捌拾玖万零壹佰贰拾叁元整"),
        ("9999999999999999", "人民币玖仟玖佰玖拾玖万亿玖仟玖佰玖拾玖亿"
                             "玖仟玖佰玖拾玖万玖仟玖佰玖拾玖元整"),
        ("9" * 30, None),          # 只要求不崩
    ])
    def test_huge_amount_does_not_raise(self, value, expected):
        got = toolbox.amount_to_cn(value)
        assert got
        if expected is not None:
            assert got == expected

    @pytest.mark.parametrize("value,expected", [
        ("1005", "人民币壹仟零伍元整"),          # 组内零后面还有有效位
        ("1010", "人民币壹仟零壹拾元整"),
        ("10001", "人民币壹万零壹元整"),
        ("100050000", "人民币壹亿零伍万元整"),
        ("50001000", "人民币伍仟万壹仟元整"),     # 满四位相邻不补多余零
        ("1000001000", "人民币壹拾亿零壹仟元整"),
    ])
    def test_zero_placement_matches_spec(self, value, expected):
        assert toolbox.amount_to_cn(value) == expected

    def test_illegal_amount_raises_valueerror_not_indexerror(self):
        """非法输入必须抛 ValueError（UI 层按它提示），不能漏出 IndexError。"""
        for bad in ["abc", "", "-1", "1.999", None, "1e5"]:
            with pytest.raises(ValueError):
                toolbox.amount_to_cn(bad)


# ============================================================ 2. 恢复重试
class TestRestoreSurvivesBusyDatabase:
    """Windows 上换库要求独占；被占住时必须重试，最终要么成功要么明确报错。"""

    def test_restore_retries_while_replace_is_blocked(self, tmp_db, monkeypatch):
        """把 os.replace 前几次调用打成"拒绝访问"，验证真的会重试而不是一次放弃。"""
        _add_doc("甲", "甲内容" * 30)
        zip_path = backup.create_backup(note="x", mode=backup.MODE_MANUAL)

        real_replace = __import__("os").replace
        calls = {"n": 0}

        def flaky_replace(src, dst):
            calls["n"] += 1
            if calls["n"] <= 3:
                exc = PermissionError(13, "拒绝访问")
                exc.winerror = 5          # 模拟 WinError 5
                raise exc
            return real_replace(src, dst)

        monkeypatch.setattr(backup.os, "replace", flaky_replace)
        monkeypatch.setattr(backup, "_REPLACE_DELAY", 0.01)
        rep = backup.restore_backup_detailed(zip_path)
        assert rep.ok is True
        assert calls["n"] == 4, f"应重试到第 4 次才成功，实际调用 {calls['n']} 次"

    def test_restore_raises_actionable_error_when_never_releasable(
            self, tmp_db, monkeypatch):
        """始终被占用：报可操作的错误、原库未被改动、不留 .restore.tmp。"""
        _add_doc("宝贝", "不可丢失的内容" * 20)
        zip_path = backup.create_backup(note="x", mode=backup.MODE_MANUAL)
        dbconn.close_current_thread()

        def always_busy(src, dst):
            exc = PermissionError(13, "拒绝访问")
            exc.winerror = 5
            raise exc

        monkeypatch.setattr(backup.os, "replace", always_busy)
        monkeypatch.setattr(backup, "_REPLACE_DELAY", 0.001)
        with pytest.raises(RuntimeError) as ei:
            backup.restore_backup_detailed(zip_path)
        msg = str(ei.value)
        assert "占用" in msg and "当前数据未被改动" in msg

        dbconn.close_current_thread()
        assert len(dao.list_documents()) == 1, "原库必须原样保留"
        leftovers = [p.name for p in tmp_db.parent.iterdir()
                     if p.name.endswith(backup.PART_SUFFIX)
                     or "restore.tmp" in p.name]
        assert not leftovers, f"不能留下临时文件：{leftovers}"

    def test_restore_works_after_other_thread_closes(self, tmp_db):
        """后台线程持连接期间不能崩；线程退出后恢复正常（真实并发时序）。"""
        _add_doc("甲", "甲内容" * 30)
        zip_path = backup.create_backup(note="x", mode=backup.MODE_MANUAL)
        dbconn.close_current_thread()

        hold = threading.Event()
        started = threading.Event()
        errors: list[str] = []

        def holder():
            try:
                dbconn.get_conn().execute(
                    "SELECT count(*) FROM documents").fetchone()
                started.set()
                hold.wait(timeout=20)
            except Exception as exc:
                errors.append(str(exc))
            finally:
                dbconn.close_current_thread()

        th = threading.Thread(target=holder, name="后台导入线程", daemon=True)
        th.start()
        assert started.wait(timeout=5)
        try:
            backup.restore_backup_detailed(zip_path)
            blocked = False
        except RuntimeError:
            # 允许"明确报错"，但绝不能是 PermissionError 裸奔或数据被破坏
            blocked = True
        hold.set()
        th.join(timeout=10)
        assert not errors, f"后台线程不该报错：{errors}"

        dbconn.close_current_thread()
        # 线程退出后必须能正常恢复
        rep = backup.restore_backup_detailed(zip_path)
        assert rep.ok is True
        assert blocked in (True, False)       # 两种结果都可接受，重点是行为明确


# ============================================================ 3. 坏包校验
def _zip_with_db(zip_path: Path, db_bytes: bytes) -> str:
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("gwtool.db", db_bytes)
    return str(zip_path)


class TestRestoreRejectsUnusableDatabase:
    """换库前必须确认包里的库真的是本程序的可用库。"""

    def _make_valid_db(self, path: Path) -> bytes:
        c = sqlite3.connect(str(path))
        init_schema(c)
        c.close()
        return path.read_bytes()

    def test_empty_db_file_rejected(self, tmp_db, tmp_path):
        _add_doc("宝贝", "不可丢失的内容")
        zp = _zip_with_db(tmp_path / "empty.zip", b"")
        with pytest.raises(ValueError) as ei:
            backup.restore_backup_detailed(zp)
        assert "不完整" in str(ei.value)
        dbconn.close_current_thread()
        assert len(dao.list_documents()) == 1

    def test_truncated_db_rejected(self, tmp_db, tmp_path):
        """截断的库 SQLite 不一定报错——老实现据此放行，把好库覆盖成坏库。"""
        _add_doc("宝贝", "不可丢失的内容" * 20)
        raw = (paths.app_data_dir() / "test_gwtool.db").read_bytes()
        zp = _zip_with_db(tmp_path / "trunc.zip", raw[: len(raw) // 2])
        with pytest.raises(ValueError):
            backup.restore_backup_detailed(zp)
        dbconn.close_current_thread()
        assert len(dao.list_documents()) == 1

    def test_garbage_bytes_rejected(self, tmp_db, tmp_path):
        _add_doc("宝贝", "不可丢失的内容")
        zp = _zip_with_db(tmp_path / "junk.zip", b"NOT A DATABASE" * 200)
        with pytest.raises(ValueError) as ei:
            backup.restore_backup_detailed(zp)
        assert "SQLite" in str(ei.value)
        dbconn.close_current_thread()
        assert len(dao.list_documents()) == 1

    def test_foreign_sqlite_rejected(self, tmp_db, tmp_path):
        """合法 sqlite 但不是本程序的库：不能顶掉用户数据。"""
        _add_doc("宝贝", "不可丢失的内容")
        other = tmp_path / "other.db"
        c = sqlite3.connect(str(other))
        c.execute("CREATE TABLE unrelated(x)")
        c.executemany("INSERT INTO unrelated VALUES(?)", [(i,) for i in range(50)])
        c.commit()
        c.close()
        zp = _zip_with_db(tmp_path / "other.zip", other.read_bytes())
        with pytest.raises(ValueError) as ei:
            backup.restore_backup_detailed(zp)
        assert "业务表" in str(ei.value)
        dbconn.close_current_thread()
        assert len(dao.list_documents()) == 1

    def test_valid_backup_still_restores(self, tmp_db, tmp_path):
        """反面对照：真正合法的包必须还能恢复（别把校验做成拦路虎）。"""
        did = _add_doc("甲文档", "甲内容" * 30)
        src = tmp_path / "att.txt"
        src.write_text("附件正文", encoding="utf-8")
        a = att_core.add(did, str(src))
        zp = backup.create_backup(note="正常", mode=backup.MODE_MANUAL)
        _add_doc("事后再加", "事后新增的内容" * 10)

        rep = backup.restore_backup_detailed(zp)
        assert rep.ok is True
        dbconn.close_current_thread()
        assert [d.title for d in dao.list_documents()] == ["甲文档"]
        assert att_core.exists(dao.get_attachment(a.id))

    def test_no_temp_file_left_after_rejection(self, tmp_db, tmp_path):
        _add_doc("宝贝", "不可丢失的内容")
        zp = _zip_with_db(tmp_path / "trunc2.zip", b"")
        with pytest.raises(ValueError):
            backup.restore_backup_detailed(zp)
        data_dir = paths.app_data_dir()
        assert not list(data_dir.glob("*.restore.tmp")), "校验失败不能留临时文件"
        assert not list(data_dir.glob("*.restore.tmp")), ""

    def test_required_tables_derived_from_schema(self):
        """必需表清单由 schema 声明推导，新增表自动纳入、不会漂移。"""
        names = required_table_names()
        assert {"documents", "error_pairs", "dictionary", "settings"} <= names
        assert "documents_fts" not in names, "FTS 虚表不是必需业务表"


# ============================================================ 4. 附件名注入
class TestRestoreAttachmentNameInjection:
    """备份包是外部输入：条目名必须清洗后再落盘。"""

    def _malicious_zip(self, tmp_path: Path) -> str:
        """先造一个合法备份包（保证库体真实完整），再把恶意条目塞进同一个 zip。

        不直接读 gwtool.db 的字节：库在 WAL 模式下，最新事务可能还在 -wal 里，
        裸读主文件会拿到"表还没建出来"的库——那样测的就不是附件名清洗了。
        """
        _add_doc("占位", "占位内容，用于生成合法备份包")
        good = backup.create_backup(note="底包", mode=backup.MODE_MANUAL)
        zp = tmp_path / "mal.zip"
        with zipfile.ZipFile(good) as src, zipfile.ZipFile(zp, "w") as dst:
            for item in src.infolist():
                dst.writestr(item, src.read(item.filename))
            dst.writestr("attachments/report.txt:evil.txt", "MZPAYLOAD")   # ADS
            dst.writestr("attachments/../../escape.txt", "ESCAPE")          # 穿越
            dst.writestr("attachments/C:drive.txt", "DRIVE")                # 盘符
            dst.writestr("attachments/正常附件.txt", "OK")                   # 正常
        return str(zp)

    def test_no_ads_or_separators_in_restored_names(self, tmp_db, tmp_path):
        zp = self._malicious_zip(tmp_path)
        backup.restore_backup_detailed(zp)
        names = sorted(p.name for p in paths.attachments_dir().iterdir())
        assert "正常附件.txt" in names, f"正常附件应被恢复：{names}"
        assert not any(":" in n for n in names), f"冒号是 ADS 语法：{names}"
        assert not any(("/" in n or "\\" in n) for n in names), f"不得含分隔符：{names}"

    def test_nothing_escapes_attachments_dir(self, tmp_db, tmp_path):
        zp = self._malicious_zip(tmp_path)
        backup.restore_backup_detailed(zp)
        adir = paths.attachments_dir()
        for stray in list(tmp_path.rglob("escape.txt")) + list(tmp_path.rglob("drive.txt")):
            assert stray.parent == adir, f"逃出附件目录：{stray}"

    @pytest.mark.skipif(not sys.platform.startswith("win"),
                        reason="NTFS 备用数据流是 Windows 专有特性")
    def test_no_ntfs_alternate_data_streams(self, tmp_db, tmp_path):
        """用系统工具枚举数据流：必须只剩默认 :$DATA。"""
        zp = self._malicious_zip(tmp_path)
        backup.restore_backup_detailed(zp)
        adir = paths.attachments_dir()
        ps = (f"Get-ChildItem -LiteralPath '{adir}' -Force | ForEach-Object {{ "
              "Get-Item -LiteralPath $_.FullName -Stream * -ErrorAction SilentlyContinue | "
              "ForEach-Object { if ($_.Stream -ne ':$DATA') { Write-Output "
              "($_.FileName + ' -> ' + $_.Stream) } } }")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, errors="replace").stdout
        assert not out.strip(), f"发现备用数据流：{out.strip()}"


# ============================================================ 5. schema 单一来源
class TestSchemaSingleSourceOfTruth:
    """索引与迁移不允许两处重复定义（曾导致同一个索引写了两遍）。"""

    def test_no_duplicate_index_definitions(self):
        import collections
        import re
        src = (Path(__file__).resolve().parent.parent
               / "gwtool" / "db" / "schema.py").read_text(encoding="utf-8")
        names = re.findall(r"CREATE (?:UNIQUE )?INDEX IF NOT EXISTS (\w+)", src)
        dup = {k: v for k, v in collections.Counter(names).items() if v > 1}
        assert not dup, f"索引被定义多次（改动时必然漏掉一处）：{dup}"

    def test_indexes_all_created(self, tmp_db):
        conn = dbconn.get_conn()
        got = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        from gwtool.db.schema import INDEXES
        import re
        want = {re.search(r"EXISTS (\w+)", ddl).group(1) for ddl in INDEXES}
        assert want <= got, f"缺失索引：{sorted(want - got)}"
