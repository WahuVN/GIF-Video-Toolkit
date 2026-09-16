@echo off
chcp 65001 >nul
cd /d "%~dp0video_cutter"
python video_cutter_gui.py
