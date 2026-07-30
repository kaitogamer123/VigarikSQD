@echo off
title ViGarik Squad Bot Launcher (Reinstall)

echo [1/3] Создание чистого виртуального окружения на Python 3.13...
py -3.13 -m venv venv

echo [2/3] Установка библиотек aiogram 3.x...
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\pip.exe install --no-cache-dir aiogram aiosqlite pydantic apscheduler

echo [3/3] Запуск бота...
venv\Scripts\python.exe main.py

pause
