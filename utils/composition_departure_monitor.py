"""24-часовая проверка лидеров составов после выхода из клана."""
import asyncio
import logging

from aiogram import Bot

from config import CLAN_CHATS, CLAN_TAGS
from league.league_db import (
    cancel_pending_departure,
    finalize_pending_departure,
    get_due_departures,
)
from services.api_service import get_player_profile

logger = logging.getLogger(__name__)
CHECK_INTERVAL_SECONDS = 10 * 60


def _clean_tag(value) -> str:
    return str(value or "").upper().strip().replace("#", "")


async def _is_in_network_telegram(bot: Bot, user_id: int) -> bool:
    for clan in (CLAN_CHATS or {}).values():
        chat_id = clan.get("chat_id") if isinstance(clan, dict) else None
        if not chat_id:
            continue
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            status = getattr(member.status, "value", str(member.status)).lower()
            if status not in ("left", "kicked"):
                return True
        except Exception:
            continue
    return False


async def _is_in_network_game(player_tag: str):
    """True/False — подтверждённый результат, None — API временно недоступен."""
    if not player_tag:
        return False
    profile = await get_player_profile(player_tag)
    if not profile:
        return None
    current_club = _clean_tag(profile.get("clan_tag"))
    network_tags = {_clean_tag(tag) for tag in (CLAN_TAGS or {}).values() if tag}
    return bool(current_club and current_club in network_tags)


async def process_due_composition_departures(bot: Bot) -> None:
    for pending in get_due_departures():
        user_id = int(pending["user_id"])
        try:
            in_telegram = await _is_in_network_telegram(bot, user_id)
            in_game = await _is_in_network_game(pending["player_tag"])
            reason = pending["reason"]

            # Если сигнал пришёл из Brawl Stars, лидер должен вернуться именно
            # в игровой клуб сети. Наличие в Telegram само по себе не отменяет
            # игровой выход. Для выхода из Telegram достаточно возвращения в
            # любой чат или подтверждённого присутствия в игре.
            recovered = in_game is True if reason == "brawl_stars" else (
                in_telegram or in_game is True
            )
            if recovered:
                cancel_pending_departure(user_id)
                logger.info(
                    f"Передача лидерства ID {user_id} отменена: "
                    f"Telegram={in_telegram}, BrawlStars={in_game}"
                )
                try:
                    await bot.send_message(
                        user_id,
                        "✅ Ожидание выхода завершено: ты снова найден в сети кланов, "
                        "поэтому лидерство составом сохранено.",
                    )
                except Exception:
                    pass
                continue

            # При временной ошибке Brawl Stars API не рискуем отдавать состав.
            if in_game is None:
                logger.warning(
                    f"Проверка лидера ID {user_id} отложена: Brawl Stars API не ответил"
                )
                continue

            result = finalize_pending_departure(user_id)
            if result.get("action") == "leadership_transferred":
                try:
                    await bot.send_message(
                        result["new_leader_id"],
                        f"👑 Ты стал лидером состава {result['league_name']}: "
                        "прежний лидер отсутствовал в кланах сети более 24 часов.",
                    )
                except Exception:
                    pass
                logger.info(
                    f"После 24 часов состав {result['league_name']} передан "
                    f"ID {result['new_leader_id']}"
                )
            elif result.get("action") == "dissolved":
                logger.info(
                    f"После 24 часов состав {result['league_name']} распущен: преемников нет"
                )
        except Exception as e:
            logger.error(f"Ошибка проверки ожидающего лидера ID {user_id}: {e}")


async def composition_departure_monitor_task(bot: Bot) -> None:
    await asyncio.sleep(60)
    while True:
        try:
            await process_due_composition_departures(bot)
        except Exception as e:
            logger.error(f"Ошибка фоновой проверки лидеров составов: {e}")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)