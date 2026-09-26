#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

PY="venv/bin/python"
PIP="venv/bin/pip"
SCREEN_NAME="vigarik_bot"
BACKUP_DIR="/tmp/vigarik_preserve_$$"

echo "=============================================="
echo "ОБНОВЛЕНИЕ БОТА VIGARIK SQUAD"
echo "=============================================="

# Файлы, которые живут на сервере и НЕ должны затираться GitHub-ом
PRESERVE_FILES=(
    "config.py"
    "admins.txt"
)

echo "[0/3] Сохраняю локальные секреты сервера..."
mkdir -p "$BACKUP_DIR"
for f in "${PRESERVE_FILES[@]}"; do
    if [ -f "$f" ]; then
        cp -a "$f" "$BACKUP_DIR/$f"
        echo "  saved $f"
    fi
done

echo "[1/3] Загрузка изменений с Git..."
git fetch origin
git reset --hard origin/master

echo "[1.5/3] Возвращаю серверные секреты..."
for f in "${PRESERVE_FILES[@]}"; do
    if [ -f "$BACKUP_DIR/$f" ]; then
        cp -a "$BACKUP_DIR/$f" "$f"
        echo "  restored $f"
    fi
done
rm -rf "$BACKUP_DIR"

if [ -f "requirements.txt" ]; then
    echo "[2/3] Проверка и установка зависимостей..."
    $PIP install -q -r requirements.txt
fi

echo "[3/3] Перезапуск процесса бота..."
screen -S "$SCREEN_NAME" -X quit 2>/dev/null || true
sleep 1
screen -dmS "$SCREEN_NAME" $PY main.py

echo "=============================================="
echo "Бот обновлен и перезапущен."
echo "config.py и admins.txt на сервере сохранены."
screen -ls || true
echo "=============================================="
