"""Синхронизация закреплённых ростеров, кубков и таймера обновления."""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import LinkPreviewOptions

import database as db
from config import CLAN_CHATS, CLAN_TAGS, ROSTER_TOPICS
from services.api_service import get_player_profile
from utils.formatting import format_roster
from league.league_db import cancel_pending_departure, handle_clan_departure
from utils.clan_account_service import get_all_twinks, get_clan_twinks, update_twink

logger = logging.getLogger(__name__)
NEXT_UPDATE_TIME = datetime.now() + timedelta(hours=1)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _clean_tag(value: Any) -> str:
    return str(value or "").upper().strip().replace("#", "")


async def _remember_target(clan_key: str, chat_id: int, thread_id: int) -> None:
    await db.set_setting(f"roster_target_chat_{clan_key}", str(chat_id))
    await db.set_setting(f"roster_target_thread_{clan_key}", str(thread_id))


async def _target_is_current(clan_key: str, chat_id: int, thread_id: int) -> bool:
    stored_chat = _safe_int(await db.get_setting(f"roster_target_chat_{clan_key}"), 0)
    stored_thread = _safe_int(await db.get_setting(f"roster_target_thread_{clan_key}"), 0)
    return stored_chat == chat_id and stored_thread == thread_id


async def sync_roster_msg(bot: Bot, clan_key: str, force: bool = False) -> bool:
    """
    Обновляет ростер в настроенном топике. Если старое сообщение было создано
    в другом топике либо до сохранения target-ID, создаёт новое и открепляет старое.
    Возвращает True при успешном обновлении/создании.
    """
    topic_info = (ROSTER_TOPICS or {}).get(clan_key) or {}
    if not isinstance(topic_info, dict):
        logger.error(f"ROSTER_TOPICS[{clan_key}] должен быть словарём")
        return False

    chat_id = _safe_int(topic_info.get("chat_id"), 0)
    thread_id = _safe_int(topic_info.get("thread_id"), 0)
    if not chat_id or not thread_id:
        logger.error(f"Для ростера {clan_key} не настроены chat_id/thread_id")
        return False

    members = await db.get_clan_members(clan_key)
    # Твинки хранятся отдельно, но отображаются в общем ростере и ведут на
    # Telegram-профиль владельца.
    for twink in await get_clan_twinks(clan_key):
        members.append({
            "user_id": twink.get("owner_user_id"),
            "username": twink.get("username"),
            "first_name": twink.get("first_name"),
            "last_name": twink.get("last_name"),
            "game_nick": twink.get("game_nick"),
            "player_tag": twink.get("player_tag"),
            "trophies": twink.get("trophies", 0),
            "clan": clan_key,
            "role": twink.get("role") or "member",
            "registered": 1,
        })
    base_text = format_roster(clan_key, members)

    now = datetime.now()
    if NEXT_UPDATE_TIME > now:
        minutes, seconds = divmod(int((NEXT_UPDATE_TIME - now).total_seconds()), 60)
        timer_text = f"{minutes:02d}:{seconds:02d}"
    else:
        timer_text = "00:00 (обновление...)"

    final_text = f"{base_text}\n\n⏳ Трофеи обновятся через: {timer_text}"
    if len(final_text) > 4096:
        logger.error(
            f"Ростер {clan_key} слишком длинный: {len(final_text)} символов. "
            "Telegram допускает максимум 4096."
        )
        return False

    raw_message_id = await db.get_roster_message_id(clan_key)
    if isinstance(raw_message_id, (tuple, list)):
        raw_message_id = raw_message_id[0] if raw_message_id else None
    message_id = _safe_int(raw_message_id, 0)
    target_current = await _target_is_current(clan_key, chat_id, thread_id)
    preview = LinkPreviewOptions(is_disabled=True)

    # Старые версии сохраняли только message_id. При первом запуске новой версии
    # создаём ростер точно в актуальном топике и больше не плодим сообщения.
    if message_id and target_current:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=final_text,
                parse_mode="HTML",
                link_preview_options=preview,
            )
            await _remember_target(clan_key, chat_id, thread_id)
            logger.info(f"Ростер {clan_key} обновлён: участников {len(members)}")
            return True
        except TelegramBadRequest as e:
            message = str(getattr(e, "message", e)).lower()
            if "message is not modified" in message:
                await _remember_target(clan_key, chat_id, thread_id)
                return True
            logger.warning(f"Не удалось изменить ростер {clan_key}, создаю новый: {e}")
        except Exception as e:
            logger.error(f"Ошибка редактирования ростера {clan_key}: {e}")

    # Если старый ростер больше не является целевым, убираем его из закрепа.
    if message_id:
        try:
            await bot.unpin_chat_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass

    try:
        new_message = await bot.send_message(
            chat_id=chat_id,
            message_thread_id=thread_id,
            text=final_text,
            parse_mode="HTML",
            disable_notification=True,
            link_preview_options=preview,
        )
        try:
            await bot.pin_chat_message(
                chat_id=chat_id,
                message_id=new_message.message_id,
                disable_notification=True,
            )
        except Exception as e:
            logger.warning(f"Ростер {clan_key} создан, но не закреплён: {e}")

        await db.save_roster_message_id(clan_key, new_message.message_id)
        await _remember_target(clan_key, chat_id, thread_id)
        logger.info(
            f"Создан новый ростер {clan_key}: message_id={new_message.message_id}, "
            f"участников={len(members)}"
        )
        return True
    except Exception as e:
        logger.error(f"Не удалось создать ростер {clan_key}: {e}")
        return False


async def sync_roster_after_member_change(bot: Bot, clan_key: str, attempts: int = 3) -> bool:
    """Надёжное обновление сразу после регистрации/выхода участника."""
    if clan_key not in (ROSTER_TOPICS or {}):
        return False
    for attempt in range(1, attempts + 1):
        if await sync_roster_msg(bot, clan_key, force=True):
            return True
        if attempt < attempts:
            await asyncio.sleep(attempt * 2)
    logger.error(f"Ростер {clan_key} не обновился после {attempts} попыток")
    return False


async def sync_all_rosters(bot: Bot) -> None:
    """Обновляет ростеры всех настроенных кланов независимо друг от друга."""
    for clan_key in (ROSTER_TOPICS or {}).keys():
        try:
            await sync_roster_msg(bot, clan_key)
        except Exception as e:
            logger.error(f"sync_roster_msg({clan_key}) failed: {e}")


async def auto_update_trophies_task(bot: Bot) -> None:
    """Каждый час обновляет игровые ники и кубки, затем полностью перерисовывает ростеры."""
    global NEXT_UPDATE_TIME
    await asyncio.sleep(10)

    while True:
        NEXT_UPDATE_TIME = datetime.now() + timedelta(hours=1)
        logger.info("Запуск ежечасного обновления игровых ников и кубков...")
        try:
            members = await db.get_all_members() or []
            for member in members:
                tag = member.get("player_tag") if member else None
                user_id = member.get("user_id") if member else None
                clan_key = member.get("clan") if member else None
                if not tag or not user_id or clan_key not in (CLAN_TAGS or {}):
                    continue

                try:
                    profile = await get_player_profile(tag)
                    if not profile:
                        continue

                    api_club_tag = _clean_tag(profile.get("clan_tag"))
                    configured_tag = _clean_tag((CLAN_TAGS or {}).get(clan_key))
                    network_by_tag = {
                        _clean_tag(value): key
                        for key, value in (CLAN_TAGS or {}).items()
                        if value
                    }
                    actual_network_clan = network_by_tag.get(api_club_tag)

                    # Удаляем только при двух достоверных непустых тегах. Ошибка API
                    # либо пустой конфиг не должны случайно удалить участника.
                    if api_club_tag and configured_tag and api_club_tag != configured_tag:
                        # Переход между Squad/Academy/Events не является выходом
                        # из сети. Не удаляем и не кикаем: окончательное действие
                        # выберет администрация после входа игрока в новый чат.
                        if actual_network_clan:
                            cancel_pending_departure(user_id)
                            logger.info(
                                f"Игрок {member.get('game_nick')} перешёл из {clan_key} "
                                f"в {actual_network_clan}; ожидается решение администрации."
                            )
                            continue
                        logger.info(
                            f"Игрок {member.get('game_nick')} ({tag}) вышел из {clan_key}."
                        )
                        composition_result = handle_clan_departure(
                            user_id,
                            reason="brawl_stars",
                            old_clan=clan_key,
                            player_tag=tag,
                        )
                        if composition_result.get("action") == "leadership_pending":
                            logger.info(
                                f"Лидерство {composition_result['league_name']} сохранено до "
                                f"{composition_result.get('check_after')}"
                            )
                            continue
                        await db.remove_member(user_id)
                        chat_info = (CLAN_CHATS or {}).get(clan_key) or {}
                        chat_id = chat_info.get("chat_id") if isinstance(chat_info, dict) else None
                        if chat_id:
                            try:
                                await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                                await bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
                            except Exception as e:
                                logger.error(f"Не удалось удалить ID {user_id} из Telegram-чата: {e}")
                        continue

                    # Игрок снова подтверждён в своём клубе сети.
                    cancel_pending_departure(user_id)

                    await db.upsert_member(
                        user_id=user_id,
                        username=member.get("username"),
                        first_name=member.get("first_name"),
                        last_name=member.get("last_name"),
                        game_nick=profile.get("name") or member.get("game_nick"),
                        player_tag=tag,
                        trophies=_safe_int(profile.get("trophies"), 0),
                        ranked_elo=_safe_int(profile.get("ranked_elo"), 0),
                        clan=clan_key,
                        role=member.get("role") or "member",
                        registered=_safe_int(member.get("registered"), 1),
                    )
                except Exception as e:
                    logger.error(f"Ошибка обновления профиля {tag}: {e}")

                await asyncio.sleep(0.5)

            # Обновляем игровые ники и кубки твинков из отдельного хранилища.
            for twink in await get_all_twinks():
                try:
                    profile = await get_player_profile(twink.get("player_tag"))
                    if profile:
                        await update_twink(
                            twink.get("player_tag"),
                            profile.get("name") or twink.get("game_nick") or "Игрок",
                            _safe_int(profile.get("trophies"), 0),
                        )
                except Exception as e:
                    logger.error(f"Ошибка обновления твинка {twink.get('player_tag')}: {e}")
                await asyncio.sleep(0.5)

            await sync_all_rosters(bot)
        except Exception as e:
            logger.error(f"Ошибка ежечасного обновления игровых данных: {e}")

        await asyncio.sleep(3600)


async def auto_refresh_timer_task(bot: Bot) -> None:
    """Каждую минуту обновляет таймер и заодно подхватывает изменения состава."""
    await asyncio.sleep(15)
    while True:
        try:
            await sync_all_rosters(bot)
        except Exception as e:
            logger.error(f"Ошибка минутного обновления ростеров: {e}")
        await asyncio.sleep(60)