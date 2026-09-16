#!/bin/bash
# ============================================================
#  公文汇编助手 —— 麒麟 V10 ARM64 打包脚本（在麒麟目标机上执行）
#  前置：Python 3.9+ 与 python3-venv 已安装
#        （sudo apt install python3 python3-venv python3-pip）
# ============================================================
set -e
cd "$(dirname "$0")/.."

echo "[1/6] 创建虚拟环境..."
python3 -m venv .venv
source .venv/bin/activate

echo "[2/6] 安装依赖（优先使用离线 wheel 目录 wheels_aarch64/）..."
if [ -d wheels_aarch64 ] && ls wheels_aarch64/*.whl >/dev/null 2>&1; then
    pip install --no-index --find-links wheels_aarch64 -r requirements.txt
else
    echo "未发现离线 wheel 目录，尝试在线安装（需要网络）："
    pip install -r requirements.txt \
        -i https://pypi.tuna.tsinghua.edu.cn/simple
fi

echo "[2.5/6] 安装可选推理栈（L4 神经精排 / L5 语法纠错）..."
# 这一层决定"增强层能否启用"，与主依赖一样按离线优先处理：
# 离线机上若没有这两组 wheel，L4/L5 **永远**开不起来——那正是"麒麟 ARM64 也要
# 完整实现全部功能"这条要求里最容易被忽略的死角（有代码、无依赖 = 功能不可达）。
# 因此：能装就装，装完当场**断言可导入**；装不上只告警不阻断（主包照常产出，
# 退化为三级流水线，语义与"未导入增强包"完全一致）。
_opt_ok=0
if [ -d wheels_aarch64 ] && ls wheels_aarch64/onnxruntime-*.whl >/dev/null 2>&1; then
    pip install --no-index --find-links wheels_aarch64 -r requirements-optional.txt && _opt_ok=1
elif pip install -r requirements-optional.txt \
        -i https://pypi.tuna.tsinghua.edu.cn/simple; then
    _opt_ok=1
fi
if [ "$_opt_ok" = "1" ]; then
    python - <<'PYOPT'
import importlib.util as u
import platform
mods = ("onnxruntime", "tokenizers", "numpy")
print("      平台：", platform.machine(), platform.python_version())
missing = [m for m in mods if u.find_spec(m) is None]
for m in mods:
    if m not in missing:
        mod = __import__(m)
        print(f"      {m:12s} {getattr(mod, '__version__', '?')}")
if missing:
    raise SystemExit("      警告：离线已装但缺模块 -> " + ", ".join(missing))
print("      L4/L5 推理栈就绪，增强层在麒麟 ARM64 上可用。")
PYOPT
else
    echo "      警告：可选推理栈安装失败 —— 主包不受影响，但本机装配的产物"
    echo "            无法启用 L4/L5（退化为三级流水线）。如需启用，请在有网"
    echo "            机器上先跑 scripts/kylin_offline_wheels.sh 补齐 wheels_aarch64/。"
fi

echo "[3/6] 运行测试确认环境正常..."
python -m pytest tests/ -q

echo "[4/6] PyInstaller 打包（参数唯一来源：gwtool.spec，双平台共用）..."
# 不要再把 --exclude-module/--add-data 抄一遍：本脚本曾手写一整套参数，
# 与 gwtool.spec 漂移后漏掉了 --hidden-import PySide6.QtSvg，打包版的
# 工具栏图标会全部空白（icons.py 用 QImage.fromData(..., "SVG") 画图标）。
# 统一入口后，spec 里的 hiddenimports/datas/excludes 只有一处维护。
pyinstaller --noconfirm --clean gwtool.spec

echo "[4.5/6] 集成 Tesseract OCR（已安装时）..."
if command -v tesseract >/dev/null 2>&1; then
    mkdir -p dist/gwtool/ocr/bin dist/gwtool/ocr/lib dist/gwtool/ocr/tessdata
    cp /usr/bin/tesseract dist/gwtool/ocr/bin/
    ldd /usr/bin/tesseract | awk '/=> \//{print $3}' | while read -r lib; do
        case "$lib" in
            */libc.so*|*/libm.so*|*/libpthread*|*/libdl*|*/librt*|*/ld-linux*) ;;
            *) cp -L "$lib" dist/gwtool/ocr/lib/ ;;
        esac
    done
    cp -r /usr/share/tesseract-ocr/*/tessdata/. dist/gwtool/ocr/tessdata/ 2>/dev/null || true
    if command -v patchelf >/dev/null 2>&1; then
        patchelf --set-rpath '$ORIGIN/../lib' dist/gwtool/ocr/bin/tesseract 2>/dev/null || true
    fi
    echo "      已集成 Tesseract + 中文包。"
else
    echo "      未检测到 tesseract，跳过 OCR 集成。"
fi

echo "[5/6] 放入启动器并自检运行库..."
cp scripts/gwtool.sh dist/gwtool/gwtool.sh
chmod +x dist/gwtool/gwtool dist/gwtool/gwtool.sh
MISSING=""
for target in dist/gwtool/gwtool $(find dist/gwtool -name 'libqxcb.so' 2>/dev/null); do
    MISSING="$MISSING$(ldd "$target" 2>/dev/null | grep 'not found' || true)"
done
if [ -n "$MISSING" ]; then
    echo "警告：本机缺少以下运行库（成品在目标机上也会缺，需安装）："
    echo "$MISSING"
    echo "可执行：sudo apt-get install -y libgl1 libegl1 libglib2.0-0 libxkbcommon0 \\"
    echo "  libxkbcommon-x11-0 libfontconfig1 libdbus-1-3 libxcb-cursor0 \\"
    echo "  libxcb-xinerama0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \\"
    echo "  libxcb-render-util0 libxcb-shape0 libxcb-randr0 libxcb-xfixes0 libxcb-xkb1"
fi

echo "[6/6] 打包 .run 自解压安装脚本（makeself，若已安装）..."
if command -v makeself >/dev/null 2>&1; then
    cp scripts/install_kylin_run.sh dist/gwtool/gwtool-install.sh
    sed -i "s/__TARGET_ARCH__/$(uname -m | sed 's/amd64/x86_64/')/" dist/gwtool/gwtool-install.sh
    chmod +x dist/gwtool/gwtool-install.sh
    makeself dist/gwtool gwtool_kylin_$(uname -m).run \
      "公文汇编助手 安装程序" \
      ./gwtool-install.sh
    echo "安装包：$(pwd)/gwtool_kylin_$(uname -m).run"
else
    echo "未安装 makeself，跳过 .run 打包（可使用 sudo apt install makeself 后重跑，或直接分发 dist/gwtool 目录）"
fi

echo "完成！可执行文件：dist/gwtool/gwtool"
