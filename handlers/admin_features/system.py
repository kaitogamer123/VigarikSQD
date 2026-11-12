"""Системные команды админ-панели."""
import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from utils.username_monitor import check_and_update_usernames
from database import get_member, get_setting
from utils.keyboards import (
    main_menu, admin_panel_keyboard, admin_push_keyboard, admin_members_keyboard,
    admin_participants_keyboard, admin_scrims_keyboard,
)

logger = logging.getLogger(__name__)
router = Router()


# ─── ПОДСКАЗКА С КЛЮЧЕВЫМИ КОМАНДАМИ ДЛЯ ОБОИХ СОЗДАТЕЛЕЙ ─────────────────────
@router.message(F.text == "⚙️ Системные команды")
async def cmd_show_system_commands_guide(message: Message):
    """Выводит список скрытых текстовых команд Создателям бота (копирование по клику)."""
    # СИНХРОНИЗИРОВАНО: Проверка открыта для обоих ID владельцев
    if message.from_user.id not in (7899153362, 5281584435):
        return
    guide_text = (
        "⚙️ ПАНЕЛЬ СКРЫТЫХ СИСТЕМНЫХ КОМАНД \n\n"
        "Нажмите на любую команду в списке ниже, чтобы мгновенно скопировать её:\n\n"
        "🛠 Развертывание и конфиг: \n"
        "• /SetupBotVigarikThreads — Авто-создание топиков логов в чате и привязка к БД.\n"
        "• /reload_config — Перезагрузка ролей и топиков из config.py на лету.\n"
        "• get_id — Напишите это слово в любом топике, чтобы узнать его точный ID чата.\n\n"
        "🏆 Управление сезонами пуша Основы: \n"
        "• /start_season — Запуск нового сезона (техническая фиксация).\n"
        "• /check_season — Финиш сезона, опрос Brawl Stars API и вывод отчета по штрафникам.\n\n"
        "⚠️ Команды защищены и работают только под вашим Telegram ID. Для выполнения отправьте скопированную команду сообщением в этот чат. "
    )
    await message.answer(guide_text, parse_mode="HTML")


# ─── КНОПКИ СЕРВИСНОЙ НАВИГАЦИИ И ВЫХОДА ──────────────────────────────────────
@router.message(F.text == "🔙 Назад в админку")
async def back_to_main_admin_menu(message: Message):
    """Возвращает администратора из подменю обратно в корень админки."""
    member = await get_member(message.from_user.id)
    if not member or member.get("role") == "member":
        return
    role = member.get("role", "member")
    # ИСПРАВЛЕНО: Передаем user_id при возврате, чтобы кнопка создателей не исчезала
    await message.answer(
        "🔙 Возврат в главное меню админ-панели.",
        reply_markup=admin_panel_keyboard(role, user_id=message.from_user.id)
    )


@router.message(F.text == "◀️ Выйти из админки")
@router.message(F.text.contains("Выйти из админ"))
async def cmd_close_admin_panel(message: Message):
    member = await get_member(message.from_user.id)
    await message.answer(
        "🔄 Возвращаюсь в главное меню игрока.",
        reply_markup=main_menu(member)
    )


# ─── ГЛУБОКИЕ СИСТЕМНЫЕ УТИЛИТЫ ВЛАДЕЛЬЦЕВ ────────────────────────────────────
@router.message(F.text == "/SetupBotVigarikThreads")
async def cmd_auto_setup_vigarik_threads(message: Message, bot: Bot):
    """ Секретная команда автоматического развертывания топиков логов. Доступна СТРОГО обоим владельцам сети. """
    # СИНХРОНИЗИРОВАНО: Теперь оба ID могут разворачивать топики логов
    if message.from_user.id not in (7899153362, 5281584435):
        return
    import config
    from database import set_setting
    chat_id = config.LOGS_CHAT_ID or config.ADMIN_CHAT_ID
    if not chat_id:
        await message.answer(
            "❌ Ошибка настройки: \n"
            "Сначала пропиши ID чата в LOGS_CHAT_ID внутри файла config.py !",
            parse_mode="HTML"
        )
        return
    await message.answer("⏳ Запуск развертывания... \nСоздаю топики логов в административном чате...", parse_mode="HTML")
    topics_config = {
        "main_admin": ("👔 Общие логи админки", 0x6FB9F0),  # Синий
        "squad": ("👑 Логи Основы (Squad)", 0xFFD700),  # Золотой
        "academy": ("🎓 Логи Академии", 0x1CB0F6),  # Голубой
        "events": ("🎉 Логи Ивентов", 0xFF8500)  # Оранжевый
    }
    results = []
    for key, (name, color) in topics_config.items():
        try:
            topic = await bot.create_forum_topic(
                chat_id=chat_id,
                name=name,
                icon_color=color
            )
            await set_setting(f"topic_id_{key}", str(topic.message_thread_id))
            await bot.send_message(
                chat_id=chat_id,
                message_thread_id=topic.message_thread_id,
                text=f"📌 Топик успешно инициализирован. Сюда будут поступать логи категории: {name} .",
                parse_mode="HTML"
            )
            results.append(f"✅ {name} — ID темы: {topic.message_thread_id} ")
        except Exception as e:
            await message.answer(
                f"❌ Ошибка при создании топика {name} : {e}\nУбедись, что бот добавлен в чат и выдан статус Администратора!",
                parse_mode="HTML")
            return
    report = (
        "🚀 Система логирования успешно настроена! \n\n"
        "Все топики созданы и привязаны к базе данных:\n" + "\n".join(results) +
        "\n\n Перезапуск бота не требуется. Логгер уже начал перехват действий! "
    )
    await message.answer(report, report_type="HTML")


@router.message(F.text == "/get_id")
async def cmd_get_chat_id_live(message: Message):
    """Временная команда для моментального получения ID группы."""
    await message.answer(
        f"🆔 ID этого чата: {message.chat.id} \n"
        f"📌 Скопируй его и вставь в config.py в поле LOGS_CHAT_ID"
    )


@router.callback_query(F.data == "run_username_check")
async def handle_manual_username_check(call: CallbackQuery, bot: Bot):
    # Показываем всплывающее плашечное уведомление
    await call.answer("⏳ Запускаю проверку юзернеймов...", show_alert=False)
    # Отправляем сообщение о начале проверки
    status_msg = await call.message.answer("🔄 Проверка юзернеймов запущена... \nЭто займет несколько секунд.", parse_mode="HTML")
    try:
        # Запускаем нашу фоновую функцию вручную
        await check_and_update_usernames(bot)
        # Меняем текст сообщения по завершении
        await status_msg.edit_text(
            "✅ Проверка юзернеймов успешно завершена! \nЕсли у кого-то менялся тег — уведомления уже отправлены.",
            parse_mode="HTML")
    except Exception as e:
        await status_msg.edit_text(f"❌ Произошла ошибка при проверке: \n {e} ", parse_mode="HTML")
