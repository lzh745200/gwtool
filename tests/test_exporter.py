# -*- coding: utf-8 -*-
"""批量导出与移交包（C2）的测试。

关键点：
  · manifest 与 `backup.py` 的 `attachments` 段**同构** —— 两套解析同一份
    格式是缺陷温床，将来做"从移交包导入"时要能直接复用备份的校验逻辑；
  · 附件超限必须记入 excluded 并注明原因，**绝不静默丢**（与备份同一纪律）；
  · 原始文件存在就复制原文件，缺失才退化为 txt。
"""
from __future__ import annotations

import hashlib
import json
import zipfile

import pytest

from gwtool.core import exporter
from gwtool.db import dao


def _add(title: str, tmp_path, **kw) -> int:
    base = dict(title=title, content_text=f"{title}的正文", blocks_json="[]",
                file_path=str(tmp_path / "不存在" / f"{title}.docx"))
    base.update(kw)
    return dao.add_document(dao.Document(**base))


class TestSelection:
    def test_all_documents(self, tmp_path, tmp_db):
        _add("甲", tmp_path)
        _add("乙", tmp_path)
        assert len(exporter.select_documents(exporter.ExportRequest())) == 2

    def test_by_category(self, tmp_path, tmp_db):
        cid = dao.add_category("综合")
        _add("甲", tmp_path, category_id=cid)
        _add("乙", tmp_path)
        got = exporter.select_documents(exporter.ExportRequest(category_id=cid))
        assert [d.title for d in got] == ["甲"]

    def test_by_tag(self, tmp_path, tmp_db):
        _add("甲", tmp_path, tags="安全,综合")
        _add("乙", tmp_path, tags="财务")
        got = exporter.select_documents(exporter.ExportRequest(tag="安全"))
        assert [d.title for d in got] == ["甲"]

    def test_by_date_range(self, tmp_path, tmp_db):
        _add("甲", tmp_path)
        got = exporter.select_documents(
            exporter.ExportRequest(date_from="2999-01-01"))
        assert got == []

    def test_sorted_by_import_time(self, tmp_path, tmp_db):
        _add("甲", tmp_path)
        _add("乙", tmp_path)
        got = exporter.select_documents(exporter.ExportRequest())
        assert [d.title for d in got] == ["甲", "乙"]

    def test_empty_range_raises(self, tmp_path, tmp_db):
        with pytest.raises(ValueError):
            exporter.build(exporter.ExportRequest(
                out_path=str(tmp_path / "p.zip")))


class TestBuild:
    def test_creates_zip_with_manifest(self, tmp_path, tmp_db):
        _add("甲", tmp_path)
        out = tmp_path / "p.zip"
        rep = exporter.build(exporter.ExportRequest(out_path=str(out)))
        assert rep["documents"] == 1
        with zipfile.ZipFile(str(out)) as zf:
            assert exporter.MANIFEST_NAME in zf.namelist()

    def test_copies_original_file_when_present(self, tmp_path, tmp_db):
        src = tmp_path / "原件.docx"
        src.write_bytes(b"original-bytes")
        dao.add_document(dao.Document(title="甲", content_text="x",
                                      file_path=str(src)))
        out = tmp_path / "p.zip"
        exporter.build(exporter.ExportRequest(out_path=str(out)))
        with zipfile.ZipFile(str(out)) as zf:
            entry = [n for n in zf.namelist() if n.startswith("documents/")][0]
            assert entry.endswith(".docx")
            assert zf.read(entry) == b"original-bytes"
        man = exporter.read_manifest(out)
        assert man["documents"][0]["source"] == "原文件"

    def test_falls_back_to_text_when_missing(self, tmp_path, tmp_db):
        _add("乙", tmp_path)          # file_path 指向不存在的文件
        out = tmp_path / "p.zip"
        exporter.build(exporter.ExportRequest(out_path=str(out)))
        with zipfile.ZipFile(str(out)) as zf:
            entry = [n for n in zf.namelist() if n.startswith("documents/")][0]
            assert entry.endswith(".txt")
            assert zf.read(entry).decode("utf-8") == "乙的正文"
        man = exporter.read_manifest(out)
        assert man["documents"][0]["source"] == "导出文本"

    def test_duplicate_titles_do_not_overwrite(self, tmp_path, tmp_db):
        """同名文档在包内必须各占一条，否则交接时凭空少一份。"""
        # content_text 必须不同：DAO 按内容哈希去重，内容相同会被判重而不入库
        _add("同名", tmp_path, content_text="正文一")
        _add("同名", tmp_path, content_text="正文二")
        out = tmp_path / "p.zip"
        rep = exporter.build(exporter.ExportRequest(out_path=str(out)))
        assert rep["documents"] == 2
        with zipfile.ZipFile(str(out)) as zf:
            docs = [n for n in zf.namelist() if n.startswith("documents/")]
        assert len(docs) == len(set(docs)) == 2

    def test_manifest_records_sha256(self, tmp_path, tmp_db):
        _add("甲", tmp_path)
        out = tmp_path / "p.zip"
        exporter.build(exporter.ExportRequest(out_path=str(out)))
        man = exporter.read_manifest(out)
        entry = man["documents"][0]
        with zipfile.ZipFile(str(out)) as zf:
            blob = zf.read(entry["path"])
        assert entry["sha256"] == hashlib.sha256(blob).hexdigest()
        assert entry["bytes"] == len(blob)

    def test_manifest_records_scope(self, tmp_path, tmp_db):
        cid = dao.add_category("综合")
        _add("甲", tmp_path, category_id=cid, tags="安全,综合")
        out = tmp_path / "p.zip"
        exporter.build(exporter.ExportRequest(
            out_path=str(out), category_id=cid, tag="安全",
            date_from="2026-01-01", note="移交说明"))
        man = exporter.read_manifest(out)
        assert man["scope"]["category_id"] == cid
        assert man["scope"]["tag"] == "安全"
        assert man["note"] == "移交说明"
        assert man["kind"] == "handover"


class TestManifestCompatibilityWithBackup:
    def test_attachments_segment_shape(self, tmp_path, tmp_db):
        """与 backup._manifest_json 的 attachments 段同构。"""
        _add("甲", tmp_path)
        out = tmp_path / "p.zip"
        exporter.build(exporter.ExportRequest(out_path=str(out)))
        seg = exporter.read_manifest(out)["attachments"]
        for key in ("mode", "limit_mb", "limit_text", "included", "excluded"):
            assert key in seg, f"attachments 段缺少 {key}"

    def test_manifest_is_valid_json_utf8(self, tmp_path, tmp_db):
        _add("中文标题", tmp_path)
        out = tmp_path / "p.zip"
        exporter.build(exporter.ExportRequest(out_path=str(out)))
        with zipfile.ZipFile(str(out)) as zf:
            data = zf.read(exporter.MANIFEST_NAME).decode("utf-8")
        json.loads(data)
        assert "中文标题" in data

    def test_backup_and_exporter_share_top_level_keys(self):
        """"created/version/note" 三个顶层键两边都有，便于将来统一解析。"""
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        src = (root / "gwtool" / "core" / "backup.py").read_text(encoding="utf-8")
        for key in ('"created"', '"version"', '"note"'):
            assert key in src


class TestAttachmentLimits:
    def test_excluded_records_reason(self, tmp_path, tmp_db, monkeypatch):
        """附件体积超限必须写入 excluded 并注明原因，绝不静默丢。"""
        did = _add("甲", tmp_path)
        from gwtool.paths import attachments_dir
        att_file = attachments_dir() / "big.pdf"
        att_file.parent.mkdir(parents=True, exist_ok=True)
        att_file.write_bytes(b"x" * 5000)
        dao.add_attachment(did, "big.pdf", "attachments/big.pdf", 5000)
        out = tmp_path / "p.zip"
        rep = exporter.build(exporter.ExportRequest(
            out_path=str(out), limit_mb=0))       # 0 MB -> 一律超限
        assert rep["excluded"] == 1
        seg = exporter.read_manifest(out)["attachments"]
        assert seg["excluded"][0]["reason"]

    def test_attachments_included_by_default(self, tmp_path, tmp_db):
        did = _add("甲", tmp_path)
        from gwtool.paths import attachments_dir
        att_file = attachments_dir() / "small.pdf"
        att_file.parent.mkdir(parents=True, exist_ok=True)
        att_file.write_bytes(b"y" * 10)
        dao.add_attachment(did, "small.pdf", "attachments/small.pdf", 10)
        out = tmp_path / "p.zip"
        rep = exporter.build(exporter.ExportRequest(out_path=str(out)))
        assert rep["attachments"] == 1

    def test_attachments_can_be_skipped(self, tmp_path, tmp_db):
        did = _add("甲", tmp_path)
        from gwtool.paths import attachments_dir
        att_file = attachments_dir() / "a.pdf"
        att_file.parent.mkdir(parents=True, exist_ok=True)
        att_file.write_bytes(b"z")
        dao.add_attachment(did, "a.pdf", "attachments/a.pdf", 1)
        out = tmp_path / "p.zip"
        rep = exporter.build(exporter.ExportRequest(
            out_path=str(out), include_attachments=False))
        assert rep["attachments"] == 0
        assert exporter.read_manifest(out)["attachments"]["mode"] == "excluded"

    def test_missing_attachment_file_is_reported(self, tmp_path, tmp_db):
        did = _add("甲", tmp_path)
        dao.add_attachment(did, "ghost.pdf", "attachments/ghost.pdf", 100)
        out = tmp_path / "p.zip"
        rep = exporter.build(exporter.ExportRequest(out_path=str(out)))
        assert rep["excluded"] == 1
        seg = exporter.read_manifest(out)["attachments"]
        assert "不在数据目录" in seg["excluded"][0]["reason"]


class TestWiring:
    def test_menu_entry_exists(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        src = (root / "gwtool" / "ui" / "main_window.py").read_text(
            encoding="utf-8")
        assert "批量导出与移交包…" in src
        assert "exporter.build" in src

    def test_default_filename_is_zip(self):
        assert exporter.default_filename().endswith(".zip")
