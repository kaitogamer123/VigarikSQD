"""
Утилита для синхронизации и бесшумного обновления текстовых списков в топиках групп.
ИСПРАВЛЕНО: жёсткая защита от None в ROSTER_TOPICS / CLAN_TAGS / profile.clan_tag,
не падаем даже если конфиг неполный.
"""
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from aiogram import Bot
from aiogram.types import LinkPreviewOptions
from aiogram.exceptions import TelegramBadRequest

import database as db
from utils.formatting import format_roster
from config import ROSTER_TOPICS, CLAN_CHATS, ROLES, CLAN_TAGS
from services.api_service import get_player_profile

logger = logging.getLogger(__name__)

# Время следующего глобального обновления кубков
NEXT_UPDATE_TIME = datetime.now() + timedelta(hours=1)


def _safe_upper(value: Any) -> str:
    """Никогда не падает на None — превращает любое значение в безопасную строку."""
    if value is None:
        return ""
    return str(value).upper().strip().replace("#", "")


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return default


async def sync_roster_msg(bot: Bot, clan_key: str) -> None:
    """Генерирует актуальный список клана с таймером и сортировкой по кубкам внутри ролей."""
    topic_info = (ROSTER_TOPICS or {}).get(clan_key) or {}
    chat_id = topic_info.get("chat_id") if isinstance(topic_info, dict) else None
    thread_id = topic_info.get("thread_id") if isinstance(topic_info, dict) else None
    if not chat_id or not thread_id:
        return

    members = await db.get_clan_members(clan_key)

    ROLE_ORDER = {
        "president": 1,
        "grand_vice_president": 2,
        "grand_vice": 3,
        "vice": 4,
        "helper": 5,
        "member": 6,
    }
    members = sorted(
        members,
        key=lambda m: (
            ROLE_ORDER.get((m or {}).get("role", "member"), 999),
            -_safe_int((m or {}).get("trophies"), 0),
        ),
    )

    base_text = format_roster(clan_key, members)

    now = datetime.now()
    if NEXT_UPDATE_TIME > now:
        diff = NEXT_UPDATE_TIME - now
        minutes, seconds = divmod(int(diff.total_seconds()), 60)
        timer_str = f"{minutes:02d}:{seconds:02d}"
    else:
        timer_str = "00:00 (Обновление...)"

    final_text = f"{base_text}\n\n⏳ Трофеи обновятся через: {timer_str} "

    raw_msg_id = await db.get_roster_message_id(clan_key)
    if raw_msg_id is not None:
        msg_id = raw_msg_id[0] if isinstance(raw_msg_id, (tuple, list)) else raw_msg_id
    else:
        msg_id = None
    msg_id = _safe_int(msg_id, 0) or None

    preview_options = LinkPreviewOptions(is_disabled=True)

    if msg_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=final_text,
                parse_mode="HTML",
                link_preview_options=preview_options,
            )
            return
        except TelegramBadRequest as e:
            if e.message and "message is not modified" in e.message.lower():
                return
            logger.warning(f"Не удалось отредактировать список {clan_key}, отправляем новый: {e.message}")
        except Exception as e:
            logger.error(f"Непредвиденная ошибка редактирования ростера {clan_key}: {e}")

    try:
        new_msg = await bot.send_message(
            chat_id=chat_id,
            message_thread_id=thread_id,
            text=final_text,
            parse_mode="HTML",
            disable_notification=True,
            link_preview_options=preview_options,
        )
        try:
            await bot.pin_chat_message(
                chat_id=chat_id,
                message_id=new_msg.message_id,
                disable_notification=True,
            )
        except Exception as e:
            logger.warning(f"Не удалось закрепить сообщение ростера в {clan_key}: {e}")
        await db.save_roster_message_id(clan_key, new_msg.message_id)
    except Exception as e:
        logger.error(f"Критическая ошибка отправки нового списка в чат {clan_key}: {e}")


async def sync_all_rosters(bot: Bot) -> None:
    """Синхронизирует списки для всех кланов из конфигурации."""
    for clan_key in (CLAN_CHATS or {}).keys():
        try:
            await sync_roster_msg(bot, clan_key)
        except Exception as e:
            logger.error(f"sync_roster_msg({clan_key}) failed: {e}")


async def auto_update_trophies_task(bot: Bot) -> None:
    """Фоновый цикл: раз в час опрашивает Brawl Stars API и кикает ушедших из клубов."""
    global NEXT_UPDATE_TIME
    await asyncio.sleep(10)

    while True:
        logger.info("Запуск планового ежечасного обновления кубков и проверки состава...")
        NEXT_UPDATE_TIME = datetime.now() + timedelta(hours=1)

        try:
            all_members = await db.get_all_members() or []
            for member in all_members:
                if not member:
                    continue
                tag = member.get("player_tag")
                user_id = member.get("user_id")
                clan_key = member.get("clan")
                if not tag or not user_id or not clan_key:
                    continue

                try:
                    profile = await get_player_profile(tag)
                except Exception as e:
                    logger.error(f"Ошибка API для тега {tag}: {e}")
                    continue

                if not profile:
                    await asyncio.sleep(0.3)
                    continue

                # >>> ИСПРАВЛЕНО: защита от None в любых полях профиля и конфига
                api_club_tag = _safe_upper(profile.get("clan_tag"))
                configured_tag = _safe_upper((CLAN_TAGS or {}).get(clan_key))

                if api_club_tag != configured_tag:
                    logger.info(
                        f"Игрок {member.get('game_nick')} (тег {tag}) вне нашего клана. Авто-кик..."
                    )
                    try:
                        await db.remove_member(user_id)
                    except Exception as e:
                        logger.error(f"Не удалось удалить {user_id} из БД: {e}")

                    topic_info = (ROSTER_TOPICS or {}).get(clan_key) or {}
                    tg_chat_id = topic_info.get("chat_id") if isinstance(topic_info, dict) else None
                    if tg_chat_id:
                        try:
                            await bot.ban_chat_member(chat_id=tg_chat_id, user_id=user_id)
                            await bot.unban_chat_member(chat_id=tg_chat_id, user_id=user_id)
                        except Exception as e:
                            logger.error(f"Не удалось кикнуть {user_id} из TG чата: {e}")
                    continue

                try:
                    await db.upsert_member(
                        user_id=user_id,
                        game_nick=profile.get("name") or member.get("game_nick"),
                        player_tag=tag,
                        trophies=_safe_int(profile.get("trophies"), 0),
                        ranked_elo=_safe_int(profile.get("ranked_elo"), 0),
                        clan=clan_key,
                        role=member.get("role") or "member",
                        registered=member.get("registered", 1),
                        username=member.get("username"),
                        first_name=member.get("first_name"),
                        last_name=member.get("last_name"),
                    )
                except Exception as e:
                    logger.error(f"Не удалось обновить кубки {user_id}: {e}")

                await asyncio.sleep(0.5)

            try:
                await sync_all_rosters(bot)
            except Exception as e:
                logger.error(f"sync_all_rosters после обновления упал: {e}")

        except Exception as e:
            logger.error(f"Ошибка в фоновом цикле обновления кубков/киков: {e}")

        await asyncio.sleep(3600)


async def auto_refresh_timer_task(bot: Bot) -> None:
    """Каждую минуту обновляет список с таймером."""
    await asyncio.sleep(15)
    while True:
        try:
            await sync_all_rosters(bot)
        except Exception as e:
            logger.error(f"Ошибка в фоновом цикле обновления таймера: {e}")
        await asyncio.sleep(60)
