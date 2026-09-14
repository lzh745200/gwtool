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
    # L4 精度增强包（可选）：requirements-optional.txt 装了才真正生效。
    # 未安装时 PyInstaller 只记为 missing（warning），不会让构建失败 ——
    # 这正是我们要的语义："有模型依赖就把 L4 带上，没有就静默退化为三级流水线"。
    # 注意它们都依赖 numpy，故下方 excludes 里绝不能再排除 numpy。
    'onnxruntime',
    'tokenizers',
]
if sys.platform.startswith('win'):
    hiddenimports.append('win32timezone')   # pywin32 动态导入

excludes = [
    'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebChannel',
    'PySide6.QtQuick3D', 'PySide6.QtQuick', 'PySide6.QtQml', 'PySide6.QtCharts',
    'PySide6.QtMultimedia', 'PySide6.QtSql', 'PySide6.QtNetworkAuth',
    'PySide6.QtPositioning', 'PySide6.QtSensors', 'PySide6.QtSerialPort',
    'PySide6.QtTest', 'PySide6.QtDesigner', 'PySide6.Qt3DCore',
    'PySide6.QtDataVisualization',
    'tkinter', 'matplotlib', 'pandas',
    # 不要在这里加 'numpy'：
    #   core/csc_gec.py 与 core/csc_neural.py 都 `import numpy as np`，
    #   onnxruntime 导入即依赖 numpy。老版本排除了 numpy，导致本机构建出的
    #   产物"有 onnxruntime 却没有 numpy"——打包版 L4 增强包导入后静默无效
    #   （被宽 except 兜住，不报任何错），而 CI 构建干脆不装 onnxruntime，
    #   于是"同一份 spec、不同机器产出不同能力"。
    # 若将来决定打包版不支持 L4，请反过来把 onnxruntime/tokenizers 显式写进
    # 本列表，并同步修改 README 的功能表——两者必须一致。
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
