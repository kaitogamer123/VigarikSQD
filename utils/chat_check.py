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
            logger.info(f"👤 {user_id} в INITIAL_ADMINS -> возвращаю клан: {allocated_clan}")
            return [allocated_clan]
        logger.info(f"👤 {user_id} в INITIAL_ADMINS БЕЗ clan -> возвращаю ВСЕ кланы")
        return list(CLAN_CHATS.keys())

    user_clans = []
    logger.info(f"🔎 Проверяю {user_id} в каждом чате клана...")

    for clan_key, chat_info in CLAN_CHATS.items():
        try:
            member = await bot.get_chat_member(chat_id=chat_info["chat_id"], user_id=user_id)
            
            logger.info(f"   └─ Клан '{clan_key}' (ID: {chat_info['chat_id']})")
            logger.info(f"      📊 Статус: {member.status}")
            logger.info(f"      🎖️  Админ: {member.is_member if hasattr(member, 'is_member') else 'N/A'}")

            # ИСПРАВЛЕНО: Строгое отсечение неактивных статусов (защита от багов кэша Telegram)
            if member.status in ["left", "kicked"]:
                logger.info(f"      ❌ Статус 'left/kicked' - пропускаю")
                continue

            if member.status in ["creator", "owner", "administrator", "member", "restricted"]:
                logger.info(f"      ✅ НАЙДЕН в этом клане!")
                user_clans.append(clan_key)
            else:
                logger.info(f"      ⚠️ Неизвестный статус '{member.status}' - пропускаю")

        except TelegramBadRequest as e:
            # ИСПРАВЛЕНО: Логируем, если бот потерял доступ к какому-то из клановых чатов
            logger.error(f"   └─ ❌ БОТ БЕЗ ДОСТУПА к чату клана {clan_key} (ID: {chat_info['chat_id']}) - {e.message}")
            continue
        except Exception as e:
            logger.error(f"   └─ ❌ Непредвиденная ошибка при проверке {clan_key}: {e}")
            continue

    logger.info(f"📊 ИТОГ: {user_id} найден в кланах: {user_clans if user_clans else 'НИЧЕГО'}")
    return user_clans


async def is_chat_admin(bot: Bot, user_id: int) -> bool:
    """
    Определяет администратора по принципу его нахождения в ADMIN_CHAT_ID.
    """
    if user_id in INITIAL_ADMINS:
        logger.info(f"👤 {user_id} в INITIAL_ADMINS -> ✅ админ")
        return True

    if not ADMIN_CHAT_ID:
        logger.warning(f"⚠️ ADMIN_CHAT_ID не установлен в config.py")
        return False

    try:
        logger.info(f"🔎 Проверяю статус {user_id} в админ-чате (ID: {ADMIN_CHAT_ID})...")
        member = await bot.get_chat_member(chat_id=ADMIN_CHAT_ID, user_id=user_id)
        
        logger.info(f"   📊 Статус: {member.status}")

        # ИСПРАВЛЕНО: Исключаем вышедших участников из админ-прав
        if member.status in ["left", "kicked"]:
            logger.info(f"   ❌ Статус 'left/kicked' -> НЕ админ")
            return False

        is_admin = member.status in ["owner", "administrator"]
        logger.info(f"   {'✅' if is_admin else '❌'} Статус подходит для админа: {is_admin}")
        return is_admin
        
    except TelegramBadRequest as e:
        logger.error(f"❌ БОТ БЕЗ ДОСТУПА к админ-чату (ADMIN_CHAT_ID: {ADMIN_CHAT_ID}). Проверьте, добавлен ли бот туда. Ошибка: {e.message}")
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке админ-чата: {e}")
        return False
