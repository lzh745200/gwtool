# -*- coding: utf-8 -*-
"""v1.7.1 全面排查的回归护栏。

对应 `系统全面排查报告_v1.7.1.md`，把四项**实测结论**固化成断言，
防止将来（有意或无意地）改回缺陷行为：

  - A7 标题编号：「两、」必须算作 2；不得因此误报"跳号"；真跳号仍须报出
  - A3 并发连接：主线程持写事务时，另一线程**首次连接**必须成功
  - A10 块同步：乱序 / 同形 / 空白变体下，结构化块替换偏移必须正确
  - A6 段落偏移：derive_blocks 的 char_offset 必须能切出块文本本身

设计说明：A6/A10 的现行为经实测确认**正确**，故本文件只固化行为、不改逻辑
（详见报告 §五.1）。
"""
from __future__ import annotations

import json
import threading

import pytest


@pytest.fixture()
def guard_db(tmp_path):
    """把连接指向独立临时库（不依赖既有夹具的语义）。"""
    from gwtool.db import connection as dbconn

    dbconn.configure(tmp_path / "guard.db")
    yield dbconn.current_db_file()
    dbconn.close_current_thread()


class TestTitleNumbering:
    """A7：中文数字与编号识别必须包含「两」。"""

    def test_liang_maps_to_two(self):
        from gwtool.core import inspector

        assert inspector._cn_to_int("两") == 2
        assert inspector._cn_to_int("二") == 2
        # 「两」在十位：两十 = 20（原实现直接塞进 digits 字符串会让后续数字错位）
        assert inspector._cn_to_int("两十") == 20
        # 既有行为不得回退
        assert inspector._cn_to_int("一") == 1
        assert inspector._cn_to_int("十") == 10
        assert inspector._cn_to_int("十二") == 12
        assert inspector._cn_to_int("二十一") == 21

    def test_liang_participates_in_chain_without_false_positive(self):
        """「一、→两、→三、」链条必须全部被识别，不得误报跳号/回退。"""
        from gwtool.core import inspector

        text = ("关于有关工作的通知\n"
                "一、总体要求\n"
                "（一）第一点内容。\n"
                "两、主要任务\n"
                "（一）第二点内容。\n"
                "三、保障措施\n")
        details = [f.detail for f in inspector.inspect_text(text)
                   if f.item == "标题编号"]
        assert details == [], details

    def test_liang_as_first_item_is_still_a_gap(self):
        """「两」= 2 的语义要一致：它作首项时应当报跳号（不能为消误报而放水）。"""
        from gwtool.core import inspector

        text = "关于有关工作的通知\n两、主要任务\n三、保障措施\n"
        details = [f.detail for f in inspector.inspect_text(text)
                   if f.item == "标题编号"]
        assert any("跳号" in d for d in details), details

    def test_real_gap_still_reported(self):
        from gwtool.core import inspector

        text = "关于节日安排的通知\n一、总体要求\n三、主要任务\n"
        details = [f.detail for f in inspector.inspect_text(text)]
        assert any("跳号" in d for d in details), details


class TestConcurrentConnect:
    """A3：连接初始化不得与进行中的写事务相撞。"""

    def test_first_connect_during_write_transaction(self, guard_db):
        """主线程持未提交写事务时，另一线程首次 get_conn() 必须成功。

        缺陷形态（修复前）：每个新连接都要跑一遍 init_schema，其
        `PRAGMA user_version=N` 是写操作，撞上写事务后**立即**
        `database is locked`（busy_timeout 对写锁升级不生效）。
        """
        from gwtool.db import connection as dbconn
        from gwtool.db import dao

        dao.add_document(dao.Document(title="预热", content_text="预热内容" * 20))
        conn = dbconn.get_conn()

        result: dict = {}

        def bg() -> None:
            try:
                dbconn.get_conn()
                result["ok"] = True
            except Exception as exc:
                result["err"] = "%s: %s" % (type(exc).__name__, exc)

        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO documents(title,content_text,text_hash,word_count,"
            "import_time,updated_time) VALUES('占位','x','h-guard',1,'t','t')")
        try:
            t = threading.Thread(target=bg, name="guard-bg-connect")
            t.start()
            t.join(timeout=20)
        finally:
            conn.rollback()

        assert result.get("ok") is True, result.get("err", "后台线程未完成")

    def test_add_document_returns_negative_on_duplicate(self, guard_db):
        """重复内容仍返回 -1（A3 修复不得改变既有语义）。"""
        from gwtool.db import dao

        first = dao.add_document(dao.Document(title="甲", content_text="同一份内容"))
        assert first > 0
        again = dao.add_document(dao.Document(title="乙", content_text="同一份内容"))
        assert again == -1

    def test_schema_version_stable_across_reconnect(self, guard_db):
        """幂等短路不得影响 user_version 语义（重连后仍是最新版本号）。"""
        from gwtool.db import connection as dbconn
        from gwtool.db.schema import SCHEMA_VERSION

        c1 = dbconn.get_conn()
        v1 = c1.execute("PRAGMA user_version").fetchone()[0]
        dbconn.close_current_thread()
        c2 = dbconn.get_conn()
        v2 = c2.execute("PRAGMA user_version").fetchone()[0]
        assert v1 == SCHEMA_VERSION
        assert v2 == SCHEMA_VERSION


class TestBlockSync:
    """A10：已确认真实错词下，块同步偏移必须正确（实测 5 场景全对）。

    用**内置精标对**（布署→部署、按装→安装）驱动，不依赖数据库里的词表；
    每次测试前清一次纠错缓存，避免与其它用例共享的模块级缓存互相干扰。
    """

    @pytest.fixture(autouse=True)
    def _clean_corrector(self, guard_db):
        from gwtool.core import corrector

        corrector.invalidate_cache()
        yield

    @staticmethod
    def _apply(old_text, blocks, confirmed):
        from gwtool.core.batch import _apply_to_blocks

        out = _apply_to_blocks(json.dumps(blocks, ensure_ascii=False),
                               old_text, confirmed, {})
        return [b.get("text") for b in json.loads(out)]

    def test_ordered_blocks(self):
        got = self._apply(
            "甲布署完成。乙按装良好。",
            [{"text": "甲布署完成。"}, {"text": "乙按装良好。"}],
            [(1, 3, "布署", "部署"), (7, 9, "按装", "安装")])
        assert got == ["甲部署完成。", "乙安装良好。"]

    def test_reordered_blocks(self):
        got = self._apply(
            "甲布署完成。乙按装良好。",
            [{"text": "乙按装良好。"}, {"text": "甲布署完成。"}],
            [(1, 3, "布署", "部署"), (7, 9, "按装", "安装")])
        assert got == ["乙安装良好。", "甲部署完成。"]

    def test_same_text_only_second_confirmed(self):
        got = self._apply(
            "布署完成。中间段。布署完成。",
            [{"text": "布署完成。"}, {"text": "中间段。"}, {"text": "布署完成。"}],
            [(9, 11, "布署", "部署")])
        assert got == ["布署完成。", "中间段。", "部署完成。"]

    def test_whitespace_variant(self):
        got = self._apply(
            "甲布署完成。 乙布署完成。",
            [{"text": "甲布署完成。"}, {"text": "乙布署完成。"}],
            [(1, 3, "布署", "部署"), (8, 10, "布署", "部署")])
        assert got == ["甲部署完成。", "乙部署完成。"]


class TestParagraphOffsets:
    """A6：derive_blocks 的 char_offset 必须能切出块文本本身。"""

    def test_offsets_are_correct_and_monotonic(self):
        from gwtool.core import paragraph_ref as pr

        content = "第一段内容。重复文本块。第二段内容。重复文本块。第四段内容。"
        blocks = [
            {"type": "p", "text": "重复文本块。"},
            {"type": "p", "text": "第一段内容。"},
            {"type": "p", "text": "第二段内容。"},
            {"type": "p", "text": "重复文本块。"},
            {"type": "p", "text": "第四段内容。"},
        ]
        out = pr.derive_blocks(json.dumps(blocks, ensure_ascii=False), content)
        offsets = []
        for b in out:
            text = b.get("text") if isinstance(b, dict) else getattr(b, "text", "")
            off = b.get("char_offset") if isinstance(b, dict) else getattr(b, "char_offset", None)
            assert off is not None, b
            # 关键断言：offset 处切出来的就是块文本本身（不重叠、不偏移）
            assert content[off:off + len(text)] == text, (text, off)
            offsets.append(off)
        # 块顺序是刻意乱序的，故 offsets 不单调属正常；但**偏移集合**必须与
        # 正文中五个块的真实起点一一对应（无交错、无错位）。
        assert sorted(offsets) == [0, 6, 12, 18, 24], offsets


# ================================================================ v1.7.2 收尾
class TestBackupEncryptionFlag:
    """B14：加密备份判定改读包内明文标记，文件名约定降级为回退。"""

    @staticmethod
    def _mk_zip(path, *, flag: bool, data: bytes = b"x"):
        import zipfile

        with zipfile.ZipFile(path, "w") as zf:
            if flag:
                zf.writestr("ENCRYPTED", "")
            zf.writestr("gwtool.db", data)
        return path

    def test_plain_package_not_encrypted(self, tmp_path):
        from gwtool.core import backup

        p = self._mk_zip(tmp_path / "gwtool_backup_a.zip", flag=False)
        assert backup.is_encrypted_backup(p) is False

    def test_flag_detected_even_after_rename(self, tmp_path):
        """核心收益：用户改了名，包内标记仍在（文件名约定做不到这一点）。"""
        from gwtool.core import backup

        p = self._mk_zip(tmp_path / "我的重要备份.zip", flag=True)
        assert backup.is_encrypted_backup(p) is True

    def test_legacy_filename_fallback(self, tmp_path):
        """旧版加密包（无标记条目）按文件名 "_加密" 约定回退识别。"""
        from gwtool.core import backup

        p = self._mk_zip(tmp_path / "gwtool_backup_x_加密.zip", flag=False)
        assert backup.is_encrypted_backup(p) is True

    def test_broken_package_returns_false(self, tmp_path):
        from gwtool.core import backup

        p = tmp_path / "broken.zip"
        p.write_bytes(b"not a zip at all")
        assert backup.is_encrypted_backup(p) is False


class TestBatchCooperativeCancel:
    """B5：批量循环必须支持协作式取消（cancelled 回调，按篇落点中断）。"""

    def test_batch_compile_each_stops_on_cancel(self, tmp_path, monkeypatch):
        from gwtool.core import batch

        # 3 份文档，第 2 份开始前取消：第 1 份照常完成，第 3 份不再尝试
        calls = {"n": 0}

        def fake_get_document(did):
            calls["n"] += 1
            return None          # 全部"文档不存在"，走最快失败路径

        monkeypatch.setattr(batch.dao, "get_document", fake_get_document)
        cancel_after_first = {"done": 0}

        def cancelled():
            return cancel_after_first["done"] >= 1

        def progress_cb(i, n):
            cancel_after_first["done"] = i

        paths, failures = batch.batch_compile_each(
            [1, 2, 3], None, str(tmp_path), cancelled=cancelled,
            progress_cb=progress_cb)
        # 第 1 篇失败后回调 progress(1,3)，此后 cancelled 为真 —— 只碰了 1 篇
        assert calls["n"] == 1, calls

    def test_scan_stops_on_cancel(self, guard_db, monkeypatch):
        """_scan 在 cancelled 为真时不再取下一篇（已扫描部分保留）。"""
        from gwtool.core import batch

        rows = [{"id": i, "title": f"t{i}", "content_text": "内容。"}
                for i in range(5)]

        def fake_iter(**kwargs):
            yield from rows

        monkeypatch.setattr(batch.dao, "iter_documents_content", fake_iter)
        monkeypatch.setattr(batch.dao, "count_documents", lambda *a, **k: 5)
        res = batch.batch_correct(doc_ids=[1, 2, 3, 4, 5], cancelled=lambda: True)
        assert res.scanned == 0        # 一开始就取消：一篇都不碰

        seen = {"n": 0}
        res2 = batch.batch_correct(
            doc_ids=[1, 2, 3, 4, 5],
            progress_cb=lambda i, n: seen.__setitem__("n", i),
            cancelled=lambda: seen["n"] >= 2)
        assert res2.scanned == 2, res2.scanned


class TestComProbeSkipSwitch:
    """§三.8：COM 探测测试必须可经 GWTOOL_SKIP_COM_TESTS=1 模块级跳过。"""

    def test_switch_present_in_all_three_probe_files(self):
        from pathlib import Path

        for name in ("test_doc_probe.py", "test_genchain_probe.py",
                     "test_import_probe.py"):
            src = (Path(__file__).parent / name).read_text(encoding="utf-8")
            assert "GWTOOL_SKIP_COM_TESTS" in src, name
            assert "allow_module_level=True" in src, name
