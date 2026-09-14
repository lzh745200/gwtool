@echo off
REM ============================================================
REM  Install the per-user Explorer context menu entry for gwtool.
REM  Adds "Import with GongWenHuiBian" on any file.
REM  Usage: install_context_menu.bat ["full path to gwtool.exe"]
REM
REM  When installed via the Inno Setup package this entry is already
REM  offered as an optional task, so this script is for the portable
REM  build / development checkout only.
REM
REM  Line endings MUST be CRLF (see .gitattributes at repo root).
REM  This file is intentionally 100%% ASCII: cmd.exe decodes batch
REM  files using the active code page, so non-ASCII bytes are a
REM  parsing hazard (the old non-ASCII version printed "installed"
REM  while never actually running reg add).
REM
REM  The registry write is delegated to Python (winreg) via
REM  `main.py --install-context-menu`: winreg stores Unicode directly,
REM  whereas `reg add /d "<chinese>"` goes through the console code
REM  page and writes mojibake on CP936 systems.
REM ============================================================
setlocal
REM Never hardcode a developer machine path: prefer the argument, then
REM probe the usual install locations.
set "EXE=%~1"
if "%EXE%"=="" if exist "%~dp0..\dist\gwtool\gwtool.exe" set "EXE=%~dp0..\dist\gwtool\gwtool.exe"
if "%EXE%"=="" if exist "%LOCALAPPDATA%\Programs\gwtool\gwtool.exe" set "EXE=%LOCALAPPDATA%\Programs\gwtool\gwtool.exe"
if "%EXE%"=="" if exist "%ProgramFiles%\gwtool\gwtool.exe" set "EXE=%ProgramFiles%\gwtool\gwtool.exe"

if "%EXE%"=="" (
    echo [ERROR] gwtool.exe not found.
    echo         Put this script under the install dir's scripts\ folder,
    echo         or pass the path: install_context_menu.bat "D:\path\gwtool.exe"
    pause
    exit /b 1
)
if not exist "%EXE%" (
    echo [ERROR] Path does not exist: %EXE%
    pause
    exit /b 1
)

REM Prefer the packaged exe (main.py is frozen into it); fall back to a
REM developer interpreter.
set "RUNNER="
if exist "%~dp0..\dist\gwtool\gwtool.exe" set "RUNNER=%~dp0..\dist\gwtool\gwtool.exe"
if not defined RUNNER if exist "%~dp0..\.venv\Scripts\python.exe" set "RUNNER=%~dp0..\.venv\Scripts\python.exe"
if not defined RUNNER set "RUNNER=python"
if not defined RUNNER goto no_runner
echo Using: %RUNNER%

"%RUNNER%" --install-context-menu "%EXE%"
if errorlevel 1 (
    echo [ERROR] Installing the context menu failed.
    pause
    exit /b 1
)

echo   target: %EXE%
echo To remove it, run uninstall_context_menu.bat
pause
exit /b 0

:no_runner
echo [ERROR] No gwtool.exe or Python interpreter available to do the install.
pause
exit /b 1
