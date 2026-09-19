"""
Фоновый сборщик личной статистики.
Каждые 10 минут опрашивает battlelog всех зарегистрированных игроков
и дописывает только новые бои в player_stats.db.
"""
import asyncio
import logging

from database import get_all_members
from player_stats_db import save_battlelog, init_player_stats_db
from services.api_service import get_player_battlelog

logger = logging.getLogger(__name__)

COLLECT_INTERVAL_SECONDS = 600  # 10 минут
PAUSE_BETWEEN_PLAYERS = 0.4     # щадим лимиты Brawl Stars API


async def collect_once() -> dict:
    """Один полный круг сбора. Возвращает счётчики для логов."""
    stats = {"players": 0, "new_battles": 0, "skipped": 0}
    try:
        members = await get_all_members() or []
    except Exception as e:
        logger.error(f"collect_once: не удалось получить участников: {e}")
        return stats

    for member in members:
        if not member:
            continue
        tag = member.get("player_tag")
        user_id = member.get("user_id")
        registered = member.get("registered")
        if not tag or not user_id or registered != 1:
            stats["skipped"] += 1
            continue

        try:
            items = await get_player_battlelog(tag)
            if items:
                added = await save_battlelog(tag, items)
                stats["new_battles"] += added
            stats["players"] += 1
        except Exception as e:
            logger.error(f"collect_once: ошибка для {tag} ({member.get('game_nick')}): {e}")

        await asyncio.sleep(PAUSE_BETWEEN_PLAYERS)

    return stats


async def auto_collect_stats_task(bot) -> None:
    """Запускается в main.py фоновой задачей."""
    await init_player_stats_db()
    await asyncio.sleep(25)

    while True:
        try:
            stats = await collect_once()
            logger.info(
                f"Сбор статистики: опрошено {stats['players']}, "
                f"новых боёв {stats['new_battles']}, пропущено {stats['skipped']}"
            )
        except Exception as e:
            logger.error(f"Ошибка цикла сбора статистики: {e}")

        await asyncio.sleep(COLLECT_INTERVAL_SECONDS)
