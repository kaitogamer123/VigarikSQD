"""Меню команд Telegram: подсказки при вводе «/» в личке и чатах."""
import logging

from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
)

from config import OWNER_IDS

logger = logging.getLogger(__name__)

# Команды для всех участников (личка и группы).
BASE_COMMANDS = [
    BotCommand(command="profilebs", description="Профиль Brawl Stars и статистика"),
    BotCommand(command="profilebsother", description="Профиль другого игрока"),
    BotCommand(command="inactive", description="Список неактивных участников"),
    BotCommand(command="mintop", description="Минималки и сколько осталось до нормы"),
    BotCommand(command="pushreport", description="Отчёт по минималкам обоих кланов"),
]

# Личка обычных участников: только личные команды.
PRIVATE_COMMANDS = BASE_COMMANDS + [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="notify_on", description="Включить уведомления"),
    BotCommand(command="notify_off", description="Выключить уведомления"),
]

# Личка владельцев: плюс админские команды.
OWNER_COMMANDS = PRIVATE_COMMANDS + [
    BotCommand(command="eventstarted", description="Запустить выбор цели пуша"),
    BotCommand(command="start_season", description="Зафиксировать старт пуш-сезона"),
    BotCommand(command="check_season", description="Проверить итоги сезона"),
    BotCommand(command="set_tournament_topic", description="Настроить топик турниров"),
]


async def setup_command_menu(bot: Bot) -> None:
    """Регистрирует команды, чтобы клиент Telegram предлагал их при вводе «/»."""
    scopes = [
        (BotCommandScopeDefault(), BASE_COMMANDS),
        (BotCommandScopeAllPrivateChats(), PRIVATE_COMMANDS),
        (BotCommandScopeAllGroupChats(), BASE_COMMANDS),
    ]
    for scope, commands in scopes:
        try:
            await bot.set_my_commands(commands=commands, scope=scope)
        except Exception as e:
            logger.warning(f"Не удалось обновить меню команд ({type(scope).__name__}): {e}")

    # Админские команды — только в личке владельцев.
    for owner_id in OWNER_IDS:
        try:
            await bot.set_my_commands(
                commands=OWNER_COMMANDS,
                scope=BotCommandScopeChat(chat_id=int(owner_id)),
            )
        except Exception as e:
            logger.warning(f"Не удалось обновить меню команд владельца {owner_id}: {e}")

    # Убираем старый общий админский набор, если он был выставлен раньше.
    try:
        await bot.delete_my_commands(scope=BotCommandScopeAllChatAdministrators())
    except Exception as e:
        logger.warning(f"Не удалось очистить меню команд админов чатов: {e}")

    logger.info("Меню команд Telegram обновлено")

