# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包唯一配置（Windows x64 与 Linux/麒麟 ARM64 共用）。

构建：pyinstaller --noconfirm --clean gwtool.spec
此前构建参数散落在 build_windows.bat 与 CI 两个 job 中且已漂移，现收敛于此文件。
"""
import sys

from PyInstaller.utils.hooks import collect_data_files

datas = [('gwtool/resources/data/seed.db', 'gwtool/resources/data')]
datas += collect_data_files('opencc')

hiddenimports = [
    # icons.py 用 QImage.fromData(..., "SVG") 画图标，需要 imageformats/qsvg 插件；
    # 代码未显式 import QtSvg，PyInstaller 不会自动收集该插件，打包后图标会全空白。
    'PySide6.QtSvg',
]
if sys.platform.startswith('win'):
    hiddenimports.append('win32timezone')   # pywin32 动态导入

# 不把 onnxruntime / tokenizers 写进 hiddenimports
# ------------------------------------------------
# 它们是「精度增强包」（L4/L5 神经纠错）的依赖，按 requirements-optional.txt
# 的设计**不进主包**：麒麟 CI 在 Debian 11 容器内用 Python 3.9 构建，
# 而 onnxruntime 新版要求 Python >= 3.11，装不上就会让 Linux 打包 job 失败。
# 主包不带模型，用户需要时离线导入独立 .zip 增强包即可。
# 这里只保证 numpy 不被排除（见下方 excludes 注释），让"打包版装了增强包依赖
# 就能用"与"没装就静默退化为三级流水线"两种情形都自洽。

excludes = [
    'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebChannel',
    'PySide6.QtQuick3D', 'PySide6.QtQuick', 'PySide6.QtQml', 'PySide6.QtCharts',
    'PySide6.QtMultimedia', 'PySide6.QtSql', 'PySide6.QtNetworkAuth',
    'PySide6.QtPositioning', 'PySide6.QtSensors', 'PySide6.QtSerialPort',
    'PySide6.QtTest', 'PySide6.QtDesigner', 'PySide6.Qt3DCore',
    'PySide6.QtDataVisualization',
    'tkinter', 'matplotlib', 'pandas',
    # 不要在这里加 'numpy'：
    #   core/csc_gec.py 与 core/csc_neural.py 都 `import numpy as np`（实测 4 处），
    #   onnxruntime 导入即依赖 numpy。老版本把 numpy 写进 excludes，本机构建出的
    #   产物就是"有 onnxruntime 却没有 numpy"——一旦有人给这种产物补上运行时，
    #   L4 会在 import 阶段直接失败（被宽 except 兜成静默无效，不报任何错）。
    # 保留 numpy 只增加约 31 MB（实测），换来增强层在依赖齐备时真的能跑。
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='gwtool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # --windowed
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='gwtool',
)
