@echo off
REM ============================================================
REM  Remove the per-user Explorer context menu entry for gwtool.
REM  Line endings MUST be CRLF; file is 100%% ASCII - see the
REM  header of install_context_menu.bat for why.
REM ============================================================
setlocal
set "RUNNER="
if exist "%~dp0..\dist\gwtool\gwtool.exe" set "RUNNER=%~dp0..\dist\gwtool\gwtool.exe"
if not defined RUNNER if exist "%~dp0..\.venv\Scripts\python.exe" set "RUNNER=%~dp0..\.venv\Scripts\python.exe"
if not defined RUNNER set "RUNNER=python"

"%RUNNER%" --uninstall-context-menu
if errorlevel 1 (
    echo [ERROR] Removing the context menu failed.
    pause
    exit /b 1
)
pause
endlocal
