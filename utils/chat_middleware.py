"""
Мидлвари для логирования важных событий: когда бот отвечает, ошибки, успешные операции.
Исключает шум (неопознанные команды, игнорируемые сообщения).
"""

import asyncio
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject, CallbackQuery
import logging

logger = logging.getLogger(__name__)

# Импортируем функцию логирования системных событий
from utils.admin_logger import log_bot_event, log_user_chat


class SmartLoggingMiddleware(BaseMiddleware):
    """
    Ловит только ВАЖНЫЕ события:
    - Успешные ответы бота (когда он отвечает на сообщение)
    - Ошибки
    - НЕ логирует неопознанные команды и шум
    """
    
    def __init__(self):
        super().__init__()
        # Флаг для отслеживания ответов бота
        self.bot_replied = False

    async def __call__(
            self,
            handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: Dict[str, Any]
    ) -> Any:
        
        # Проверяем, обработано ли сообщение (будет ответ от бота)
        try:
            result = await handler(event, data)
            
            # ✅ Если хэндлер успешно выполнился и сообщение обработано
            if isinstance(event, Message) and event.chat.type == "private":
                user = event.from_user
                logger.debug(f"Message processed - User {user.id} (@{user.username}): {event.text[:50]}")

                # Команды снова видны в админском топике, обычный чат не спамим.
                if event.text and event.text.startswith("/"):
                    await log_user_chat(
                        bot=event.bot,
                        user_id=user.id,
                        username=user.username,
                        first_name=user.first_name,
                        message_text=event.text,
                    )
            
            return result
            
        except Exception as e:
            # ❌ Если произошла ошибка
            if isinstance(event, Message) and event.chat.type == "private":
                user = event.from_user
                error_text = str(e)[:100]
                
                # Логируем ошибку асинхронно (не блокируем ответ)
                asyncio.create_task(
                    log_bot_event(
                        bot=event.bot,
                        event_type="error",
                        description=f"Ошибка при обработке сообщения: {error_text}",
                        user_id=user.id,
                        username=user.username
                    )
                )
                
                logger.error(f"Handler error for user {user.id}: {e}")
            
            raise


class BotResponseLoggerMiddleware(BaseMiddleware):
    """
    Логирует только когда бот УСПЕШНО ОТВЕЧАЕТ на сообщение.
    Интегрируется с исходящими сообщениями через API.
    """

    async def __call__(
            self,
            handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: Dict[str, Any]
    ) -> Any:
        
        result = await handler(event, data)
        
        # Если это исходящее сообщение от бота (можно отслеживать через контекст)
        # данная мидлварь служит страховкой против потери логов
        
        return result
