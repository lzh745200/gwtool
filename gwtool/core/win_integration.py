# -*- coding: utf-8 -*-
"""Windows 资源管理器右键菜单集成（仅 Windows；其它平台为 no-op）。

为什么用 Python 的 winreg 而不是批处理里的 `reg add`
--------------------------------------------------
1. **编码**：cmd.exe 按**当前代码页**解码批处理文件，非 ASCII 字节在不同
   区域设置下会被误拆（老版本就是这样：脚本打印"已安装"却从未执行 reg add）。
   `reg add /d "中文"` 传参同样要过代码页，CP936 下写进注册表的是乱码。
   winreg 的值是 Python str → UTF-16，全程不经过代码页。
2. **可测**：这段逻辑能被单元测试直接调用（读回校验），不必靠"跑个 bat 看看"。
3. 批处理里再嵌套 `powershell -Command` 会引入额外进程与参数转义问题，
   在部分环境下会直接挂住（实测）。

键位与 Inno Setup 安装包 [Registry] 段保持一致（HKCU 无需管理员权限）。
"""
from __future__ import annotations

import sys

KEY_PATH = r"Software\Classes\*\shell\GongWenHuiBian"
MENU_LABEL = "用公文汇编助手导入"


def supported() -> bool:
    return sys.platform.startswith("win")


def _key_root():
    import winreg
    return winreg, winreg.HKEY_CURRENT_USER


def install(exe_path: str) -> bool:
    """登记右键菜单；成功返回 True。非 Windows 直接返回 False。

    写入后立刻读回校验：写不进去（权限、被策略拦截）必须在这里就暴露，
    而不是让用户对着"点了没反应"的菜单猜。
    """
    if not supported():
        return False
    import os
    if not os.path.isfile(exe_path):
        raise FileNotFoundError(f"程序路径不存在：{exe_path}")
    winreg, root = _key_root()
    exe = os.path.abspath(exe_path)
    with winreg.CreateKey(root, KEY_PATH) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, MENU_LABEL)
        winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, exe)
    with winreg.CreateKey(root, KEY_PATH + r"\command") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, f'"{exe}" --import "%1"')
    # 读回校验
    with winreg.OpenKey(root, KEY_PATH + r"\command") as k:
        got = winreg.QueryValueEx(k, "")[0]
    return exe in got


def uninstall() -> bool:
    """删除右键菜单；键不存在时返回 False。"""
    if not supported():
        return False
    winreg, root = _key_root()
    try:
        with winreg.OpenKey(root, KEY_PATH + r"\command") as k:
            winreg.DeleteKey(k, "")
    except FileNotFoundError:
        return False
    with winreg.OpenKey(root, KEY_PATH) as k:
        winreg.DeleteKey(k, "")
    return True


def is_installed() -> bool:
    if not supported():
        return False
    winreg, root = _key_root()
    try:
        with winreg.OpenKey(root, KEY_PATH + r"\command") as k:
            return bool(winreg.QueryValueEx(k, "")[0])
    except FileNotFoundError:
        return False
