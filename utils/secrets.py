"""Загружает токены из неотслеживаемого Git серверного файла."""
import os
from pathlib import Path


SECRETS_PATH = Path(
    os.getenv("VIGARIK_SECRETS_FILE", "")
    or Path(__file__).resolve().parent.parent / "server_secrets.env"
)


def _read_secrets_file() -> dict[str, str]:
    """Reads KEY=VALUE lines without requiring python-dotenv."""
    values: dict[str, str] = {}
    try:
        with SECRETS_PATH.open("r", encoding="utf-8") as file:
            for raw_line in file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                values[key] = value.strip()
    except FileNotFoundError:
        return {}
    return values


_FILE_SECRETS = _read_secrets_file()

# The server file wins over environment variables to avoid stale-token overrides.
TELEGRAM_TOKEN = (
    _FILE_SECRETS.get("TELEGRAM_BOT_TOKEN", "")
    or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
)
BRAWL_API_TOKEN = (
    _FILE_SECRETS.get("BRAWL_API_TOKEN", "")
    or os.getenv("BRAWL_API_TOKEN", "").strip()
)


def require_telegram_token() -> str:
    if not TELEGRAM_TOKEN:
        raise RuntimeError(
            f"Telegram-токен не найден. Создай {SECRETS_PATH} с двумя строками: "
            "TELEGRAM_BOT_TOKEN=... и BRAWL_API_TOKEN=..."
        )
    if ":" not in TELEGRAM_TOKEN:
        raise RuntimeError(f"TELEGRAM_BOT_TOKEN в {SECRETS_PATH} имеет неверный формат")
    return TELEGRAM_TOKEN


def require_brawl_token() -> str:
    if not BRAWL_API_TOKEN:
        raise RuntimeError(
            f"Brawl API токен не найден. Добавь BRAWL_API_TOKEN в {SECRETS_PATH}."
        )
    return BRAWL_API_TOKEN