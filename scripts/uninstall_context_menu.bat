@echo off
REM 卸载 Windows 右键菜单
reg delete "HKCU\Software\Classes\*\shell\GongWenHuiBian" /f >nul 2>&1
echo 已卸载右键菜单。
pause
