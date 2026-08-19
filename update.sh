#!/usr/bin/env bash
set -e

# Переходим в папку, где лежит сам скрипт
cd "$(dirname "$0")"

# Путь к питону внутри venv (если виртуальное окружение называется venv)
PY="venv/bin/python"
PIP="venv/bin/pip"
SCREEN_NAME="vigarik_bot"

echo "=============================================="
echo "🚀 ОБНОВЛЕНИЕ BOTA VIGARIK SQUAD"
echo "=============================================="

# 1. Скачиваем свежий код с GitHub
echo "[1/3] Загрузка изменений с Git..."
git fetch origin
git reset --hard origin/master

# 2. Проверяем и обновляем библиотеки (если добавлял новые)
if [ -f "requirements.txt" ]; then
    echo "[2/3] Проверка и установка зависимостей..."
    $PIP install -q -r requirements.txt
fi

# 3. Перезапускаем бота в фоновом режиме (screen)
echo "[3/3] Перезапуск процесса бота..."

# Завершаем старую сессию бота, если она запущенна
screen -S "$SCREEN_NAME" -X quit 2>/dev/null || true
sleep 1

# Запускаем бота в новой изолированной фоновой сессии screen
screen -dmS "$SCREEN_NAME" $PY main.py

echo "=============================================="
echo "✅ Бот успешно обновлен и перезапущен!"
echo "Текущие фоновые процессы:"
screen -ls || true
echo "=============================================="