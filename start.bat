@echo off
rem 字幕工坊 Subtitle Studio 启动脚本
chcp 65001 >nul
cd /d %~dp0
python app.py
pause
