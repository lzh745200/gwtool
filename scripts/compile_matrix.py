# -*- coding: utf-8 -*-
"""一键汇编验收矩阵（开发期离线脚本，v1.15）。

对 5 类输入（纯文本 / markdown / docx 多级标题 / PDF 提取文本 / 混合格式）
各跑一遍完整汇编（DOCX + A4 PDF + A3 骑马钉），用 python-docx 与 PyMuPDF
做**量化断言**，输出 JSON + Markdown 报告。

设计约束
--------
1. **不改产品代码**：脚本只调用既有公开入口
   （`compiler.compile_docx` / `pdfrender.render_compiled_pdf` /
   `booklet.make_booklet`），断言全部在脚本侧完成。
2. **CI 可跑**：只用内置渲染器（Qt/HTML→PDF）与 PyMuPDF、python-docx，
   **不依赖 WPS/Word/LibreOffice**；渲染前自动建离屏 QApplication。
3. **不假装 PASS**：某项能力尚未就绪（典型：DOCX 的 TOC 是 Word 域，离线
   无法更新；本机缺中文字体导致 PDF 取不到字体族；页码起始/封面页码仍在修）
   一律标 SKIP 或 FAIL，并写清原因与 file:line 证据。
4. **退出码**：0 = 全 PASS/SKIP；1 = 有 FAIL；2 = 用法/依赖错误。

用法：
    python scripts/compile_matrix.py                 # 全矩阵（5 类 × 3 产物）
    python scripts/compile_matrix.py --fast          # 小样本（供测试/冒烟）
    python scripts/compile_matrix.py --out out/matrix
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

# 页码文本：模板默认格式 “— {page} —”（容忍提取出的全角/半角与空白差异）
_PAGE_LABEL_RE = re.compile(r"—\s*(\d+)\s*—")
# 目录条目行：整行只有页码数字（内置渲染器的目录是「标题一行 + 页码一行」）
_TOC_NUMBER_RE = re.compile(r"^\s*(\d+)\s*$")

# 各类输入的默认字号字号（与模板字段对应）
_COVER_TITLE_FONT = "方正小标宋简体"      # docxgen._build_cover 的硬编码标准
_A4_W, _A4_H = 595, 842
_A3_W, _A3_H = 1190, 842
_TOL = 2


# ------------------------------------------------------------------ 记录
@dataclass
class Check:
    cid: str
    scenario: str
    group: str
    item: str
    status: str
    detail: str = ""
    evidence: str = ""

    def to_dict(self) -> dict:
        return {"id": self.cid, "scenario": self.scenario, "group": self.group,
                "item": self.item, "status": self.status,
                "detail": self.detail, "evidence": self.evidence}


class Matrix:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def add(self, cid: str, scenario: str, group: str, item: str, status: str,
            detail: str = "", evidence: str = "") -> None:
        self.checks.append(Check(cid, scenario, group, item, status, detail,
                                 evidence))

    def count(self, status: str) -> int:
        return sum(1 for c in self.checks if c.status == status)


# ------------------------------------------------------------------ 环境
def _ensure_qt():
    """内置 PDF 渲染器走 Qt（QFontDatabase/PDF 打印），必须先建 QApplication。"""
    if not os.environ.get("QT_QPA_PLATFORM") and sys.platform != "win32":
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    try:
        from PySide6.QtWidgets import QApplication
    except Exception as exc:                       # pragma: no cover - 依赖缺失
        raise SystemExit(f"缺少 PySide6，无法使用内置渲染器：{exc}") from None
    return QApplication.instance() or QApplication([])


def _deps() -> dict:
    info: dict = {"python": sys.version.split()[0], "platform": sys.platform}
    try:
        import docx
        info["python-docx"] = getattr(docx, "__version__", "?")
    except Exception:
        info["python-docx"] = "MISSING"
    try:
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz  # type: ignore
        info["pymupdf"] = getattr(fitz, "__version__", "?")
    except Exception:
        info["pymupdf"] = "MISSING"
    try:
        import PySide6
        info["PySide6"] = PySide6.__version__
    except Exception:
        info["PySide6"] = "MISSING"
    return info


# ------------------------------------------------------------------ 造输入
def _clean_material_template():
    """造"材料 docx"用的模板：关掉目录/封面/版记，避免材料自带"目 录"污染汇编。"""
    from gwtool.core.template import DocTemplate
    tpl = DocTemplate()
    tpl.toc_enabled = False
    tpl.cover.enabled = False
    tpl.colophon.enabled = False
    tpl.red_header.enabled = False
    tpl.watermark_text = ""
    return tpl


def _build_inputs(base: Path) -> dict[str, list[Path]]:
    """生成 5 类输入文件（全部离线、无外部素材）。"""
    from gwtool.core import docxgen
    from gwtool.core.model import HEADING, Block, DocTree

    src = base / "inputs"
    src.mkdir(parents=True, exist_ok=True)
    mat_tpl = _clean_material_template()

    plain = src / "plain.txt"
    plain.write_text(
        "安全生产工作情况报告\n\n"
        "一、总体情况\n今年以来，全市安全生产形势总体平稳，未发生重特大事故。\n\n"
        "二、存在问题\n部分企业主体责任落实不到位，隐患排查整治还不彻底。\n\n"
        "三、下步打算\n持续开展专项整治，压实各方责任。\n",
        encoding="utf-8")

    md = src / "notes.md"
    md.write_text(
        "# 关于做好安全生产工作的通知\n\n"
        "## 一、提高思想认识\n各单位要充分认识安全生产工作的极端重要性。\n\n"
        "## 二、压实工作责任\n严格落实党政同责、一岗双责。\n\n"
        "### （一）企业主体责任\n企业要开展自查自改。\n\n"
        "### （二）部门监管责任\n行业主管部门要加强检查。\n",
        encoding="utf-8")

    multi = src / "multi.docx"
    tree = DocTree(title="多级标题材料")
    tree.blocks += [
        Block(type=HEADING, level=1, text="一、总体要求"),
        Block(text="坚持安全第一、预防为主、综合治理的方针。"),
        Block(type=HEADING, level=2, text="（一）工作目标"),
        Block(text="坚决杜绝重特大事故，有效遏制较大事故。"),
        Block(type=HEADING, level=3, text="1. 具体指标"),
        Block(text="事故起数与死亡人数同比下降百分之十以上。"),
    ]
    docxgen.generate_docx([tree], mat_tpl, str(multi))

    extracted = src / "extracted.pdf"
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz  # type: ignore
    from gwtool.core import pdfrender
    pdfrender.ensure_cjk_font()
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 120), "转发上级文件的通知", fontname="china-s",
                     fontsize=20)
    page.insert_text((72, 170), "现将有关工作要求通知如下，请结合实际抓好落实。",
                     fontname="china-s", fontsize=14)
    page.insert_text((72, 210), "一、迅速传达学习\n各单位要及时组织传达。",
                     fontname="china-s", fontsize=14)
    doc.save(str(extracted))
    doc.close()

    mixed_docx = src / "mixed.docx"
    tree2 = DocTree(title="检查情况汇总")
    tree2.blocks += [
        Block(type=HEADING, level=1, text="一、检查基本情况"),
        Block(text="本次共检查企业十二家，发现隐患二十三项。"),
        Block(type="table", rows=[["序号", "受检单位", "问题数"],
                                  ["1", "某化工企业", "5"],
                                  ["2", "某建筑工地", "3"]]),
        Block(type=HEADING, level=2, text="（一）主要问题"),
        Block(text="部分企业安全培训记录不完整。"),
    ]
    docxgen.generate_docx([tree2], mat_tpl, str(mixed_docx))

    return {
        "plain": [plain],
        "markdown": [md],
        "docx_headings": [multi],
        "pdf_extract": [extracted],
        "mixed": [mixed_docx, md, plain],
    }


SCENARIOS: tuple[tuple[str, str], ...] = (
    ("plain", "纯文本"),
    ("markdown", "Markdown"),
    ("docx_headings", "DOCX 多级标题"),
    ("pdf_extract", "PDF 提取文本"),
    ("mixed", "混合格式"),
)


# ------------------------------------------------------------------ 产物生成
def _compile_scenario(scenario: str, files: list[Path], workdir: Path,
                      tpl) -> dict:
    """跑完整汇编：docx / A4 PDF / A3 小册子，返回产物路径与页数。"""
    from gwtool.core import booklet, compiler, pdfrender

    out_dir = workdir / "artifacts" / scenario
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = [str(p) for p in files]
    out_docx = out_dir / "汇编成果.docx"
    compiler.compile_docx(compiler.CompileRequest(
        extra_paths=paths, template=tpl, out_docx=str(out_docx)))
    trees = compiler.load_trees([], paths)
    out_pdf = out_dir / "汇编成果_A4.pdf"
    pdfrender.render_compiled_pdf(trees, tpl, str(out_pdf))
    out_a3 = out_dir / "汇编成果_A3小册子.pdf"
    booklet.make_booklet(str(out_pdf), str(out_a3))
    return {"docx": out_docx, "a4_pdf": out_pdf, "a3_pdf": out_a3,
            "trees": trees, "inputs": paths}


# ------------------------------------------------------------------ 断言帮手
def _xml(el) -> str:
    """取 lxml 元素的 XML 串（python-docx 的 oxml 子类有 .xml，裸 lxml 元素没有）。"""
    try:
        return el.xml
    except AttributeError:
        from lxml import etree
        return etree.tostring(el, encoding="unicode")


def _norm(text: str) -> str:
    """去掉空白做文本比对：PDF 提取会把 “目  录” 压成 “目 录”。"""
    return re.sub(r"\s+", "", text or "")


def _page_labels(page) -> list[tuple[int, float, float]]:
    """页面上所有 “— N —” 的 (N, x0, y0)。"""
    out = []
    try:
        text = page.get_text("text")
    except Exception:
        return out
    for m in _PAGE_LABEL_RE.finditer(text):
        try:
            rects = page.search_for(m.group(0))
        except Exception:
            rects = []
        if rects:
            out.append((int(m.group(1)), rects[0].x0, rects[0].y0))
    return out


def _toc_pages(doc, tpl) -> list[int]:
    """含目录标题的页（1 基）。"""
    needle = _norm(tpl.toc_title)
    pages = []
    for i, page in enumerate(doc):
        try:
            if needle and needle in _norm(page.get_text("text")):
                pages.append(i + 1)
        except Exception:
            continue
    return pages


def _parse_toc_entries(text: str) -> list[tuple[str, int]]:
    """解析内置渲染器目录：「标题换行页码」交替出现。"""
    lines = [ln.strip() for ln in (text or "").splitlines()]
    out: list[tuple[str, int]] = []
    prev = ""
    for ln in lines:
        m = _TOC_NUMBER_RE.match(ln)
        if m and prev:
            out.append((prev, int(m.group(1))))
            prev = ""
        elif ln and not _TOC_NUMBER_RE.match(ln):
            prev = ln
    return out


def _first_nonblank_page_text(doc, pageno: int) -> str:
    try:
        text = doc[pageno - 1].get_text("text")
    except Exception:
        return ""
    for ln in text.splitlines():
        if ln.strip():
            return ln.strip()
    return ""


# ------------------------------------------------------------------ DOCX 断言
def _docx_checks(m: Matrix, scenario: str, docx_path: Path, tpl) -> None:
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document(str(docx_path))
    sections = list(doc.sections)
    # 封面正文（第一个分页符之前的段落）
    cover_paras: list = []
    for p in doc.paragraphs:
        if 'w:type="page"' in p._p.xml:
            break
        cover_paras.append(p)
    cover_text = "\n".join(p.text for p in cover_paras)

    m.add("cover.exists.docx", scenario, "封面", "DOCX 封面存在且含标题/单位/日期",
          PASS if all(x and x in cover_text
                      for x in (tpl.cover.title, tpl.cover.org, tpl.cover.date))
          else FAIL,
          f"封面段落 {len(cover_paras)} 段；标题={tpl.cover.title!r}",
          "gwtool/core/docxgen.py:210-245,366-394")

    # 封面是否有页码：首页页脚（未设 titlePg 时就是默认页脚）是否带 PAGE 域
    first_sec = sections[0] if sections else None
    if first_sec is None:
        m.add("cover.nopage.docx", scenario, "封面", "DOCX 封面无页码", SKIP,
              "文档无节信息，无法判定")
    else:
        title_pg = bool(first_sec.different_first_page_header_footer)
        cover_footer = (first_sec.first_page_footer if title_pg
                        else first_sec.footer)
        has_page = "PAGE" in (cover_footer._element.xml or "")
        m.add("cover.nopage.docx", scenario, "封面", "DOCX 封面无页码",
              FAIL if has_page else PASS,
              ("单节文档：页脚 PAGE 域作用于全部页面（含封面）"
               if has_page else "首页页脚无 PAGE 域"),
              "gwtool/core/docxgen.py:236-245,339-363")

    # 目录域存在 + 条目
    body_xml = doc.element.body.xml
    has_toc_field = "TOC \\o" in body_xml or "TOC" in body_xml
    m.add("toc.field.docx", scenario, "目录", "DOCX 目录域存在",
          PASS if has_toc_field else FAIL, "w:instrText 含 TOC 域" if has_toc_field
          else "未找到 TOC 域", "gwtool/core/docxgen.py:218-235")
    placeholder = "（打开文档后按提示更新目录页码）"
    if placeholder in body_xml:
        headings = sum(1 for p in doc.paragraphs
                       if p.style.name.startswith("Heading"))
        m.add("toc.entries.docx", scenario, "目录",
              "DOCX 目录条目>0（离线可判定）", SKIP,
              f"TOC 是 Word 域，离线无 Word/WPS 不更新；候选标题 {headings} 个，"
              "条目与页码一致性改在 PDF 侧断言",
              "gwtool/core/docxgen.py:218-235")
    else:
        m.add("toc.entries.docx", scenario, "目录",
              "DOCX 目录条目>0（离线可判定）", PASS,
              "TOC 域已含静态条目", "gwtool/core/docxgen.py:218-235")

    # 页码起始/连续：靠节结构判定（离线无法真正分页）
    restart = any("w:start=\"1\"" in (_xml(e) or "")
                  for e in doc.element.body.iter(qn("w:pgNumType")))
    front_sections = 0
    body_section_ok = False
    for sec in sections:
        foot = sec.footer._element.xml or ""
        even = sec.even_page_footer._element.xml or ""
        has_num = "PAGE" in foot or "PAGE" in even
        if not has_num:
            front_sections += 1
        elif restart:
            body_section_ok = True
    ok = bool(front_sections) and body_section_ok
    m.add("page.start.docx", scenario, "页码",
          "DOCX 正文页码从 1 起且封面/目录不计页", PASS if ok else FAIL,
          ("存在无页码的前置节 + 正文节重新编号" if ok else
           f"单节连续编号：节数={len(sections)} restart={restart}，"
           "正文首页不会是 — 1 —"),
          "gwtool/core/docxgen.py:236-245（前置节无页脚 + 正文节 pgNumType start=1）")

    # 页脚外侧：奇右偶左（取**正文节**的页脚——v1.15 起封面+目录是独立前置节，
    # 前置节没有页码，用第一节会读到 None）
    try:
        from docx.enum.text import WD_ALIGN_PARAGRAPH as WA
        body_sec = next(
            (s for s in sections
             if "PAGE" in (s.footer._element.xml or "")
             or "PAGE" in (s.even_page_footer._element.xml or "")), None)
        if body_sec is None:
            m.add("page.outside.docx", scenario, "页码",
                  "DOCX 页码外侧（奇右偶左）", SKIP, "没有含 PAGE 域的正文节",
                  "gwtool/core/docxgen.py:236-245,339-363")
        else:
            odd_align = body_sec.footer.paragraphs[0].alignment
            even_align = body_sec.even_page_footer.paragraphs[0].alignment
            ok = odd_align == WA.RIGHT and even_align == WA.LEFT
            m.add("page.outside.docx", scenario, "页码",
                  "DOCX 页码外侧（奇右偶左）", PASS if ok else FAIL,
                  f"odd={odd_align} even={even_align}",
                  "gwtool/core/docxgen.py:339-363")
    except Exception as exc:
        m.add("page.outside.docx", scenario, "页码", "DOCX 页码外侧（奇右偶左）",
              SKIP, f"读取页脚对齐失败：{exc}", "gwtool/core/docxgen.py:339-363")

    # 字体：封面标题/一级/二级/三级/正文各抽一段
    # v1.15：材料/公文标题用模板新增的 doc_title（小标宋二号），与内容一级
    # 标题「一、」的 h1（黑体三号）是两套；「一级标题」必须抽**材料标题之后
    # 的第一个 Heading 1**，否则会把材料标题误判成一级标题。
    def _east_asia(par) -> str:
        xml = par._p.xml
        mm = re.findall(r'w:eastAsia="([^"]+)"', xml)
        return mm[0] if mm else ""

    title_par = next((p for p in doc.paragraphs
                      if (p.text or "").strip() == tpl.cover.title), None)

    # v1.15：材料标题占 Heading 1（小标宋），内容「一、」可能落在 Heading 2
    # 之后（层级重定基），因此**不能按样式名抽样**，按公文序号文本抽样：
    #   一级 = 一、…；二级 = （一）…；三级 = 1.… 或 （1）…
    def _first_by_re(pattern: str):
        rx = re.compile(pattern)
        for par in doc.paragraphs:
            text = (par.text or "").strip()
            if text and rx.match(text):
                return par
        return None

    expected = [
        ("封面标题", title_par, _COVER_TITLE_FONT,
         "gwtool/core/docxgen.py:323-351"),
        ("一级标题", _first_by_re(r"^[一二三四五六七八九十]+、"), tpl.h1.font,
         "gwtool/core/docxgen.py:419-433"),
        ("二级标题", _first_by_re(r"^（[一二三四五六七八九十]+）"), tpl.h2.font,
         "gwtool/core/docxgen.py:419-433"),
        ("三级标题", _first_by_re(r"^(?:\d+[.．]|（\d+）)"), tpl.h3.font,
         "gwtool/core/docxgen.py:419-433"),
        ("正文", _first_body(doc), tpl.body_font,
         "gwtool/core/docxgen.py:434-448"),
    ]
    for label, par, want, ev in expected:
        if par is None:
            m.add(f"font.docx.{label}", scenario, "字体",
                  f"DOCX {label}字体 = {want}", SKIP, f"未找到{label}样本段落", ev)
            continue
        got = _east_asia(par)
        m.add(f"font.docx.{label}", scenario, "字体", f"DOCX {label}字体 = {want}",
              PASS if got == want else FAIL,
              f"实测 eastAsia={got!r} 期望 {want!r}（样本：{(par.text or '')[:16]!r}）",
              ev)


def _first_style(doc, style_name: str):
    for p in doc.paragraphs:
        if p.style.name == style_name and (p.text or "").strip():
            return p
    return None


def _first_body(doc):
    for p in doc.paragraphs:
        text = (p.text or "").strip()
        if p.style.name == "Normal" and len(text) >= 12 and "。" in text:
            return p
    return None


# ------------------------------------------------------------------ PDF 断言
def _pdf_checks(m: Matrix, scenario: str, pdf_path: Path, tpl,
                expected_headings: int = 0) -> dict:
    import pymupdf as fitz
    doc = fitz.open(str(pdf_path))
    info = {"pages": doc.page_count}
    try:
        # A4 尺寸
        bad = [(i + 1, round(p.rect.width, 1), round(p.rect.height, 1))
               for i, p in enumerate(doc)
               if abs(p.rect.width - _A4_W) > _TOL
               or abs(p.rect.height - _A4_H) > _TOL]
        m.add("size.a4", scenario, "A4", "A4 页面 595×842pt（±2）",
              PASS if not bad else FAIL,
              "全部页面在容差内" if not bad else f"越界页：{bad[:4]}",
              "gwtool/core/pdfrender.py:110-260")

        toc_pages = _toc_pages(doc, tpl)
        cover_pages = [1] if tpl.cover.enabled else []
        cover_text = _norm(doc[0].get_text("text")) if cover_pages else ""
        m.add("cover.exists.pdf", scenario, "封面", "PDF 封面含标题/单位/日期",
              PASS if all(_norm(x) in cover_text
                          for x in (tpl.cover.title, tpl.cover.org,
                                    tpl.cover.date)) else FAIL,
              f"封面页文本长度 {len(cover_text)}",
              "gwtool/core/pdfrender.py:110-250（封面模板）")

        # 封面 / 目录无页码
        for cid, pages, label in (
                ("cover.nopage.pdf", cover_pages, "PDF 封面无页码"),
                ("toc.nopage.pdf", toc_pages, "PDF 目录页无页码")):
            if not pages:
                m.add(cid, scenario, "封面" if "cover" in cid else "目录",
                      label, SKIP, "对应页面不存在（模板未启用）",
                      "gwtool/core/pdfrender.py:478-520,523-547")
                continue
            nums = [n for pno in pages for n, _x, _y in
                    _page_labels(doc[pno - 1])]
            m.add(cid, scenario, "封面" if "cover" in cid else "目录", label,
                  FAIL if nums else PASS,
                  f"页 {pages} 上取到页码 {nums}" if nums else f"页 {pages} 无页码",
                  "gwtool/core/pdfrender.py:511-513,523-560（按前置页 skip + 纸面页码奇偶）")

        # 目录存在 + 条目 + 抽样页码一致
        m.add("toc.exists.pdf", scenario, "目录", "PDF 目录页存在",
              PASS if toc_pages else FAIL, f"目录页：{toc_pages}",
              "gwtool/core/pdfrender.py:448-520")
        entries: list[tuple[str, int]] = []
        if toc_pages:
            toc_text = "\n".join(doc[pno - 1].get_text("text")
                                 for pno in toc_pages)
            entries = _parse_toc_entries(toc_text)
        m.add("toc.entries.pdf", scenario, "目录", "PDF 目录条目>0",
              PASS if entries else FAIL, f"解析到 {len(entries)} 条",
              "gwtool/core/pdfrender.py:448-520")
        # 条目数应与"应有标题数"一致（多/少都说明目录抓取或标题收集口径有问题）
        if expected_headings:
            m.add("toc.count.pdf", scenario, "目录",
                  "目录条目数 = 应有标题数",
                  PASS if len(entries) == expected_headings else FAIL,
                  f"目录 {len(entries)} 条 / 应有 {expected_headings} 条",
                  "gwtool/core/pdfrender.py:448-475")
        # 正文页码：起点 1、连续、外侧
        front = max(toc_pages + cover_pages) if (toc_pages or cover_pages) else 0
        body_pages = list(range(front + 1, doc.page_count + 1))

        if entries:
            # v1.15 口径：目录里的页码是**纸面页码**（正文从 1 起），不是 PDF
            # 物理页序 —— 物理页 = 前置页数 + 纸面页码。旧实现把二者混为一谈，
            # 会把正确的目录判成 FAIL。
            def _page_has(printed: int, title: str) -> bool:
                phys = front + printed
                if printed <= 0 or not (1 <= phys <= doc.page_count):
                    return False
                text = _norm(doc[phys - 1].get_text("text"))
                needle = _norm(title)
                # 长标题在目录里可能被折行/省略：按前 8 字兜底匹配
                return needle in text or (len(needle) >= 8
                                          and needle[:8] in text)

            numeric = [(t, p) for t, p in entries if p > 0]
            sample = (numeric or entries)[:3]
            hit = [_page_has(p, t) for t, p in sample]
            status = PASS if (numeric and all(hit)) else FAIL
            note = (f"抽样={sample}（前置换算 front={front}）命中={hit}"
                    if numeric else f"目录页码全部未定位（—）：{sample}")
            m.add("toc.pages.pdf", scenario, "目录",
                  "目录抽样 3 条页码与正文实际页一致", status, note,
                  "gwtool/core/pdfrender.py:478-520")
            # 全量一致性（抽样之外再兜一层：任一条目页码对不上都算 FAIL）
            zero = [(t, p) for t, p in entries if p <= 0]
            wrong = [(t, p) for t, p in entries if p > 0 and not _page_has(p, t)]
            m.add("toc.all.pdf", scenario, "目录",
                  "全部目录条目页码与正文页一致（无未定位）",
                  PASS if not zero and not wrong else FAIL,
                  f"未定位={zero[:3]}；页码不符={wrong[:3]}"
                  if (zero or wrong) else f"{len(entries)} 条全部一致",
                  "gwtool/core/pdfrender.py:478-520")
        else:
            m.add("toc.pages.pdf", scenario, "目录",
                  "目录抽样 3 条页码与正文实际页一致", SKIP,
                  "目录无可解析条目", "gwtool/core/pdfrender.py:478-520")
        labels = {pno: _page_labels(doc[pno - 1]) for pno in body_pages}
        first_labels = labels[body_pages[0]] if body_pages else []
        expect_first = 1
        m.add("page.start.pdf", scenario, "页码", "正文首页页码为 — 1 —",
              PASS if any(n == expect_first for n, _x, _y in first_labels)
              else FAIL,
              f"正文首页 p{body_pages[0] if body_pages else 0} 上的页码="
              f"{[n for n, _x, _y in first_labels]}",
              "gwtool/core/pdfrender.py:468-469（从第 1 页起盖章，含封面/目录）")

        # 连续：正文第 k 页 = k，末页 = 正文页数
        seq = []
        for pno in body_pages:
            nums = [n for n, _x, _y in labels[pno]]
            seq.append(nums[0] if nums else None)
        expect = list(range(1, len(body_pages) + 1))
        m.add("page.continuous.pdf", scenario, "页码",
              "正文页码连续且末页等于正文页数",
              PASS if seq == expect else FAIL,
              f"实测={seq} 期望={expect}",
              "gwtool/core/pdfrender.py:478-520,523-547")

        # 外侧：数字为奇 → 右侧；偶 → 左侧
        outside_bad = []
        for pno in body_pages:
            for n, x, _y in labels[pno]:
                right = x > doc[pno - 1].rect.width / 2
                if (n % 2 == 1) != right:
                    outside_bad.append((pno, n, round(x)))
        m.add("page.outside.pdf", scenario, "页码", "页码外侧（奇右偶左）",
              PASS if not outside_bad else FAIL,
              f"错位={outside_bad[:4]}" if outside_bad else "全部符外侧规则",
              "gwtool/core/pdfrender.py:523-560")

        # 字体族（能取则取，取不到 SKIP；三类都取到才 PASS）
        fonts = set()
        for page in doc:
            try:
                d = page.get_text("dict")
            except Exception:
                continue
            for blk in d.get("blocks", []):
                for line in blk.get("lines", []):
                    for span in line.get("spans", []):
                        name = (span.get("font") or "").strip()
                        if name:
                            fonts.add(name)
        expected = {"宋": ("SimSun", "Song", "宋"),
                    "黑": ("SimHei", "Hei", "黑"),
                    "楷": ("Kai", "楷")}
        matched = {k: sorted(f for f in fonts
                             if any(tok in f for tok in toks))
                   for k, toks in expected.items()}
        found = {k: v for k, v in matched.items() if v}
        if not fonts:
            m.add("font.pdf", scenario, "字体", "PDF 字体族含宋/黑/楷（可取得则查）",
                  SKIP, "PyMuPDF 未取到任何字体名", "gwtool/core/pdfrender.py:97-110")
        elif len(found) < len(expected):
            m.add("font.pdf", scenario, "字体", "PDF 字体族含宋/黑/楷（可取得则查）",
                  SKIP,
                  f"仅取到 {len(found)}/3 族（{found}），全部字体名={sorted(fonts)}；"
                  "本机缺公文标准字体时文件名会退化为同一族，无法逐族判定",
                  "gwtool/core/pdfrender.py:60-110")
        else:
            m.add("font.pdf", scenario, "字体", "PDF 字体族含宋/黑/楷（可取得则查）",
                  PASS, f"命中 {found}；全部取到 {sorted(fonts)}",
                  "gwtool/core/pdfrender.py:97-110")

        info["toc_pages"] = toc_pages
        info["body_pages"] = len(body_pages)
        info["entries"] = len(entries)
        return info
    finally:
        doc.close()


# ------------------------------------------------------------------ A3 断言
def _a3_checks(m: Matrix, scenario: str, a4_path: Path, a3_path: Path) -> dict:
    import pymupdf as fitz
    from gwtool.core.booklet import booklet_order

    a4 = fitz.open(str(a4_path))
    a3 = fitz.open(str(a3_path))
    info = {"pages": a3.page_count}
    try:
        rects = [(round(p.rect.width, 1), round(p.rect.height, 1))
                 for p in a3]
        bad = [r for r in rects
               if abs(r[0] - _A3_W) > _TOL or abs(r[1] - _A3_H) > _TOL]
        m.add("size.a3", scenario, "A3", "A3 页面 1190×842pt（±2）",
              PASS if not bad else FAIL,
              "全部页面在容差内" if not bad else f"越界：{bad[:4]}",
              "gwtool/core/booklet.py:44-79")

        m.add("a3.even", scenario, "A3", "A3 页数为偶数",
              PASS if a3.page_count % 2 == 0 else FAIL,
              f"页数={a3.page_count}", "gwtool/core/booklet.py:25-41")

        order = booklet_order(a4.page_count)
        m.add("a3.orderlen", scenario, "A3", "A3 页数 = booklet_order 长度",
              PASS if len(order) == a3.page_count else FAIL,
              f"order={len(order)} a3={a3.page_count}",
              "gwtool/core/booklet.py:25-41")

        # 2-up 位置：每张 A3 的左右半页文本应对应 booklet_order 指定的源页
        bad_pairs: list[str] = []
        checked = 0
        for i, page in enumerate(a3):
            if i >= len(order):
                break
            left_expected, right_expected = order[i]
            half = page.rect.width / 2
            texts = {"left": [], "right": []}
            try:
                blocks = page.get_text("dict").get("blocks", [])
            except Exception:
                blocks = []
            for blk in blocks:
                for line in blk.get("lines", []):
                    line_text = "".join(sp.get("text", "")
                                        for sp in line.get("spans", [])).strip()
                    if not line_text:
                        continue
                    side = "left" if line.get("bbox", [0])[0] < half else "right"
                    texts[side].append(line_text)
            for slot, srcno in (("left", left_expected),
                                ("right", right_expected)):
                joined = _norm("".join(texts[slot]))
                # 0 = 补白哨兵；> 源页数 = 骑马钉为凑 4 的倍数补的空白页
                # （booklet_order 的页码仍按补齐后的 N 编号）——两者都期望空白。
                if srcno == 0 or srcno > a4.page_count:
                    ok = not joined
                    checked += 1
                    if not ok:
                        bad_pairs.append(f"p{i + 1}{slot} 期望空白，实得"
                                         f"{joined[:12]!r}")
                    continue
                sample = _first_nonblank_page_text(a4, srcno)
                if not sample:
                    continue          # 源页无可提取文本：无法判定
                checked += 1
                if _norm(sample) not in joined:
                    bad_pairs.append(
                        f"p{i + 1}{slot} 应为源页{srcno}（{sample[:10]!r}）")
        if checked == 0:
            m.add("a3.2up", scenario, "A3", "2-up 左右页文本与 booklet_order 一致",
                  SKIP,
                  "A3 文本无法提取（show_pdf_page 嵌入的 XObject 文本不可见）",
                  "gwtool/core/booklet.py:44-79")
        else:
            m.add("a3.2up", scenario, "A3", "2-up 左右页文本与 booklet_order 一致",
                  PASS if not bad_pairs else FAIL,
                  f"校验 {checked} 个半页；不符={bad_pairs[:4]}",
                  "gwtool/core/booklet.py:44-79")
        info["checked"] = checked
        return info
    finally:
        a4.close()
        a3.close()


# ------------------------------------------------------------------ 报告
def _write_report(m: Matrix, out_dir: Path, meta: dict,
                  scenarios: list[dict]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "meta": meta,
        "scenarios": scenarios,
        "checks": [c.to_dict() for c in m.checks],
        "summary": {"total": len(m.checks), "pass": m.count(PASS),
                    "fail": m.count(FAIL), "skip": m.count(SKIP)},
        "defects": [c.to_dict() for c in m.checks if c.status == FAIL],
        "skips": [c.to_dict() for c in m.checks if c.status == SKIP],
    }
    json_path = out_dir / "compile_matrix.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                         encoding="utf-8")

    lines = ["# 一键汇编验收矩阵报告", ""]
    meta_lines = [
        f"- 生成时间：{meta.get('started', '')}",
        f"- 版本：{meta.get('version', '')}；模式：{'fast' if meta.get('fast') else 'full'}",
        f"- 依赖：{meta.get('deps', {})}",
        f"- 结果：共 {len(m.checks)} 项，PASS {m.count(PASS)}、"
        f"FAIL {m.count(FAIL)}、SKIP {m.count(SKIP)}",
    ]
    lines += meta_lines + [""]
    lines += ["## 场景与产物", "",
              "| 场景 | 输入 | A4 页数 | A3 页数 | 产物目录 |",
              "|---|---|---|---|---|"]
    for s in scenarios:
        lines.append(f"| {s['label']} | {', '.join(Path(p).name for p in s['inputs'])}"
                     f" | {s['pages'].get('a4', '-')} | {s['pages'].get('a3', '-')}"
                     f" | `{s['artifact_dir']}` |")
    lines += ["", "## 检查矩阵", "",
              "| 检查项 | 场景 | 结果 | 说明 |", "|---|---|---|---|"]
    for c in m.checks:
        detail = c.detail.replace("|", "\\|")[:80]
        lines.append(f"| {c.item} | {c.scenario} | **{c.status}** | {detail} |")
    if report["defects"]:
        lines += ["", "## FAIL 明细（含证据）", ""]
        for c in report["defects"]:
            lines.append(f"- **{c['id']}**（{c['scenario']}）：{c['detail']}")
            if c["evidence"]:
                lines.append(f"  - 证据：`{c['evidence']}`")
    if report["skips"]:
        lines += ["", "## SKIP 明细（未就绪项，不假装 PASS）", ""]
        for c in report["skips"]:
            lines.append(f"- **{c['id']}**（{c['scenario']}）：{c['detail']}")
            if c["evidence"]:
                lines.append(f"  - 位置：`{c['evidence']}`")
    md_path = out_dir / "compile_matrix.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path


# ------------------------------------------------------------------ 主流程
def run(out_dir: Path, fast: bool = False) -> int:
    t_start = time.time()
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    from gwtool import __version__

    _ensure_qt()
    workdir = Path(tempfile.mkdtemp(prefix="gwtool_matrix_"))
    matrix = Matrix()
    scenario_rows: list[dict] = []
    try:
        inputs = _build_inputs(workdir)
        from gwtool.core.template import DocTemplate
        tpl = DocTemplate()
        tpl.cover.enabled = True
        tpl.cover.title = "全市安全生产工作会议材料汇编"
        tpl.cover.org = "××市人民政府办公室"
        tpl.cover.date = "2026年10月24日"
        tpl.toc_enabled = True
        tpl.page_number_enabled = True

        chosen = SCENARIOS[:1] if fast else SCENARIOS
        for sid, label in chosen:
            files = inputs[sid]
            try:
                prod = _compile_scenario(sid, files, workdir, tpl)
                _docx_checks(matrix, sid, prod["docx"], tpl)
                from gwtool.core.pdfrender import collect_headings
                expected_headings = len(collect_headings(prod["trees"], tpl))
                pdf_info = _pdf_checks(matrix, sid, prod["a4_pdf"], tpl,
                                       expected_headings=expected_headings)
                a3_info = _a3_checks(matrix, sid, prod["a4_pdf"], prod["a3_pdf"])
            except Exception as exc:                # 单场景炸掉不拖垮整矩阵
                matrix.add(f"scenario.{sid}", sid, "场景", f"{label} 汇编",
                           FAIL, f"{type(exc).__name__}: {exc}",
                           "scripts/compile_matrix.py:run")
                scenario_rows.append({
                    "id": sid, "label": label,
                    "inputs": [str(p) for p in files], "pages": {},
                    "artifact_dir": str(workdir / "artifacts" / sid),
                    "error": traceback.format_exc(limit=2)})
                continue
            scenario_rows.append({
                "id": sid, "label": label,
                "inputs": [str(p) for p in files],
                "pages": {"a4": pdf_info.get("pages"),
                          "a3": a3_info.get("pages"),
                          "body": pdf_info.get("body_pages"),
                          "toc_entries": pdf_info.get("entries")},
                "artifact_dir": str(Path(prod["a4_pdf"]).parent),
                "artifacts": {
                    "docx": Path(prod["docx"]).name,
                    "a4_pdf": Path(prod["a4_pdf"]).name,
                    "a3_pdf": Path(prod["a3_pdf"]).name,
                },
            })

        # 产物拷进输出目录，报告里给相对路径
        artifacts_out = out_dir / "artifacts"
        if artifacts_out.exists():
            shutil.rmtree(artifacts_out, ignore_errors=True)
        for row in scenario_rows:
            src_dir = Path(row.get("artifact_dir", ""))
            if not src_dir.exists():
                continue
            dst = artifacts_out / row["id"]
            shutil.copytree(src_dir, dst)
            row["artifact_dir"] = str(dst)
            row["artifacts"] = {k: str(dst / v)
                                for k, v in row.get("artifacts", {}).items()}

        meta = {
            "tool": "compile_matrix",
            "version": __version__,
            "started": started,
            "duration_s": round(time.time() - t_start, 2),
            "fast": fast,
            "deps": _deps(),
        }
        json_path = _write_report(matrix, out_dir, meta, scenario_rows)
        summary = (f"PASS {matrix.count(PASS)} / FAIL {matrix.count(FAIL)} / "
                   f"SKIP {matrix.count(SKIP)}")
        print(f"汇编矩阵完成：{summary}")
        print(f"报告：{json_path}")
        print(f"      {json_path.with_suffix('.md')}")
        return 0 if matrix.count(FAIL) == 0 else 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="一键汇编验收矩阵（离线、纯内置渲染器）")
    ap.add_argument("--out", default="", help="报告与产物输出目录")
    ap.add_argument("--fast", action="store_true",
                    help="小样本模式（仅 1 类输入，供测试/冒烟）")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:                       # argparse 用法错误 → 2
        return int(exc.code or 2)
    out_dir = Path(args.out) if args.out else (ROOT / "out_compile_matrix")
    try:
        return run(out_dir, fast=args.fast)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"汇编矩阵无法运行：{type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

