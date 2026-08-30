#!/bin/bash
# ============================================================
#  公文汇编助手 —— 麒麟 V10 ARM64 打包脚本（在麒麟目标机上执行）
#  前置：Python 3.9+ 与 python3-venv 已安装
#        （sudo apt install python3 python3-venv python3-pip）
# ============================================================
set -e
cd "$(dirname "$0")/.."

echo "[1/5] 创建虚拟环境..."
python3 -m venv .venv
source .venv/bin/activate

echo "[2/5] 安装依赖（优先使用离线 wheel 目录 wheels_aarch64/）..."
if [ -d wheels_aarch64 ] && ls wheels_aarch64/*.whl >/dev/null 2>&1; then
    pip install --no-index --find-links wheels_aarch64 -r requirements.txt
else
    echo "未发现离线 wheel 目录，尝试在线安装（需要网络）："
    pip install -r requirements.txt \
        -i https://pypi.tuna.tsinghua.edu.cn/simple
fi

echo "[3/5] 运行测试确认环境正常..."
python -m pytest tests/ -q

echo "[4/5] PyInstaller 打包（onedir）..."
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

echo "[5/5] 打包 .run 自解压安装脚本（makeself，若已安装）..."
if command -v makeself >/dev/null 2>&1; then
    makeself dist/gwtool gwtool_kylin_arm64.run \
      "公文汇编助手 安装程序" \
      --current sh -c 'cp -r "$1"/* "$HOME/.local/opt/gwtool/" 2>/dev/null || { mkdir -p "$HOME/.local/opt/gwtool"; cp -r "$1"/* "$HOME/.local/opt/gwtool/"; }; echo 已安装到 ~/.local/opt/gwtool'
    echo "安装包：$(pwd)/gwtool_kylin_arm64.run"
else
    echo "未安装 makeself，跳过 .run 打包（可使用 sudo apt install makeself 后重跑，或直接分发 dist/gwtool 目录）"
fi

echo "完成！可执行文件：dist/gwtool/gwtool"
