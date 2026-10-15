@echo off
chcp 65001 >nul
rem ============================================================
rem  字幕工坊 Subtitle Studio · PyInstaller 打包脚本
rem  产物：dist\字幕工坊.exe（单文件，约 100MB）
rem ============================================================
cd /d %~dp0

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] 未找到 python，请先安装 Python 3.10+ 并加入 PATH
    pause
    exit /b 1
)

python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo [INFO] 正在安装 PyInstaller ...
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] PyInstaller 安装失败
        pause
        exit /b 1
    )
)

echo [INFO] 开始打包（约需 1-3 分钟）...
python -m PyInstaller subtitle_studio.spec --noconfirm --clean
if errorlevel 1 (
    echo [ERROR] 打包失败，请检查上方日志
    pause
    exit /b 1
)

echo.
echo [OK] 打包完成：dist\字幕工坊.exe（单文件，可直接拷贝分发）
echo      首次识别时模型会自动下载到用户缓存目录。
pause
