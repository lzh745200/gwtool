@echo off
REM ============================================================
REM  公文汇编助手 —— Windows x64 打包脚本
REM  产物：dist\gwtool\ 目录版（启动快）+ dist\gwtool_便携版.zip
REM ============================================================
setlocal
cd /d "%~dp0.."

echo [1/4] 清理旧构建...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [2/4] 检查依赖...
.venv\Scripts\python -m pip show pyinstaller >nul 2>&1 || .venv\Scripts\python -m pip install pyinstaller

echo [3/4] PyInstaller 打包（参数统一于 gwtool.spec）...
.venv\Scripts\pyinstaller --noconfirm --clean gwtool.spec

echo [4/4] 生成便携版 zip...
.venv\Scripts\python -c "import shutil; shutil.make_archive('dist/gwtool_便携版','zip','.','dist/gwtool')"

echo.
echo 完成！
echo   目录版：dist\gwtool\gwtool.exe
echo   便携版：dist\gwtool_便携版.zip
echo 如需安装包，请用 Inno Setup 编译 scripts\setup_windows.iss
endlocal
