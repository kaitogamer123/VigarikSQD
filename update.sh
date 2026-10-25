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

echo "[1/5] Загрузка кода..."
git fetch origin
git reset --hard origin/master
restore_server_files
trap - EXIT

if [ ! -x "$PY" ]; then
    echo "ОШИБКА: не найден $PY"
    exit 1
fi
if [ ! -x "$PIP" ]; then
    echo "ОШИБКА: не найден $PIP (venv повреждён?)"
    exit 1
fi

echo "[2/5] Проверка серверных токенов..."
"$PY" - <<'PY'
from utils.secrets import require_brawl_token, require_telegram_token
require_telegram_token()
require_brawl_token()
print("Telegram token: OK")
print("Brawl API token: OK")
PY

echo "[3/5] Установка зависимостей..."
if [ -f "requirements.txt" ]; then
    "$PIP" install -q --disable-pip-version-check --no-input -r requirements.txt
fi

# Проверка: каждый пакет из requirements.txt реально импортируется в venv.
# Если чего-то нет — пробуем доустановить повторно (verbose), потом проверяем ещё раз.
# Деплой прерывается ДО перезапуска бота, если зависимости так и не появились:
# старый процесс в screen продолжит работать на старом коде.
verify_deps() {
    "$PY" - <<'PY'
import importlib
import re
import sys

# Пакет (PyPI) -> модуль для import
MODULES = {
    "aiogram": "aiogram",
    "aiosqlite": "aiosqlite",
    "pydantic": "pydantic",
    "apscheduler": "apscheduler",
    "pillow": "PIL",
}

failed = []
try:
    with open("requirements.txt", encoding="utf-8") as f:
        lines = f.readlines()
except Exception as e:
    print(f"ОШИБКА: не удалось прочитать requirements.txt: {e}")
    sys.exit(1)

for raw in lines:
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    pkg = re.split(r"[<>=!~\[; ]", line, 1)[0].strip()
    if not pkg:
        continue
    mod = MODULES.get(pkg.lower(), pkg.replace("-", "_"))
    try:
        importlib.import_module(mod)
        print(f"  OK: {pkg} (import {mod})")
    except Exception as e:
        print(f"  НЕТ: {pkg} (import {mod}) — {e}")
        failed.append(pkg)

if failed:
    print("НЕ ХВАТАЕТ ПАКЕТОВ: " + ", ".join(failed))
    sys.exit(1)
print("Все зависимости из requirements.txt установлены и импортируются.")
PY
}

echo "[4/5] Проверка, что все библиотеки из requirements.txt доступны в venv..."
if ! verify_deps; then
    echo "Попытка доустановить отсутствующие пакеты (подробный вывод)..."
    "$PIP" install --disable-pip-version-check --no-input -r requirements.txt || true
    if ! verify_deps; then
        echo "=============================================="
        echo "ОШИБКА: зависимости НЕ установлены. Деплой прерван."
        echo "Бот НЕ перезапускался — старый процесс продолжает работать."
        echo "На сервере выполни вручную:"
        echo "  cd ~/VigarikSQD && venv/bin/pip install -r requirements.txt"
        echo "затем перезапусти:  screen -S vigarik_bot -X quit && screen -dmS vigarik_bot venv/bin/python main.py"
        echo "=============================================="
        exit 1
    fi
fi

echo "[5/5] Перезапуск бота..."
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
