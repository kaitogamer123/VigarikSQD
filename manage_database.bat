@echo off
chcp 65001 > nul
title Управление базой данных VigarikSQD

echo Подключение к серверу и запуск панели базы данных...
echo.

ssh -t root@185.251.38.246 "cd ~/VigarikSQD && if [ -x venv/bin/python ]; then venv/bin/python db_manager_launcher.py; else python3 db_manager_launcher.py; fi"

if errorlevel 1 (
    echo.
    echo Панель завершилась с ошибкой. Проверь текст выше.
) else (
    echo.
    echo Панель базы данных закрыта.
)

pause