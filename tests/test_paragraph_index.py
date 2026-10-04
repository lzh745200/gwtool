# -*- coding: utf-8 -*-
"""段落索引的**不变量**测试（规格说明书 §8.4 六条）。

段落表是**派生数据**（源 = documents.blocks_json），"可随时整表重建"是
本功能"零副作用"承诺的根基。这一组测试就是守住那个根基：只要它们全绿，
"段落表删掉/重建/损坏都不影响既有功能"就不是一句口头承诺。

六条不变量：
  1. 一致性       —— paragraphs_of 与现场派生结果完全相同
  2. 落点不变量   —— content_text[off:off+len] == text
  3. 幂等性       —— 连续两次增量重建，第二次 rescanned == 0
  4. 兜底等价     —— blocks_json='[]' 时 == 纯文本切分结果
  5. 回收站隔离   —— 软删除的文档不进段落索引
  6. 哈希驱动     —— 正文改动后段落必须被重切
"""
from __future__ import annotations

import json

from gwtool.core import paragraph_ref as pr
from gwtool.core.model import PARAGRAPH, HEADING, Block, DocTree
from gwtool.db import dao


# ---------------------------------------------------------------- 夹具
def _tree_doc(title: str = "关于开展安全生产检查的通知") -> tuple[str, str]:
    tree = DocTree(title=title)
    tree.blocks = [
        Block(type=HEADING, level=1, text=title),
        Block(type=PARAGRAPH, text="各分局："),
        Block(type=PARAGRAPH,
              text="为进一步加强安全生产管理，现就有关事项通知如下。"),
        Block(type=HEADING, level=2, text="一、工作目标"),
        Block(type=PARAGRAPH, text="通过全面排查，消除各类事故隐患。"),
        Block(type=PARAGRAPH, text="特此通知。"),
    ]
    content = "\n".join(b.text for b in tree.blocks)
    return content, tree.to_json()


def _add(title: str = "关于开展安全生产检查的通知") -> tuple[int, str, str]:
    content, blocks = _tree_doc(title)
    did = dao.add_document(dao.Document(
        title=title, content_text=content, blocks_json=blocks))
    assert did > 0
    return did, content, blocks


# ---------------------------------------------------------------- 1 一致性
class TestIndexMatchesLiveDerivation:
    def test_paragraphs_of_equals_live_derivation(self, tmp_db):
        did, content, blocks = _add()
        dao.rebuild_paragraphs()
        from_index = pr.paragraphs_of(did)
        live = pr.derive_blocks(blocks, content)
        assert len(from_index) == len(live)
        for a, b in zip(from_index, live):
            assert (a.ordinal, a.kind, a.level, a.text, a.char_offset) == \
                (b.ordinal, b.kind, b.level, b.text, b.char_offset)

    def test_write_path_indexes_immediately(self, tmp_db):
        """入库即写：`add_document` 之后段落索引当场就有。

        这是"刚导入的材料立刻能在段落检索里搜到"的前提 —— 若只靠启动时
        回填，用户导入完马上检索会什么都搜不到。
        """
        did, _, _ = _add()
        assert dao.paragraph_count(did) > 0

    def test_works_without_index_at_all(self, tmp_db):
        """把索引整表清掉后功能照常 —— 这是"可整表重建"的实证。"""
        did, _, blocks = _add()
        dao.rebuild_paragraphs()
        from gwtool.db.connection import get_conn
        get_conn().execute("DELETE FROM paragraphs_fts")
        get_conn().execute("DELETE FROM paragraphs")
        get_conn().execute("DELETE FROM fts_index_state WHERE kind='paragraphs'")
        get_conn().commit()
        assert dao.paragraph_count() == 0          # 索引确实没了
        refs = pr.paragraphs_of(did)               # 改走现场派生
        assert refs and refs[0].text.startswith("关于开展")
        assert [r.ordinal for r in refs] == list(range(len(refs)))

    def test_index_is_fresh_after_rebuild(self, tmp_db):
        did, _, _ = _add()
        dao.rebuild_paragraphs()
        assert pr.index_is_fresh(did) is True

    def test_index_stays_fresh_after_dao_write(self, tmp_db):
        """经 DAO 改内容后索引**当场就是新的**（写入路径同步维护）。"""
        did, _, _ = _add()
        dao.rebuild_paragraphs()
        dao.update_document_content(did, "新标题", "新标题\n只有一段正文")
        assert pr.index_is_fresh(did) is True
        assert [r.text for r in dao.list_paragraphs(did)] == ["新标题", "只有一段正文"]

    def test_index_stale_only_after_out_of_band_write(self, tmp_db):
        """绕过 DAO 直接改库才会让索引落后 —— 这正是哈希判据要抓的情况。"""
        did, _, _ = _add()
        dao.rebuild_paragraphs()
        from gwtool.db.connection import get_conn
        get_conn().execute(
            "UPDATE documents SET content_text=?, text_hash=? WHERE id=?",
            ("外部改过的正文。", dao.text_hash("外部改过的正文。"), did))
        get_conn().commit()
        assert pr.index_is_fresh(did) is False


# ---------------------------------------------------------------- 2 落点不变量
class TestAnchorInvariant:
    def test_offset_slice_equals_paragraph_text(self, tmp_db):
        did, content, _ = _add()
        dao.rebuild_paragraphs()
        rows = [r for r in dao.list_paragraphs(did) if r.char_offset >= 0]
        assert rows, "至少应有一个段落成功定位"
        for r in rows:
            assert content[r.char_offset:r.char_offset + len(r.text)] == r.text

    def test_unlocatable_text_gets_minus_one(self, tmp_db):
        """块里有正文里根本不存在的文本 -> 偏移必须是 -1，不能是猜的位置。"""
        blocks = json.dumps([
            Block(type=PARAGRAPH, text="正文里有这句").to_dict(),
            Block(type=PARAGRAPH, text="这句在正文里没有").to_dict(),
        ], ensure_ascii=False)
        rows = pr.derive_blocks(blocks, "正文里有这句")
        assert rows[0].char_offset == 0
        assert rows[1].char_offset == -1        # 不猜

    def test_resolve_anchor_returns_true_slice(self, tmp_db):
        did, content, _ = _add()
        dao.rebuild_paragraphs()
        ref = pr.paragraphs_of(did)[3]          # "一、工作目标"
        off, length = pr.resolve_anchor(ref)
        assert off >= 0
        assert content[off:off + length] == ref.text

    def test_resolve_anchor_falls_back_to_live_locate(self, tmp_db):
        """索引里的偏移过期时，resolve_anchor 现场重定位而不是返回错位置。"""
        did, content, _ = _add()
        dao.rebuild_paragraphs()
        ref = pr.paragraphs_of(did)[0]
        ref.char_offset = -1                    # 模拟索引里没有偏移
        off, length = pr.resolve_anchor(ref)
        assert off >= 0
        assert content[off:off + length] == ref.text

    def test_resolve_anchor_gives_up_cleanly(self, tmp_db):
        """正文里找不到该段 -> 返回 -1，调用方据此降级，不抛异常。"""
        did, _, _ = _add()
        dao.rebuild_paragraphs()
        ref = pr.paragraphs_of(did)[0]
        ref.text = "这段文本在正文里完全不存在"
        ref.char_offset = -1
        assert pr.resolve_anchor(ref) == (-1, 0)


# ---------------------------------------------------------------- 3 幂等性
class TestIdempotency:
    def test_second_incremental_rebuild_rescans_nothing(self, tmp_db):
        _add()
        first = dao.rebuild_paragraphs()
        assert first["documents"] >= 1
        second = dao.rebuild_paragraphs()
        assert second["rescanned"] == 0, "内容未变却重新切分了，哈希判据失效"
        assert second["paragraphs"] == first["paragraphs"]

    def test_full_rebuild_still_correct(self, tmp_db):
        did, content, blocks = _add()
        dao.rebuild_paragraphs()
        counts = dao.rebuild_paragraphs(incremental=False)
        assert counts["rescanned"] >= 1
        live = pr.derive_blocks(blocks, content)
        assert counts["paragraphs"] == len(live)

    def test_rebuild_twice_does_not_duplicate_paragraphs(self, tmp_db):
        did, _, _ = _add()
        dao.rebuild_paragraphs()
        n1 = dao.paragraph_count(did)
        dao.rebuild_paragraphs()
        dao.rebuild_paragraphs()
        assert dao.paragraph_count(did) == n1, "重复重建产生了重复段落"

    def test_fts_rows_do_not_accumulate(self, tmp_db):
        """整体替换必须先清 FTS 旧行：否则每重建一次就多一批孤儿 FTS 行。"""
        from gwtool.db.connection import get_conn
        did, _, _ = _add()
        dao.rebuild_paragraphs()
        n1 = get_conn().execute("SELECT COUNT(*) FROM paragraphs_fts").fetchone()[0]
        dao.rebuild_paragraphs(incremental=False)
        n2 = get_conn().execute("SELECT COUNT(*) FROM paragraphs_fts").fetchone()[0]
        assert n1 == n2
        assert n1 == dao.paragraph_count()


# ---------------------------------------------------------------- 4 兜底等价
class TestPlainTextFallback:
    def test_empty_blocks_falls_back_to_text_split(self, tmp_db):
        text = "标题\n\n第一段正文。\n第二段正文。"
        rows = pr.derive_blocks("[]", text)
        assert [r.text for r in rows] == ["标题", "第一段正文。", "第二段正文。"]

    def test_broken_json_falls_back_to_text_split(self, tmp_db):
        text = "标题\n第一段正文。"
        rows = pr.derive_blocks("{不是合法 JSON", text)
        assert [r.text for r in rows] == ["标题", "第一段正文。"]

    def test_first_short_line_becomes_level1_heading(self, tmp_db):
        rows = pr.derive_blocks("[]", "关于X的通知\n正文内容在这里。")
        assert rows[0].kind == HEADING
        assert rows[0].level == 1

    def test_long_first_line_is_not_a_heading(self, tmp_db):
        long_first = "这是一段很长很长完全没有标题形态的正文内容" * 3
        rows = pr.derive_blocks("[]", long_first)
        assert rows[0].kind == PARAGRAPH

    def test_fallback_matches_indexed_result(self, tmp_db):
        """同一份"只有文本没有块"的文档，索引路径与现场路径必须一致。"""
        text = "标题\n第一段。\n第二段。"
        did = dao.add_document(dao.Document(
            title="标题", content_text=text, blocks_json="[]"))
        dao.rebuild_paragraphs()
        indexed = pr.paragraphs_of(did)
        live = pr.derive_blocks("[]", text)
        assert [r.text for r in indexed] == [r.text for r in live]
        assert [r.char_offset for r in indexed] == [r.char_offset for r in live]

    def test_table_block_becomes_one_paragraph(self, tmp_db):
        blocks = json.dumps([Block(type="table", rows=[
            ["年度", "数量"], ["2026", "12"]]).to_dict()], ensure_ascii=False)
        rows = pr.derive_blocks(blocks, "年度 | 数量\n2026 | 12")
        assert len(rows) == 1
        assert rows[0].kind == "table"
        assert "2026" in rows[0].text

    def test_stale_blocks_fall_back_to_content(self, tmp_db):
        """块与正文完全对不上 -> 判定块陈旧，改用正文切分。

        不能继续信块：那会让用户在"我的参考文献"里看到一段自己文档里
        根本没有的内容，并可能把它当成自己的东西用下去。
        """
        blocks = json.dumps([Block(type=PARAGRAPH, text="这是很久以前的旧正文").to_dict()],
                            ensure_ascii=False)
        rows = pr.derive_blocks(blocks, "这是刚改过的新正文。")
        assert [r.text for r in rows] == ["这是刚改过的新正文。"]

    def test_partially_locatable_blocks_are_kept(self, tmp_db):
        """只要有块能在正文里定位到，就继续信块（不因个别失配就整体丢弃）。"""
        blocks = json.dumps([
            Block(type=PARAGRAPH, text="第一段。").to_dict(),
            Block(type=PARAGRAPH, text="某段在正文里没有").to_dict(),
        ], ensure_ascii=False)
        rows = pr.derive_blocks(blocks, "第一段。")
        assert len(rows) == 2
        assert rows[0].char_offset == 0
        assert rows[1].char_offset == -1


# ---------------------------------------------------------------- 5 回收站隔离
class TestRecycleBinIsolation:
    def test_deleted_doc_is_not_searchable(self, tmp_db):
        did, _, _ = _add("关于安全生产检查的专项通知")
        dao.rebuild_paragraphs()
        assert dao.search_paragraphs_fts("安全生产", 20)
        dao.delete_document(did)                 # 移入回收站
        dao.rebuild_paragraphs()
        assert not dao.search_paragraphs_fts("安全生产", 20), \
            "回收站里的材料又变成可检索了"

    def test_deleted_doc_paragraphs_are_dropped_on_rebuild(self, tmp_db):
        did, _, _ = _add()
        dao.rebuild_paragraphs()
        assert dao.paragraph_count(did) > 0
        dao.delete_document(did)
        dao.rebuild_paragraphs()
        assert dao.paragraph_count(did) == 0

    def test_search_joins_documents_so_stale_rows_stay_hidden(self, tmp_db):
        """即便段落行残留在库里（外部改库/旧备份），检索也不得命中。

        search_paragraphs_fts 里 JOIN documents 并追加 deleted_time='' 就是
        为这种情况准备的：软删除时 FTS 行会被摘掉，但残留行仍可能来自
        恢复旧备份 —— 那道保险必须真的生效。
        """
        did, _, _ = _add("关于安全生产检查的专项通知")
        dao.rebuild_paragraphs()
        # 制造"文档已进回收站、段落行却还在"的状态
        from gwtool.db.connection import get_conn
        get_conn().execute("UPDATE documents SET deleted_time=? WHERE id=?",
                           ("2026-09-23 10:00:00", did))
        get_conn().commit()
        assert dao.paragraph_count(did) > 0, "前提：段落行仍在"
        assert not dao.search_paragraphs_fts("安全生产", 20), \
            "残留在索引里的回收站材料被检索命中了"


# ---------------------------------------------------------------- 6 哈希驱动
class TestContentHashDrivesReslice:
    def test_content_change_reflected_immediately(self, tmp_db):
        """改内容后段落当场就是新的（写入路径同步重切）。"""
        did, _, _ = _add()
        dao.rebuild_paragraphs()
        dao.update_document_content(did, "改后标题", "改后标题\n新正文一段。\n新正文两段。")
        assert [r.text for r in dao.list_paragraphs(did)] == \
            ["改后标题", "新正文一段。", "新正文两段。"]

    def test_rebuild_after_dao_write_rescans_nothing(self, tmp_db):
        """写入路径已经重切过，随后的增量重建不该再做重复劳动。"""
        did, _, _ = _add()
        dao.rebuild_paragraphs()
        dao.update_document_content(did, "改后标题", "改后标题\n新正文一段。")
        assert dao.rebuild_paragraphs()["rescanned"] == 0

    def test_out_of_band_edit_is_resliced_on_rebuild(self, tmp_db):
        """绕过 DAO 改库后，哈希判据必须让重建把它重切。"""
        did, _, _ = _add()
        dao.rebuild_paragraphs()
        assert dao.rebuild_paragraphs()["rescanned"] == 0
        from gwtool.db.connection import get_conn
        get_conn().execute(
            "UPDATE documents SET content_text=?, text_hash=? WHERE id=?",
            ("外部改后的正文。", dao.text_hash("外部改后的正文。"), did))
        get_conn().commit()
        counts = dao.rebuild_paragraphs()
        assert counts["rescanned"] >= 1, "正文变了段落却没重切"
        assert [r.text for r in dao.list_paragraphs(did)] == ["外部改后的正文。"]

    def test_paragraphs_of_reslices_stale_doc_on_the_fly(self, tmp_db):
        """索引过期时 paragraphs_of 必须体现最新内容，而不是返回旧段。"""
        did, _, _ = _add()
        dao.rebuild_paragraphs()
        from gwtool.db.connection import get_conn
        get_conn().execute(
            "UPDATE documents SET content_text=?, text_hash=? WHERE id=?",
            ("唯一的新正文。", dao.text_hash("唯一的新正文。"), did))
        get_conn().commit()
        refs = pr.paragraphs_of(did)
        assert [r.text for r in refs] == ["唯一的新正文。"]
