"""
Фоновый сборщик личной статистики Brawl Stars.
Каждые 10 минут сохраняет новые бои всех зарегистрированных игроков
в единственную накопительную базу player_stats.db.
"""
import asyncio
import logging

from database import get_all_members, update_member_ranked
from player_stats_db import (
    ensure_player_tracked,
    init_player_stats_db,
    save_battlelog,
)
from services.api_service import get_player_battlelog, get_player_profile

logger = logging.getLogger(__name__)

COLLECT_INTERVAL_SECONDS = 10 * 60
PAUSE_BETWEEN_PLAYERS = 0.6


def _safe_int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def collect_once() -> tuple[int, int]:
    """Один проход. Возвращает (обработано игроков, добавлено новых боёв)."""
    members = await get_all_members() or []
    processed = 0
    new_total = 0

    for member in members:
        if not member or member.get("registered") != 1:
            continue
        tag = member.get("player_tag")
        if not tag:
            continue

        try:
            # Профиль нужен и для ELO, и для дельты кубков по боям без trophyChange.
            profile = await get_player_profile(tag)
            battles = await get_player_battlelog(tag)
            new_total += await save_battlelog(
                tag,
                battles,
                current_ranked_elo=_safe_int((profile or {}).get("ranked_elo")),
                current_trophies=_safe_int((profile or {}).get("trophies")),
            )

            if profile:
                # Ранкед обновляем тем же проходом, что и кубки, но в ростер не выводим.
                await update_member_ranked(
                    member.get("user_id"),
                    ranked_elo=_safe_int(profile.get("ranked_elo")),
                    ranked_rank_name=profile.get("ranked_rank_name"),
                    ranked_season_max_rank=profile.get("ranked_season_max_rank"),
                    ranked_season_max_elo=_safe_int(profile.get("ranked_season_max_elo")),
                )
                await ensure_player_tracked(
                    tag,
                    profile.get("name") or member.get("game_nick") or "Игрок",
                    profile.get("trophies") or member.get("trophies") or 0,
                )
            processed += 1
        except Exception as e:
            logger.error(f"[stats_collector] {tag}: {e}")

        await asyncio.sleep(PAUSE_BETWEEN_PLAYERS)

    return processed, new_total


async def auto_collect_stats_task(bot=None) -> None:
    """Бесконечная фоновая задача, подключаемая в main.py."""
    await init_player_stats_db()
    await asyncio.sleep(40)

    while True:
        try:
            processed, new_total = await collect_once()
            logger.info(
                f"[stats_collector] игроков: {processed}, новых боёв: {new_total}"
            )
        except Exception as e:
            logger.error(f"[stats_collector] сбой прохода: {e}")

        await asyncio.sleep(COLLECT_INTERVAL_SECONDS)
