# -*- coding: utf-8 -*-
"""OCR 模块（gwtool/core/ocr.py）真实链路深探。

全部真实调用、零 mock：真实 Tesseract 可执行文件（系统安装位置探测 +
设置项注入 + 环境变量 PATH 操纵）、真实 chi_sim 语言包、真实 PDF 渲染
（PyMuPDF 内置 CJK 字体 china-s 生成扫描件）、真实 SQLite 设置项。

捆绑命中场景通过**真实文件系统构造**覆盖：在解释器旁硬链接真实
tesseract.exe 与 chi_sim.traineddata 组成捆绑目录树，测试后彻底清理。
唯一的门控是 skip——目标机没有 Tesseract/chi_sim 时跳过识别类断言，
这是能力探测，不是替身。
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pymupdf as fitz
import pytest

from gwtool.core import ocr
from gwtool.db import dao

_TESS_CAND = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


def _wipe_bundled_tree() -> None:
    """真实清理解释器旁可能残留的捆绑树（Windows 文件锁下重试）。

    任何上一轮测试崩溃都可能留下半截树；捆绑场景一律先自愈清理，
    不对环境做"应该没有"的假设。
    """
    root = Path(sys.executable).resolve().parent / "tesseract"
    for _ in range(5):
        shutil.rmtree(root, ignore_errors=True)
        if not root.exists():
            return
        time.sleep(0.5)


def _real_tess() -> str:
    """真实定位系统 Tesseract：PATH 优先，其次 choco 默认安装位置。"""
    return shutil.which("tesseract") or (str(_TESS_CAND) if _TESS_CAND.exists() else "")


def _chi_sim_ok(tess: str) -> bool:
    """真实探测 chi_sim 是否可用（有 Tesseract 但缺中文包时跳过识别断言）。"""
    return bool(tess) and ocr.has_chi_sim(tess)


@pytest.fixture()
def clean_settings(tmp_db):
    """真实 SQLite 设置项：进入清空 tesseract_path，退出恢复。"""
    dao.set_setting("tesseract_path", "")
    yield
    dao.set_setting("tesseract_path", "")


@pytest.fixture()
def no_tess_env(clean_settings, monkeypatch):
    """真实环境操纵：清空 PATH 让 shutil.which 找不到 tesseract。

    本机 .venv/Scripts 旁无捆绑目录、无 /opt/gwtool —— 三种来源全空，
    tesseract_path()/ocr_image()/ocr_pdf() 必须走"未找到"分支。
    """
    monkeypatch.setenv("PATH", r"C:\Windows\System32")
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    yield


@pytest.fixture()
def real_tess_configured(clean_settings):
    """走产品主路径的真实配置：设置项指向系统 Tesseract。

    用户机上的正常形态就是「设置里指定路径」或「随包捆绑」；开发机
    没有捆绑，所以用真实 SQLite 设置项注入（tesseract_path 的
    configured 分支），全程零 mock。
    """
    tess = _real_tess()
    if not tess:
        pytest.skip("本机无 Tesseract 可执行文件")
    dao.set_setting("tesseract_path", tess)
    return tess


@pytest.fixture()
def bundled_tree(clean_settings):
    """真实构造随包捆绑目录树（硬链接真 exe 与 chi_sim，零拷贝）。

    布局对齐 ocr._bundled() 第一候选：
    {exe_dir}/tesseract/tesseract.exe + {exe_dir}/tesseract/tessdata/
    """
    tess = _real_tess()
    if not tess:
        pytest.skip("本机无 Tesseract 可执行文件")
    chi = Path(tess).parent / "tessdata" / "chi_sim.traineddata"
    if not chi.exists():
        pytest.skip("本机 tessdata 无 chi_sim 语言包")

    exe_dir = Path(sys.executable).resolve().parent
    root = exe_dir / "tesseract"
    exe_link = root / "tesseract.exe"
    td_dir = root / "tessdata"
    chi_link = td_dir / "chi_sim.traineddata"
    _wipe_bundled_tree()          # 自愈：清掉任何残留，保证从零构造

    root.mkdir(parents=True)
    td_dir.mkdir(parents=True)
    try:
        # exe 运行需要同目录 DLL：全部硬链接（零拷贝），树内 exe 才能真实执行
        src_dir = Path(tess).parent
        for dll in src_dir.glob("*.dll"):
            os.link(dll, root / dll.name)
        os.link(tess, exe_link)
        os.link(chi, chi_link)
        yield {"root": root, "exe": exe_link, "tessdata": td_dir}
    finally:
        # Windows：刚退出的子进程可能短暂锁住 exe，重试清理直至消失
        for _ in range(5):
            shutil.rmtree(root, ignore_errors=True)
            if not root.exists():
                break
            import time
            time.sleep(0.5)


# ---------------------------------------------------------- 捆绑探测
def test_bundled_empty_on_clean_machine(tmp_db):
    """解释器旁无捆绑目录：三种候选全部落空，返回空对。"""
    _wipe_bundled_tree()          # 先保证无树，再验证落空路径
    exe_dir = Path(sys.executable).resolve().parent
    assert not (exe_dir / "tesseract" / "tesseract.exe").exists()
    assert ocr._bundled() == ("", "")
    assert ocr._bundled_tessdata() == ""


def test_bundled_first_candidate_hit(bundled_tree):
    """真实硬链接树命中第一候选：返回 (exe, tessdata)，tessdata 探测为真。"""
    exe, td = ocr._bundled()
    assert Path(exe) == bundled_tree["exe"]
    assert Path(td) == bundled_tree["tessdata"]
    assert ocr._bundled_tessdata() == td


def test_bundled_tree_drives_full_ocr_path(bundled_tree, tmp_path):
    """无设置项时捆绑树接管整条链路：path 解析(56) → TESSDATA_PREFIX(86)
    → 树内真实 exe + 捆绑 chi_sim 识别成功。"""
    if not _chi_sim_ok(str(bundled_tree["exe"])):
        pytest.skip("捆绑树内 chi_sim 不可用")
    # 设置项保持为空（clean_settings），tesseract_path 必须落到捆绑分支
    assert ocr.tesseract_path() == str(bundled_tree["exe"])

    img = tmp_path / "bundled.png"
    _make_text_png(img, ["捆绑模式识别验证文本"])
    text = ocr.ocr_image(str(img))          # tess 留空：走捆绑解析 + 前缀设置
    assert len(text) >= 4
    assert any("\u4e00" <= ch <= "\u9fff" for ch in text), f"未识别出汉字: {text!r}"


def test_bundled_tessdata_false_when_dir_missing(tmp_db):
    """捆绑 exe 存在但 tessdata 目录缺失：_bundled_tessdata 返回空。"""
    exe_dir = Path(sys.executable).resolve().parent
    root = exe_dir / "tesseract"
    exe_link = root / "tesseract.exe"
    # 捆绑树必须建在「解释器所在目录」下（对齐 ocr._bundled 第一候选）。CI 的
    # Linux 容器解释器位于 /usr/bin，而容器已 apt 安装真实 /usr/bin/tesseract
    # （普通文件）：root 与其撞名，mkdir 的 exist_ok 只豁免目录、遇文件仍抛
    # FileExistsError（CI 实测）。此类环境无法在此处构造捆绑树，探测后跳过。
    if (root.exists() or root.is_symlink()) and not root.is_dir():
        pytest.skip(f"解释器旁已被同名非目录文件占用，无法构造捆绑树：{root}")
    _wipe_bundled_tree()
    tess = _real_tess()
    if not tess:
        pytest.skip("本机无 Tesseract 可执行文件")
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.link(tess, exe_link)
        exe, td = ocr._bundled()
        assert Path(exe) == exe_link and not Path(td).exists()
        assert ocr._bundled_tessdata() == ""
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------- 路径解析
def test_tesseract_path_prefers_configured_setting(clean_settings):
    """设置项指向真实存在的 exe：优先于捆绑与 PATH。"""
    tess = _real_tess()
    if not tess:
        pytest.skip("本机无 Tesseract")
    dao.set_setting("tesseract_path", tess)
    assert ocr.tesseract_path() == tess
    assert ocr.available() is True


def test_tesseract_path_skips_dead_setting(clean_settings):
    """设置项指向不存在的路径：跳过设置，落到捆绑/系统 PATH 的真实结果。"""
    dao.set_setting("tesseract_path", r"C:\definitely\not\here\tesseract.exe")
    got = ocr.tesseract_path()
    assert got != r"C:\definitely\not\here\tesseract.exe"
    expected = ocr._bundled()[0] or shutil.which("tesseract") or ""
    assert got == expected


def test_tesseract_path_empty_when_no_source(no_tess_env):
    """设置空 + 无捆绑 + PATH 无 tesseract：返回空，available 为假。"""
    assert ocr.tesseract_path() == ""
    assert ocr.available() is False


# ---------------------------------------------------------- 语言包探测
def test_has_chi_sim_real():
    tess = _real_tess()
    if not tess:
        pytest.skip("本机无 Tesseract")
    assert ocr.has_chi_sim(tess) is True


def test_has_chi_sim_bad_executable_returns_false(clean_settings):
    """传入不存在的 exe：subprocess 失败被兜底，返回 False 不抛异常。"""
    assert ocr.has_chi_sim(r"C:\definitely\not\here\tesseract.exe") is False


def test_has_chi_sim_empty_path_returns_false(no_tess_env):
    """无任何 tesseract 来源：直接 False（短路分支）。"""
    assert ocr.has_chi_sim() is False


def test_has_chi_sim_passes_tessdata_prefix_to_subprocess(bundled_tree, monkeypatch):
    """捆绑 tessdata 存在时：TESSDATA_PREFIX 随 env 传给子进程，**不改**进程全局。

    为什么不能写 os.environ：OCR 会在后台线程里跑（批量导入扫描件），
    那是进程级全局状态，两条调用路径并发时会互相覆盖、也会覆盖用户手工设置。
    现在改为 subprocess 的 env 参数，这里守住这个契约。
    """
    dao.set_setting("tesseract_path", str(bundled_tree["exe"]))
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)

    seen: dict = {}
    real_run = subprocess.run

    def spy_run(cmd, *a, **kw):
        seen["env"] = kw.get("env")
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(ocr.subprocess, "run", spy_run)
    assert ocr.has_chi_sim() is True
    assert seen.get("env") is not None, "必须显式传 env 给子进程"
    assert seen["env"].get("TESSDATA_PREFIX") == str(bundled_tree["tessdata"])
    assert "TESSDATA_PREFIX" not in os.environ, "不得写入进程全局环境"


# ---------------------------------------------------------- 单图识别
def _make_text_png(path: Path, lines: list[str], fontsize: float = 18.0) -> None:
    """用 PyMuPDF 内置 CJK 字体渲染真实扫描图（PNG）。"""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)      # A4
    y = 100.0
    for line in lines:
        page.insert_text((72, y), line, fontname="china-s", fontsize=fontsize)
        y += fontsize * 2.2
    pix = page.get_pixmap(dpi=220)
    pix.save(str(path))
    doc.close()


def test_ocr_image_reads_rendered_chinese(clean_settings, tmp_path):
    """真实渲染中文 PNG → 真实 Tesseract OCR：结果含可读文本。"""
    tess = _real_tess()
    if not tess:
        pytest.skip("本机无 Tesseract")
    if not _chi_sim_ok(tess):
        pytest.skip("本机 Tesseract 无 chi_sim 语言包")
    img = tmp_path / "sample.png"
    _make_text_png(img, ["公文汇编助手扫描识别测试", "第二行正文内容用于校验输出"])
    text = ocr.ocr_image(str(img), tess)
    assert len(text) >= 4
    assert any("\u4e00" <= ch <= "\u9fff" for ch in text), f"未识别出汉字: {text!r}"


def test_ocr_image_without_tesseract_raises(no_tess_env, tmp_path):
    img = tmp_path / "x.png"
    _make_text_png(img, ["任意内容"])
    with pytest.raises(RuntimeError, match="未找到 tesseract"):
        ocr.ocr_image(str(img))


# ---------------------------------------------------------- 整本 PDF OCR
def _make_scan_pdf(path: Path, pages: list[list[str]]) -> None:
    doc = fitz.open()
    for lines in pages:
        page = doc.new_page(width=595, height=842)
        y = 100.0
        for line in lines:
            page.insert_text((72, y), line, fontname="china-s", fontsize=18)
            y += 40.0
    doc.save(str(path))
    doc.close()


def test_ocr_pdf_full_pipeline(real_tess_configured, tmp_path):
    """真实两页扫描 PDF → 逐页渲染 + OCR：结构、标题、进度回调全链路。

    tesseract_path() 走真实设置项注入（产品主路径），内部再真实
    subprocess 调 Tesseract —— ocr_pdf 的 tesseract 参数留空，
    覆盖其内部路径解析链。
    """
    tess = real_tess_configured
    if not _chi_sim_ok(tess):
        pytest.skip("本机 Tesseract 无 chi_sim 语言包")

    pdf = tmp_path / "扫描样张.pdf"
    _make_scan_pdf(pdf, [
        ["公文汇编助手整本识别标题", "第一页正文内容必须足够长以便识别"],
        ["第二页继续正文段落内容测试", "止"],   # 单字符行：真实触发短行过滤
    ])
    progress: list[tuple[int, int]] = []
    tree = ocr.ocr_pdf(str(pdf), progress_cb=lambda cur, total: progress.append((cur, total)))

    assert progress == [(1, 2), (2, 2)]          # 每页真实回调一次
    assert tree.blocks, "两页中文扫描件 OCR 结果不应为空"
    for blk in tree.blocks:
        assert len(blk.text.strip()) >= 2        # 短行过滤真实生效（"止"不入块）
    head = tree.blocks[0]
    assert head.type == "heading" and head.level == 1
    # 产品行为：blocks 非空时标题提升为首个识别行（截断 50 字）
    assert tree.title == head.text[:50]


def test_ocr_pdf_blank_page_yields_empty_blocks(real_tess_configured, tmp_path):
    """纯空白页：OCR 无文本 → blocks 为空，标题保持 stem 且无首行提升。"""
    pdf = tmp_path / "blank_scan.pdf"
    _make_scan_pdf(pdf, [[]])
    tree = ocr.ocr_pdf(str(pdf))
    assert tree.blocks == []
    assert tree.title == "blank_scan"


def test_ocr_pdf_without_tesseract_raises(no_tess_env, tmp_path):
    pdf = tmp_path / "x.pdf"
    _make_scan_pdf(pdf, [["内容"]])
    with pytest.raises(RuntimeError, match="未找到 tesseract"):
        ocr.ocr_pdf(str(pdf))
