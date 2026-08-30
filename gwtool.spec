# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包唯一配置（Windows x64 与 Linux/麒麟 ARM64 共用）。

构建：pyinstaller --noconfirm --clean gwtool.spec
此前构建参数散落在 build_windows.bat 与 CI 两个 job 中且已漂移，现收敛于此文件。
"""
import sys

from PyInstaller.utils.hooks import collect_data_files

datas = [('gwtool/resources/data/seed.db', 'gwtool/resources/data')]
datas += collect_data_files('opencc')

hiddenimports = []
if sys.platform.startswith('win'):
    hiddenimports.append('win32timezone')   # pywin32 动态导入

excludes = [
    'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebChannel',
    'PySide6.QtQuick3D', 'PySide6.QtQuick', 'PySide6.QtQml', 'PySide6.QtCharts',
    'PySide6.QtMultimedia', 'PySide6.QtSql', 'PySide6.QtNetworkAuth',
    'PySide6.QtPositioning', 'PySide6.QtSensors', 'PySide6.QtSerialPort',
    'PySide6.QtTest', 'PySide6.QtDesigner', 'PySide6.Qt3DCore',
    'PySide6.QtDataVisualization',
    'tkinter', 'matplotlib', 'numpy', 'pandas',
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
