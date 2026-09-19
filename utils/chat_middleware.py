"""
Мидлвари для автоматического логирования и трансляции диалогов ЛС в топик администрации.
Защищены от пропусков сообщений и блокировок Telegram API.

ВОССТАНОВЛЕНО: полноценная "слежка" за ВСЕМИ сообщениями пользователей боту в ЛС
(текст, команды, фото/видео/стикеры/голосовые и т.д.), а также нажатия инлайн-кнопок.
"""

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject

from utils.admin_logger import log_user_chat

logger = logging.getLogger(__name__)


def _describe_non_text(message: Message) -> str:
    """Возвращает человекочитаемое описание не-текстового сообщения для лога."""
    if message.photo:
        base = "🖼 [Фото]"
    elif message.video:
        base = "🎬 [Видео]"
    elif message.video_note:
        base = "⭕ [Видео-кружок]"
    elif message.voice:
        base = "🎤 [Голосовое сообщение]"
    elif message.audio:
        base = "🎵 [Аудио]"
    elif message.document:
        base = "📎 [Документ]"
    elif message.sticker:
        base = f"🩹 [Стикер {message.sticker.emoji or ''}]"
    elif message.animation:
        base = "🎞 [GIF]"
    elif message.contact:
        base = "📞 [Контакт]"
    elif message.location:
        base = "📍 [Геолокация]"
    elif message.poll:
        base = "📊 [Опрос]"
    else:
        base = "📦 [Вложение]"

    caption = message.caption
    if caption:
        return f"{base} {caption.strip()}"
    return base


class ChatLoggingMiddleware(BaseMiddleware):
    """
    Входящая мидлварь: перехватывает ВСЕ сообщения и колбэки от игроков в ЛС.
    Регистрируется как dp.message.outer_middleware() и dp.callback_query.outer_middleware()
    """

    async def __call__(
            self,
            handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: Dict[str, Any],
    ) -> Any:
        # Логирование обёрнуто в собственный try/except, чтобы сбой логгера
        # НИКОГДА не ломал обработку самого сообщения пользователем.
        try:
            await self._safe_log(event)
        except Exception as e:
            logger.error(f"[ChatLoggingMiddleware] Сбой логирования события: {e}")

        # Передаём управление хэндлеру бота в любом случае
        return await handler(event, data)

    async def _safe_log(self, event: TelegramObject) -> None:
        # ─── ОБЫЧНЫЕ СООБЩЕНИЯ В ЛС ─────────────────────────────
        if isinstance(event, Message):
            user = event.from_user
            # Защита от служебных сообщений без автора (каналы, миграции и т.п.)
            if user is None:
                return
            # Логируем только личные сообщения игроков боту
            if event.chat.type != "private":
                return

            # Текст, подпись к медиа или описание вложения — логируем ВСЁ
            if event.text:
                message_text = event.text
            elif event.caption:
                message_text = _describe_non_text(event)
            else:
                message_text = _describe_non_text(event)

            await log_user_chat(
                bot=event.bot,
                user_id=user.id,
                username=user.username,
                first_name=user.first_name,
                message_text=message_text,
                is_bot_reply=False,
            )
            return

        # ─── НАЖАТИЯ ИНЛАЙН-КНОПОК В ЛС ─────────────────────────
        if isinstance(event, CallbackQuery):
            user = event.from_user
            if user is None:
                return
            msg = event.message
            # Логируем только если кнопка нажата в личке
            if msg is None or getattr(msg.chat, "type", None) != "private":
                return

            await log_user_chat(
                bot=event.bot,
                user_id=user.id,
                username=user.username,
                first_name=user.first_name,
                message_text=f"🔘 [Нажал кнопку] {event.data}",
                is_bot_reply=False,
            )
            return


class BotResponseLoggingMiddleware(BaseMiddleware):
    """
    Исходящая мидлварь-заглушка. Оставлена для совместимости.
    Логирование ответов бота выполняется точечно через log_user_chat(is_bot_reply=True)
    в местах, где боту важно зафиксировать ответ.
    """

    async def __call__(
            self,
            handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: Dict[str, Any],
    ) -> Any:
        return await handler(event, data)
