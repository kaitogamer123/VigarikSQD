#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PY="venv/bin/python"
PIP="venv/bin/pip"
SCREEN_NAME="vigarik_bot"
REQ_FILE="requirements.txt"
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

echo "[2/5] Проверка серверных токенов..."
"$PY" - <<'PY'
from utils.secrets import require_brawl_token, require_telegram_token
require_telegram_token()
require_brawl_token()
print("Telegram token: OK")
print("Brawl API token: OK")
PY

echo "[3/5] Проверка venv и requirements.txt..."
if [ ! -x "$PY" ]; then
    echo "ОШИБКА: не найден $PY"
    echo "Создай окружение: python3 -m venv venv"
    exit 1
fi
if [ ! -x "$PIP" ]; then
    echo "pip не найден в venv — пробую восстановить..."
    if ! "$PY" -m ensurepip --upgrade; then
        echo "ОШИБКА: не удалось установить pip в venv."
        exit 1
    fi
fi
if [ ! -s "$REQ_FILE" ]; then
    echo "ОШИБКА: $REQ_FILE отсутствует или пуст. Перезапуск отменён."
    exit 1
fi
REQ_COUNT="$(grep -c -v -E '^[[:space:]]*(#|$)' "$REQ_FILE" || true)"
echo "Зависимостей в $REQ_FILE: $REQ_COUNT"
if [ "$REQ_COUNT" -eq 0 ]; then
    echo "ОШИБКА: в $REQ_FILE нет ни одной библиотеки. Перезапуск отменён."
    exit 1
fi

echo "[4/5] Установка зависимостей и проверка библиотек..."
# До 3 попыток: сеть на VPS иногда отваливается, но при провале деплой стопорится.
INSTALL_OK=0
for attempt in 1 2 3; do
    if "$PIP" install -q --upgrade --disable-pip-version-check -r "$REQ_FILE"; then
        INSTALL_OK=1
        break
    fi
    echo "⚠️ Попытка установки $attempt/3 не удалась, повтор через 3 секунды..."
    sleep 3
done
if [ "$INSTALL_OK" -ne 1 ]; then
    echo "❌ ОШИБКА: не удалось установить зависимости из $REQ_FILE."
    echo "⛔ Перезапуск бота ОТМЕНЁН: старый процесс продолжает работать."
    exit 1
fi

# pip check: предупреждаем о конфликтах версий (не блокируем деплой, если всё импортируется)
echo "Проверка целостности окружения (pip check):"
"$PIP" check || echo "⚠️ pip check нашёл конфликты — смотри вывод выше."

# Жёсткая проверка: каждая библиотека из requirements.txt установлена и импортируется.
"$PY" - <<'PY'
import re
import sys
from importlib import metadata

# имя пакета в requirements -> модуль для импорта
MODULES = {
    "aiogram": "aiogram",
    "aiosqlite": "aiosqlite",
    "pydantic": "pydantic",
    "apscheduler": "apscheduler",
    "pillow": "PIL",
}
# библиотеки, без которых бот точно не запустится
CRITICAL = set(MODULES)

try:
    with open("requirements.txt", encoding="utf-8") as fh:
        raw = fh.read()
except FileNotFoundError:
    print("❌ requirements.txt не найден")
    sys.exit(1)

requirements = []
for line in raw.splitlines():
    line = line.split("#", 1)[0].strip()
    if not line or line.startswith("-"):
        continue
    match = re.match(r"^([A-Za-z0-9_.\-]+)\s*(.*)$", line)
    if not match:
        print(f"⚠️ Не удалось разобрать строку: {line}")
        continue
    requirements.append((match.group(1), match.group(2).strip()))

if not requirements:
    print("❌ В requirements.txt не найдено ни одной библиотеки")
    sys.exit(1)

print("📦 Проверка библиотек из requirements.txt:")
failed = []
for name, spec in requirements:
    key = name.lower()
    module = MODULES.get(key, key.replace("-", "_"))
    label = f"{name}{(' ' + spec) if spec else ''}"
    try:
        version = metadata.version(name)
    except metadata.PackageNotFoundError:
        failed.append((label, f"пакет не установлен (модуль {module})"))
        print(f"  ❌ {label} — НЕ УСТАНОВЛЕН")
        continue
    try:
        __import__(module)
    except Exception as exc:  # noqa: BLE001
        failed.append((label, f"модуль {module} не импортируется: {exc}"))
        print(f"  ❌ {label} — установлен ({version}), но импорт упал: {exc}")
        continue
    print(f"  ✅ {label} — установлен {version}, импорт {module} OK")

if failed:
    print("\n❌ ОТСУТСТВУЮТ ИЛИ НЕ РАБОТАЮТ БИБЛИОТЕКИ:")
    for label, reason in failed:
        print(f"  • {label}: {reason}")
    print("\n⛔ Перезапуск бота ОТМЕНЁН: старый процесс продолжает работать.")
    print("   Установи вручную: venv/bin/pip install -r requirements.txt")
    sys.exit(1)

missing_critical = CRITICAL - {n.lower() for n, _ in requirements}
if missing_critical:
    print("\n⚠️ ВНИМАНИЕ: в requirements.txt не указаны критичные библиотеки: "
          + ", ".join(sorted(missing_critical)))
    print("⛔ Перезапуск отменён, чтобы не запустить бота без нужных зависимостей.")
    sys.exit(1)

print("\n✅ Все библиотеки из requirements.txt установлены и импортируются.")
PY

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
echo "Проверка зависимостей: все библиотеки из requirements.txt на месте."
screen -ls || true
