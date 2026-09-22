# -*- coding: utf-8 -*-
"""公文归档与保管期限（A3）的测试。

要点：
  · **在办件不许归档**（会造成卷内缺件，比不归档更难收拾）；
  · **已有档号的不重编**——档案号一旦错乱，实物卷与台账就再也对不上；
  · 移交清单是签字盖章随卷走的正式文书，必须走公文体式渲染。
"""
from __future__ import annotations

import pytest

from gwtool.core import archive
from gwtool.db import dao


def _rec(**kw) -> dao.Receive:
    base = dict(reg_no="收〔2026〕1号", incoming_no="×政发〔2026〕12号",
                title="关于做好安全生产工作的通知", doc_type="通知",
                from_org="××市人民政府办公室", receive_date="2026-09-01",
                due_date="2026-09-05", done_date="2026-09-06",
                status="已办结", handler_dept="安全科")
    base.update(kw)
    return dao.Receive(**base)


class TestSelection:
    def test_includes_closed_unarchived(self, tmp_db):
        dao.add_receive(_rec())
        assert len(archive.list_archivable(year="2026")) == 1

    def test_excludes_unfinished(self, tmp_db):
        """在办件归档会造成卷内缺件。"""
        dao.add_receive(_rec(done_date="", status="承办"))
        assert archive.list_archivable(year="2026") == []

    def test_excludes_already_archived(self, tmp_db):
        dao.add_receive(_rec(status="已归档", archive_no="A2026-0001"))
        assert archive.list_archivable(year="2026") == []

    def test_excludes_rows_with_archive_no_even_if_status_stale(self, tmp_db):
        """状态没同步但已有档号，同样视作已归档（不重复入册）。"""
        dao.add_receive(_rec(status="已办结", archive_no="A2026-0001"))
        assert archive.list_archivable(year="2026") == []

    def test_include_archived_flag(self, tmp_db):
        dao.add_receive(_rec(status="已归档", archive_no="A2026-0001"))
        got = archive.list_archivable(year="2026", include_archived=True)
        assert len(got) == 1

    def test_sorted_by_done_date(self, tmp_db):
        dao.add_receive(_rec(done_date="2026-09-20", title="晚"))
        dao.add_receive(_rec(done_date="2026-09-02", title="早"))
        got = archive.list_archivable(year="2026")
        assert [r.title for r in got] == ["早", "晚"]


class TestArchiveNo:
    def test_format(self):
        assert archive.next_archive_no("XX局", "2026", 7) == "XX局2026-0007"

    def test_padding(self):
        assert archive.next_archive_no("", "2026", 123) == "2026-0123"

    def test_suggest_skips_existing_numbers(self, tmp_db):
        rows = [_rec(archive_no="A2026-0005"), _rec(title="乙"), _rec(title="丙")]
        got = [(r.title, no) for r, no in archive.suggest_batch(rows, "A", "2026")]
        # 已有档号的那条被跳过，序号从 0001 起分给其余两条
        assert got == [("乙", "A2026-0001"), ("丙", "A2026-0002")]


class TestBatchArchive:
    def test_writes_numbers_and_status(self, tmp_db):
        rid = dao.add_receive(_rec())
        rows = [dao.get_receive(rid)]
        rep = archive.batch_archive(rows, "XX局", "30年", archive_date="2026-10-01")
        assert rep["archived"] == 1
        got = dao.get_receive(rid)
        assert got.archive_no == "XX局2026-0001"
        assert got.retention == "30年"
        assert got.archive_date == "2026-10-01"
        assert got.status == "已归档"

    def test_does_not_renumber_existing(self, tmp_db):
        """已有档号的记录不得重编——档案号错乱后实物卷与台账就对不上了。"""
        rid = dao.add_receive(_rec(archive_no="旧号-001", status="已办结"))
        rows = [dao.get_receive(rid)]
        rep = archive.batch_archive(rows, "XX局", "永久")
        assert rep["skipped"] == 1
        assert dao.get_receive(rid).archive_no == "旧号-001"

    def test_defaults_to_today(self, tmp_db):
        rid = dao.add_receive(_rec())
        rep = archive.batch_archive([dao.get_receive(rid)], "XX局", "10年")
        assert rep["date"]
        assert dao.get_receive(rid).archive_date == rep["date"]

    def test_retention_choices_cover_regulation(self):
        assert set(archive.RETENTION_CHOICES) == {"永久", "30年", "10年"}

    # ---- P3：整批单事务 ------------------------------------------------
    def test_batch_archive_is_atomic_on_failure(self, tmp_db):
        """中途失败必须**整批回滚**，不能留下"一半已归档"的台账。

        归档语义是整批成立：移交清单写着 3 件、台账只落 2 件，比整批失败
        难收拾得多（实物卷已经按清单装订）。逐行 commit 的旧实现下，前两行
        会先落盘 —— 那时本用例为红。
        """
        ids = [dao.add_receive(_rec(title=f"第{i}件"))
               for i in range(1, 4)]
        rows = [dao.get_receive(i) for i in ids]
        # 让第 3 行的某个字段无法绑定到 SQL 参数 → 触发 executemany 中途失败
        rows[-1].pages = object()

        with pytest.raises(Exception):
            archive.batch_archive(rows, "XX局", "永久", year="2026")

        for rid in ids:
            got = dao.get_receive(rid)
            assert got.archive_no == "", f"第 {rid} 件被落盘了，整批回滚失效"
            assert got.status != "已归档"

    def test_batch_archive_does_not_use_per_row_commit(self, tmp_db, monkeypatch):
        """批量归档不得再走逐行提交的 `dao.update_receive`。

        真正的原子性由 `test_batch_archive_is_atomic_on_failure` 断言；这里
        守住"没有退回逐行接口"这条结构性回归 —— `update_receive` 自带
        `conn.commit()`，N 次调用即 N 次提交。

        （不用 SQLite trace 回调数 UPDATE 语句：executemany 同样会按参数
        逐个回调，数出来仍是 N 条，区分不了两种实现。）
        """
        calls: list[int] = []
        real = dao.update_receive

        def spy(r):
            calls.append(r.id)
            return real(r)

        monkeypatch.setattr(dao, "update_receive", spy)
        ids = [dao.add_receive(_rec(title=f"第{i}件")) for i in range(1, 4)]
        rows = [dao.get_receive(i) for i in ids]
        rep = archive.batch_archive(rows, "XX局", "永久", year="2026")
        assert rep["archived"] == 3 and rep["skipped"] == 0
        assert calls == [], "批量归档仍在逐行调用 update_receive（每行一次提交）"
        for rid in ids:
            assert dao.get_receive(rid).status == "已归档"


class TestSummary:
    def test_groups_by_retention(self):
        rows = [_rec(retention="永久"), _rec(retention="30年"),
                _rec(retention="30年"), _rec(retention="")]
        got = archive.retention_summary(rows)
        assert got["30年"] == 2
        assert got["永久"] == 1
        assert got["未标注"] == 1

    def test_empty(self):
        assert archive.retention_summary([]) == {}


class TestManifest:
    def test_tree_structure(self):
        rows = [_rec(retention="30年", archive_no="A2026-0001")]
        tree = archive.build_manifest_tree(rows, year="2026", org="XX局")
        assert "2026" in tree.title
        tables = [b for b in tree.blocks if b.type == "table"]
        assert len(tables) == 1
        head = tables[0].rows[0]
        assert head == ["序号", "档号", "来文字号", "来文机关", "标题",
                        "办结日期", "保管期限"]
        assert tables[0].rows[1][1] == "A2026-0001"

    def test_tree_has_signature_line(self):
        tree = archive.build_manifest_tree([_rec()], year="2026")
        text = "\n".join(b.text for b in tree.blocks if b.text)
        assert "签字" in text, "移交清单必须有签字栏（这是它的用途所在）"

    def test_export_docx(self, tmp_db, tmp_path):
        out = tmp_path / "清单.docx"
        archive.export_manifest([_rec(archive_no="A2026-0001")], str(out),
                                year="2026", org="XX局")
        assert out.exists() and out.stat().st_size > 0

    def test_export_xlsx(self, tmp_db, tmp_path):
        out = tmp_path / "清单.xlsx"
        n = archive.export_manifest_xlsx([_rec(archive_no="A2026-0001")], str(out))
        assert n == 1
        assert out.exists()

    def test_docx_uses_official_template_path(self):
        """必须走 docxgen 通路（公文体式），不能自己拼 docx。"""
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        src = (root / "gwtool" / "core" / "archive.py").read_text(
            encoding="utf-8")
        assert "from .docxgen import generate_docx" in src
        assert "default_template" in src


class TestWiring:
    def test_receive_dialog_has_archive_button(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        src = (root / "gwtool" / "ui" / "receive_dialog.py").read_text(
            encoding="utf-8")
        assert '"归档", self.archive_selected' in src
        assert "archive.batch_archive" in src

    def test_ui_blocks_unfinished_archiving(self):
        import pathlib
        src_path = (pathlib.Path(__file__).resolve().parent.parent
                    / "gwtool" / "ui" / "receive_dialog.py")
        src = src_path.read_text(encoding="utf-8")
        idx = src.index("def archive_selected")
        block = src[idx:idx + 900]
        assert "is_closed" in block, "界面层也要拦住未办结的件"
