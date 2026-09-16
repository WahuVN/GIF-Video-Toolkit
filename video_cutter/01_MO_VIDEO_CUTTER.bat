@echo off
chcp 65001 >nul
setlocal
cd /d %~dp0
if "%~1"=="" (
    python video_cutter_gui.py
) else (
    python video_cutter.py %*
)
