# -*- coding: utf-8 -*-
"""打包与安装配置回归测试。

这些约束都不是"代码风格"，而是每一条都对应一次真实故障：
  - 依赖浮动版本 -> PySide6 6.8+ 收紧枚举访问，口令锁启动即崩（v1.2.1）
  - [Code] 里裸用 MsgBox -> /SUPPRESSMSGBOXES 管不到它，静默安装永久挂死
  - 缺 PySide6.QtSvg hiddenimport -> 打包后 imageformats/qsvg 插件不进产物，图标全空白
  - 脚本里硬编码 C:\\gwtool -> 用户机右键菜单指向不存在的路径，静默失效
CI 只会跑打包命令，不会因为这些问题报错，所以必须在测试里守住。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


# ------------------------------------------------------------ 依赖锁定
def test_requirements_pins_pyside6_exactly_per_platform():
    """PySide6 必须按平台精确锁定，不能是 >= 浮动。

    Windows 与 Linux/麒麟 需要不同版本：官方 aarch64 wheel 自 6.8.1 起要求
    glibc>=2.39，麒麟全系（glibc 2.31）不满足，故 Linux 侧必须停在 6.8.0.2。
    """
    text = _read("requirements.txt")
    lines = [ln.strip() for ln in text.splitlines()
             if ln.strip().lower().startswith("pyside6")]
    assert len(lines) == 2, f"PySide6 应分平台各锁一条，实际：{lines}"
    for ln in lines:
        assert "==" in ln, f"PySide6 必须精确锁定，实际：{ln}"
        assert ">=" not in ln, f"PySide6 不允许浮动版本，实际：{ln}"
    joined = " ".join(lines)
    assert "sys_platform == 'win32'" in joined, "缺少 Windows 平台标记"
    assert "6.8.0.2" in joined, "Linux/麒麟 必须锁 6.8.0.2 以满足 glibc 2.31"


def test_requirements_has_no_unpinned_runtime_deps():
    """运行依赖一律精确锁定；带 python_version 分档的包允许出现两条。"""
    offenders = []
    for ln in _read("requirements.txt").splitlines():
        s = ln.split("#")[0].strip()
        if not s or s.startswith("-"):
            continue
        if "==" not in s:
            offenders.append(s)
    assert not offenders, f"以下依赖未精确锁定：{offenders}"


# ------------------------------------------------------------ Inno Setup
def test_installer_code_has_no_unguarded_msgbox():
    """[Code] 段里的 MsgBox 必须由 WizardSilent 守卫。

    普通 MsgBox 不受 /SUPPRESSMSGBOXES 影响，静默安装时会弹模态框并永久阻塞
    （实测 /VERYSILENT 安装挂住 5 分钟以上毫无进展），批量部署直接挂死。
    """
    text = _read("scripts", "setup_windows.iss")
    code = text.split("[Code]", 1)
    assert len(code) == 2, "未找到 [Code] 段"
    # 先剥离 Pascal 注释，否则注释里提到的 MsgBox 字样会干扰下面的顺序判断
    body = re.sub(r"\{.*?\}", "", code[1], flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)

    calls = [ln.strip() for ln in body.splitlines() if re.search(r"\bMsgBox\s*\(", ln)]
    assert calls, "预期 [Code] 段里有 MsgBox 提示；若已删除请同步调整本测试"
    assert "WizardSilent" in body, (
        "[Code] 段存在 MsgBox 却没有 WizardSilent 守卫，静默安装会挂死")

    # 守卫必须在第一个 MsgBox 调用之前出现（同一条 if 语句里）
    guard_at = body.index("WizardSilent")
    first_call = re.search(r"\bMsgBox\s*\(", body).start()
    assert guard_at < first_call, "WizardSilent 守卫应出现在 MsgBox 调用之前"


def test_installer_context_menu_uses_app_path_not_hardcoded():
    """右键菜单必须写 {app}\\gwtool.exe，不能是构建机上的绝对路径。"""
    text = _read("scripts", "setup_windows.iss")
    assert "[Registry]" in text, "安装包应自带右键菜单注册表项"
    reg = text.split("[Registry]", 1)[1].split("[", 1)[0]
    assert "{app}\\gwtool.exe" in reg, "右键菜单命令未使用 {app} 安装路径"
    assert "C:\\gwtool" not in reg


# ------------------------------------------------------------ PyInstaller
def test_spec_bundles_qtsvg_for_svg_icons():
    """ui/icons.py 用 QImage.fromData(..., "SVG")，需要 imageformats/qsvg 插件。

    代码从不显式 import PySide6.QtSvg，PyInstaller 便不会收集该插件，
    结果是开发环境图标正常、装到用户机上工具栏图标全部空白且不报错。
    """
    text = _read("gwtool.spec")
    assert "PySide6.QtSvg" in text, "gwtool.spec 未把 PySide6.QtSvg 列为 hiddenimport"
    hidden = text.split("hiddenimports", 1)[1].split("]", 1)[0]
    assert "PySide6.QtSvg" in hidden, "PySide6.QtSvg 不在 hiddenimports 列表里"


def test_spec_bundles_seed_db():
    """离线词典与纠错库必须随包分发，否则首启动没有纠错能力。"""
    text = _read("gwtool.spec")
    assert "gwtool/resources/data/seed.db" in text


# ------------------------------------------------------------ 硬编码路径
def test_no_hardcoded_build_machine_paths():
    """全库不得出现构建机的绝对路径 C:\\gwtool——用户机上它不存在。"""
    skip_dirs = {".git", "__pycache__", ".venv", "build", "dist", ".pytest_cache",
                 "node_modules", ".qoder"}
    skip_suffix = {".pyc", ".pdf", ".pptx", ".db", ".zip", ".exe", ".png", ".ico"}
    hits = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() in skip_suffix:
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.name == Path(__file__).name:      # 本测试自身会提到该字符串
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(content.splitlines(), 1):
            if re.search(r"[Cc]:\\+gwtool\b", line):
                hits.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:90]}")
    assert not hits, "发现硬编码构建机路径：\n" + "\n".join(hits)


def test_build_script_does_not_assume_venv_exists():
    """build_windows.bat 必须在缺少 .venv 时回退到 PATH 上的 python。

    原实现直接调 .venv\\Scripts\\python，没有 venv 时命令失败却仍打印"完成"。
    """
    text = _read("scripts", "build_windows.bat")
    assert "if not exist" in text, "缺少 .venv 存在性判断"
    assert "errorlevel 1" in text, "缺少失败退出判断，打包失败会被当成成功"
