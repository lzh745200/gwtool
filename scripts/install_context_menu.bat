@echo off
REM ============================================================
REM  公文汇编助手 —— Windows 右键菜单安装脚本（便携版/开发环境用）
REM  作用：任意文件右键 ->「用公文汇编助手导入」
REM  用法：install_context_menu.bat [gwtool.exe 的完整路径]
REM  注：用 Inno Setup 安装包安装时，右键菜单已作为可选任务内置，
REM      无需再跑本脚本。
REM ============================================================
setlocal
REM 不硬编码开发机路径：优先用参数，其次自动探测常见安装位置
set "EXE=%~1"
if "%EXE%"=="" if exist "%~dp0..\dist\gwtool\gwtool.exe" set "EXE=%~dp0..\dist\gwtool\gwtool.exe"
if "%EXE%"=="" if exist "%LOCALAPPDATA%\Programs\gwtool\gwtool.exe" set "EXE=%LOCALAPPDATA%\Programs\gwtool\gwtool.exe"
if "%EXE%"=="" if exist "%ProgramFiles%\gwtool\gwtool.exe" set "EXE=%ProgramFiles%\gwtool\gwtool.exe"

if "%EXE%"=="" (
    echo [错误] 未找到 gwtool.exe。
    echo        请把本脚本放在安装目录的 scripts\ 下运行，
    echo        或显式指定路径：install_context_menu.bat "D:\某处\gwtool.exe"
    pause
    exit /b 1
)
if not exist "%EXE%" (
    echo [错误] 指定的路径不存在：%EXE%
    pause
    exit /b 1
)

reg add "HKCU\Software\Classes\*\shell\GongWenHuiBian" /ve /d "用公文汇编助手导入" /f
reg add "HKCU\Software\Classes\*\shell\GongWenHuiBian" /v Icon /d "%EXE%" /f
reg add "HKCU\Software\Classes\*\shell\GongWenHuiBian\command" /ve /d "\"%EXE%\" --import \"%%1\"" /f

echo 已安装右键菜单（当前用户）：任意文件右键 ->「用公文汇编助手导入」
echo 指向程序：%EXE%
echo 卸载请运行 uninstall_context_menu.bat
pause
