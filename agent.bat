@echo off
title Claude agent
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [XATO] .venv topilmadi. Avval quyidagini bajaring:
    echo     python -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

:loop
echo.
echo === Agent ishga tushyapti (%date% %time%) ===
".venv\Scripts\python.exe" agent.py

if %errorlevel% equ 0 (
    echo.
    echo === Agent to'xtadi ^(ataylab^). Qayta yoqilmaydi. ===
    timeout /t 5 /nobreak >nul
    exit /b 0
)

echo.
echo === Agent yiqildi (kod %errorlevel%). 10 sekunddan keyin qayta urinaman. ===
timeout /t 10 /nobreak >nul
goto loop
