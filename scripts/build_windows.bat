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

echo [3/4] PyInstaller 打包（onedir，排除未用 Qt 模块以控制体积）...
.venv\Scripts\pyinstaller ^
  --noconfirm --clean ^
  --name gwtool ^
  --windowed ^
  --add-data "gwtool\resources\data\seed.db;gwtool/resources/data" ^
  --collect-data opencc ^
  --exclude-module PySide6.QtWebEngineCore ^
  --exclude-module PySide6.QtWebEngineWidgets ^
  --exclude-module PySide6.QtWebChannel ^
  --exclude-module PySide6.QtQuick3D ^
  --exclude-module PySide6.QtQuick ^
  --exclude-module PySide6.QtQml ^
  --exclude-module PySide6.Qt3DCore ^
  --exclude-module PySide6.QtCharts ^
  --exclude-module PySide6.QtDataVisualization ^
  --exclude-module PySide6.QtMultimedia ^
  --exclude-module PySide6.QtNetworkAuth ^
  --exclude-module PySide6.QtPositioning ^
  --exclude-module PySide6.QtSensors ^
  --exclude-module PySide6.QtSerialPort ^
  --exclude-module PySide6.QtTest ^
  --exclude-module PySide6.QtDesigner ^
  --exclude-module PySide6.QtSql ^
  --exclude-module tkinter ^
  --exclude-module matplotlib ^
  --exclude-module numpy ^
  --exclude-module pandas ^
  --hidden-import win32timezone ^
  main.py

echo [4/4] 生成便携版 zip...
.venv\Scripts\python -c "import shutil; shutil.make_archive('dist/gwtool_便携版','zip','.','dist/gwtool')"

echo.
echo 完成！
echo   目录版：dist\gwtool\gwtool.exe
echo   便携版：dist\gwtool_便携版.zip
echo 如需安装包，请用 Inno Setup 编译 scripts\setup_windows.iss
endlocal
