# -*- coding: utf-8 -*-
"""大数据量性能与**存储**基线（D4 / P1-35）。

**为什么先做基线而不是直接加分页**：方案里 D4 的结论是"先量化再决定"。
分页/懒加载/虚拟列表都要改 UI 数据流，是典型的"改了不一定有用、
但一定引入新缺陷"的优化。先用数据回答再动手：

  1. 资料库到几千篇时，列表构建到底慢多少？
  2. 全文检索（FTS5）随数据量增长的曲线如何？
  3. 段落索引让库**真实**变大了多少（v1.10 起：WAL checkpoint + dbstat 页统计）？

用法：
    python scripts/bench_scale.py                    # 默认 300 / 1000 / 3000 三档
    python scripts/bench_scale.py 5000               # 指定档位
    python scripts/bench_scale.py --para-index-max=1000 3000
        # 给临时库设「段落索引篇数上限」，观察超预算时的体积（0=不限）

⚠ 体积口径（v1.10 修）：旧实现直接 `stat()` 主库文件，而 WAL 模式下刚写完的
页还在 `-wal` 里没回主库 —— 于是 300/1000 档打印出 **0.0%**（假数据），
3000 档才偶然越过 checkpoint 阈值显示 88.9%。现在每次测量前
`PRAGMA wal_checkpoint(TRUNCATE)`，再用 `dbstat` 按表统计页数（连空闲页、
各 FTS 影子表都算进去），数字可复现且能归因到具体表。

脚本在**临时数据目录**里跑，不碰用户的真实库。
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gwtool import paths
from gwtool.db import connection as dbconn
from gwtool.db import dao
from gwtool.db.schema import init_schema

SAMPLE = ("为深入贯彻落实上级关于进一步加强和改进新形势下安全生产工作的"
          "各项部署要求，现就有关事项通知如下，请各部门结合实际抓好落实。")

# 段落索引自己在 dbstat 里的表名（含 FTS5 影子表与段落表索引）
_PARA_TABLES = ("paragraphs", "paragraphs_fts_content", "paragraphs_fts_data",
                "paragraphs_fts_idx", "paragraphs_fts_docsize",
                "paragraphs_fts_config", "idx_paragraphs_doc")


def _timed(fn, *a, **kw):
    start = time.perf_counter()
    result = fn(*a, **kw)
    return time.perf_counter() - start, result


def _flush(conn) -> None:
    """把 WAL 回写主库并截断：否则 `stat()` 量到的是不完整的库文件。

    旧实现正是在这里出的错：刚 rebuild 完的页还在 -wal 里，主库文件没长，
    于是算出 0.0% 的"零开销"假结论。
    """
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.OperationalError:
        pass


def _db_mb(path: Path) -> float:
    return path.stat().st_size / 1024 / 1024


def _dbstat_mb(conn) -> dict[str, float]:
    """按表统计页占用（MB）；sqlite 未编译 dbstat 虚拟表时返回空 dict。"""
    try:
        rows = conn.execute(
            "SELECT name, SUM(pgsize) AS bytes FROM dbstat GROUP BY name").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {str(r["name"]): (r["bytes"] or 0) / 1024 / 1024 for r in rows}


def _para_mb(pages: dict[str, float]) -> float:
    return sum(mb for name, mb in pages.items() if name in _PARA_TABLES)


def _seed_dictionary(conn) -> int:
    """把随包 seed.db 的词典灌进临时库，返回词条数（不灌纠错对）。

    只灌 dictionary：写作参考检索的耗时几乎全在词典源，纠错对只影响
    `corrector.check_text()`，而那一步在空词库下也已经能反映热路径开销。
    这样压测库体积与构建时间都可控，同时词典规模与真实交付一致。
    """
    from gwtool.paths import bundled_db_seed_path

    seed = bundled_db_seed_path()
    if not seed.exists():
        print("  [警告] 未找到随包 seed.db，词典检索基线将失真（空表）")
        return 0
    cur = conn.cursor()
    cur.execute("ATTACH DATABASE ? AS seed", (str(seed),))
    cur.execute("INSERT OR IGNORE INTO dictionary(word,pinyin,definition,"
                "example,source) SELECT word,pinyin,definition,example,source"
                " FROM seed.dictionary")
    conn.commit()
    cur.execute("DETACH DATABASE seed")
    return int(conn.execute("SELECT count(*) FROM dictionary").fetchone()[0])


def bench_one(n_docs: int, para_index_max: int = 0) -> dict:
    """在独立临时库里造 n_docs 篇文档，测各环节耗时与存储占用。"""
    tmp = Path(tempfile.mkdtemp(prefix="gwtool_bench_"))
    try:
        paths.set_app_data_dir(tmp)
        dbconn.configure(tmp / "bench.db")
        init_schema(dbconn.get_conn())
        # 词典必须灌成**真实规模**：写作参考检索的耗时几乎全在词典源上
        # （12.3 万词条的前缀/包含两级）。不灌的话测出来是 0.1 ms 的空表假数据，
        # 会把 v1.19 优化前后的差别完全掩盖掉。
        _seed_dictionary(dbconn.get_conn())
        if para_index_max:
            dao.set_setting(dao.PARA_INDEX_MAX_DOCS_KEY, str(int(para_index_max)))

        conn = dbconn.get_conn()
        insert_t, _ = _timed(_bulk_insert, conn, n_docs)
        # 批量 SQL 绕过了 DAO 的 FTS 维护，必须重建索引——否则检索测试是在
        # 空索引上跑的，得到"0 ms 命中 0 条"的假数据。建索引的耗时不计入
        # 检索指标（真实场景中它发生在写入时，且只做一次）。
        _timed(dao.rebuild_fts)
        _flush(conn)
        db_mb_before = _db_mb(tmp / "bench.db")
        pages_before = _dbstat_mb(conn)

        # **预热**：jieba 首次调用要加载词典（数百毫秒）。不预热的话
        # 第一档（300 篇）会把这段固定开销算进检索耗时，得到
        # "数据越少越慢"的荒谬曲线——实测 300 篇 733 ms / 3000 篇 0.3 ms，
        # 差异全部来自这里，与数据量无关。
        dao.search_documents("预热")

        # v7 段落索引：整库回填的耗时与体积增量（规格 §8.2 的性能验收项）。
        # 批量插入用的是 blocks_json='[]'，所以这里同时也在压测**纯文本兜底
        # 切分**这条路径——它正是"块结构缺失"时真正会走的那条。
        para_build_t, para_counts = _timed(dao.rebuild_paragraphs)
        para_second_t, para_second = _timed(dao.rebuild_paragraphs)
        dao.search_paragraphs_fts("预热", 5)
        para_search_t, para_hits = _timed(dao.search_paragraphs_fts, "安全生产", 30)

        # v1.19 写作参考链路：词典 / 三源参考检索 / 校对。
        #
        # 为什么必须并入本脚本而不是另写一个：这三项与段落检索同属"参考库加载
        # 速度"的用户体感，必须与规模一起看趋势。v1.19 实测发现
        # `search_dictionary` 的前缀/包含两级都是 12.3 万行的全表扫描
        # （EXPLAIN: SCAN … COVERING INDEX），把默认参考检索拖到 ≈85 ms；
        # 改为范围查询 + 有界包含探测后降到 ≈10 ms。没有这里的基线，
        # 这类退化不会被任何测试发现。
        from gwtool.core import corrector, reference
        dao.search_dictionary("预热", 5)
        dict_t, dict_hits = _timed(dao.search_dictionary, "安全", 50)
        # 长查询（词典里通常无前缀命中）会一路跌到包含级 —— 这是最常见的真实输入
        dict_long_t, dict_long_hits = _timed(
            dao.search_dictionary, "安全生产检查", 50)
        ref_t, ref_items = _timed(reference.lookup, "安全生产检查")
        corrector.invalidate_cache()
        corrector.check_text("预热")
        corr_t, corr_hits = _timed(
            corrector.check_text,
            "工作布署务必落实，截止目前再接再励，请及时反应问题。")
        _flush(conn)
        db_mb = _db_mb(tmp / "bench.db")
        pages_after = _dbstat_mb(conn)

        list_t, rows = _timed(dao.list_documents)
        content_t, rows2 = _timed(dao.list_documents, include_content=True)
        search_t, hits = _timed(dao.search_documents, "安全生产")
        count_t, total = _timed(dao.count_documents)

        para_mb = _para_mb(pages_after)
        total_pages_mb = sum(pages_after.values()) if pages_after else db_mb
        growth_pct = ((db_mb - db_mb_before) / db_mb_before * 100
                      if db_mb_before > 0 else 0.0)
        return {
            "docs": n_docs,
            "insert_s": insert_t,
            "list_ms": list_t * 1000,
            "list_rows": len(rows),
            "list_content_ms": content_t * 1000,
            "list_content_rows": len(rows2),
            "search_ms": search_t * 1000,
            "search_hits": len(hits),
            "count_ms": count_t * 1000,
            "count": total,
            "db_mb": db_mb,
            "db_mb_before_para": db_mb_before,
            "growth_pct": growth_pct,
            "para_mb": para_mb,
            "total_pages_mb": total_pages_mb,
            "para_share_pct": (para_mb / total_pages_mb * 100
                               if total_pages_mb else 0.0),
            "pages_before": pages_before,
            "pages_after": pages_after,
            "para_build_s": para_build_t,
            "para_second_s": para_second_t,
            "para_rescanned2": para_second.get("rescanned", -1),
            "para_count": para_counts.get("paragraphs", 0),
            "para_skipped": int(para_counts.get("skipped", 0)),
            "para_reason": para_counts.get("reason", ""),
            "para_search_ms": para_search_t * 1000,
            "para_hits": len(para_hits),
            "dict_ms": dict_t * 1000,
            "dict_hits": len(dict_hits),
            "dict_long_ms": dict_long_t * 1000,
            "dict_long_hits": len(dict_long_hits),
            "ref_ms": ref_t * 1000,
            "ref_items": len(ref_items),
            "corr_ms": corr_t * 1000,
            "corr_hits": len(corr_hits),
        }
    finally:
        dbconn.close_current_thread()
        paths.set_app_data_dir(None)
        shutil.rmtree(tmp, ignore_errors=True)


def _bulk_insert(conn, n_docs: int) -> int:
    """批量插入。直接走 SQL 而不逐条调 dao.add_document：
    后者每篇一次 commit，造数据本身会成为瓶颈，掩盖我们要测的读取性能。
    """
    rows = [(f"材料{i:05d}", SAMPLE * 3, "[]", f"bench_{i}.docx",
             "docx", i) for i in range(n_docs)]
    conn.executemany(
        "INSERT INTO documents(title,content_text,blocks_json,file_path,"
        "file_type,word_count,text_hash,import_time,updated_time) "
        "VALUES(?,?,?,?,?,?,?,datetime('now','localtime'),"
        "datetime('now','localtime'))",
        [(t, c, b, f, ft, w, f"hash{i}") for i, (t, c, b, f, ft, w) in
         enumerate(rows)])
    conn.commit()
    return n_docs


def _parse_args(argv: list) -> tuple[list[int], int]:
    sizes: list[int] = []
    para_max = 0
    for arg in argv[1:]:
        if arg.startswith("--para-index-max="):
            try:
                para_max = max(0, int(arg.split("=", 1)[1]))
            except ValueError:
                para_max = 0
        elif arg.isdigit():
            sizes.append(int(arg))
    return sizes or [300, 1000, 3000], para_max


def main(argv: list) -> int:
    sizes, para_max = _parse_args(argv)
    print("大数据量性能与存储基线（临时库，不影响用户数据）")
    if para_max:
        print(f"（本次设置段落索引上限 = {para_max} 篇）")
    print("-" * 78)
    header = (f"{'文档数':>7} {'入库(s)':>9} {'列表(ms)':>10} "
              f"{'含正文(ms)':>12} {'检索(ms)':>10} {'计数(ms)':>9} {'库(MB)':>8}")
    print(header)
    print("-" * 78)
    results = []
    for n in sizes:
        r = bench_one(n, para_index_max=para_max)
        results.append(r)
        print(f"{r['docs']:>7} {r['insert_s']:>9.2f} {r['list_ms']:>10.1f} "
              f"{r['list_content_ms']:>12.1f} {r['search_ms']:>10.1f} "
              f"{r['count_ms']:>9.1f} {r['db_mb']:>8.1f}")
    print("-" * 78)

    # 结论：按"是否需要在 UI 上做分页"给出可执行的判断
    worst = max(results, key=lambda r: r["list_ms"])
    print(f"\n最长列表查询：{worst['list_ms']:.1f} ms（{worst['docs']} 篇）")
    if worst["list_ms"] < 100:
        print("→ 列表查询远低于一帧（16 ms）的十倍量级，"
              "**无需分页**；再做懒加载属过早优化。")
    elif worst["list_ms"] < 300:
        print("→ 列表查询已可感知。建议先加搜索框引导缩小范围，"
              "仍不必上虚拟列表。")
    else:
        print("→ 列表查询明显卡顿，建议实施分页/增量加载。")
    slowest_search = max(results, key=lambda r: r["search_ms"])
    print(f"最长检索：{slowest_search['search_ms']:.1f} ms"
          f"（{slowest_search['docs']} 篇，命中 {slowest_search['search_hits']} 条）")

    # v7 段落参考：回填耗时、体积增量、段落检索与幂等性
    print("\n段落参考（v7）基线")
    print("-" * 78)
    print(f"{'文档数':>7} {'段落数':>8} {'回填(s)':>9} {'二次(s)':>9} "
          f"{'重切':>5} {'检索(ms)':>10} {'命中':>6} {'增量%':>8} "
          f"{'段落占库%':>10} {'索引':>6}")
    print("-" * 78)
    for r in results:
        index_state = "跳过" if r["para_skipped"] else "建立"
        print(f"{r['docs']:>7} {r['para_count']:>8} {r['para_build_s']:>9.2f} "
              f"{r['para_second_s']:>9.3f} {r['para_rescanned2']:>5} "
              f"{r['para_search_ms']:>10.1f} {r['para_hits']:>6} "
              f"{r['growth_pct']:>8.1f} {r['para_share_pct']:>10.1f} "
              f"{index_state:>6}")
    print("-" * 78)
    print("口径：增量% =（checkpoint 后主库大小差 / 加段落索引前大小）；"
          "段落占库% = dbstat 段落相关表页数 / 全库页数。")
    built = [r for r in results if not r["para_skipped"]]
    if built:
        worst_para = max(built, key=lambda r: r["para_build_s"])
        print(f"最长全量回填：{worst_para['para_build_s']:.1f} s"
              f"（{worst_para['docs']} 篇 / {worst_para['para_count']} 段）"
              "　—— 注意：它在**后台线程**执行，不阻塞 UI")
        worst_psearch = max(built, key=lambda r: r["para_search_ms"])
        print(f"最长段落检索：{worst_psearch['para_search_ms']:.1f} ms"
              f"（{worst_psearch['docs']} 篇，命中 {worst_psearch['para_hits']} 段）")
        idem_ok = all(r["para_rescanned2"] == 0 for r in built)
        print(f"增量幂等（第二次重切数应为 0）：{'通过' if idem_ok else '不通过'}")
        worst_growth = max(built, key=lambda r: r["growth_pct"])
        print(f"段落索引体积增量：{worst_growth['growth_pct']:.1f}%"
              f"（{worst_growth['docs']} 篇，验收线 < 40%）")
        worst_share = max(built, key=lambda r: r["para_share_pct"])
        print(f"段落索引占库：{worst_share['para_share_pct']:.1f}%"
              f"（{worst_share['docs']} 篇）")
        if worst_growth["growth_pct"] >= 40:
            print("→ 增量超验收线：正文在 documents / documents_fts / paragraphs /"
                  " paragraphs_fts 各存一份，这是结构性的；控盘手段是设置"
                  "「段落索引上限」（--para-index-max=N），超限时改为现场派生。")
    skipped = [r for r in results if r["para_skipped"]]
    for r in skipped:
        print(f"→ {r['docs']} 篇：段落索引已跳过（{r['para_reason']}），"
              f"增量 {r['growth_pct']:.1f}%，检索走现场派生。")

    # v1.19 写作参考链路：词典 / 三源参考检索 / 校对
    print("\n写作参考链路（v1.19）基线　单位 ms，越小越好")
    print("-" * 78)
    print(f"{'文档数':>7} {'词典(ms)':>10} {'长词(ms)':>10} "
          f"{'参考检索(ms)':>13} {'参考条数':>9} {'校对(ms)':>10} {'命中':>6}")
    print("-" * 78)
    for r in results:
        print(f"{r['docs']:>7} {r['dict_ms']:>10.1f} {r['dict_long_ms']:>10.1f} "
              f"{r['ref_ms']:>13.1f} {r['ref_items']:>9} "
              f"{r['corr_ms']:>10.1f} {r['corr_hits']:>6}")
    print("-" * 78)
    print("口径：词典 = search_dictionary('安全',50)，含前缀级 + 包含级；")
    print("      长词 = search_dictionary('安全生产检查',50)，真实默认输入；")
    print("      参考检索 = reference.lookup()，三源合并（用户按回车后的等待）；")
    print("      校对 = corrector.check_text()，约 30 字含 5 处真实错别字。")
    worst_ref = max(results, key=lambda r: r["ref_ms"])
    print(f"\n最长参考检索：{worst_ref['ref_ms']:.1f} ms"
          f"（{worst_ref['docs']} 篇）")
    if worst_ref["ref_ms"] < 40:
        print("→ 参考检索低于 40 ms，属【即时】体感范围（人眼可感知阈值约 100 ms）；"
              "若此值回升到 60 ms 以上，先查 search_dictionary 是否退回全表 LIKE。")
    elif worst_ref["ref_ms"] < 100:
        print("→ 参考检索已可感知（40~100 ms）。检查词典源是否走了范围查询。")
    else:
        print("→ 参考检索明显偏慢（>100 ms）。排查 search_dictionary 的"
              "前缀/包含两级是否退化为全表扫描（EXPLAIN 看 SCAN vs SEARCH）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
