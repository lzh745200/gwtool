# -*- coding: utf-8 -*-
"""端到端自检脚本：在真实数据目录上走通「种子导入→建分类→导入材料→
汇编 docx→A4 PDF→A3 小册子→检索→写作参考→纠错→备份」全流程。

用法：.venv/Scripts/python scripts/e2e_check.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 英文区域设置的 Windows 与 GitHub Windows runner 控制台默认是 charmap 编码，
# print 中文会抛 UnicodeEncodeError 让自检脚本自己先崩掉，故强制 UTF-8 输出。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import os

if not sys.platform.startswith("win"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    ok = 0
    fail = []

    def step(name, cond, detail=""):
        nonlocal ok
        if cond:
            ok += 1
            print(f"  [PASS] {name} {detail}")
        else:
            fail.append(name)
            print(f"  [FAIL] {name} {detail}")

    # 0) 数据库（真实目录）
    from gwtool.db import connection as dbconn
    from gwtool import app
    dbconn.configure(__import__("gwtool.paths", fromlist=["db_path"]).db_path())
    t0 = __import__("time").time()
    app.ensure_database_seeded()
    seed_t = __import__("time").time() - t0
    from gwtool.db import dao
    step("首启动种子导入(秒)", seed_t < 15, f"耗时 {seed_t:.1f}s")
    # 启动性能基线（第 18 轮固化）：主窗口离屏构造须秒级，
    # 防止面板/词典/图标路径的回归把冷启动拖垮。
    from gwtool.ui import main_window as _mw
    from PySide6.QtWidgets import QApplication as _QA
    _qa = _QA.instance() or _QA([])   # MainWindow 需要 QApplication 先行
    # 主窗口构造中的字体缺失提示是模态框（QMessageBox 静态方法），
    # 离屏自检必须临时屏蔽，测完恢复。
    from PySide6.QtWidgets import QMessageBox as _MB
    _saved_mb = {m: getattr(_MB, m) for m in
                 ("information", "warning", "critical", "question")}
    _MB.information = staticmethod(lambda *a, **k: None)
    _MB.warning = staticmethod(lambda *a, **k: None)
    _MB.critical = staticmethod(lambda *a, **k: None)
    _MB.question = staticmethod(
        lambda *a, **k: _MB.StandardButton.No)
    _t0 = __import__("time").time()
    _win = _mw.MainWindow()
    _win_t = __import__("time").time() - _t0
    _win.close()
    for _m, _f in _saved_mb.items():
        setattr(_MB, _m, _f)
    step("主窗口离屏构造(秒)", _win_t < 5, f"耗时 {_win_t:.2f}s")
    step("纠错库≥3万", dao.count_error_pairs() >= 30000,
         f"{dao.count_error_pairs()} 条")

    # 1) 准备样例材料
    from docx import Document as DX
    sample_dir = Path(tempfile.mkdtemp(prefix="gwtool_e2e_"))
    p1 = sample_dir / "关于季度工作报告.docx"
    d = DX()
    d.add_heading("关于第一季度工作的报告", level=1)
    d.add_paragraph("一季度以来，各项工作平稳有序推进，重点任务完成情况良好。")
    d.add_heading("（一）主要成效", level=2)
    d.add_paragraph("项目建设布署已经完成，资金拨付截止本月底。")   # 故意放错误
    d.save(str(p1))
    p2 = sample_dir / "会议纪要.txt"
    p2.write_text("关于安全生产的会议纪要\n会议强调，要压实安全生产责任，坚决防范各类事故发生。",
                  encoding="gbk")
    p3 = sample_dir / "政策要点.md"
    p3.write_text("# 乡村振兴政策要点\n\n坚持农业农村优先发展，巩固拓展脱贫攻坚成果。\n",
                  encoding="utf-8")

    # 2) 分类 + 导入（幂等：重复内容复用已入库文档）
    existing = {d.title: d.id for d in dao.list_documents()}
    cat_name = "E2E自检分类"
    cat = next((c.id for c in dao.list_categories() if c.name == cat_name), None)
    if cat is None:
        cat = dao.add_category(cat_name)
    from gwtool.core.importer import parse_any
    ids = []
    for p in (p1, p2, p3):
        r = parse_any(str(p))
        step(f"解析 {p.name}", r.ok, r.error if not r.ok else "")
        if r.ok:
            did = dao.add_document(dao.Document(
                title=r.tree.title, content_text=r.tree.plain_text(),
                blocks_json=r.tree.to_json(), file_type=p.suffix.lstrip("."),
                category_id=cat))
            if did < 0:
                did = existing.get(r.tree.title, 0)
            if did:
                ids.append(did)

    # 3) 汇编 docx
    from gwtool.core.template import default_template
    from gwtool.core.compiler import compile_docx, CompileRequest
    tpl = default_template()
    tpl.red_header.org = "××市自主创新示范区政府办公室文件"
    tpl.red_header.doc_number = "×政办发〔2026〕12号"
    out_docx = sample_dir / "汇编成果.docx"
    compile_docx(CompileRequest(doc_ids=ids, template=tpl, out_docx=str(out_docx)))
    step("汇编 docx", out_docx.exists() and out_docx.stat().st_size > 10000,
         f"{out_docx.stat().st_size/1024:.0f} KB")
    from docx import Document as DX2
    dd = DX2(str(out_docx))
    xml = dd.settings.element.xml
    step("docx 目录域+奇偶页脚", "TOC" in "".join(p._p.xml for p in dd.paragraphs)
         and "updateFields" in xml and "evenAndOddHeaders" in xml)

    # 4) 内置渲染 A4 PDF（含目录页码与页码盖章）
    from PySide6.QtWidgets import QApplication
    _qapp = QApplication.instance() or QApplication([])
    from gwtool.core import pdfrender
    trees = __import__("gwtool.core.compiler", fromlist=["load_trees"]).load_trees(ids, [])
    out_pdf = sample_dir / "汇编成果.pdf"
    pdfrender.render_compiled_pdf(trees, tpl, str(out_pdf))
    step("A4 PDF(两遍渲染)", out_pdf.exists() and out_pdf.stat().st_size > 10000,
         f"{out_pdf.stat().st_size/1024:.0f} KB")

    # 5) A3 骑马钉小册子
    from gwtool.core.booklet import make_booklet
    out_bk = sample_dir / "汇编成果_小册子A3.pdf"
    n = make_booklet(str(out_pdf), str(out_bk))
    step("A3 小册子", out_bk.exists() and n > 0, f"{n} 页")

    # 6) 全文检索
    res = dao.search_documents("安全生产")
    step("FTS5 检索『安全生产』", len(res) >= 1, f"{len(res)} 条")

    # 7) 写作参考
    from gwtool.core import reference
    items = reference.lookup("发展")
    step("写作参考检索", len(items) > 0, f"{len(items)} 条")

    # 8) 纠错（验收对）
    from gwtool.core import corrector
    corr = corrector.check_text("工作布署已完成，报名截止本月底。")
    pairs = {c.wrong: c.suggestion for c in corr}
    step("纠错：布署→部署", pairs.get("布署") == "部署")
    step("纠错：截止→截至", pairs.get("截止") == "截至")
    # 标点重复：同字符重复合并，但不同标点相邻不得被错并
    punct = {(c.wrong, c.suggestion) for c in corrector.check_text("工作结束。。。")}
    step("纠错：标点重复合并", ("。。。", "。") in punct)
    mixed = {(c.wrong, c.suggestion) for c in corrector.check_text("甲。，乙")}
    step("纠错：不同标点不误并", ("。，", "，") not in mixed)

    # 8b) 格式体检：正文里多处日期都应报出（原 break 只报第一处）
    from gwtool.core import inspector
    _txt = "第一处 2026年08月30日，第二处 2026年09月01日。"
    _dates = "".join(f.detail for f in inspector.inspect_text(_txt)
                     if f.item == "成文日期")
    step("体检：多处日期全部报出",
         "2026年08月30日" in _dates and "2026年09月01日" in _dates)

    # 8c) 重复字：词典全量零误报 + 公文范文零误报
    from gwtool.core import repeat_rules
    _words = dao.all_dictionary_words()
    _fp = [w for w in _words if repeat_rules.check_repeat(w)]
    step("重复字：词典全量零误报", len(_fp) == 0,
         f"{len(_words)} 词 / {len(_fp)} 误报")
    _essay = ("各单位要高度重视，扎实推进各项工作部署。针对当前存在的问题，"
              "必须认真研究，逐一加以解决。各部门之间要加强协调配合，"
              "形成工作合力，确保任务圆满完成。")
    _rh = repeat_rules.check_repeat(_essay)
    step("重复字：公文范文零误报", len(_rh) == 0, f"{len(_rh)} 命中")

    # 9) 词表导入：预检 → 预览 → 事务写入 → 生效与回归校验
    #
    # ⚠️ 本脚本跑在**真实数据目录**上，所以自检用的词表**导入后必须删掉**，
    # 不能把测试数据留在用户的库里。同时验证：
    #   · 导入的规则真的在纠错里生效（不是"导入成功但没作用"）；
    #   · 新增来源不会让重复字检测产生误报（导入污染的核心回归点）。
    from gwtool.core import wordlist as _wl
    try:
        _src = "E2E自检词表"
        _csv = Path(sample_dir) / "e2e_wordlist.csv"
        _csv.write_text("错误写法,正确写法\n各自,各\n", encoding="utf-8")
        _parsed = _wl.parse(str(_csv), "pairs")
        _rep = _wl.precheck(_parsed, _src, "pairs")
        _applied = _wl.apply(_parsed, _rep, _src, "pairs", label="E2E 自检")
        _preview = "／".join(f"{k}{v}" for k, v in _rep.counts.items())
        step("词表导入（预检→预览→事务写入）",
             _applied.ok and _applied.pairs_added >= 1,
             f"新增 {_applied.pairs_added} 条；预检 {_preview}")

        from gwtool.core.corrector import check_text
        _hit = [c for c in check_text("各自负责。") if c.wrong == "各自"]
        step("词表导入后纠错生效", bool(_hit),
             f"命中 {[c.wrong + '→' + c.suggestion for c in _hit[:3]]}")

        from gwtool.core import repeat_rules
        _fp = repeat_rules.check_repeat("现在会在结果对话框中逐条点名。")
        step("词表导入后重复字零误报回归", len(_fp) == 0, f"{len(_fp)} 命中")
    except Exception as _exc:                       # 自检失败要报出来，不吞
        step("词表导入（预检→预览→事务写入）", False,
             f"{type(_exc).__name__}: {_exc}")
    finally:
        try:
            _wl.delete_source(_src)                 # 不留测试数据在用户库里
        except Exception:
            pass

    # 10) 备份 —— 并且**验证它真能用**
    #
    # 为什么必须做往返断言：此前这一项只判 `Path(bz).exists()`。实测注入
    # "备份内容为空库"的缺陷后，pytest 有 11 条报错，而 e2e **仍然通过** ——
    # 也就是说发布前的最后一道人工可读门禁，会对"备份不可用"给出绿灯。
    # 备份是这个产品唯一的数据退路，必须断言"备份 → 恢复 → 内容一致"。
    from gwtool.core.backup import create_backup, restore_backup_detailed

    # ⚠ 恢复会**整库替换**，绝不能在用户真实数据目录上做。
    # 先把数据目录切到临时目录，恢复验完再切回来（用户库分毫不动）。
    import gwtool.paths as _paths
    _real_data_dir = _paths.app_data_dir()
    _sandbox = Path(tempfile.mkdtemp(prefix="gwtool_e2e_restore_"))

    bz = create_backup(note="E2E自检")
    step("一键备份", Path(bz).exists() and Path(bz).stat().st_size > 0,
         f"{Path(bz).name}（{Path(bz).stat().st_size // 1024 if Path(bz).exists() else 0} KB）")

    try:
        _before = sorted(d.title for d in dao.list_documents())
        _before_n = len(_before)

        _paths.set_app_data_dir(_sandbox)
        dbconn.configure(_paths.db_path())
        app.ensure_database_seeded()
        _before_sandbox = len(dao.list_documents())

        _rep = restore_backup_detailed(str(bz))
        _after = sorted(d.title for d in dao.list_documents())

        step("备份→恢复 内容一致",
             bool(_rep.ok) and _after == _before,
             f"{_before_sandbox} 篇(沙箱) → 恢复后 {len(_after)} 篇；"
             f"标题集合{'一致' if _after == _before else '不一致'}")
        step("恢复报告无降级项",
             not getattr(_rep, "warnings", None),
             "；".join(getattr(_rep, "warnings", []) or []) or "无")
    except Exception as _exc:                       # 自检失败要报出来，不吞
        step("备份→恢复 内容一致", False, f"{type(_exc).__name__}: {_exc}")
    finally:
        # 无论成败都要切回真实目录，否则后续步骤写进沙箱
        _paths.set_app_data_dir(_real_data_dir)
        dbconn.configure(_paths.db_path())

    print(f"\n===== E2E 自检结果：{ok} 项通过，{len(fail)} 项失败 =====")
    if fail:
        print("失败项：", fail)
    print(f"样例输出目录：{sample_dir}")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
