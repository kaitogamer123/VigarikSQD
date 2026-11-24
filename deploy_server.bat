@echo off
title Deploy VigarikSQD

echo ==============================================
echo [1/2] Pushing changes to GitHub...
echo ==============================================
git push VigarikSQD master
if errorlevel 1 (
    echo.
    echo ERROR: git push failed. Deploy aborted.
    pause
    exit /b 1
)

echo.
echo ==============================================
echo [2/2] Updating and restarting bot on server...
echo ==============================================
ssh root@45.130.254.28 "cd ~/VigarikSQD && chmod +x update.sh && ./update.sh"
if errorlevel 1 (
    echo.
    echo ERROR: server update script failed. Check the output above.
    pause
    exit /b 1
)

echo.
echo ==============================================
echo  Deploy finished successfully.
echo ==============================================

set /p show_logs="Open bot logs console (screen -r vigarik_bot)? (Y/N): "
if /i "%show_logs%"=="Y" (
    ssh -t root@45.130.254.28 "screen -r vigarik_bot"
)

pause
