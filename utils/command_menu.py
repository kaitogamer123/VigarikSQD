"""Меню команд Telegram: подсказки при вводе «/» в личке и чатах."""
import logging

from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
)

logger = logging.getLogger(__name__)

# Команды для всех участников (личка и группы).
BASE_COMMANDS = [
    BotCommand(command="profilebs", description="Профиль Brawl Stars и статистика"),
    BotCommand(command="profilebsother", description="Профиль другого игрока"),
    BotCommand(command="inactive", description="Список неактивных участников"),
    BotCommand(command="mintop", description="Минималки и сколько осталось до нормы"),
    BotCommand(command="pushreport", description="Отчёт по минималкам обоих кланов"),
]

# Только в личке с ботом.
PRIVATE_COMMANDS = BASE_COMMANDS + [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="notify_on", description="Включить уведомления"),
    BotCommand(command="notify_off", description="Выключить уведомления"),
    BotCommand(command="eventstarted", description="Запустить выбор цели пуша"),
    BotCommand(command="start_season", description="Зафиксировать старт пуш-сезона"),
    BotCommand(command="check_season", description="Проверить итоги сезона"),
    BotCommand(command="set_tournament_topic", description="Настроить топик турниров"),
]

# В группах админам показываем расширенный список.
GROUP_ADMIN_COMMANDS = BASE_COMMANDS + [
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
        (BotCommandScopeAllChatAdministrators(), GROUP_ADMIN_COMMANDS),
    ]
    for scope, commands in scopes:
        try:
            await bot.set_my_commands(commands=commands, scope=scope)
        except Exception as e:
            logger.warning(f"Не удалось обновить меню команд ({type(scope).__name__}): {e}")
    logger.info("Меню команд Telegram обновлено")
