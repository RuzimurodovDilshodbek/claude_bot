@echo off
title Claude Code - Telegram bot
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
echo === Bot ishga tushyapti (%date% %time%) ===
".venv\Scripts\python.exe" bot.py

rem Chiqish kodi 0 = ataylab to'xtatilgan (Ctrl+C, yoki boshqa nusxa
rem allaqachon ishlayapti). Bunda qayta yoqmaymiz — aks holda cheksiz halqa.
if %errorlevel% equ 0 (
    echo.
    echo === Bot to'xtadi ^(ataylab^). Qayta yoqilmaydi. ===
    timeout /t 5 /nobreak >nul
    exit /b 0
)

echo.
echo === Bot yiqildi (kod %errorlevel%). 10 sekunddan keyin qayta urinaman. ===
echo === Butunlay to'xtatish uchun bu oynani yoping yoki Ctrl+C bosing. ===
timeout /t 10 /nobreak >nul
goto loop
