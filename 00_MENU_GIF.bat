@echo off
chcp 65001 >nul
set "ROOT=%~dp0"
cls
echo ================================
echo            GIF TOOL HUB
echo ================================
echo [1] Tao GIF - giao dien
echo [2] Cat video - giao dien
echo [3] GIF CLI
ECHO [0] Thoat
choice /c 1230 /n /m "Chon: "
if errorlevel 4 goto END
if errorlevel 3 goto CLI
if errorlevel 2 goto CUT
if errorlevel 1 goto GUI
:GUI
call "%ROOT%01_TAO_GIF.bat"
goto END
:CUT
call "%ROOT%02_CAT_VIDEO.bat"
goto END
:CLI
call "%ROOT%03_GIF_CLI.bat"
:END
exit /b 0
