@echo off
REM ============================================================
REM  公文汇编助手 —— Windows 右键菜单安装脚本（便携版/开发环境用）
REM  作用：任意文件右键 ->「用公文汇编助手导入」
REM  用法：install_context_menu.bat ["gwtool.exe 的完整路径"]
REM  注：用 Inno Setup 安装包安装时，右键菜单已作为可选任务内置，
REM      无需再跑本脚本。
REM
REM  行尾必须是 CRLF（见仓库根 .gitattributes）：cmd.exe 在 CP936 下
REM  按字节解码 UTF-8 中文会错位，LF 版会被整段拆错行 —— 表现为脚本
REM  打印"已安装"却从未执行 reg add（静默失败）。
REM ============================================================
setlocal
REM 切到 UTF-8：本文件含中文，且要把中文写进注册表。
REM 不加这一行，CP936 控制台传给 reg 的中文已是乱码，并且结尾引号会被吞，
REM /f 会被并进 /d 的值里，菜单文案与导入命令双双报废。
chcp 65001 >nul

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
if errorlevel 1 goto failed
reg add "HKCU\Software\Classes\*\shell\GongWenHuiBian" /v Icon /d "%EXE%" /f
if errorlevel 1 goto failed
reg add "HKCU\Software\Classes\*\shell\GongWenHuiBian\command" /ve /d "\"%EXE%\" --import \"%%1\"" /f
if errorlevel 1 goto failed

REM 回读校验：确认真的写进去了（老版本只打印成功，写没写成功无从得知）
reg query "HKCU\Software\Classes\*\shell\GongWenHuiBian\command" /ve >nul 2>&1
if errorlevel 1 goto failed

echo 已安装右键菜单（当前用户）：任意文件右键 ->「用公文汇编助手导入」
echo 指向程序：%EXE%
echo 卸载请运行 uninstall_context_menu.bat
pause
exit /b 0

:failed
echo [错误] 写入注册表失败（reg add 返回 %errorlevel%）。
echo        请确认当前用户对 HKCU\Software\Classes 有写权限后重试。
pause
exit /b 1
