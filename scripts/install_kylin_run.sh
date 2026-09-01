#!/usr/bin/env bash
# ============================================================
#  公文汇编助手 .run 自解压安装负载（由 makeself 调用，运行于解压目录）
#  打包时 __TARGET_ARCH__ 会被替换为 arm64 / x86_64。
#  步骤：架构校验 → 安装到 ~/.local/opt/gwtool → 写桌面入口。
# ============================================================
set -e
ARCH="$(uname -m)"
[ "$ARCH" = "amd64" ] && ARCH="x86_64"

if [ "$ARCH" != "__TARGET_ARCH__" ]; then
  echo "=============================================="
  echo " 错误：本安装包为 __TARGET_ARCH__ 版本，当前系统为 $ARCH。"
  echo " 请到发布页下载与系统架构一致的安装包"
  echo " （终端执行 uname -m 可查看本机架构）。"
  echo "=============================================="
  exit 1
fi

SRC="$(pwd)"
DEST="$HOME/.local/opt/gwtool"
echo "安装 公文汇编助手 -> $DEST ..."
mkdir -p "$DEST"
# 覆盖安装：先清掉旧版本主程序与库目录，避免新旧文件混杂
rm -rf "$DEST"/gwtool "$DEST"/gwtool.sh "$DEST"/gwtool-install.sh "$DEST"/_internal "$DEST"/lib 2>/dev/null || true
find "$SRC" -mindepth 1 -maxdepth 1 ! -name 'gwtool-install.sh' \
  -exec cp -r {} "$DEST/" \;
chmod +x "$DEST/gwtool" "$DEST/gwtool.sh" 2>/dev/null || true

# 桌面入口（用户级，无需 root）
APPDIR="$HOME/.local/share/applications"
mkdir -p "$APPDIR"
cat > "$APPDIR/gwtool.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=公文汇编助手
Comment=单机离线版智能公文汇编与写作辅助工具
Exec=$DEST/gwtool.sh
Path=$DEST
Icon=applications-office
Terminal=false
Categories=Office;Viewer;
MimeType=application/vnd.openxmlformats-officedocument.wordprocessingml.document;application/pdf;text/plain;application/rtf;text/html;text/markdown;
EOF
command -v update-desktop-database >/dev/null 2>&1 && \
  update-desktop-database "$APPDIR" 2>/dev/null || true

echo "安装完成：$DEST"
echo "  - 开始菜单：公文汇编助手"
echo "  - 命令行  ：$DEST/gwtool.sh"
echo "若启动报缺少运行库，诊断日志为 ~/gwtool_启动诊断.log"
