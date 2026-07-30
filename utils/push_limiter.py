"""
Утилита для проверки лимита времени на изменение цели пуша.
Синхронизирована с конфигурацией и адаптирована под новую БД.
"""

from datetime import datetime, timedelta
import aiosqlite

import config
from database import DB_PATH, get_push_goals


def is_locked(chosen_at: str) -> bool:
    """
    Проверяет, прошёл ли лимит времени с момента выбора цели.
    Динамически берет настройки дней из config.py.
    """
    if not chosen_at:
        return False

    try:
        # Пробуем распарсить стандартный формат SQLite
        chosen_time = datetime.strptime(chosen_at, "%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            chosen_time = datetime.fromisoformat(chosen_at)
        except Exception:
            return False

    # Получаем количество дней из конфига (дефолт — 2 дня)
    deadline_days = getattr(config, "PUSH_CHANGE_DEADLINE_DAYS", 2)

    return datetime.now() - chosen_time > timedelta(days=deadline_days)


async def get_locked_users(season_id: str = "current") -> list[int]:
    """
    Возвращает список user_id игроков, у которых истёк лимит изменения выбора.
    ИСПРАВЛЕНО: Прямой быстрый запрос к push_goals для предотвращения KeyError/KeyMissing.
    """
    locked = []

    # Делаем точечную выборку строго тех полей, которые нужны для таймера
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, chosen_at FROM push_goals WHERE season_id = ?",
            (season_id,)
        ) as cur:
            rows = await cur.fetchall()

            for r in rows:
                if is_locked(r["chosen_at"]):
                    locked.append(int(r["user_id"]))

    return locked
