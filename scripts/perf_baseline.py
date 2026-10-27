# -*- coding: utf-8 -*-
"""性能基准（真实体量）：写作参考库 / 校对 / 检索 / 汇编 的关键路径耗时。

为什么需要它
------------
现有测试只有两处性能门槛（FTS 检索 < 1s、一致性 check < 200ms），
写作参考库（段落索引 + 三源检索）、冷启动首用、批量校对的端到端耗时
没有任何性能契约 —— 用户体感"加载慢"无法被任何测试发现。

本脚本用**随包 seed.db 的真实规模**（4 万纠错对 + 12 万词典）建库，
再灌入真实感的公文语料，逐项测中位数耗时。

用法：
    .venv\\Scripts\\python.exe -X utf8 scripts/perf_baseline.py
    .venv\\Scripts\\python.exe -X utf8 scripts/perf_baseline.py --docs 800 --json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_PARA = [
    "为深入贯彻落实党中央决策部署，切实抓好安全生产工作，经研究决定开展专项检查。",
    "各部门要高度重视，落实主体责任，建立健全隐患排查治理长效机制，确保不留死角。",
    "一、检查范围。覆盖全部生产场所、办公区域及所属各单位，重点检查消防设施配置情况。",
    "二、工作要求。各单位主要负责同志要亲自部署、亲自督办，形成闭环管理。",
    "三、时间安排。自本通知印发之日起至十二月三十一日，分三个阶段推进实施。",
    "对工作不力、推进缓慢的单位，将予以通报批评并追究相关责任人责任。",
    "请各单位于每季度末前将工作进展情况报送办公室，重要情况及时报告。",
    "加强宣传引导，营造良好氛围，推动形成全社会共同参与的工作格局。",
    "截止目前，全区共排查各类隐患三百余处，整改完成率达到百分之九十五以上。",
    "下一步，我们将继续巩固整治成果，推动安全生产工作常态化、制度化、规范化。",
]


def _seed(dbmod, dao, docs: int, paragraphs: int, phrases: int,
          with_seed: bool) -> dict:
    """灌入与真实规模同量级的数据；with_seed 时先导入随包 seed.db。"""
    t0 = time.perf_counter()
    info = {}
    conn = dbmod.get_conn()
    if with_seed:
        from gwtool.paths import bundled_db_seed_path
        seed = bundled_db_seed_path()
        if seed.exists():
            t1 = time.perf_counter()
            info["seed_import_ms"] = round((time.perf_counter() - t1) * 1000, 1)
            # 用 ATTACH 直接搬，模拟首启动种子导入的量级
            t1 = time.perf_counter()
            cur = conn.cursor()
            cur.execute("ATTACH DATABASE ? AS seed", (str(seed),))
            cur.execute(
                "INSERT OR IGNORE INTO error_pairs(wrong,correct,category,"
                "confidence,enabled,source)"
                " SELECT wrong,correct,category,confidence,enabled,source"
                " FROM seed.error_pairs")
            cur.execute(
                "INSERT OR IGNORE INTO dictionary(word,pinyin,definition,"
                "example,source)"
                " SELECT word,pinyin,definition,example,source FROM seed.dictionary")
            conn.commit()
            cur.execute("DETACH DATABASE seed")
            info["seed_import_ms"] = round((time.perf_counter() - t1) * 1000, 1)
            info["error_pairs"] = conn.execute(
                "SELECT count(*) FROM error_pairs").fetchone()[0]
            info["dictionary"] = conn.execute(
                "SELECT count(*) FROM dictionary").fetchone()[0]

    cat_ids = [dao.add_category(n, 0) for n in
               ("综合材料", "领导讲话", "政策文件", "会议纪要")]
    for i in range(docs):
        body = "\n".join(_PARA[(i + k) % len(_PARA)] for k in range(paragraphs))
        dao.add_document(dao.Document(
            title=f"关于第{i}批安全生产检查工作的通知",
            content_text=body, blocks_json="[]", file_path="",
            file_type="txt", category_id=cat_ids[i % 4],
            tags="安全，检查" if i % 3 == 0 else "安全"))
    conn.executemany(
        "INSERT INTO user_phrases(phrase,context,source,tag) VALUES(?,?,?,?)",
        [(f"请各单位按照职责分工，认真抓好第{i}项工作的贯彻落实。",
          f"第{i}项工作", "user", "通用") for i in range(phrases)])
    conn.commit()
    info["docs"] = docs
    info["paragraphs_per_doc"] = paragraphs
    info["phrases"] = phrases
    info["seed_total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return info


def _timeit(fn, repeat: int = 5) -> float:
    fn()                                     # 预热（剔除首次建连接/懒加载）
    samples = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return round(statistics.median(samples), 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=1000)
    ap.add_argument("--paragraphs", type=int, default=12)
    ap.add_argument("--phrases", type=int, default=2000)
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--no-seed", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="gwtool_perf_"))
    from gwtool import paths
    paths.set_app_data_dir(tmp)
    from gwtool.db import connection as dbconn
    from gwtool.db import dao
    dbconn.configure(tmp / "gwtool.db")
    dbconn.get_conn()

    t_seed = time.perf_counter()
    info = _seed(dbconn, dao, args.docs, args.paragraphs, args.phrases,
                 not args.no_seed)
    seed_wall = round((time.perf_counter() - t_seed) * 1000, 1)

    from gwtool.core import (compiler, corrector, docxgen, paragraph_ref,
                             reference, writing_hints)
    from gwtool.core.template import default_template
    from gwtool.db import tokenize

    r: dict[str, float] = {}

    # ============ 一、冷启动首用 ===========
    tokenize.invalidate() if hasattr(tokenize, "invalidate") else None
    corrector.invalidate_cache()
    r["冷启动: 纠错规则首载"] = _timeit(lambda: corrector.check_text("布署工作截止目前"), 3)
    r["冷启动后: 纠错(短)"] = _timeit(
        lambda: corrector.check_text("关于布署工作的通知，截止目前已完成。"), args.repeat)

    # ============ 二、写作参考库 ===========
    r["参考: 整篇三源检索"] = _timeit(
        lambda: reference.lookup("安全生产检查"), args.repeat)
    r["参考: 段落检索"] = _timeit(
        lambda: paragraph_ref.search_paragraphs("安全生产检查", limit=30),
        args.repeat)

    ids_all = [d.id for d in dao.list_documents()]

    def _first_index_build():
        """模拟首次进入段落模式：对全库派生段落索引。"""
        for did in ids_all:
            d = dao.get_document(did)
            paragraph_ref.derive_blocks(d.blocks_json, d.content_text)
    r["参考: 段落索引首建(全库)"] = _timeit(_first_index_build, 3)

    r["参考: 段落索引复用(全库)"] = _timeit(
        lambda: [paragraph_ref.paragraphs_of(d.id, d.title)
                 for d in dao.list_documents()], 3)

    sample = ("关于安全生产检查的通知\n各部门：\n"
              "为贯彻落实上级部署，决定开展专项检查。\n一、检查范围\n覆盖全部生产场所。")
    r["参考: 文种识别"] = _timeit(lambda: writing_hints.detect_kind(sample),
                                  args.repeat)
    r["参考: 写作提示(短稿)"] = _timeit(lambda: writing_hints.hints(sample),
                                        args.repeat)
    big_draft = "\n".join(_PARA) * 12        # ≈ 6000 字，接近对话框采样上限
    r["参考: 写作提示(6千字)"] = _timeit(lambda: writing_hints.hints(big_draft), 3)

    # ============ 三、校对 ===========
    huge = "\n".join(_PARA) * 40             # ≈ 2 万字
    r["校对: 万字"] = _timeit(lambda: corrector.check_text(huge[:10000]), 3)
    r["校对: 2万字"] = _timeit(lambda: corrector.check_text(huge), 3)

    # ============ 四、检索 ===========
    r["检索: 文档FTS"] = _timeit(lambda: dao.search_documents("安全生产", limit=50),
                                 args.repeat)
    r["检索: 词典"] = _timeit(lambda: dao.search_dictionary("安全", limit=50),
                              args.repeat)
    r["检索: 句式"] = _timeit(lambda: dao.search_phrases("工作", limit=50),
                              args.repeat)

    # ============ 五、汇编 ===========
    ids50 = ids_all[:50]
    r["汇编: 取材料树×50"] = _timeit(lambda: compiler.load_trees(ids50, []), 3)

    def _gen():
        trees = compiler.load_trees(ids50, [])
        docxgen.generate_docx(trees, default_template(), str(tmp / "o.docx"))
    r["汇编: 生成DOCX×50篇"] = _timeit(_gen, 3)

    payload = {"seed": info, "seed_wall_ms": seed_wall, "results_ms": r}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("\n== 数据规模 ==")
        for k, v in info.items():
            print(f"  {k:<22} {v}")
        print("\n== 耗时（中位数 ms，越小越好）==")
        width = max(len(k) for k in r)
        for k, v in r.items():
            print(f"  {k:<{width}}  {v:9.1f} ms")

    dbconn.close_current_thread()
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
