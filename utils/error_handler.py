"""
Единый модуль подключения логирования сообщений и глобального перехвата ошибок.

Использование в main.py (внутри async def main(), где создан dp):

    from utils.error_handler import setup_logging_and_errors
    setup_logging_and_errors(dp)

Это заменяет строку:
    dp.message.outer_middleware(ChatLoggingMiddleware())
"""
import logging
import traceback

from aiogram import Dispatcher
from aiogram.types import ErrorEvent

from utils.chat_middleware import ChatLoggingMiddleware

logger = logging.getLogger(__name__)


def setup_logging_and_errors(dp: Dispatcher) -> None:
    """Подключает слежку за ВСЕМИ сообщениями/кнопками и глобальный обработчик ошибок."""

    # 1. Логируем ВСЕ входящие сообщения игроков (текст, медиа, команды)
    dp.message.outer_middleware(ChatLoggingMiddleware())
    # 2. Логируем нажатия инлайн-кнопок в ЛС
    dp.callback_query.outer_middleware(ChatLoggingMiddleware())

    # 3. Глобальный перехват ошибок: пишем ПОЛНЫЙ traceback в консоль/лог-файл.
    #    Это позволяет увидеть точную строку, где возникает 'int(None)' или 'NoneType subscriptable',
    #    вместо голого текста ошибки.
    @dp.errors()
    async def global_error_handler(event: ErrorEvent):
        exc = event.exception

        user_id = None
        username = None
        try:
            update = event.update
            u = None
            if update.message and update.message.from_user:
                u = update.message.from_user
            elif update.callback_query and update.callback_query.from_user:
                u = update.callback_query.from_user
            if u:
                user_id = u.id
                username = u.username
        except Exception:
            pass

        user_info = f"@{username}" if username else (f"ID:{user_id}" if user_id else "неизвестно")
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        logger.error(
            f"❌ Необработанная ошибка при обработке апдейта.\n"
            f"👤 Пользователь: {user_info}\n"
            f"📝 Тип: {type(exc).__name__}: {exc}\n"
            f"🔻 Traceback:\n{tb}"
        )

        # Дублируем краткую ошибку в админ-чат (как раньше через log_bot_event)
        try:
            from utils.admin_logger import log_bot_event
            bot = None
            try:
                if event.update.message:
                    bot = event.update.message.bot
                elif event.update.callback_query:
                    bot = event.update.callback_query.bot
            except Exception:
                bot = None
            if bot:
                await log_bot_event(
                    bot=bot,
                    event_type="error",
                    description=f"Ошибка при обработке сообщения: {type(exc).__name__}: {exc}",
                    user_id=user_id,
                    username=username,
                )
        except Exception as e:
            logger.error(f"Не удалось отправить ошибку в админ-чат: {e}")

        return True
