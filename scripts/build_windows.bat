@echo off
REM ============================================================
REM  Windows x64 build script (PyInstaller onedir + portable zip)
REM  Output: dist\gwtool\  and  dist\gwtool_portable.zip
REM  Runs the dist smoke check; no zip is produced if it fails.
REM
REM  Line endings MUST be CRLF (see .gitattributes at repo root).
REM  This file is intentionally 100%% ASCII. cmd.exe decodes batch
REM  files using the ACTIVE CODE PAGE, so any non-ASCII byte is a
REM  latent hazard on non-UTF-8 consoles (miscounted bytes can make
REM  cmd.exe split one REM line into a bogus command line).
REM
REM  Do NOT "fix" that with `chcp 65001`: under the UTF-8 code page
REM  cmd.exe mis-parses the for/if blocks below. Verified: with
REM  chcp 65001 the for-loop never set TS_DIR, so Tesseract was not
REM  bundled while the script still printed success.
REM
REM  Chinese documentation for this script lives in README.md.
REM ============================================================
setlocal
cd /d "%~dp0.."

echo [1/5] Cleaning previous build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [2/5] Checking dependencies...
REM Do not assume .venv exists: use it when present, else fall back to PATH
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -c "import sys" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] No usable Python interpreter found.
    echo         Run: python -m venv .venv
    echo              .venv\Scripts\pip install -r requirements.txt
    exit /b 1
)
echo       interpreter: %PY%
"%PY%" -m pip show pyinstaller >nul 2>&1 || "%PY%" -m pip install -r requirements.txt

echo [3/5] PyInstaller packaging (all options live in gwtool.spec)...
"%PY%" -m PyInstaller --noconfirm --clean gwtool.spec
if errorlevel 1 (
    echo [ERROR] PyInstaller failed.
    exit /b 1
)

echo [3.5/5] Bundling Tesseract OCR (local install if present; CI uses choco)...
set "TS_DIR="
for /d %%D in ("%ProgramFiles%\Tesseract-OCR") do set "TS_DIR=%%D"
if not defined TS_DIR goto no_ocr
if not exist "%TS_DIR%\tesseract.exe" goto no_ocr

REM The path breaks when handed to a program: "C:\Program Files" contains a
REM space, and a quoted token ending in a backslash escapes its own closing
REM quote, so Python receives argv[1] = "C:\Program" and fails with
REM FileNotFoundError (hit in practice). Convert to the 8.3 short form, which
REM never contains spaces. Strip the surrounding quotes first, otherwise the
REM FOR variable is the literal string including quotes and ~s has no effect.
set "TS_PLAIN=%TS_DIR:"=%"
for %%I in ("%TS_PLAIN%") do set "TS_SHORT=%%~sI"
echo       source: %TS_SHORT%

REM Let Python do the copying: it copes with non-ASCII file names and fails
REM loudly instead of silently doing nothing.
"%PY%" -c "import shutil,sys,pathlib; src=pathlib.Path(sys.argv[1]); dst=pathlib.Path('dist/gwtool/tesseract'); (dst/'tessdata').mkdir(parents=True,exist_ok=True); shutil.copy2(src/'tesseract.exe', dst); [shutil.copy2(p, dst) for p in src.glob('*.dll')]; td=src/'tessdata'; [shutil.copy2(td/n, dst/'tessdata'/n) for n in ('eng.traineddata','osd.traineddata','chi_sim.traineddata') if (td/n).exists()]; print('  tessdata ->', sorted(p.name for p in (dst/'tessdata').iterdir()))" %TS_SHORT%
if errorlevel 1 (
    echo [ERROR] Bundling Tesseract failed.
    exit /b 1
)
goto after_ocr

:no_ocr
echo       Tesseract not found locally, skipping bundle.
echo       (set tesseract_path in Settings to use an external install)

:after_ocr
echo [4/5] Dist smoke check (resources + real launch + first-run seeding)...
"%PY%" scripts\smoke_dist.py dist\gwtool
if errorlevel 1 (
    echo [ERROR] Dist smoke check failed - do not ship this build.
    exit /b 1
)

echo [5/5] Creating portable zip...
REM ASCII output name only: a non-ASCII name depends on the console code
REM page and is a "works on my machine" hazard. errorlevel IS checked -
REM the old version printed "Done." even when make_archive raised.
"%PY%" -c "import shutil; shutil.make_archive('dist/gwtool_portable','zip','.','dist/gwtool')"
if errorlevel 1 (
    echo [ERROR] Portable zip creation failed.
    exit /b 1
)

echo.
echo Done.
echo   onedir  : dist\gwtool\gwtool.exe
echo   portable: dist\gwtool_portable.zip
echo For the installer, compile scripts\setup_windows.iss with Inno Setup.
endlocal
