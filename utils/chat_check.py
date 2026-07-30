"""
Утилиты для проверки членства пользователя в чатах кланов и администрации.
"""

import logging
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from config import CLAN_CHATS, ADMIN_CHAT_ID, INITIAL_ADMINS

logger = logging.getLogger(__name__)


async def get_user_clans(bot: Bot, user_id: int) -> list[str]:
    """
    Проверяет, в каких клановых чатах состоит пользователь.
    Возвращает список ключей кланов (например: ['squad', 'academy']).
    """
    # Сначала проверяем жестко заданных в конфиге создателей
    if user_id in INITIAL_ADMINS:
        allocated_clan = INITIAL_ADMINS[user_id].get("clan")
        # Если у лидера в конфиге уже прописан его клан, сразу возвращаем его
        if allocated_clan and allocated_clan in CLAN_CHATS:
            return [allocated_clan]
        return list(CLAN_CHATS.keys())

    user_clans = []

    for clan_key, chat_info in CLAN_CHATS.items():
        try:
            member = await bot.get_chat_member(chat_id=chat_info["chat_id"], user_id=user_id)

            # ИСПРАВЛЕНО: Строгое отсечение неактивных статусов (защита от багов кэша Telegram)
            if member.status in ["left", "kicked"]:
                continue

            if member.status in ["owner", "administrator", "member", "restricted"]:
                user_clans.append(clan_key)

        except TelegramBadRequest as e:
            # ИСПРАВЛЕНО: Логируем, если бот потерял доступ к какому-то из клановых чатов
            logger.error(f"Бот не имеет доступа к чату клана {clan_key} (ID: {chat_info['chat_id']}). Ошибка: {e.message}")
            continue
        except Exception as e:
            logger.error(f"Непредвиденная ошибка при проверке чата {clan_key}: {e}")
            continue

    return user_clans


async def is_chat_admin(bot: Bot, user_id: int) -> bool:
    """
    Определяет администратора по принципу его нахождения в ADMIN_CHAT_ID.
    """
    if user_id in INITIAL_ADMINS:
        return True

    if not ADMIN_CHAT_ID:
        return False

    try:
        member = await bot.get_chat_member(chat_id=ADMIN_CHAT_ID, user_id=user_id)

        # ИСПРАВЛЕНО: Исключаем вышедших участников из админ-прав
        if member.status in ["left", "kicked"]:
            return False

        return member.status in ["owner", "administrator"]
    except TelegramBadRequest as e:
        logger.error(f"Бот не может проверить админ-чат (ADMIN_CHAT_ID: {ADMIN_CHAT_ID}). Проверьте, добавлен ли бот туда. Ошибка: {e.message}")
        return False
    except Exception:
        return False
