@echo off
chcp 65001 >nul
title Deploy VigarikSQD

echo ============================================
echo  [1/2] Push to GitHub VigarikSQD/master
echo ============================================
git push VigarikSQD master
if errorlevel 1 echo ERROR: git push failed. Deploy stopped.
if not errorlevel 1 echo Push OK.

echo.
echo ============================================
echo  [2/2] Server update: deps check, restart
echo ============================================
if not errorlevel 1 ssh root@185.251.38.246 "cd ~/VigarikSQD && chmod +x update.sh && ./update.sh"
if errorlevel 1 echo ERROR: server deploy failed. See output above. Bot keeps old version.
if not errorlevel 1 echo SUCCESS: bot updated, all libraries from requirements.txt verified.

echo.
pause
