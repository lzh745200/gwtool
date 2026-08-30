#!/bin/bash
# ============================================================
#  在【有网络的 x86 机器】上为麒麟 ARM64 预下载离线 wheel 包。
#  说明：pip download 支持用 --platform 交叉下载 ARM64 wheel。
#  产物：wheels_aarch64/ 目录，随源码拷到麒麟目标机，
#        由 build_kylin_arm64.sh 使用 --no-index 离线安装。
# ============================================================
set -e
cd "$(dirname "$0")/.."

mkdir -p wheels_aarch64
pip download -r requirements.txt \
  --platform linux_aarch64 \
  --only-binary=:all: \
  --python-version 3.9 \
  --implementation cp \
  -d wheels_aarch64 \
  -i https://pypi.tuna.tsinghua.edu.cn/simple

echo "完成：$(ls wheels_aarch64 | wc -l) 个 wheel 已下载到 wheels_aarch64/"
echo "注意：若麒麟上的 Python 版本不是 3.9，请同步修改 --python-version。"
