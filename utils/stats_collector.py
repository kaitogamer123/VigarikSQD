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
    get_active_tags,
    get_tracked_player,
    init_player_stats_db,
    save_battlelog,
)
from services.api_service import get_player_battlelog, get_player_profile

logger = logging.getLogger(__name__)

COLLECT_INTERVAL_SECONDS = 10 * 60
PAUSE_BETWEEN_PLAYERS = 0.6

# Быстрый дозор за теми, кто играет прямо сейчас: раз в минуту проверяем
# только недавно активных игроков, чтобы не долбить API по всей базе.
ACTIVE_INTERVAL_SECONDS = 60
ACTIVE_WINDOW_MINUTES = 45
ACTIVE_TAGS_LIMIT = 30
ACTIVE_PAUSE = 0.25


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


async def collect_active_once() -> int:
    """
    Быстрый проход по недавно активным игрокам.

    Сначала тянем профиль (1 запрос). Battlelog запрашиваем только если
    изменились кубки или ELO — так на каждого активного игрока уходит
    1–2 запроса в минуту вместо постоянных двух.
    """
    tags = await get_active_tags(ACTIVE_WINDOW_MINUTES, ACTIVE_TAGS_LIMIT)
    updated = 0
    for tag in tags:
        try:
            tracked = await get_tracked_player(tag)
            profile = await get_player_profile(tag)
            if not profile:
                await asyncio.sleep(ACTIVE_PAUSE)
                continue

            elo_now = _safe_int(profile.get("ranked_elo")) or 0
            tr_now = _safe_int(profile.get("trophies")) or 0
            elo_prev = int((tracked or {}).get("last_ranked_elo") or 0)
            tr_prev = int((tracked or {}).get("last_trophies") or 0)

            # Ничего не поменялось — новый бой маловероятен, battlelog не тратим.
            if tracked and elo_now == elo_prev and tr_now == tr_prev:
                await asyncio.sleep(ACTIVE_PAUSE)
                continue

            battles = await get_player_battlelog(tag)
            await save_battlelog(
                tag,
                battles,
                current_ranked_elo=elo_now,
                current_trophies=tr_now,
            )
            updated += 1
        except Exception as e:
            logger.error(f"[stats_active] {tag}: {e}")
        await asyncio.sleep(ACTIVE_PAUSE)

    return updated


async def auto_collect_active_task(bot=None) -> None:
    """Проверка активных игроков раз в минуту (ELO/кубки почти по каждому бою)."""
    await init_player_stats_db()
    await asyncio.sleep(25)

    while True:
        try:
            updated = await collect_active_once()
            if updated:
                logger.info(f"[stats_active] обновлено игроков: {updated}")
        except Exception as e:
            logger.error(f"[stats_active] сбой прохода: {e}")

        await asyncio.sleep(ACTIVE_INTERVAL_SECONDS)


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
