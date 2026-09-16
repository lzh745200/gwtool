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
    """[Code] 段里**若**出现 MsgBox，则必须由 WizardSilent 守卫。

    普通 MsgBox 不受 /SUPPRESSMSGBOXES 影响，静默安装时会弹模态框并永久阻塞
    （实测 /VERYSILENT 安装挂住 5 分钟以上毫无进展），批量部署直接挂死。

    v1.5.3 起安装包内置 OCR，过时的引导提示弹窗已从 [Code] 段整体删除：
    「无 [Code] 段」与「有 [Code] 段但段内无 MsgBox」两种形态都不存在静默
    安装挂死风险，视为通过；但只要段内出现未守卫的 MsgBox 仍必须报错。
    """
    text = _read("scripts", "setup_windows.iss")
    parts = text.split("[Code]", 1)
    if len(parts) != 2:
        return  # 无 [Code] 段 → 无 MsgBox → 无静默安装挂死风险
    # 先剥离 Pascal 注释，否则注释里提到的 MsgBox 字样会干扰下面的顺序判断
    body = re.sub(r"\{.*?\}", "", parts[1], flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)

    calls = [ln.strip() for ln in body.splitlines() if re.search(r"\bMsgBox\s*\(", ln)]
    if not calls:
        return  # [Code] 段存在但没有 MsgBox → 同样无风险
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


def test_spec_explicitly_collects_optional_inference_stack():
    """spec 必须**显式**收集 onnxruntime/tokenizers/numpy，不能靠静态分析侥幸。

    L4/L5 的 import 全部写在函数体内（延迟导入），PyInstaller 的静态分析
    "通常"能扫到，但这不是契约：PyInstaller 只自带 hook-numpy.py，
    **没有 onnxruntime / tokenizers 的 hook**（_pyinstaller_hooks_contrib 未装），
    原生扩展的依赖链无人兜底；且 Linux/ARM64 收集 .so 依赖走的是另一条路径。

    一旦漏收，得到的是最坏的一类缺陷：依赖装了、CI 断言"装成了"、产物里却没有
    → 用户机上 L4/L5 永久不可用且不报错。ARM64 由于是首次获得这些依赖，
    最容易踩到。故用测试守住"显式收集"这一契约。
    """
    text = _read("gwtool.spec")
    assert "collect_submodules" in text, "spec 未递归收集推理栈子模块"
    for mod in ("onnxruntime", "tokenizers", "numpy"):
        assert mod in text, f"spec 未显式收集 {mod}"
    # 必须容错：主包默认不带这些依赖，包缺席时打包**不能中断**
    assert "__import__" in text or "find_spec" in text, (
        "spec 未做存在性探测：依赖缺席时 PyInstaller 会因 hidden import 找不到而中断整条打包")
    assert "except Exception" in text, "spec 缺少容错分支"


def test_spec_does_not_hardcode_hiddenimports_for_optional_deps():
    """不得用固定列表硬写可选依赖的模块名。

    `hiddenimports += ['onnxruntime']` 这种写法在包缺席时会让 PyInstaller
    直接报 "Hidden import not found" 并终止打包——而"主包不带推理栈"
    恰是默认且被允许的情形。
    """
    text = _read("gwtool.spec")
    for mod in ("onnxruntime", "tokenizers"):
        assert f"'{mod}'" not in text or "append" in text, (
            f"{mod} 疑似被硬写进列表，缺席时会中断打包")


# ------------------------------------------------------------ 硬编码路径
def test_no_hardcoded_build_machine_paths():
    """全库不得出现构建机的绝对路径 C:\\gwtool——用户机上它不存在。

    .workbuddy 是 IDE 工作目录（memory/skills/agent 记录），非产品代码，
    笔记里写本机解释器路径属正常使用，扫描应跳过。
    """
    skip_dirs = {".git", "__pycache__", "build", "dist", ".pytest_cache",
                 "node_modules", ".qoder", ".workbuddy"}
    # 虚拟环境目录按前缀匹配（.venv / .venv64 / .venv_win …），里面必然有本机路径
    skip_prefixes = (".venv",)
    skip_suffix = {".pyc", ".pdf", ".pptx", ".db", ".zip", ".exe", ".png", ".ico"}
    hits = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() in skip_suffix:
            continue
        if any(part in skip_dirs or part.startswith(skip_prefixes)
               for part in path.parts):
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


def test_cli_scripts_force_utf8_stdout():
    """输出中文的脚本必须自行把 stdout 重配为 UTF-8。

    英文区域设置的 Windows 与 GitHub Windows runner 控制台是 charmap 编码，
    print 中文会抛 UnicodeEncodeError，让自检脚本在跑出结论之前就崩掉
    （CI 实测：build-windows 在「端到端自检」步骤因此失败）。
    """
    for script in ("e2e_check.py", "smoke_dist.py"):
        text = _read("scripts", script)
        assert "reconfigure" in text, f"{script} 未重配 stdout 编码"
        assert 'encoding="utf-8"' in text, f"{script} 未指定 UTF-8 输出编码"
        # 重配必须在模块顶层完成，早于 main 里的任何中文输出
        assert text.index("reconfigure") < text.index("def main"), (
            f"{script} 的编码重配晚于 main，输出中文时仍会崩")


def test_no_non_ascii_in_strftime_format():
    """strftime 的格式串里绝不能有中文。

    Windows 上 strftime 会把格式串交给 C 运行时按 locale 编码处理，英文区域
    设置的机器（含 GitHub windows runner）遇到「年月日」直接抛
    UnicodeEncodeError: 'locale' codec can't encode character。
    中文区域设置的开发机上完全看不出来，属典型的"只在我机器上好的"缺陷。
    正确写法是格式串只用 ASCII 占位符，中文单位在外面用 f-string 拼。
    """
    offenders = []
    # strftime("...") 直接调用，以及 f-string 里的 :%Y... 日期格式符
    patterns = [re.compile(r"strftime\(\s*[rR]?([\"'])(.*?)\1", re.S),
                re.compile(r":(%[-\dA-Za-z%]+)\}")]
    for base in ("gwtool", "scripts"):
        for path in (ROOT / base).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for n, line in enumerate(text.splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                for rx in patterns:
                    for m in rx.finditer(line):
                        fmt = m.group(2) if rx.groups >= 2 else m.group(1)
                        if any(ord(ch) > 127 for ch in fmt):
                            offenders.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:80]}")
    assert not offenders, (
        "以下 strftime/日期格式串含非 ASCII 字符，英文区域设置 Windows 上会崩：\n"
        + "\n".join(offenders))


# ------------------------------------------------------------ ARM64 完整性
def test_optional_stack_covers_aarch64_without_arch_fork():
    """可选推理栈必须让 aarch64 可用，且**不得**为架构单独分叉版本。

    背景：这一行曾被注释掉（"aarch64 轮子可用性不稳，需上机核对 glibc"），
    导致麒麟 ARM64 上 L4/L5 **永远**装不起来 —— 代码在、依赖不在 = 功能不可达，
    而 CI 当时是静默降级，谁也不会发现。

    实测核对（2026-09-15，PyPI 元数据）后确认顾虑不成立：
      · onnxruntime 1.17.3 有 cp39 aarch64 轮子，标签 manylinux_2_27/2_28，
        glibc 基线 2.27/2.28 **低于**本项目 ARM64 底线 2.31；
      · tokenizers 0.15.2 有 cp39 aarch64（manylinux_2_17）。
    故两行都必须处于**生效状态**（未注释）。

    同时禁止加 `platform_machine == 'aarch64'` 的独立行：那会让 aarch64 与
    x86_64 落在不同版本上，制造"同源码不同行为"的分叉 —— 正是 v1.2.1 事故的成因。
    """
    active = []
    for ln in _read("requirements-optional.txt").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        active.append(s)

    assert any(s.startswith("onnxruntime==") for s in active), (
        "onnxruntime 无生效行：aarch64 上将无法安装 L4 运行时")
    assert any(s.startswith("tokenizers==") for s in active), (
        "tokenizers 无生效行：aarch64 上将无法安装 L5 运行时")

    # 禁止按架构分叉：环境标记里不能出现 platform_machine
    for s in active:
        assert "platform_machine" not in s, (
            f"不得按架构分叉版本（会与 x86_64 产生行为差异）：{s}")

    # Python < 3.11 那一档同时覆盖 aarch64（CI 容器是 3.9），必须存在
    older = [s for s in active if "python_version < '3.11'" in s]
    assert len(older) >= 2, (
        "缺少 python_version < '3.11' 档（CI 容器 Python 3.9 与 aarch64 走这一档）")


def test_inference_stack_checker_exists_and_asserts():
    """必须有可复用的推理栈自检脚本，且默认是**失败即非零退出**。

    这是"麒麟 ARM64 也要完整实现全部功能"的守护点：把"装没装上"从人肉观察
    变成 CI 断言。若哪天有人把它改回静默告警，这条测试会红。
    """
    text = _read("scripts", "check_inference_stack.py")
    for mod in ("onnxruntime", "tokenizers", "numpy"):
        assert mod in text, f"自检脚本未覆盖 {mod}"
    assert "return 1" in text, "缺失时未返回非零退出码（等于静默放行）"
    assert "--optional" in text, "缺少显式降级开关（降级必须是主动选择）"
    assert "def main" in text


def test_ci_asserts_optional_stack_on_both_platforms():
    """CI 两个平台都必须**断言**推理栈装成，不能静默降级。

    另需保证 Windows job 也安装了可选栈 —— 曾经只有 Linux job 装，
    于是 Windows 安装包同样"代码在、依赖不在"，两边功能面不一致。
    """
    text = _read(".github", "workflows", "build.yml")
    assert text.count("check_inference_stack.py") >= 2, (
        "Windows 与 ARM64 两个 job 都应调用推理栈自检脚本")
    assert "requirements-optional.txt" in text, "CI 未安装可选推理栈"
    # 旧的静默降级写法不得复活
    assert "|| echo \"::warning::requirements-optional.txt" not in text, (
        "静默降级写法已复活：装不上也不报错，功能面会被悄悄削弱")


def test_smoke_dist_checks_runtime_capabilities():
    """打包冒烟必须让**产物自己**报告能力面（而非在构建机上 import）。"""
    text = _read("scripts", "smoke_dist.py")
    assert "--runtime-report" in text, "冒烟脚本未调用产物的能力报告"
    assert "BUILTIN_STACK" in text, "冒烟脚本未核对推理栈模块"


def test_runtime_report_flag_wired_in_entrypoint():
    """main.py 必须支持 --runtime-report 并走"不启动 GUI"的早退路径。"""
    text = _read("main.py")
    assert "--runtime-report" in text, "入口未支持能力报告参数"
    assert "_runtime_report()" in text, "入口未调用能力报告实现"
    # 必须早于 GUI 启动：在 `from gwtool.app import run` 之前 sys.exit
    assert text.index("_runtime_report()") < text.index("from gwtool.app import run"), (
        "能力报告晚于 GUI 导入，CI 上会因无显示环境卡住")


def test_shell_scripts_keep_aarch64_paths():
    """麒麟离线 wheel 与构建脚本必须包含可选推理栈，否则离线机永远无法启用 L4/L5。"""
    wheels = _read("scripts", "kylin_offline_wheels.sh")
    assert "requirements-optional.txt" in wheels, (
        "离线 wheel 未含可选推理栈：离线麒麟机上 L4/L5 永久不可达")
    build = _read("scripts", "build_kylin_arm64.sh")
    assert "requirements-optional.txt" in build, (
        "麒麟构建脚本未安装可选推理栈")


def test_launcher_does_not_rely_on_echo_interpreting_newlines():
    """启动器提示里的多行文本不得写成字面量 \\n 交给 echo。

    bash 内置 echo 默认**不解释** \\n（那是 `echo -e` 的行为），会把 "\\n"
    原样打成反斜杠+n。而这些提示恰好都在"启动失败"路径上（架构不匹配、缺库），
    用户看到的将是一行乱码般的单行文本 —— 最需要可读性的时刻反而最不可读。
    实测确认过该行为（od -c 显示输出里是反斜杠 + n 两个字符）。

    正确做法：多行消息写真实换行；需要转义时用 printf。
    """
    text = _read("scripts", "gwtool.sh")

    # say/popup 的消息构造里不应出现字面量 \n
    offenders = []
    for n, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # echo 后跟含 \n 的字符串，或 MSG= / popup 参数里含 \n
        if r"\n" in line and ("echo" in line or "MSG=" in line
                              or "popup" in line):
            offenders.append(f"{n}: {line.strip()[:80]}")
    assert not offenders, (
        "启动器里有把字面量 \\n 交给 echo/popup 的写法（bash 的 echo 不解释 \\n，"
        "失败提示会显示成一行乱码文本）：\n" + "\n".join(offenders))

    # say 必须用 printf（能正确输出多行与格式），而不是 echo
    say_line = next((ln for ln in text.splitlines()
                     if ln.strip().startswith("say()")), "")
    assert "printf" in say_line, (
        f"say() 未使用 printf，带 \\n 的提示会原样打出反斜杠+n：{say_line.strip()}")


def test_linux_toolchain_orders_archive_before_https_snapshot():
    """顺序护栏：必须**先**用 HTTP 归档源装 ca-certificates，**再**切 HTTPS 快照源。

    背景（2026-09-16 的真实 CI 失败，exit 100）：
      · Debian 11 已 EOL，bullseye-security 池中的 .deb 被上游撤下（openssl /
        ca-certificates 等随机 404）；
      · `debian:11` 官方镜像**不含 ca-certificates**，而 snapshot.debian.org
        是 HTTPS —— 若一上来就切过去，会卡在"要证书才能连源、要连源才能装证书"
        的鸡生蛋问题，整个 build-linux job 在第一步就失败。
    正确顺序：先用 archive.debian.org（HTTP、无需证书）装上 ca-certificates，
    再切 snapshot（固定时间点，保证可复现）。调换顺序会让麒麟包彻底打不出来。
    """
    src = (ROOT / ".github" / "workflows" / "build.yml").read_text(
        encoding="utf-8")
    # 只看**实际命令行**：注释里同样会提到这两个域名，若一并参与 index 比较，
    # 结果会被注释的先后位置带偏（本测试首版即因此误报）。
    body = "\n".join(ln for ln in src.splitlines()
                     if not ln.strip().startswith("#"))
    assert "archive.debian.org" in body, "缺少 HTTP 归档源兜底"
    assert "snapshot.debian.org" in body, "缺少可复现的时间戳快照源"
    assert body.index("archive.debian.org") < body.index("snapshot.debian.org"), (
        "archive 源必须出现在 snapshot 源之前 —— 顺序颠倒会使第一次 "
        "apt-get install ca-certificates 走在线源并 404")
    # 装证书那一句不能落在切到 HTTPS 源之后
    i_ca = body.index("--no-install-recommends ca-certificates")
    assert i_ca < body.index("snapshot.debian.org"), (
        "ca-certificates 必须在切到 HTTPS 快照源之前装好")
