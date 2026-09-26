#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PY="venv/bin/python"
PIP="venv/bin/pip"
SCREEN_NAME="vigarik_bot"
BACKUP_DIR="$(mktemp -d /tmp/vigarik-config.XXXXXX)"
PRESERVE_FILES=("config.py" "admins.txt" "server_secrets.env" "config_local.py")

restore_server_files() {
    for file in "${PRESERVE_FILES[@]}"; do
        if [ -f "$BACKUP_DIR/$file" ]; then
            cp -a "$BACKUP_DIR/$file" "$file"
            case "$file" in
                server_secrets.env|config_local.py) chmod 600 "$file" ;;
            esac
        fi
    done
    rm -rf "$BACKUP_DIR"
}
trap restore_server_files EXIT

echo "=============================================="
echo "ОБНОВЛЕНИЕ VIGARIK SQUAD"
echo "=============================================="

if [ ! -f "server_secrets.env" ]; then
    echo "ОШИБКА: отсутствует $(pwd)/server_secrets.env"
    echo "Создай его с TELEGRAM_BOT_TOKEN и BRAWL_API_TOKEN перед деплоем."
    exit 1
fi

# Явно сохраняем серверные файлы вокруг reset. Так секреты останутся на месте,
# даже если их случайно добавили в Git. Обычный перезапуск бота их не удаляет.
for file in "${PRESERVE_FILES[@]}"; do
    if [ -f "$file" ]; then
        cp -a "$file" "$BACKUP_DIR/$file"
    fi
done

echo "[1/4] Загрузка кода..."
git fetch origin
git reset --hard origin/master
restore_server_files
trap - EXIT

if [ ! -x "$PY" ]; then
    echo "ОШИБКА: не найден $PY"
    exit 1
fi

echo "[2/4] Проверка серверных токенов..."
"$PY" - <<'PY'
from utils.secrets import require_brawl_token, require_telegram_token
require_telegram_token()
require_brawl_token()
print("Telegram token: OK")
print("Brawl API token: OK")
PY

echo "[3/4] Установка зависимостей..."
if [ -f "requirements.txt" ]; then
    "$PIP" install -q -r requirements.txt
fi

echo "[4/4] Перезапуск бота..."
screen -S "$SCREEN_NAME" -X quit 2>/dev/null || true
sleep 1
screen -dmS "$SCREEN_NAME" "$PY" main.py
sleep 3

if ! screen -list | grep -q "$SCREEN_NAME"; then
    echo "ОШИБКА: screen-сессия бота не запустилась."
    "$PY" main.py
    exit 1
fi

echo "Бот обновлён; server_secrets.env и серверные конфиги сохранены."
screen -ls || true