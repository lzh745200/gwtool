#!/usr/bin/env bash
# ============================================================
#  公文汇编助手 启动器（Linux/麒麟）
#  职责：架构自检 → 系统运行库预检（缺库时给出可操作的修复命令）
#        → 启动主程序 → 失败时写诊断日志并弹窗提示。
#  桌面双击场景看不到终端输出，因此提示同时写入日志文件并尝试弹窗。
# ============================================================
set -u

DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
EXE="$DIR/gwtool"
LOG="$HOME/gwtool_启动诊断.log"

say() { printf '%s\n' "$*"; printf '%s\n' "$*" >> "$LOG" 2>/dev/null || true; }

# ---- 会话环境落盘：闪退时这是唯一能还原现场的信息 ----
{
  printf '\n[%s] 启动\n' "$(date '+%F %T')"
  printf 'arch=%s kernel=%s\n' "$(uname -m)" "$(uname -r)"
  command -v ldd >/dev/null 2>&1 && ldd --version 2>/dev/null | head -1
  printf 'session=%s desktop=%s\n' "${XDG_SESSION_TYPE:-}" "${XDG_CURRENT_DESKTOP:-}"
  printf 'XMODIFIERS=%s GTK_IM_MODULE=%s QT_IM_MODULE=%s\n' \
    "${XMODIFIERS:-}" "${GTK_IM_MODULE:-}" "${QT_IM_MODULE:-}"
} >> "$LOG" 2>/dev/null || true

# ---- v1.21 输入法：Qt6 需要显式选择 fcitx5/ibus 插件 ----
# 不覆盖用户显式设置；只在未设置时按会话推断。插件是否存在由程序内
# gwtool/ui/ime.py 再核一遍（找不到会弹可操作提示）。
if [ -z "${QT_IM_MODULE:-}" ]; then
  case "${XMODIFIERS:-}${GTK_IM_MODULE:-}" in
    *fcitx*) export QT_IM_MODULE=fcitx ;;
    *ibus*)  export QT_IM_MODULE=ibus ;;
  esac
fi
# 系统安装的 fcitx5-qt6/ibus 插件在发行版 Qt6 目录里；PyInstaller 默认只把
# QT_PLUGIN_PATH 指向包内，追加系统目录才能被 Qt 找到。
for d in /usr/lib/aarch64-linux-gnu/qt6/plugins /usr/lib/x86_64-linux-gnu/qt6/plugins \
         /usr/lib/qt6/plugins /usr/lib64/qt6/plugins; do
  [ -d "$d" ] || continue
  case ":${QT_PLUGIN_PATH:-}:" in
    *":$d:"*) ;;
    *) QT_PLUGIN_PATH="${QT_PLUGIN_PATH:+$QT_PLUGIN_PATH:}$d" ;;
  esac
done
[ -n "${QT_PLUGIN_PATH:-}" ] && export QT_PLUGIN_PATH

# 注意：参数里若带 \n，必须用 printf 转义，不能交给 echo。
# bash 内置 echo 默认**不解释** \n，会把 "\n" 原样打成反斜杠+n —— 而这些提示
# 恰好都在"启动失败"路径上（架构不匹配、缺库），用户看到的将是一行乱码般的
# 单行文本，最需要可读性的时刻反而最不可读。实测确认过该行为。
popup() {  # 依次尝试可用的图形提示工具
  if command -v kdialog >/dev/null 2>&1; then kdialog --error "$1" --title "公文汇编助手" 2>/dev/null; return; fi
  if command -v zenity >/dev/null 2>&1; then zenity --error --width=560 --text="$1" --title "公文汇编助手" 2>/dev/null; return; fi
  if command -v xmessage >/dev/null 2>&1; then xmessage "$1" 2>/dev/null; return; fi
  if command -v notify-send >/dev/null 2>&1; then notify-send --urgency=critical "公文汇编助手" "$1" 2>/dev/null; return; fi
  true
}

if [ ! -x "$EXE" ]; then
  say "未找到主程序：$EXE"
  popup "未找到主程序：$EXE
请确认安装包完整（详见 $LOG）"
  exit 1
fi

# ---- 架构自检：防止 ARM64 包装到 x86_64 机器（或反之）后报二进制无法执行 ----
if command -v file >/dev/null 2>&1; then
  case "$(file -b "$EXE" 2>/dev/null)" in
    *aarch64*)          EXEARCH="aarch64" ;;
    *x86-64*|*x86_64*)  EXEARCH="x86_64" ;;
    *)                  EXEARCH="" ;;
  esac
  MYARCH="$(uname -m)"
  [ "$MYARCH" = "amd64" ] && MYARCH="x86_64"
  if [ -n "$EXEARCH" ] && [ "$MYARCH" != "$EXEARCH" ]; then
    # 多行提示直接用真实换行（这里写实换行而非 \n）：
    # popup 的 kdialog/zenity 会自行解释 \n，但 say 走的 printf 与日志文件
    # 不会——为保证两处显示一致，消息里就写成实换行。
    MSG="程序架构（$EXEARCH）与当前系统（$MYARCH）不匹配。
请下载与系统架构一致的安装包（本机架构：$(uname -m)）。"
    say "$MSG"
    popup "$MSG"
    exit 1
  fi
fi

# ---- 运行库预检：主程序 + xcb 平台插件，列出全部缺失的系统库 ----
MISSING=""
TARGETS="$(find "$DIR" -name 'libqxcb.so' 2>/dev/null; echo "$EXE")"
for target in $TARGETS; do
  [ -f "$target" ] || continue
  MISSING="$MISSING$(ldd "$target" 2>/dev/null | grep 'not found' || true)"
done
if [ -n "$MISSING" ]; then
  say "检测到缺失的系统运行库："
  say "$MISSING"
  say "修复方法（联网环境执行；离线环境请在同版本系统上下载这些 deb 后安装）："
  say "  sudo apt-get install -y libgl1 libegl1 libglx0 libxcb-glx0 libglib2.0-0 \\"
  say "      libxkbcommon0 libxkbcommon-x11-0 libfontconfig1 libdbus-1-3 \\"
  say "      libx11-6 libx11-xcb1 libxcb1 libxcb-cursor0 libxcb-xinerama0 \\"
  say "      libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \\"
  say "      libxcb-render0 libxcb-shape0 libxcb-randr0 libxcb-xfixes0 \\"
  say "      libxcb-xkb1 libxext6 libxrender1 libsm6 libice6"
  say "详细日志：$LOG"
  popup "缺少系统运行库，程序可能无法启动。

修复命令已写入：$LOG
（sudo apt-get install -y libxcb-cursor0 libxcb-icccm4 libxcb-xinerama0 libxkbcommon-x11-0 libgl1 libegl1 等）"
fi

# ---- 运行库路径：包内优先（含随包分发的 libstdc++/libgcc_s）----
# 麒麟 V10 的 libstdc++ 可能低于 Qt6 要求；包里带一份放在 $DIR，靠这里优先加载。
export LD_LIBRARY_PATH="$DIR:$DIR/_internal:${LD_LIBRARY_PATH:-}"

# ---- OCR（内置 Tesseract）：预置路径，无内置时自动回退系统安装 ----
if [ -d "$DIR/ocr/bin" ]; then
    export PATH="$DIR/ocr/bin:$PATH"
    export LD_LIBRARY_PATH="$DIR/ocr/lib:${LD_LIBRARY_PATH:-}"
    export TESSDATA_PREFIX="$DIR/ocr/tessdata"
fi

# ---- 启动主程序；stderr/stdout 同时落盘（桌面双击时这是唯一证据）----
if command -v tee >/dev/null 2>&1; then
  set -o pipefail
  "$EXE" "$@" 2>&1 | tee -a "$LOG"
  rc=${PIPESTATUS[0]}
else
  "$EXE" "$@" >> "$LOG" 2>&1
  rc=$?
fi

if [ "$rc" -ne 0 ]; then
  TAIL="$(tail -n 80 "$LOG" 2>/dev/null || true)"
  MSG="程序异常退出（退出码 $rc）。"
  case "$TAIL" in
    *GLIBCXX_*|*"GLIBCXX_"*)
      MSG="$MSG

系统 C++ 运行库（libstdc++）版本低于程序要求（GLIBCXX 不匹配）。
本机：$(ldd --version 2>/dev/null | head -1)
修复：sudo apt install -y libstdc++6；若仍报错，说明系统过旧，请联系维护者获取
与麒麟 V10 匹配的安装包。
";;
    *GLIBC_2.*|*"GLIBC_2."*)
      MSG="$MSG

系统 glibc 版本低于程序要求（GLIBC 不匹配）。
本机：$(ldd --version 2>/dev/null | head -1)
修复：升级系统或使用与麒麟 V10 匹配的安装包。
";;
    *"Could not load the Qt platform plugin"*|*"no Qt platform plugin"*)
      MSG="$MSG

Qt 图形平台插件加载失败，通常是缺少 xcb 运行库。
修复：sudo apt install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 \\
  libxcb-keysyms1 libxcb-render-util0 libxcb-xinerama0 libxkbcommon-x11-0 \\
  libgl1 libegl1
";;
    *inputcontext*|*InputContext*|*"input method"*)
      MSG="$MSG

输入法插件加载失败，可能出现无法输入中文。
修复：sudo apt install -y fcitx5-frontend-qt6（或 ibus）
";;
  esac
  say "$MSG"
  say "详细信息：$LOG"
  popup "$MSG
详细信息：$LOG"
fi
exit "$rc"
