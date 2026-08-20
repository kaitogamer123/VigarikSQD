@echo off
chcp 65001 > nul
title Отправка изменений в GitHub

cd /d "%~dp0"

echo ==============================================
echo Проверка изменений в Git...
echo ==============================================
git status --short

if errorlevel 1 (
    echo.
    echo Не удалось выполнить git status.
    pause
    exit /b 1
)

for /f %%A in ('git status --porcelain') do set "HAS_CHANGES=1"
if not defined HAS_CHANGES (
    echo.
    echo Изменений для отправки нет.
    pause
    exit /b 0
)

echo.
set /p CONFIRM="Добавить все новые и измененные файлы в коммит? (Y/N): "
if /i not "%CONFIRM%"=="Y" (
    echo Операция отменена.
    pause
    exit /b 0
)

git add -A
if errorlevel 1 (
    echo.
    echo Не удалось добавить файлы.
    pause
    exit /b 1
)

echo.
set /p COMMIT_MESSAGE="Введите сообщение коммита: "
if not defined COMMIT_MESSAGE set "COMMIT_MESSAGE=Update files"

git commit -m "%COMMIT_MESSAGE%"
if errorlevel 1 (
    echo.
    echo Коммит не создан.
    pause
    exit /b 1
)

echo.
echo ==============================================
echo Отправка коммита в GitHub...
echo ==============================================
git push VigarikSQD master
if errorlevel 1 (
    echo.
    echo Не удалось отправить изменения в GitHub.
    pause
    exit /b 1
)

echo.
echo Готово! Изменения отправлены в GitHub.
pause
