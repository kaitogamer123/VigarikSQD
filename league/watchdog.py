"""Фоновая проверка зависших скримов."""
import asyncio
import logging
from aiogram import Bot
from league import league_db as db
from league.league_db import get_expired_scrims
from utils.notify_settings import is_muted

logger = logging.getLogger(__name__)


async def scrim_watchdog_task(bot: Bot) -> None:
    await asyncio.sleep(30)
    while True:
        try:
            for scrim in get_expired_scrims():
                db.set_scrim_status(int(scrim["id"]), "expired")
                try:
                    challenger = int(scrim["challenger_leader_id"])
                    if scrim["status"] == "searching":
                        if not await is_muted(challenger, "scrims"):
                            await bot.send_message(
                                challenger,
                                f"⏰ Поиск соперника для рандомного скрима #{scrim['id']} завершён: "
                                f"за сутки никто не откликнулся. Создай новый поиск.",
                            )
                    else:
                        if not await is_muted(challenger, "scrims"):
                            await bot.send_message(
                                challenger,
                                f"⏰ Приглашение на скрим #{scrim['id']} сгорело: соперник не ответил за сутки.",
                            )
                        opponent = int(scrim["opponent_leader_id"] or 0)
                        if opponent and not await is_muted(opponent, "scrims"):
                            await bot.send_message(
                                opponent,
                                f"⏰ Приглашение на скрим #{scrim['id']} сгорело.",
                            )
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"scrim watchdog error: {e}")
        await asyncio.sleep(300)
