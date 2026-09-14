@echo off
REM 卸载 Windows 右键菜单
REM 行尾必须是 CRLF（见仓库根 .gitattributes），否则 cmd.exe 解析错乱。
setlocal
chcp 65001 >nul
reg delete "HKCU\Software\Classes\*\shell\GongWenHuiBian" /f >nul 2>&1
if errorlevel 1 (
    echo 未发现已安装的右键菜单（可能已卸载过）。
    pause
    exit /b 0
)
echo 已卸载右键菜单。
pause
endlocal
