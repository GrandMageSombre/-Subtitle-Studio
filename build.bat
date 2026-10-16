@echo off
rem ============================================================
rem  Subtitle Studio - PyInstaller build script
rem  Output: project root exe (single file, ~110MB, name per spec)
rem  The exe sits next to start.bat, so double-clicking the exe
rem  and running from source share the SAME data\ directory.
rem  Copy the whole folder to another PC and the behavior is
rem  identical (portable: data travels with the folder).
rem  NOTE: keep this file ASCII-only - cmd.exe mis-parses batch
rem  lines containing non-ASCII text, with or without "chcp 65001".
rem ============================================================
cd /d %~dp0

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] python not found. Install Python 3.10+ and add it to PATH.
    pause
    exit /b 1
)

python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo [INFO] Installing PyInstaller ...
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] Failed to install PyInstaller.
        pause
        exit /b 1
    )
)

echo [INFO] Building (about 1-3 minutes) ...
python -m PyInstaller subtitle_studio.spec --noconfirm --clean
if errorlevel 1 (
    echo [ERROR] Build failed. Check the log above.
    pause
    exit /b 1
)

rem Move the artifact to the project root so the data dir (exe-side data\)
rem matches the source-mode data\ directory. Wildcard avoids non-ASCII here;
rem the spec builds exactly one exe.
move /y "dist\*.exe" . >nul
if errorlevel 1 (
    echo [ERROR] Cannot move the exe. Is Subtitle Studio still running? Close it and retry.
    pause
    exit /b 1
)
rem dist is empty now; if it still holds runtime data left by an OLD exe, keep it untouched.
rd dist 2>nul

echo.
echo [OK] Build finished: exe in project root (next to start.bat, single file, portable)
echo      Data dir: data\ next to the exe (shared by exe and source mode)
echo      Models are downloaded on first use.
pause
