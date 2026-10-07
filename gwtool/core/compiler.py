# -*- coding: utf-8 -*-
"""一键汇编编排：所选材料 -> 合并 -> 模板渲染 -> docx / PDF。"""
from __future__ import annotations

import os
import subprocess
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .. import logs
from ..db import dao
from .model import DocTree
from .template import DocTemplate
from . import docxgen


# 附录标题。与 build_sources_tree 里的 DocTree.title 保持一致：
# docxgen 会按 tree.title 插入一级标题，两者不一致会出现标题重复。
SOURCES_TITLE = "汇编材料来源清单"


@dataclass
class CompileRequest:
    doc_ids: list[int] = field(default_factory=list)      # 资料库文档
    extra_paths: list[str] = field(default_factory=list)  # 未入库文件
    template: DocTemplate | None = None
    out_docx: str = ""
    material_titles: list[str] | None = None              # 覆盖材料标题
    include_sources: bool = False                          # 追加材料来源清单


def load_trees_ex(doc_ids: list[int], extra_paths: list[str]
                  ) -> tuple[list[DocTree], list[tuple[str, str]], list[bool]]:
    """`load_trees` 的完整形态：同时返回失败清单与"输入槽位是否成功"。

    返回 `(成功解析的 trees, failures, slot_ok)`：
      - failures 元素为 `(展示名, 原因)`，展示名对文档是 `文档#id`、
        对文件是 `Path(p).name`（仅用于日志/提示，不做身份匹配）；
      - `slot_ok` 与输入 `[*doc_ids, *extra_paths]` **逐位对齐**，第 i 位
        表示第 i 个输入是否产出了材料。

    为什么要有它：`load_trees` 对进不了汇编的输入**静默丢弃**（返回值里
    看不出少了谁），而调用方若按"输入个数"准备材料标题，丢弃后标题会
    **整体错位**——第 N 份材料被冠以第 N+1 份的标题（第五轮深审实测：
    坏文件在前时，好材料被冠以坏材料的标题）。要对齐，就必须按**输入
    槽位**逐位过滤标题：坏文件只跳过自己那一坑，不挤占别人的。

    软删除（已进回收站）与不存在的文档同样视为"进不了汇编"，与
    `collect_sources` 的口径一致——否则会出现"正文里有它、来源清单里
    没有它"的自相矛盾。
    """
    trees: list[DocTree] = []
    failures: list[tuple[str, str]] = []
    slot_ok: list[bool] = []
    for did in doc_ids:
        d = dao.get_document(did)
        if not d:
            failures.append((f"文档#{did}", "材料不存在（可能已被彻底删除）"))
            slot_ok.append(False)
            continue
        if (d.deleted_time or "").strip():
            failures.append((d.title or f"文档#{did}", "材料已在回收站中"))
            slot_ok.append(False)
            continue
        tree = DocTree.from_json(d.title, d.blocks_json)
        if not tree.blocks:  # 旧数据兜底：按纯文本段落
            from .model import Block
            for line in (d.content_text or "").splitlines():
                if line.strip():
                    tree.blocks.append(Block(text=line.strip()))
        trees.append(tree)
        slot_ok.append(True)
    from .importer import parse_any
    for p in extra_paths:
        try:
            r = parse_any(p)
            reason = "" if (r.ok and r.tree) else (r.error or "解析失败")
        except Exception as exc:      # 单个文件炸掉不能拖垮整次汇编
            reason = str(exc) or exc.__class__.__name__
            r = None
        if r is not None and r.ok and r.tree:
            trees.append(r.tree)
            slot_ok.append(True)
        else:
            failures.append((Path(p).name, reason or "解析失败"))
            slot_ok.append(False)
    return trees, failures, slot_ok


def load_trees(doc_ids: list[int], extra_paths: list[str]) -> list[DocTree]:
    """合并资料库文档与额外文件的解析结果（保持既有签名：只返回 trees）。

    ⚠ 进不了汇编的输入（解析失败 / 文档不存在 / 已进回收站）会被**静默
    丢弃**且调用方无从得知 —— 需要失败清单或要做"材料标题对齐"的调用方
    请改用 `load_trees_ex`。
    """
    return load_trees_ex(doc_ids, extra_paths)[0]


def collect_sources(doc_ids: list[int], extra_paths: list[str],
                    extra_parsed: list[str] | None = None) -> list[dict]:
    """收集汇编材料的来源信息（标题 / 原文件名 / 导入时间 / 所属分类）。

    **为什么要它**：汇编类公文需要可追溯性 —— "这份汇编里的内容从哪来"
    必须能答出来（审计、责任划分、领导询问）。此前成品是一个孤立的 DOCX，
    来源只存在于操作者的记忆里，隔周就说不清了。

    **口径必须与 `load_trees_ex` 一致**：解析失败的额外文件内容根本没进
    汇编，列进来源清单就是"虚列出处"——清单说内容来自它，实际来自别处。
    （第五轮深审实测：坏 docx 被列入清单、其内容缺席，且材料标题整体错位。）

    `extra_parsed` 是调用方已经成功解析过的额外文件集合（`compile_docx`
    传入以避免同一批文件被解析两遍）；传 None 时本函数自行解析判定，
    独立调用（如测试、预览）同样得到过滤后的清单。
    """
    out: list[dict] = []
    try:
        cats = {c.id: c.name for c in dao.list_categories()}
    except Exception:
        cats = {}
    for did in doc_ids:
        d = dao.get_document(did)
        if not d:
            continue          # 材料在汇编前被删掉了：跳过而不是编造一行
        # 软删除（已进回收站）的材料同样不该出现在清单里——
        # get_document 仍能取到它，只看 `d` 是否存在会把它列进去
        if (d.deleted_time or "").strip():
            continue
        out.append({
            "title": d.title or Path(d.file_path or "").stem,
            "file": Path(d.file_path).name if d.file_path else "",
            "time": (d.import_time or "")[:16],
            "category": cats.get(d.category_id, "") or "未分类",
        })
    if extra_parsed is None:
        from .importer import parse_any
        extra_parsed = []
        for p in extra_paths:
            try:
                r = parse_any(p)
            except Exception:
                continue
            if r.ok and r.tree:
                extra_parsed.append(p)
            else:
                logs.get_logger("compile").warning(
                    "额外材料解析失败，未列入来源清单：%s（%s）",
                    Path(p).name, r.error or "解析失败")
    ok = {str(p) for p in extra_parsed}
    for p in extra_paths:
        if str(p) not in ok:
            continue
        out.append({
            "title": Path(p).stem,
            "file": Path(p).name,
            "time": "",
            "category": "（未入库）",
        })
    return out


def build_sources_tree(sources: list[dict]) -> DocTree:
    """把来源清单组织成 DocTree，复用既有公文排版（不另起排版逻辑）。"""
    from .model import HEADING, TABLE, Block

    tree = DocTree(title=SOURCES_TITLE)
    tree.blocks.append(Block(type=HEADING, level=1, text=SOURCES_TITLE))
    rows = [["序号", "材料标题", "原文件名", "导入时间", "所属分类"]]
    for i, s in enumerate(sources, start=1):
        rows.append([str(i), s.get("title", ""), s.get("file", ""),
                     s.get("time", ""), s.get("category", "")])
    tree.blocks.append(Block(type=TABLE, rows=rows))
    return tree


def compile_docx(req: CompileRequest) -> str:
    tpl = req.template or DocTemplate()
    trees, failures, slot_ok = load_trees_ex(req.doc_ids, req.extra_paths)
    for name, reason in failures:
        # 静默丢材料是"用户以为进汇编了，其实没有"——必须留痕。
        # UI 主路径（向导）的材料先入库再汇编，失败在导入步已逐条点名；
        # 这里兜住直接走 CompileRequest 的调用方（脚本/测试/后续入口）。
        logs.get_logger("compile").warning(
            "汇编材料进不了成品，已跳过：%s（%s）", name, reason)
    if not trees:
        raise ValueError("没有可汇编的材料")
    if req.material_titles:
        # 标题按**输入槽位**对齐：第 i 个标题属于第 i 个输入（先文档后
        # 文件），进不了汇编的输入只跳过自己那一坑。旧实现直接
        # zip(trees, titles)，坏文件被丢弃后标题整体错位（实测：坏材料
        # 的标题被冠到好材料头上）。
        aligned = [t for t, ok in zip(req.material_titles, slot_ok) if ok]
        for t, title in zip(trees, aligned):
            if title:
                t.title = title
    if req.include_sources:
        # 把成功解析的额外文件传下去，避免同一批文件被解析两遍
        ok_extra = [p for p, ok in zip(req.extra_paths,
                                       slot_ok[len(req.doc_ids):]) if ok]
        sources = collect_sources(req.doc_ids, req.extra_paths,
                                  extra_parsed=ok_extra)
        # 无材料时不追加空清单（否则会凭空多出一个只有表头的附录）
        if sources:
            trees.append(build_sources_tree(sources))
    # 字体可观测性（N9）：缺字体时 Word/WPS 会静默替换字体，版心规格
    # 实际不成立而成品不合规。这里不改变生成行为，只把风险写进日志；
    # 用户可见的提示由 UI 层（汇编完成对话框）调 fontcheck.missing_note()。
    try:
        from .fontcheck import missing_fonts
        _missing = missing_fonts()
        if _missing:
            logs.get_logger("compile").warning(
                "本机缺失公文标准字体，生成的 docx 可能被替换字体渲染：%s",
                "、".join(_missing))
    except Exception:
        pass  # 探测失败绝不拦住汇编
    return docxgen.generate_docx(trees, tpl, req.out_docx)


# ------------------------------------------------------------------ PDF 输出
def docx_to_pdf(docx_path: str, out_pdf: str = "") -> str:
    """docx -> PDF。离线策略：优先调用本机 Word/WPS COM（Windows），
    其次 LibreOffice；都不可用则抛出提示（可用内置 PDF 渲染器替代）。

    两处收尾都不能省：
      - LibreOffice 分支的临时目录必须 rmtree（老实现每次转 PDF 都在 %TEMP%
        留一个 gwtool_pdf_* 目录，批量转 200 份就是 200 个残留目录）；
      - COM 分支必须 finally 里 Quit + Close，否则 SaveAs2 抛异常时会留下
        看不见的 WPS/Word 后台进程，反复导出后进程越堆越多。
    """
    out_pdf = out_pdf or str(Path(docx_path).with_suffix(".pdf"))
    if shutil.which("soffice") or shutil.which("libreoffice"):
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        outdir = tempfile_dir()
        try:
            result = subprocess.run([soffice, "--headless", "--convert-to",
                                     "pdf", "--outdir", outdir, docx_path],
                                    capture_output=True, timeout=180,
                                    check=False)
            produced = Path(outdir) / (Path(docx_path).stem + ".pdf")
            if produced.exists():
                shutil.move(str(produced), out_pdf)
                return out_pdf
            # 转换失败要留痕：stderr 摘要写日志，否则用户只看到
            # "未找到可用组件"，误以为缺依赖而非文档本身有问题。
            from .logs import get_logger
            get_logger("compile").warning(
                "LibreOffice 转 PDF 失败（exit=%s）：%s", result.returncode,
                (result.stderr or b"").decode("utf-8", errors="replace")[:300])
        finally:
            shutil.rmtree(outdir, ignore_errors=True)
    if os.name == "nt":
        try:
            import win32com.client  # type: ignore
        except ImportError:
            pass
        else:
            for progid in ("kwps.Application", "wps.Application", "Word.Application"):
                app = None
                doc = None
                try:
                    app = win32com.client.Dispatch(progid)
                    app.Visible = False
                    doc = app.Documents.Open(str(Path(docx_path).resolve()), ReadOnly=True)
                    doc.SaveAs2(str(Path(out_pdf).resolve()), FileFormat=17)  # wdFormatPDF
                    if Path(out_pdf).exists():
                        return out_pdf
                except Exception:
                    continue
                finally:
                    # 顺序与 COM 约定一致：先关文档再退应用；任一步失败都继续退应用
                    if doc is not None:
                        try:
                            doc.Close(False)
                        except Exception:
                            pass
                    if app is not None:
                        try:
                            app.Quit()
                        except Exception:
                            pass
    raise RuntimeError(
        "本机未找到可用的 docx->PDF 转换组件（WPS/Word/LibreOffice）。\n"
        "请使用程序内置的「PDF 预览/导出」功能（内置渲染器直接生成 PDF）。")


def tempfile_dir() -> str:
    import tempfile
    return tempfile.mkdtemp(prefix="gwtool_pdf_")
