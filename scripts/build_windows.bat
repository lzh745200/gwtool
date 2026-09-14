@echo off
REM ============================================================
REM  公文汇编助手 —— Windows x64 打包脚本
REM  产物：dist\gwtool\ 目录版（启动快）+ dist\gwtool_portable.zip
REM  打包后会自动跑产物冒烟校验，未通过则不生成 zip。
REM
REM  行尾必须是 CRLF（见仓库根 .gitattributes）：
REM  cmd.exe 在 CP936 控制台下按字节解码 UTF-8 中文会错位，
REM  LF 行尾的 .bat 会被整段拆错行，脚本根本跑不起来。
REM ============================================================
setlocal
REM 切换到 UTF-8 代码页：本文件含中文，且下面要传中文参数给 Python。
REM 不加这一行，CP936 控制台会把 UTF-8 参数解成乱码（曾导致
REM "gwtool_便携版" 这个文件名变成非法路径而 make_archive 直接抛错）。
chcp 65001 >nul
cd /d "%~dp0.."

echo [1/5] 清理旧构建...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [2/5] 检查依赖...
REM 不假定 .venv 必然存在：有就用，没有则回退到 PATH 上的 python
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -c "import sys" >nul 2>&1
if errorlevel 1 (
    echo [错误] 找不到可用的 Python 解释器。
    echo        请先执行：python -m venv .venv
    echo                  .venv\Scripts\pip install -r requirements.txt
    exit /b 1
)
echo       使用解释器：%PY%
"%PY%" -m pip show pyinstaller >nul 2>&1 || "%PY%" -m pip install -r requirements.txt

echo [3/5] PyInstaller 打包（参数统一于 gwtool.spec）...
"%PY%" -m PyInstaller --noconfirm --clean gwtool.spec
if errorlevel 1 (
    echo [错误] PyInstaller 打包失败。
    exit /b 1
)

echo [3.5/5] 集成 Tesseract OCR（本机已安装时；CI 由 choco 提供）...
set "TS_DIR="
for /d %%D in ("%ProgramFiles%\Tesseract-OCR") do set "TS_DIR=%%D"
if exist "%TS_DIR%\tesseract.exe" (
    mkdir dist\gwtool\tesseract\tessdata 2>nul
    copy /y "%TS_DIR%\tesseract.exe" dist\gwtool\tesseract\ >nul
    copy /y "%TS_DIR%\*.dll" dist\gwtool\tesseract\ >nul
    copy /y "%TS_DIR%\tessdata\eng.traineddata" dist\gwtool\tesseract\tessdata\ >nul
    if exist "%TS_DIR%\tessdata\chi_sim.traineddata" copy /y "%TS_DIR%\tessdata\chi_sim.traineddata" dist\gwtool\tesseract\tessdata\ >nul
    echo       已集成 Tesseract。
) else (
    echo       未检测到本机 Tesseract，跳过集成（OCR 仍可通过设置指定路径）。
)

echo [4/5] 产物冒烟校验（资源齐全 + 真实启动 + 首启动种子导入）...
"%PY%" scripts\smoke_dist.py dist\gwtool
if errorlevel 1 (
    echo [错误] 产物冒烟校验未通过，请勿发布该产物。
    exit /b 1
)

echo [5/5] 生成便携版 zip...
REM 文件名用 ASCII：中文名依赖控制台代码页，是"换个机器就失败"的隐患。
REM 同时检查 errorlevel —— 老版本没有检查，zip 生成失败仍打印"完成！"并返回 0。
"%PY%" -c "import shutil; shutil.make_archive('dist/gwtool_portable','zip','.','dist/gwtool')"
if errorlevel 1 (
    echo [错误] 便携版 zip 生成失败。
    exit /b 1
)

echo.
echo 完成！
echo   目录版：dist\gwtool\gwtool.exe
echo   便携版：dist\gwtool_portable.zip
echo 如需安装包，请用 Inno Setup 编译 scripts\setup_windows.iss
endlocal
