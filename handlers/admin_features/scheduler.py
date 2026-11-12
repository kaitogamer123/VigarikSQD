"""Планировщик автоматического старта турниров."""
import asyncio
import logging
from aiogram import Router, F, Bot
from .tournament_db import get_due_tournaments, init_tournament_tables
from .tournament_start import start_tournament

logger = logging.getLogger(__name__)
router = Router()


async def tournament_scheduler_task(bot: Bot) -> None:
    """Фоновая задача: раз в минуту стартует турниры, у которых наступило время."""
    await asyncio.sleep(45)
    while True:
        try:
            for row in get_due_tournaments():
                try:
                    await start_tournament(bot, int(row["id"]))
                except Exception as e:
                    logger.error(f"Ошибка автостарта турнира {row['id']}: {e}")
        except Exception as e:
            logger.error(f"tournament scheduler error: {e}")
        await asyncio.sleep(60)


@router.startup()
async def _launch_tournament_scheduler(bot: Bot = None, **kwargs):
    """Поднимает планировщик турниров вместе с ботом (без правок main.py)."""
    bot = bot or kwargs.get("bot")
    if bot is None:
        return
    init_tournament_tables()
    asyncio.create_task(tournament_scheduler_task(bot))
    logger.info("Планировщик автостарта турниров запущен")
