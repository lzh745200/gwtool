#!/bin/bash
# ============================================================
#  为麒麟 ARM64 预下载离线 wheel 包（给完全离线的目标机用）。
#
#  为什么必须在 **Linux + Python 3.9** 上执行：
#    1) requirements.txt 里有环境标记（`sys_platform == 'win32'`、
#       `python_version < '3.10'`）。pip 按**运行主机**求值这些标记，
#       `--python-version` 管不到它们 —— 在 Windows 上跑会选中
#       PySide6==6.11.2 / pywin32==312 这些 Windows 分支，再去下
#       linux_aarch64 的 wheel 必然 "No matching distribution"。
#    2) `--platform linux_aarch64` 匹配不到 manylinux 轮子（PyPI 上的
#       aarch64 轮子标签是 manylinux_2_31_aarch64 / manylinux2014_aarch64），
#       而 PySide6 的 aarch64 轮子只此一种，写死 --platform 等于必然失败。
#    3) jieba==0.42.1 在 PyPI 只有 sdist，`--only-binary=:all:` 会直接报错。
#  因此本脚本不再做"交叉下载"，而是在与麒麟 CI 同代的 Linux + Python 3.9
#  环境里直接下载；目标机与构建环境必须同为 Python 3.9 / linux-aarch64。
#
#  用法（在一台有网络的 Linux ARM64 机器或 x86 机器上的 arm64 容器内）：
#      bash scripts/kylin_offline_wheels.sh
#  产物：wheels_aarch64/ —— 随源码拷到麒麟目标机，
#        由 build_kylin_arm64.sh 自动检测并用 --no-index 离线安装。
#
#  注意：在 x86_64 Linux 上直接跑会下载 x86_64 轮子（同样"成功"但不可用），
#        因此脚本会校验 uname -m，非 aarch64 直接拒绝。
# ============================================================
set -e
cd "$(dirname "$0")/.."

# ---- 前置校验：架构 + Python 版本，二者不符就当场失败（别产出一堆没用的轮子）
ARCH="$(uname -m)"
if [ "$ARCH" != "aarch64" ] && [ "$ARCH" != "arm64" ]; then
    cat >&2 <<EOF
错误：当前架构是 $ARCH，不是 ARM64。
      pip download 会下成 $ARCH 的轮子，拷到麒麟上一装就报"无法执行二进制文件"。
      请在 ARM64 机器（或 arm64 容器）上执行本脚本：
          docker run --rm -v "\$PWD":/w -w /w arm64v8/debian:11 bash scripts/kylin_offline_wheels.sh
EOF
    exit 1
fi

PY="${PYTHON:-python3}"
"$PY" - <<'PYCHECK'
import sys
if sys.version_info[:2] != (3, 9):
    raise SystemExit(
        "错误：当前 Python 为 %d.%d，本脚本要求 3.9。\n"
        "      requirements.txt 按 Python 版本分档（PyMuPDF / markdown / "
        "chardet / pytest 在 3.9 与 3.10+ 是不同版本），\n"
        "      用别的版本下载会拿到与麒麟目标机不匹配的依赖。"
        % sys.version_info[:2])
print("环境校验通过：linux/%s + Python %d.%d"
      % (sys.platform, *sys.version_info[:2]))
PYCHECK

mkdir -p wheels_aarch64

# 不放 --platform / --python-version / --only-binary：
# 环境标记交给 pip 按本机（已校验为 linux + py3.9）求值，jieba 的 sdist 也允许下载。
"$PY" -m pip download -r requirements.txt \
  -d wheels_aarch64 \
  -i https://pypi.tuna.tsinghua.edu.cn/simple

echo
echo "完成：$(find wheels_aarch64 -maxdepth 1 -type f | wc -l) 个文件已下载到 wheels_aarch64/"
echo "下一步：把整个项目（含 wheels_aarch64/）拷到麒麟 ARM64 机器，执行"
echo "        bash scripts/build_kylin_arm64.sh"
