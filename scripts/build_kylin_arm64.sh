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

echo "[3/6] 运行测试确认环境正常..."
python -m pytest tests/ -q

echo "[4/6] PyInstaller 打包（onedir）..."
pyinstaller --noconfirm --clean \
  --name gwtool \
  --windowed \
  --add-data "gwtool/resources/data/seed.db:gwtool/resources/data" \
  --collect-data opencc \
  --exclude-module PySide6.QtWebEngineCore \
  --exclude-module PySide6.QtWebEngineWidgets \
  --exclude-module PySide6.QtWebChannel \
  --exclude-module PySide6.QtQuick3D \
  --exclude-module PySide6.QtQuick \
  --exclude-module PySide6.QtQml \
  --exclude-module PySide6.QtCharts \
  --exclude-module PySide6.QtMultimedia \
  --exclude-module PySide6.QtSql \
  --exclude-module tkinter \
  main.py

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
