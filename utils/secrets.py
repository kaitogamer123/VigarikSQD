"""Единая загрузка секретов, которые запрещено хранить в Git."""
import os


try:
    import config_local
except ImportError:
    config_local = None


def _local_value(name: str) -> str:
    if config_local is None:
        return ""
    return str(getattr(config_local, name, "") or "").strip()


# Переменные окружения имеют первый приоритет, config_local.py — второй.
TELEGRAM_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    or _local_value("TOKEN")
)

BRAWL_API_TOKEN = (
    os.getenv("BRAWL_API_TOKEN", "").strip()
    or _local_value("BRAWL_API_TOKEN")
)


def require_telegram_token() -> str:
    if not TELEGRAM_TOKEN:
        raise RuntimeError(
            "Telegram-токен не найден. Создай /root/VigarikSQD/config_local.py "
            "со строкой TOKEN = 'токен от BotFather'. Этот файл не должен попадать в Git."
        )
    if ":" not in TELEGRAM_TOKEN:
        raise RuntimeError("TOKEN в config_local.py имеет неверный формат")
    return TELEGRAM_TOKEN


def require_brawl_token() -> str:
    if not BRAWL_API_TOKEN:
        raise RuntimeError(
            "Brawl API токен не найден. Добавь BRAWL_API_TOKEN в config_local.py."
        )
    return BRAWL_API_TOKEN