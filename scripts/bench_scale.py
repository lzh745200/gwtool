# -*- coding: utf-8 -*-
"""大数据量性能基线（D4）。

**为什么先做基线而不是直接加分页**：方案里 D4 的结论是"先量化再决定"。
分页/懒加载/虚拟列表都要改 UI 数据流，是典型的"改了不一定有用、
但一定引入新缺陷"的优化。先用数据回答两个问题再动手：

  1. 资料库到几千篇时，列表构建到底慢多少？
  2. 全文检索（FTS5）随数据量增长的曲线如何？

用法：
    python scripts/bench_scale.py            # 默认 300 / 1000 / 3000 三档
    python scripts/bench_scale.py 5000       # 指定档位

脚本在**临时数据目录**里跑，不碰用户的真实库。
"""
from __future__ import annotations

import shutil
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


def _timed(fn, *a, **kw):
    start = time.perf_counter()
    result = fn(*a, **kw)
    return time.perf_counter() - start, result


def bench_one(n_docs: int) -> dict:
    """在独立临时库里造 n_docs 篇文档，测各环节耗时。"""
    tmp = Path(tempfile.mkdtemp(prefix="gwtool_bench_"))
    try:
        paths.set_app_data_dir(tmp)
        dbconn.configure(tmp / "bench.db")
        init_schema(dbconn.get_conn())

        conn = dbconn.get_conn()
        insert_t, _ = _timed(_bulk_insert, conn, n_docs)
        # 批量 SQL 绕过了 DAO 的 FTS 维护，必须重建索引——否则检索测试是在
        # 空索引上跑的，得到"0 ms 命中 0 条"的假数据。建索引的耗时不计入
        # 检索指标（真实场景中它发生在写入时，且只做一次）。
        _timed(dao.rebuild_fts)

        # **预热**：jieba 首次调用要加载词典（数百毫秒）。不预热的话
        # 第一档（300 篇）会把这段固定开销算进检索耗时，得到
        # "数据越少越慢"的荒谬曲线——实测 300 篇 733 ms / 3000 篇 0.3 ms，
        # 差异全部来自这里，与数据量无关。
        dao.search_documents("预热")

        list_t, rows = _timed(dao.list_documents)
        content_t, rows2 = _timed(dao.list_documents, include_content=True)
        search_t, hits = _timed(dao.search_documents, "安全生产")
        count_t, total = _timed(dao.count_documents)
        size_mb = (tmp / "bench.db").stat().st_size / 1024 / 1024
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
            "db_mb": size_mb,
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


def main(argv: list) -> int:
    sizes = [int(a) for a in argv[1:] if a.isdigit()] or [300, 1000, 3000]
    print("大数据量性能基线（临时库，不影响用户数据）")
    print("-" * 74)
    header = (f"{'文档数':>7} {'入库(s)':>9} {'列表(ms)':>10} "
              f"{'含正文(ms)':>12} {'检索(ms)':>10} {'计数(ms)':>9} {'库(MB)':>8}")
    print(header)
    print("-" * 74)
    results = []
    for n in sizes:
        r = bench_one(n)
        results.append(r)
        print(f"{r['docs']:>7} {r['insert_s']:>9.2f} {r['list_ms']:>10.1f} "
              f"{r['list_content_ms']:>12.1f} {r['search_ms']:>10.1f} "
              f"{r['count_ms']:>9.1f} {r['db_mb']:>8.1f}")
    print("-" * 74)

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
