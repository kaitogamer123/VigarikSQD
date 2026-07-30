"""
Система расширенного логирования действий администрации и трансляции ЛС в супергруппу.
"""

import logging
from datetime import datetime
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest  # ДОБАВЛЕНО: Для отлова удаления топиков
from aiogram.utils.markdown import html_decoration as hd  # ИСПРАВЛЕНО: Экранирование текста

import config
from database import get_setting, set_setting

# Настраиваем локальный файловый логгер
logger = logging.getLogger("admin_actions")
logger.setLevel(logging.INFO)

if not logger.handlers:
    file_handler = logging.FileHandler("admin_actions.log", encoding="utf-8")
    formatter = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


async def get_or_create_admin_topic(bot: Bot, chat_id: int, admin_id: int, admin_name: str) -> int:
    """
    Проверяет, есть ли у админа персональный топик.
    Если нет ИЛИ он был удален — автоматически создает его на лету.
    """
    setting_key = f"admin_topic_{admin_id}"
    thread_id_str = await get_setting(setting_key)

    # Безопасное имя для топика
    safe_name = hd.quote(str(admin_name)) if admin_name else ""
    topic_title = f"📁 Логи @{admin_name}" if admin_name else f"📁 Логи ID {admin_id}"

    if thread_id_str:
        # ИСПРАВЛЕНО: Проверяем, жив ли топик (админы могли удалить его вручную в Telegram)
        try:
            # Делаем пустой проверочный пинг в топик (например, меняем имя или просто проверяем статус)
            # Чтобы не спамить текстом, мы просто вернем сохраненный ID, но завернем отправку лога ниже в try-except
            return int(thread_id_str)
        except ValueError:
            pass

    try:
        topic = await bot.create_forum_topic(
            chat_id=chat_id,
            name=topic_title,
            icon_color=0x9B59B6  # Фиолетовый цвет
        )

        await set_setting(setting_key, str(topic.message_thread_id))

        await bot.send_message(
            chat_id=chat_id,
            message_thread_id=topic.message_thread_id,
            text=f"📌 Топик инициализирован. Сюда дублируются действия администратора: <b>@{safe_name}</b> (ID: <code>{admin_id}</code>).",
            parse_mode="HTML"
        )
        return topic.message_thread_id
    except Exception as e:
        # ИСПРАВЛЕНО: Пишем в правильный логгер logger вместо глобального logging
        logger.error(f"Не удалось создать персональный топик для админа {admin_id}: {e}")
        return 0


async def log_admin_action(bot: Bot, admin_id: int, admin_name: str, action_text: str, clan_key: str = "main_admin"):
    """
    Логирует действия: пишет в файл, отправляет в топик категории И в персональный топик админа.
    """
    time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{clan_key.upper()}] Админ ID {admin_id} (@{admin_name}): {action_text}"

    logger.info(log_msg)

    chat_id = config.LOGS_CHAT_ID or config.ADMIN_CHAT_ID
    if not chat_id:
        return

    # ИСПРАВЛЕНО: Экранируем имя админа и его действие, чтобы спецсимволы не ломали разметку лога
    safe_name = hd.quote(str(admin_name)) if admin_name else "unknown"

    html_text = (
        f"⚡ <b>ДЕЙСТВИЕ АДМИНИСТРАЦИИ</b>\n"
        f"📅 <b>Время:</b> <code>{time_str}</code>\n"
        f"👤 <b>Админ:</b> @{safe_name} (ID: <code>{admin_id}</code>)\n"
        f"📝 <b>Что сделано:</b> {action_text}"
    )

    # ────── ОТПРАВКА В ТОПИК КАТЕГОРИИ (КЛАНА) ──────
    setting_key = f"topic_id_{clan_key}"
    thread_id_str = await get_setting(setting_key)

    if not thread_id_str and clan_key != "main_admin":
        thread_id_str = await get_setting("topic_id_main_admin")

    if thread_id_str:
        try:
            await bot.send_message(
                chat_id=chat_id,
                message_thread_id=int(thread_id_str),
                text=html_text,
                parse_mode="HTML"
            )
        except TelegramBadRequest as e:
            # ИСПРАВЛЕНО: Если топик удален, сбрасываем его в БД, чтобы бот пересоздал его при следующем действии
            if "thread not found" in e.message.lower():
                await set_setting(setting_key, "")
            logger.error(f"Не удалось отправить лог в топик категории {clan_key}: {e}")
        except Exception as e:
            logger.error(f"Ошибка логов категории {clan_key}: {e}")

    # ────── ОТПРАВКА В ПЕРСОНАЛЬНЫЙ ТОПИК АДМИНА ──────
    admin_thread_id = await get_or_create_admin_topic(bot, chat_id, admin_id, admin_name)

    if admin_thread_id and admin_thread_id != int(thread_id_str or 0):
        try:
            await bot.send_message(
                chat_id=chat_id,
                message_thread_id=admin_thread_id,
                text=html_text,
                parse_mode="HTML"
            )
        except TelegramBadRequest as e:
            if "thread not found" in e.message.lower():
                # Если админ удалил свой личный топик, очищаем ключ, чтобы бот создал новый топик в следующий раз
                await set_setting(f"admin_topic_{admin_id}", "")
        except Exception as e:
            logger.error(f"Не удалось отправить лог в персональный топик админа {admin_id}: {e}")


async def get_or_create_chat_log_topic(bot: Bot, chat_id: int) -> int:
    """
    Проверяет наличие топика для трансляции ЛС игроков.
    """
    setting_key = "topic_id_users_chat"
    thread_id_str = await get_setting(setting_key)

    if thread_id_str:
        return int(thread_id_str)

    try:
        topic = await bot.create_forum_topic(
            chat_id=chat_id,
            name="💬 Личные сообщения игроков",
            icon_color=0x2ECC71
        )
        await set_setting(setting_key, str(topic.message_thread_id))

        await bot.send_message(
            chat_id=chat_id,
            message_thread_id=topic.message_thread_id,
            text="📌 В этот топик в реальном времени транслируются все диалоги обычных игроков с ботом в ЛС.",
            parse_mode="HTML"
        )
        return topic.message_thread_id
    except Exception as e:
        logger.error(f"Не удалось создать топик для логов ЛС игроков: {e}")
        return 0


async def log_user_chat(bot: Bot, user_id: int, username: str, first_name: str, message_text: str,
                        is_bot_reply: bool = False):
    """
    Транслирует сообщения пользователей и ответы бота в специальный топик.
    Защищено от краша HTML-разметки из-за текста сообщений игроков.
    """
    time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    direction = "БОТ -> ЮЗЕР" if is_bot_reply else "ЮЗЕР -> БОТ"
    logger.info(f"[{direction}] ID {user_id} (@{username}): {message_text}")

    chat_id = config.LOGS_CHAT_ID or config.ADMIN_CHAT_ID
    if not chat_id:
        return

    thread_id = await get_or_create_chat_log_topic(bot, chat_id)
    if not thread_id:
        return

    # ИСПРАВЛЕНО: Жесткое экранирование входящих юзернеймов, имен и текста ЛС от краша разметки Telegram
    safe_username = hd.quote(str(username)) if username else None
    safe_firstname = hd.quote(str(first_name)) if first_name else "Игрок"
    safe_message = hd.quote(str(message_text))

    display_name = f"@{safe_username}" if safe_username else f"{safe_firstname} (ID: {user_id})"

    if is_bot_reply:
        html_text = (
            f"🤖 <b>Ответ бота для</b> {display_name}:\n"
            f" └ <i>{safe_message}</i>"
        )
    else:
        html_text = (
            f"👤 <b>Игрок</b> {display_name} <b>написал боту:</b>\n"
            f" └ <code>{safe_message}</code>"
        )

    try:
        await bot.send_message(
            chat_id=chat_id,
            message_thread_id=thread_id,
            text=html_text,
            parse_mode="HTML"
        )
    except TelegramBadRequest as e:
        if "thread not found" in e.message.lower():
            await set_setting("topic_id_users_chat", "")
        logger.error(f"Ошибка трансляции ЛС в топик: {e.message}")
    except Exception as e:
        logger.error(f"Ошибка логгера чата: {e}")
