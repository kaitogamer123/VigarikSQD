@echo off
title ViGarik Squad Bot Launcher (API Ready)
chcp 65001 > nul

:: Проверяем, создано ли уже виртуальное окружение, чтобы не качать библиотеки каждый раз
if exist venv\Scripts\python.exe (
    echo [ИНФО] Виртуальное окружение уже существует. Пропускаем шаг установки.
    goto LAUNCH
)

echo [1/3] Создание чистого виртуального окружения на Python 3.13...
py -3.13 -m venv venv
if errorlevel 1 (
    echo [КРИТИЧЕСКАЯ ОШИБКА] На вашем компьютере не установлен Python 3.13! Установите его с python.org
    pause
    exit
)

echo [2/3] Обновление PIP и установка библиотек ViGarik Squad под API...
venv\Scripts\python.exe -m pip install --upgrade pip
:: ИСПРАВЛЕНО: Явно добавлена библиотека aiohttp для защиты от ModuleNotFoundError в api_service
venv\Scripts\pip.exe install --no-cache-dir aiogram aiosqlite pydantic apscheduler aiohttp

:LAUNCH
echo [3/3] Запуск бота ViGarik Squad...
echo ⊱━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━⊰
echo [ВНИМАНИЕ] Для полной остановки бота закройте это окно крестиком.
echo ⊱━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━⊰

:: ИСПРАВЛЕНО: Бесконечный цикл авто-перезапуска. Если бот упадет из-за сети, он сам поднимется обратно!
:LOOP
venv\Scripts\python.exe main.py
echo [ПРЕДУПРЕЖДЕНИЕ] Бот завершил работу или упал с ошибкой. Перезапуск через 3 секунды...
timeout /t 3 > nul
goto LOOP
