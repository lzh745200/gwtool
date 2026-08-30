@echo off
REM ============================================================
REM  公文汇编助手 —— Windows 右键菜单安装脚本
REM  作用：任意文件右键 ->「用公文汇编助手导入」
REM  用法：右键“以管理员身份运行”本脚本（或普通运行仅当前用户生效）
REM  如程序路径不同，请先修改下方 EXE 变量
REM ============================================================
setlocal
set EXE=C:\gwtool\gwtool.exe
if exist "%~dp0..\dist\gwtool\gwtool.exe" set EXE=%~dp0..\dist\gwtool\gwtool.exe

reg add "HKCU\Software\Classes\*\shell\GongWenHuiBian" /ve /d "用公文汇编助手导入" /f
reg add "HKCU\Software\Classes\*\shell\GongWenHuiBian" /v Icon /d "%EXE%" /f
reg add "HKCU\Software\Classes\*\shell\GongWenHuiBian\command" /ve /d "\"%EXE%\" --import \"%%1\"" /f

echo 已安装右键菜单（当前用户）：任意文件右键 ->「用公文汇编助手导入」
echo 卸载请运行 uninstall_context_menu.bat
pause
