"""
Утилита для синхронизации и бесшумного обновления текстовых списков в топиках групп.
Автоматически обновляет кубки всех игроков каждый час, выстраивает топ по трофеям внутри каждой роли
и выводит таймер до следующего апдейта.
"""

import logging
import asyncio
from datetime import datetime, timedelta
from aiogram import Bot
from aiogram.types import LinkPreviewOptions
from aiogram.exceptions import TelegramBadRequest

import database as db
from utils.formatting import format_roster
# ИСПРАВЛЕНО: Добавлен импорт CLAN_TAGS для исключения ошибки NameError в фоновом таске
from config import ROSTER_TOPICS, CLAN_CHATS, ROLES, CLAN_TAGS
from services.api_service import get_player_profile

logger = logging.getLogger(__name__)

# Переменная для хранения времени следующего глобального обновления кубков
NEXT_UPDATE_TIME = datetime.now() + timedelta(hours=1)

async def sync_roster_msg(bot: Bot, clan_key: str) -> None:
    """
    Генерирует актуальный список клана с таймером и сортировкой по кубкам внутри ролей.
    Мягко редактирует существующее сообщение в топике.
    """
    topic_info = ROSTER_TOPICS.get(clan_key)
    if not topic_info or not topic_info.get("chat_id") or not topic_info.get("thread_id"):
        return

    chat_id = topic_info["chat_id"]
    thread_id = topic_info["thread_id"]

    # 1. Получаем участников из БД
    members = await db.get_clan_members(clan_key)

    # 2. ИЕРАРХИЧЕСКАЯ СОРТИРОВКА ПО ТРОФЕЯМ ВНУТРИ РОЛЕЙ
    ROLE_ORDER = {
        "president": 1,
        "grand_vice_president": 2,
        "grand_vice": 3,
        "vice": 4,
        "helper": 5,
        "member": 6
    }

    # Сортируем: сначала по важности роли (1, 2, 3...),
    # а затем по кубкам от большего к меньшему (знак минус '-')
    members = sorted(
        members,
        key=lambda m: (
            ROLE_ORDER.get(m.get("role", "member"), 999),
            -m.get("trophies", 0)
        )
    )

    # 3. Форматируем отсортированный список в красивый HTML
    base_text = format_roster(clan_key, members)

    # 4. Рассчитываем оставшееся время для таймера
    now = datetime.now()
    if NEXT_UPDATE_TIME > now:
        diff = NEXT_UPDATE_TIME - now
        minutes, seconds = divmod(int(diff.total_seconds()), 60)
        timer_str = f"{minutes:02d}:{seconds:02d}"
    else:
        timer_str = "00:00 (Обновление...)"

    # Добавляем плашку таймера в самый низ текста
    final_text = f"{base_text}\n\n⏳ <i>Трофеи обновятся через: {timer_str}</i>"

    # 5. Получаем ID старого сообщения из базы данных
    raw_msg_id = await db.get_roster_message_id(clan_key)

    # ИСПРАВЛЕНО: Безопасное извлечение ID сообщения на случай возврата кортежа из SQLite
    if raw_msg_id is not None:
        msg_id = raw_msg_id[0] if isinstance(raw_msg_id, (tuple, list)) else raw_msg_id
    else:
        msg_id = None

    preview_options = LinkPreviewOptions(is_disabled=True)
    if msg_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=int(msg_id),
                text=final_text,
                parse_mode="HTML",
                link_preview_options=preview_options
            )
            return  # Успешно обновили, выходим из функции
        except TelegramBadRequest as e:
            if "message is not modified" in e.message.lower():
                return
            logger.warning(f"Не удалось отредактировать список {clan_key}, отправляем новый: {e.message}")
        except Exception as e:
            logger.error(f"Непредвиденная ошибка редактирования ростера {clan_key}: {e}")

    # 6. Если сообщения не было или оно удалено — отправляем заново
    try:
        new_msg = await bot.send_message(
            chat_id=chat_id,
            message_thread_id=thread_id,
            text=final_text,
            parse_mode="HTML",
            disable_notification=True,
            link_preview_options=preview_options
        )
        try:
            await bot.pin_chat_message(
                chat_id=chat_id,
                message_id=new_msg.message_id,
                disable_notification=True
            )
        except Exception as e:
            logger.warning(f"Не удалось закрепить сообщение ростера в {clan_key}: {e}")
        await db.save_roster_message_id(clan_key, new_msg.message_id)
    except Exception as e:
        logger.error(f"Критическая ошибка отправки нового списка в чат {clan_key}: {e}")


async def sync_all_rosters(bot: Bot) -> None:
    """Синхронизирует списки для всех кланов из конфигурации."""
    for clan_key in CLAN_CHATS.keys():
        await sync_roster_msg(bot, clan_key)


# ─── ФОНОВЫЕ ЗАДАЧИ АВТО-ОНОВЛЕНИЯ ДАННЫХ И ТАЙМЕРА ─────────────────────────

async def auto_update_trophies_task(bot: Bot) -> None:
    """
    Фоновый цикл: раз в час скачивает кубки игроков.
    Если игрок вышел из клуба в Brawl Stars — автоматически кикает его из Telegram-чата.
    """
    global NEXT_UPDATE_TIME
    await asyncio.sleep(10)
    while True:
        logger.info("Запуск планового ежечасного обновления кубков и проверки состава...")
        NEXT_UPDATE_TIME = datetime.now() + timedelta(hours=1)
        try:
            all_members = await db.get_all_members()
            for member in all_members:
                tag = member.get("player_tag")
                user_id = member.get("user_id")
                clan_key = member.get("clan")

                if tag and user_id:
                    profile = await get_player_profile(tag)
                    if profile:
                        api_club_tag = profile.get("clan_tag", "").upper().strip().replace("#", "")

                        # Теперь CLAN_TAGS импортирован корректно, ошибки не будет
                        configured_tag = CLAN_TAGS.get(clan_key, "").upper().strip().replace("#", "")

                        # ПРОВЕРКА: Если тег клуба из API не совпадает с настройками бота (игрок ливнул)
                        if api_club_tag != configured_tag:
                            logger.info(f"Игрок {member.get('game_nick')} вышел из клана {clan_key} в игре. Авто-кик из TG...")

                            # 1. Удаляем игрока из локальной SQLite базы данных
                            await db.remove_member(user_id)

                            # 2. Выгоняем его из соответствующего кланового Telegram-чата
                            topic_info = ROSTER_TOPICS.get(clan_key)
                            if topic_info and topic_info.get("chat_id"):
                                try:
                                    # Кикаем и сразу разбаниваем, чтобы мог войти заново при вступлении в игре
                                    await bot.ban_chat_member(chat_id=topic_info["chat_id"], user_id=user_id)
                                    await bot.unban_chat_member(chat_id=topic_info["chat_id"], user_id=user_id)
                                except Exception as e:
                                    logger.error(f"Не удалось кикнуть {user_id} из TG чата: {e}")
                            continue

                        # Если игрок на месте, точечно обновляем кубки и никнейм в SQLite
                        await db.upsert_member(
                            user_id=user_id,
                            game_nick=profile["name"],
                            player_tag=tag,
                            trophies=profile["trophies"],
                            ranked_elo=profile.get("ranked_elo", 0),
                            clan=clan_key,
                            role=member.get("role", "member"),
                            registered=member.get("registered", 1),
                            username=member.get("username"),
                            first_name=member.get("first_name"),
                            last_name=member.get("last_name")
                        )
                # Микро-пауза 0.5с между игроками, чтобы Brawl Stars API не выдал 429 Too Many Requests
                await asyncio.sleep(0.5)

            # После полного круга проверок перерисовываем все списки в каналах
            await sync_all_rosters(bot)

        except Exception as e:
            logger.error(f"Ошибка в фоновом цикле обновления кубков/киков: {e}")

        # Ждем ровно 1 час до следующего запуска
        await asyncio.sleep(3600)


async def auto_refresh_timer_task(bot: Bot) -> None:
    """
    Фоновый цикл: раз в минуту обновляет сообщения в Telegram-топиках,
    чтобы у игроков наглядно тикал живой таймер обратного отсчета кубков.
    """
    await asyncio.sleep(15)
    while True:
        try:
            await sync_all_rosters(bot)
        except Exception as e:
            logger.error(f"Ошибка в фоновом цикле обновления таймера: {e}")

        # Обновляем текст в Telegram строго каждые 60 секунд
        await asyncio.sleep(60)
