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

# ---- 可选推理栈（L4 神经精排 / L5 语法纠错）的显式收集 ----
# 背景：core/csc_neural.py 与 core/csc_gec.py 里的 import 全部写在**函数体内**
# （延迟导入，避免无依赖时拉高启动开销）。PyInstaller 的静态分析**通常**能扫到
# 函数内 import，但这不是契约 —— 而它决定了下面的 hook 会不会被触发：
#   · onnxruntime 有官方 hook（_pyinstaller_hooks_contrib/stdhooks/
#     hook-onnxruntime.py，负责收集 capi/ 下的 provider 动态库）。
#     **但 hook 只在 PyInstaller 识别到"有人 import onnxruntime"时才生效**；
#     函数内 import 一旦漏扫，hook 根本不会被调用，provider .dll/.so 全丢。
#   · tokenizers **没有任何 hook**（hooks_contrib 里查无此项），完全依赖
#     静态分析 + 顶层收集。
#   · Linux/ARM64 上收集原生扩展走的是 ldd 路径，与 Windows 不同，历史行为
#     不能外推。
# 一旦漏收，得到的是最坏的一类缺陷：依赖装了、CI 断言"装成了"、产物里却没有
# → 用户机上 L4/L5 永久不可用且不报错。因此这里**显式声明**，把"依赖齐备时
# 产物必含推理栈"变成确定性行为（同时也就保证了 onnxruntime 的 hook 被触发）。
#
# 关键约束：本文件必须能在**未安装**这些包的环境下照常工作（主包默认不带它们，
# 麒麟 CI 也允许它们缺席）——故用 try/except 探测，装了才收集、没装就跳过。
# 不用 `hiddenimports += [...]` 硬写模块名：那样在包缺席时 PyInstaller 会
# 直接报 "Hidden import not found" 而中断整个打包。
for _mod in ('onnxruntime', 'tokenizers', 'numpy'):
    try:
        __import__(_mod)
    except Exception:                    # 未安装属正常情形（主包默认不带）
        continue
    hiddenimports.append(_mod)
    # numpy 只声明顶层即可：PyInstaller 自带 hook-numpy.py 会正确处理它，
    # 且它的 300+ 个子模块里绝大多数用不到（含 numpy.tests.*，白白增大产物，
    # 还会引入 'ascii__mypyc' 之类的噪音警告）。真正需要递归收集的是
    # onnxruntime / tokenizers —— 原生扩展藏在子包里。
    if _mod == 'numpy':
        continue
    # 递归收集子模块：onnxruntime.capi.* 里放的是真正的原生扩展，
    # 只声明顶层模块名拿不到它们。
    #
    # 排除的子包（均为**训练/文档/量化侧**，本项目的推理场景用不到）：
    #   quantization / training / transformers / tools / backend：
    #       导入即需要 onnx 训练库（本项目不装 onnx），不排除就会打印
    #       "WARNING: Failed to collect submodules for 'onnxruntime.backend'
    #        because importing ... raised: ModuleNotFoundError: No module named 'onnx'"；
    #   datasets：只是文档示例定位工具（get_example），且会把 charset_normalizer
    #       一并拉进来，产生一串 'ascii__mypyc' / 'confusion__mypyc' 之类的
    #       "Hidden import not found"（mypyc 编译残留）。
    # 这些警告都无害，但**会训练使用者忽略打包警告**——等真出问题时已被噪声麻痹。
    # on_error='ignore' 再兜一层同类噪音。ARM64 CI 上同样会刷，故一并处理。
    # 注意：filter 必须在**导入之前**生效才不产生警告，故不能写成"先收集再筛选"。
    try:
        from PyInstaller.utils.hooks import collect_submodules

        _EXCLUDE_SUB = ('quantization', 'training', 'transformers', 'tools',
                        'backend', 'datasets')
        hiddenimports += collect_submodules(
            _mod,
            filter=lambda name: not any(
                name.startswith(f'{_mod}.{p}') for p in _EXCLUDE_SUB),
            on_error='ignore',
        )
    except Exception:
        pass

# 关于"模型不随主包分发"
# ------------------------------------------------
# 上面收集的是**运行时（onnxruntime/tokenizers/numpy）**，不是**模型**。
# 两者的边界要分清：
#   · 运行时：装进产物，让 L4/L5 具备可启用条件（本文件负责）；
#   · 模型（精度增强包 .zip）：**永不随主包分发**，由用户按需离线导入。
# 主包不含模型，所以即便带了运行时，未导入增强包时 L4/L5 仍全程静默，
# 纠错行为与三级流水线完全一致（见 core/csc_neural.py 的硬约束说明）。
# 这里只保证 numpy 不被排除（见下方 excludes 注释），让"打包版装了增强包依赖
# 就能用"与"没装就静默退化为三级流水线"两种情形都自洽。
#
# 注：原注释曾写"麒麟 CI 用 Python 3.9 而 onnxruntime 新版要求 >=3.11，装不上会让
# Linux 打包 job 失败"。该理由**已被实测推翻**——onnxruntime 1.17.3 提供 cp39
# 的 aarch64 轮子（manylinux_2_27/2_28，glibc 基线低于本项目底线 2.31），
# 故 <3.11 档走得通，CI 也已改为"必须真装成"。详见 requirements-optional.txt。

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
