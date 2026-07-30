@echo off
chcp 65001 > nul
title Управление базой данных VigarikSQD

echo Подключение к серверу и запуск панели базы данных...
ssh -t root@185.251.38.246 "cd ~/VigarikSQD && python3 db_manager.py"

pause