@echo off
REM ============================================================
REM  公文汇编助手 —— Windows x64 打包脚本
REM  产物：dist\gwtool\ 目录版（启动快）+ dist\gwtool_便携版.zip
REM  打包后会自动跑产物冒烟校验，未通过则不生成 zip。
REM ============================================================
setlocal
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
if exist "%TS_DIR%	esseract.exe" (
    mkdir dist\gwtool	esseract	essdata 2>nul
    copy /y "%TS_DIR%	esseract.exe" dist\gwtool	esseract\ >nul
    copy /y "%TS_DIR%\*.dll" dist\gwtool	esseract\ >nul
    copy /y "%TS_DIR%	essdata\eng.traineddata" dist\gwtool	esseract	essdata\ >nul
    if exist "%TS_DIR%	essdata\chi_sim.traineddata" copy /y "%TS_DIR%	essdata\chi_sim.traineddata" dist\gwtool	esseract	essdata\ >nul
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
"%PY%" -c "import shutil; shutil.make_archive('dist/gwtool_便携版','zip','.','dist/gwtool')"

echo.
echo 完成！
echo   目录版：dist\gwtool\gwtool.exe
echo   便携版：dist\gwtool_便携版.zip
echo 如需安装包，请用 Inno Setup 编译 scripts\setup_windows.iss
endlocal
