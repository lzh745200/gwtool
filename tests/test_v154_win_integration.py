# -*- coding: utf-8 -*-
"""v1.5.4：右键菜单集成 + 批处理脚本健壮性回归。

背景（都来自真实复现）：
1. `install_context_menu.bat` 老版本是 UTF-8 中文 + LF 行尾，cmd.exe 在 CP936 下
   按字节解码会整段拆错行 —— 脚本打印「已安装」却**从未执行 reg add**（静默失败）。
2. 补 `chcp 65001` 也不是解药：实测在 UTF-8 代码页下 cmd.exe 会误解析
   `for /d %%D in (...) do set "TS_DIR=%%D"`，导致 build_windows.bat 的
   Tesseract 集成整段失效（打印成功但没拷贝任何文件）。
3. `reg add /d "中文"` 本身要过控制台代码页，CP936 下写进注册表的是乱码，
   且结尾引号会被吞掉、`/f` 被并进 `/d` 的值里。
结论：批处理文件保持 **纯 ASCII**，中文与注册表操作交给 Python（winreg）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def _bytes(p: Path) -> bytes:
    return p.read_bytes()


# ============================================================ 批处理文件卫生
class TestBatchFileHygiene:
    BAT_FILES = ["build_windows.bat", "install_context_menu.bat",
                 "uninstall_context_menu.bat"]

    @pytest.mark.parametrize("name", BAT_FILES)
    def test_is_pure_ascii(self, name):
        """批处理必须全 ASCII：非 ASCII 字节在 CP936 下会被误拆。"""
        raw = _bytes(SCRIPTS / name)
        bad = [(i, b) for i, b in enumerate(raw) if b > 127]
        assert not bad, (
            f"{name} 含 {len(bad)} 个非 ASCII 字节（首个在偏移 {bad[0][0]}）："
            "cmd.exe 按当前代码页解码批处理，中文注释也会成为解析隐患。"
            "中文说明请写进 README.md。")

    @pytest.mark.parametrize("name", BAT_FILES)
    def test_is_crlf(self, name):
        """批处理必须 CRLF（见 .gitattributes）。"""
        raw = _bytes(SCRIPTS / name)
        cr, lf = raw.count(13), raw.count(10)
        assert cr == lf and lf > 0, f"{name} 不是 CRLF（CR={cr} LF={lf}）"

    @pytest.mark.parametrize("name", BAT_FILES)
    def test_no_chcp_hack(self, name):
        """禁止用 chcp 65001「解决」编码问题：实测会破坏 for/if 解析。"""
        text = _bytes(SCRIPTS / name).decode("ascii")
        for line in text.splitlines():
            stripped = line.strip().lower()
            if stripped.startswith("rem"):
                continue
            assert "chcp" not in stripped, \
                f"{name} 使用了 chcp（实测会让 cmd 误解析 for/if 块）"

    def test_build_bat_checks_errorlevel_for_zip(self):
        """便携版 zip 生成必须检查 errorlevel（老版本失败仍打印 Done.）。"""
        text = _bytes(SCRIPTS / "build_windows.bat").decode("ascii")
        idx = text.find("make_archive")
        assert idx > 0
        tail = text[idx:]
        assert "if errorlevel 1" in tail, "make_archive 之后必须检查 errorlevel"

    def test_build_bat_does_not_use_cjk_zip_name(self):
        """便携版 zip 名必须是 ASCII（中文名依赖控制台代码页）。"""
        text = _bytes(SCRIPTS / "build_windows.bat").decode("ascii")
        assert "gwtool_portable" in text


# ============================================================ 右键菜单集成
win_only = pytest.mark.skipif(not sys.platform.startswith("win"),
                              reason="右键菜单集成仅 Windows")


@win_only
class TestContextMenuIntegration:
    """直接驱动 win_integration（含真实注册表往返），并在结束时清干净。"""

    @pytest.fixture(autouse=True)
    def _cleanup(self):
        from gwtool.core import win_integration
        win_integration.uninstall()
        yield
        win_integration.uninstall()

    def test_install_writes_unicode_label_and_command(self, tmp_path):
        from gwtool.core import win_integration
        import winreg

        exe = tmp_path / "gwtool.exe"
        exe.write_bytes(b"MZ")                    # 只要是个文件即可
        assert win_integration.install(str(exe)) is True

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            win_integration.KEY_PATH) as k:
            label = winreg.QueryValueEx(k, "")[0]
            icon = winreg.QueryValueEx(k, "Icon")[0]
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            win_integration.KEY_PATH + r"\command") as k:
            cmd = winreg.QueryValueEx(k, "")[0]

        assert label == "用公文汇编助手导入", "中文必须原样写入（不是乱码）"
        assert icon == str(exe)
        assert cmd == f'"{exe}" --import "%1"', f"命令串被改写：{cmd!r}"

    def test_install_rejects_missing_exe(self, tmp_path):
        from gwtool.core import win_integration
        with pytest.raises(FileNotFoundError):
            win_integration.install(str(tmp_path / "不存在.exe"))

    def test_is_installed_and_uninstall(self, tmp_path):
        from gwtool.core import win_integration
        exe = tmp_path / "gwtool.exe"
        exe.write_bytes(b"MZ")
        assert win_integration.is_installed() is False
        win_integration.install(str(exe))
        assert win_integration.is_installed() is True
        assert win_integration.uninstall() is True
        assert win_integration.is_installed() is False
        # 再卸载一次：键已不存在，返回 False 而不是抛异常
        assert win_integration.uninstall() is False

    def test_reinstall_is_idempotent(self, tmp_path):
        """重复安装不应报错（CreateKey 是幂等的）。"""
        from gwtool.core import win_integration
        exe = tmp_path / "gwtool.exe"
        exe.write_bytes(b"MZ")
        assert win_integration.install(str(exe)) is True
        assert win_integration.install(str(exe)) is True


class TestMainCli:
    def test_cli_uninstall_without_install_is_graceful(self):
        """未安装时 --uninstall-context-menu 应正常退出（不抛异常）。

        编码必须显式钉死：子进程的 stdout 编码由**子进程自己的** locale 决定
        （Windows 上默认是 ANSI 代码页，CI 的 runner 是 cp1252，本地是 cp936），
        而 text=True 用**父进程**的 locale 去解码 —— 两边不一致就是乱码
        （CI 实测拿到 'æœªå‘çŽ°å·²å®‰è£…...'）。所以两边都钉成 UTF-8。
        """
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        r = subprocess.run([sys.executable, str(ROOT / "main.py"),
                            "--uninstall-context-menu"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60, cwd=str(ROOT),
                           env=env, stdin=subprocess.DEVNULL)
        assert r.returncode == 0, f"rc={r.returncode} {(r.stderr or '')[:200]}"
        assert "右键菜单" in (r.stdout or ""), f"输出编码不符：{r.stdout!r}"

    def test_main_parses_context_menu_flags(self):
        """--install-context-menu / --uninstall-context-menu 必须被解析到。"""
        text = (ROOT / "main.py").read_text(encoding="utf-8")
        assert "--install-context-menu" in text
        assert "--uninstall-context-menu" in text
        assert "win_integration" in text
