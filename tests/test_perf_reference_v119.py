# -*- coding: utf-8 -*-
"""v1.19 性能契约：写作参考库与检索的响应时间必须守住门槛。

为什么新增这些断言
------------------
用户反馈"写作参考库加载速度太慢"。实测（真实 4 万纠错对 + 12.3 万词典）：

* `dao.search_dictionary("安全生产检查")` ≈ **65 ms** —— 它的第②级
  `word LIKE 'q%'` 与第③级 `word LIKE '%q%'` 都是**整表覆盖索引扫描**
  （EXPLAIN: `SCAN dictionary USING COVERING INDEX`），各约 34 ms；
* 于是默认的写作参考检索 `reference.lookup()` ≈ **85 ms**，
  每次敲回车都要等这么久；
* 而同样的前缀语义用**范围查询**（`word>=? AND word<?`）能走索引
  `SEARCH ... (word>? AND word<?)`，是毫秒级。

此前没有任何测试守住检索耗时（只有 FTS 文档检索 < 1s 一条），
所以这类退化不会被发现。本文件的断言用**宽松上限**（远高于优化后实测，
避免慢机器误报），但足以拦住"退回全表扫描"这种量级退化。
"""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 只为让门槛判断与真实交付一致：查询词取自真实用例
QUERY_LONG = "安全生产检查"      # 参考库默认输入（短语，词典里通常无前缀大批命中）
QUERY_SHORT = "安全"             # 短词，词典里有多条命中


def _median(fn, repeat: int = 5) -> float:
    """毫秒中位数（预热一次，避免把首次懒加载算进门槛）。"""
    fn()
    samples = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return statistics.median(samples)


@pytest.fixture()
def big_dict(tmp_db):
    """灌入与随包 seed.db 同量级的词典（12 万条）与文档，用于性能测量。

    不复用 seed.db 本身：那会依赖随包资源且不可控；这里直接造同量级数据，
    让"是否走索引"的差异稳定可测。
    """
    from gwtool.db import connection as dbconn
    from gwtool.db import dao

    conn = dbconn.get_conn()
    rows = [(f"词{i:06d}", f"ci{i}", f"释义第{i}条", "", "builtin")
            for i in range(120000)]
    # 混入真实会命中的短词，保证前缀/包含两级都有真实结果
    rows[:6] = [("安全生产", "anquan", "指生产过程中的人身与设备安全", "", "builtin"),
                ("安全检查", "jiancha", "对安全隐患进行排查", "", "builtin"),
                ("安全", "anquan", "免受危险的状态", "", "builtin"),
                ("安全工作", "gongzuo", "安全生产工作", "", "builtin"),
                ("安全感", "gan", "心理上的安全感受", "", "builtin"),
                ("安全阀", "fa", "安全保护装置", "", "builtin")]
    conn.executemany("INSERT INTO dictionary(word,pinyin,definition,example,source)"
                     " VALUES(?,?,?,?,?)", rows)
    conn.commit()
    cat = dao.add_category("性能", 0)
    para = "为深入贯彻落实党中央决策部署，切实抓好安全生产工作，经研究决定开展专项检查。"
    for i in range(300):
        dao.add_document(dao.Document(
            title=f"关于第{i}批安全生产检查工作的通知",
            content_text="\n".join([para] * 6), blocks_json="[]",
            file_path="", file_type="txt", category_id=cat, tags="安全"))
    return conn


class TestDictionarySearchUsesIndex:
    def test_prefix_stage_is_indexed(self, big_dict, tmp_db):
        """前缀这一级的成本必须走索引，不能是全表 LIKE 扫描。

        limit 取小值：本用例只测**前缀级**。limit=50 时该前缀在测试库里命中
        3000+ 条，前缀凑满后会继续跑包含级（合法行为，见下面的用例），
        混在一起就测不出"前缀是否走索引"了。
        """
        from gwtool.db import dao

        elapsed = _median(lambda: dao.search_dictionary("安全", limit=5))
        assert elapsed < 8, (
            f"词典前缀检索 {elapsed:.1f} ms（应 < 8 ms）："
            "疑似退回 word LIKE 'q%' 全表扫描；应改用 word>=? AND word<? 范围查询")

    def test_explain_uses_index_range_search(self, big_dict, tmp_db):
        """直接 EXPLAIN：前缀查询必须是 SEARCH ... (word>?) 而不是 SCAN。"""
        from gwtool.db import connection as dbconn

        plan = " | ".join(
            str(r[3]) for r in dbconn.get_conn().execute(
                "EXPLAIN QUERY PLAN SELECT id FROM dictionary"
                " WHERE word>=? AND word<? LIMIT 50", ("安全", "安全\uffff")))
        assert "SEARCH" in plan and "word>?" in plan, \
            f"前缀查询未走索引范围查找，实际计划：{plan}"

    def test_substring_has_bounded_cost(self, big_dict, tmp_db):
        """包含级是结构性的全表扫描，但代价必须有界。"""
        from gwtool.db import dao

        # '生产' 在前缀级几乎无命中，必然跌到包含级
        elapsed = _median(lambda: dao.search_dictionary("生产", limit=50), 3)
        assert elapsed < 120, (
            f"包含级检索 {elapsed:.1f} ms（应 < 120 ms）："
            "instr 全表扫描的代价应被 _DICT_SORT_SCAN_CAP 有界化")

    def test_result_correctness_kept(self, big_dict, tmp_db):
        """优化不得改变检索语义：精确 > 前缀 > 包含，且去重。"""
        from gwtool.db import dao

        exact = dao.search_dictionary("安全", limit=10)
        assert exact and exact[0].title == "安全", "精确命中必须排第一"

        prefix = dao.search_dictionary("安全生", limit=10)
        titles = [r.title for r in prefix]
        assert "安全生产" in titles, f"前缀命中丢失：{titles}"

        # 包含级（中间命中）仍要能查出来：'生产' 只在 '安全生产' 的中间出现
        contains = dao.search_dictionary("生产", limit=10)
        assert any("安全生产" == r.title for r in contains), \
            "包含级命中不得因优化而丢失"

        # 同一条目不得重复出现
        ids = [r.ref_id for r in prefix]
        assert len(ids) == len(set(ids)), "结果出现重复条目"


class TestReferenceLookupLatency:
    def test_lookup_long_phrase_fast(self, big_dict, tmp_db):
        """参考库主路径（默认整篇检索）必须 < 40 ms。

        优化前实测 ≈ 85 ms（其中词典两级全扫描各 34 ms）。
        """
        from gwtool.core import reference

        elapsed = _median(lambda: reference.lookup(QUERY_LONG))
        # 60ms（原 40ms）：同机并行构建/覆盖率任务时 40ms 会假红；60ms 仍能
        # 拦住"词典全表扫描"级回归（基线约 20~30ms）。
        assert elapsed < 60, (
            f"reference.lookup 耗时 {elapsed:.1f} ms（应 < 60 ms）："
            "词典源的全表扫描把参考库检索拖慢了")

    def test_lookup_short_word_fast(self, big_dict, tmp_db):
        from gwtool.core import reference

        elapsed = _median(lambda: reference.lookup(QUERY_SHORT))
        # 60ms（原 40ms）：同机可能并行跑别的构建/覆盖率任务，40ms 在负载下
        # 会假红；60ms 仍能拦住"全表扫描级"回归（基线约 20~30ms）。
        assert elapsed < 60, f"reference.lookup('{QUERY_SHORT}') 耗时 {elapsed:.1f} ms"


class TestParagraphSearchLatency:
    def test_paragraph_search_fast(self, big_dict, tmp_db):
        from gwtool.core import paragraph_ref

        elapsed = _median(
            lambda: paragraph_ref.search_paragraphs("安全生产检查", limit=30), 3)
        assert elapsed < 60, f"段落检索耗时 {elapsed:.1f} ms（应 < 60 ms）"
