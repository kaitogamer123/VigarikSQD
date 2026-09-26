@echo off
chcp 65001 > nul
title VigarikSQD DB Manager

echo Connecting to server and starting DB panel...
echo.

ssh -t root@185.251.38.246 "cd /root/VigarikSQD && ./venv/bin/python db_manager.py"

echo.
echo Panel closed.
pause
