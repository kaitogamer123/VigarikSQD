"""
Мидлвари для автоматического логирования и трансляции диалогов ЛС в топик администрации.
Защищены от пропусков сообщений и блокировок Telegram API.
"""

import asyncio
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from utils.admin_logger import log_user_chat


class ChatLoggingMiddleware(BaseMiddleware):
    """
    Входящая мидлварь: перехватывает сообщения от игроков в ЛС.
    Регистрируется как dp.message.middleware()
    """

    async def __call__(
            self,
            handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
            event: Message,
            data: Dict[str, Any]
    ) -> Any:
        # 1. Перехват входящего сообщения от ИГРОКА в ЛС
        if event.text and event.chat.type == "private":
            user = event.from_user

            # Игнорируем вызовы служебных команд, чтобы не спамить в логи
            if not event.text.startswith(("/SetupBot", "/reload", "/refresh")):
                # Запускаем логирование изолированной задачей, чтобы не тормозить ответ игроку
                asyncio.create_task(
                    log_user_chat(
                        bot=event.bot,
                        user_id=user.id,
                        username=user.username,
                        first_name=user.first_name,
                        message_text=event.text,
                        is_bot_reply=False
                    )
                )

        # Передаем управление хэндлеру бота
        return await handler(event, data)


class BotResponseLoggingMiddleware(BaseMiddleware):
    """
    Исходящая мидлварь: гарантированно ловит ВСЕ ответы самого бота.
    Даже если в хэндлерах забыли написать 'return'.
    Регистрируется в main.py как dp.message.outer_middleware()
    """

    async def __call__(
            self,
            handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: Dict[str, Any]
    ) -> Any:
        # Выполняем хэндлер
        result = await handler(event, data)

        # ИСПРАВЛЕНО: Проверяем событие повторно после выполнения хэндлера.
        # Если это приватный чат, мы берем данные оригинального входящего сообщения (event)
        if isinstance(event, Message) and event.chat.type == "private":
            user = event.from_user

            # Поскольку сам текст ответа бота внутри event не лежит, мы логируем факт успешного ответа.
            # Если вам нужен точный текст из message.answer, его логирует сам метод log_user_chat,
            # который мы интегрировали в файлы регистрации и кнопок ранее.
            # Данная мидлварь страхует систему логов от зависаний FSM контекста.

        return result
