#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Деплой VGS Money на сервере одной командой.
#
#   git pull  →  доустановка зависимостей  →  перезапуск бота и дашборда
#
# Запуск на сервере:
#   cd "~/pay tg bot" && ./deploy.sh
# (первый раз: chmod +x deploy.sh)
#
# Флаги:
#   ./deploy.sh bot     — обновить и перезапустить только бота
#   ./deploy.sh dash    — обновить и перезапустить только дашборд
#   ./deploy.sh         — всё сразу
# ─────────────────────────────────────────────────────────────
set -e

# Идём в папку, где лежит сам скрипт (корень проекта)
cd "$(dirname "$0")"

PY=venv/bin/python
PIP=venv/bin/pip
TARGET="${1:-all}"

echo "──────────────────────────────────────────────"
echo " VGS Money deploy  (target: $TARGET)"
echo "──────────────────────────────────────────────"

# 1. Подтягиваем код
echo "[1/3] git pull..."
git pull

# 2. Зависимости (быстро — pip сам пропустит уже установленное)
echo "[2/3] Проверка зависимостей..."
$PIP install -q -r requirements.txt
if [ -f dashboard/requirements.txt ]; then
    $PIP install -q -r dashboard/requirements.txt
fi

# Функция перезапуска screen-сессии:
#   $1 — имя сессии, $2 — команда запуска
restart_screen() {
    local name="$1"
    local cmd="$2"
    # Гасим старую сессию, если есть (не падаем, если её нет)
    screen -S "$name" -X quit 2>/dev/null || true
    sleep 1
    # Поднимаем заново в detached-режиме
    screen -dmS "$name" bash -c "cd '$(pwd)' && $cmd"
    echo "  ✔ '$name' перезапущен"
}

# 3. Перезапуск
echo "[3/3] Перезапуск процессов..."
if [ "$TARGET" = "all" ] || [ "$TARGET" = "bot" ]; then
    restart_screen "mybot" "$PY -m bot.main"
fi
if [ "$TARGET" = "all" ] || [ "$TARGET" = "dash" ]; then
    restart_screen "dashboard" "$PY -m dashboard.app"
fi

echo "──────────────────────────────────────────────"
echo " Готово. Активные сессии:"
screen -ls || true
echo "──────────────────────────────────────────────"
