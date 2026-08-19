"""
Главный файл запуска Telegram-бота ViGarik Squad.
Полная исправленная версия с ежечасным обновлением трофеев и таймером.
"""

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Message
from utils.chat_middleware import ChatLoggingMiddleware

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from utils.username_monitor import check_and_update_usernames
from config import TOKEN
from database import init_db

# Импортируем правильную утилиту синхронизации и фоновые задачи
from utils.roster_sync import sync_all_rosters, auto_update_trophies_task, auto_refresh_timer_task

# Хэндлеры базовой системы
from handlers.change_name import router as change_name_router
from handlers.start import router as start_router
from handlers.registration import router as reg_router
from handlers.proposals import router as proposals_router
from push_system import push_system_router
from handlers.chat_events import router as chat_router
from handlers.clan_list import router as clan_list_router
from league.handlers import router as league_router
from game_database import init_game_db
from Commands.trophies_game import router as trophies_router
from Commands.inactive import router as inactive_router

# Модульный админ-роутер
from handlers.admin_features import admin_main_router

logging.basicConfig(level=logging.INFO)

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()


@dp.startup()
async def on_startup():
    """Выполняет технические задачи при старте сервера."""
    await init_db()
    await init_game_db()
    logging.info("Database initialized successfully.")

    try:
        await sync_all_rosters(bot)
        logging.info("Initial roster sync done")
    except Exception as e:
        logging.error(f"Failed to sync rosters on startup: {e}")


async def main():
    dp.message.outer_middleware(ChatLoggingMiddleware())

    # ─── БЛОК СИСТЕМНЫХ КОМАНД СТРОГО ДЛЯ ВЛАДЕЛЬЦА @Ka1D3en (ID: 7899153362) ─

    @dp.message(F.text.in_({"/reload_config", "reload_config", "/reload_config@Vigarik_Sqd_bot"}))
    async def cmd_reload_config_direct(message: Message):
        if message.from_user.id != 7899153362:
            return
        try:
            from utils.admin_logger import reload_bot_config, log_admin_action
            reload_bot_config()
            await message.answer(
                "🔄 <b>Конфигурация бота успешно обновлена!</b>\nНовые роли и настройки топиков вступили в силу.",
                parse_mode="HTML")
            await log_admin_action(
                bot=message.bot,
                admin_id=message.from_user.id,
                admin_name=message.from_user.username or "Ka1D3en",
                action_text="⚙️ Выполнил принудительную <b>перезагрузку файла конфигурации</b> (config.py) на лету."
            )
        except Exception as e:
            await message.answer(f"❌ Ошибка при перезагрузке файла конфигурации: {e}")

    @dp.message(F.text.in_({"/SetupBotVigarikThreads", "SetupBotVigarikThreads", "/SetupBotVigarikThreads@Vigarik_Sqd_bot"}))
    async def cmd_setup_threads_direct(message: Message, bot: Bot):
        if message.from_user.id != 7899153362:
            return
        import config
        from database import set_setting
        chat_id = config.LOGS_CHAT_ID or config.ADMIN_CHAT_ID
        if not chat_id:
            await message.answer("❌ Сначала пропиши ID чата in <code>LOGS_CHAT_ID</code> внутри <b>config.py</b>!",
                                 parse_mode="HTML")
            return

        await message.answer("⏳ <b>Запуск развертывания системы...</b>\nСоздаю топики логов в административном чате...",
                             parse_mode="HTML")
        topics_config = {
            "main_admin": ("👔 Общие логи админки", 0x6FB9F0),
            "squad": ("👑 Логи Основы (Squad)", 0xFFD700),
            "academy": ("🎓 Логи Академии", 0x1CB0F6),
            "events": ("🎉 Логи Ивентов", 0xFF8500)
        }
        results = []
        for key, (name, color) in topics_config.items():
            try:
                topic = await bot.create_forum_topic(chat_id=chat_id, name=name, icon_color=color)
                await set_setting(f"topic_id_{key}", str(topic.message_thread_id))
                await bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=topic.message_thread_id,
                    text=f"📌 Топик успешно инициализирован. Сюда будут поступать логи категории: <b>{name}</b>.",
                    parse_mode="HTML"
                )
                results.append(f"✅ {name} — ID темы: <code>{topic.message_thread_id}</code>")
            except Exception as e:
                await message.answer(
                    f"❌ Ошибка при создании топика <b>{name}</b>: {e}\nУбедись, что бот админ в группе с правом управления темами!",
                    parse_mode="HTML")
                return

        from utils.admin_logger import log_admin_action
        await log_admin_action(
            bot=bot,
            admin_id=message.from_user.id,
            admin_name=message.from_user.username or "Ka1D3en",
            action_text="🚀 Успешно выполнил <b>автоматическое развертывание топиков логов</b> системы.",
            clan_key="main_admin"
        )

        report = "🚀 <b>Система логирования успешно настроена!</b>\n\nВсе топики созданы и привязаны к базе данных:\n" + "\n".join(
            results)
        await message.answer(report, parse_mode="HTML")

    @dp.message(F.text.contains("get_id"))
    async def cmd_get_chat_id_direct(message: Message):
        thread_id = message.message_thread_id
        thread_info = f"<code>{thread_id}</code>" if thread_id else "<i>(Общий чат / General)</i>"
        await message.answer(
            f"🆔 <b>ДАННЫЕ ЭТОГО ЧАТА:</b>\n\n"
            f"1️⃣ <b>ID группы (LOGS_CHAT_ID):</b> <code>{message.chat.id}</code>\n"
            f"2️⃣ <b>ID текущего топика:</b> {thread_info}\n\n"
            f"👉 Скопируй ID группы с минусом и вставь в config.py в поле LOGS_CHAT_ID",
            parse_mode="HTML"
        )

    # ─── РЕГИСТРАЦИЯ ВСЕХ РОУТЕРОВ В ДИСПЕТЧЕРЕ ────────────────────────────────

    dp.include_router(start_router)
    dp.include_router(reg_router)
    dp.include_router(proposals_router)
    dp.include_router(push_system_router)
    dp.include_router(chat_router)
    dp.include_router(clan_list_router)
    dp.include_router(admin_main_router)
    dp.include_router(change_name_router)
    dp.include_router(league_router)
    dp.include_router(trophies_router)
    dp.include_router(inactive_router)

    # ─── НАСТРОЙКА ПЛАНИРОВЩИКА ЗАДАЧ (APScheduler) ───────────────────────────
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_and_update_usernames,
        trigger="interval",
        hours=12,
        args=[bot]
    )
    scheduler.start()

    # ─── ДОБАВЛЕНО: ЗАПУСК ФОНОВЫХ ЗАДАЧ ОБНОВЛЕНИЯ КУБКОВ И ТАЙМЕРА ─────────
    asyncio.create_task(auto_update_trophies_task(bot))
    asyncio.create_task(auto_refresh_timer_task(bot))

    await on_startup()
    logging.info("Bot successfully initialized and started polling.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped manually.")
