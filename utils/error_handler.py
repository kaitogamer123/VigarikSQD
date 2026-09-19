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

        # Пытаемся достать пользователя, у которого возникла ошибка
        user_info = "неизвестно"
        try:
            update = event.update
            if update.message and update.message.from_user:
                u = update.message.from_user
                user_info = f"@{u.username}" if u.username else f"ID:{u.id}"
            elif update.callback_query and update.callback_query.from_user:
                u = update.callback_query.from_user
                user_info = f"@{u.username}" if u.username else f"ID:{u.id}"
        except Exception:
            pass

        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        logger.error(
            f"❌ Необработанная ошибка при обработке апдейта.\n"
            f"👤 Пользователь: {user_info}\n"
            f"📝 Тип: {type(exc).__name__}: {exc}\n"
            f"🔻 Traceback:\n{tb}"
        )

        # Возвращаем True — говорим aiogram, что ошибка обработана и бот продолжает работу.
        return True
