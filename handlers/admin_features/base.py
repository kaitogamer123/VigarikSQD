"""
Главный модуль админ-панели управления.
Включает распределение по папкам меню (пуш-система и управление участниками).
Полная синхронизация для двух создателей.
"""

import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.state import State, StatesGroup
from utils.username_monitor import check_and_update_usernames

from database import get_member
from utils.permissions import is_any_admin
# ИСПРАВЛЕНО: Добавили импорт клавиатуры папки участников (admin_members_keyboard)
from utils.keyboards import main_menu, admin_panel_keyboard, admin_push_keyboard, admin_members_keyboard
from config import ROLE_LABELS

logger = logging.getLogger(__name__)
router = Router()

class AdminStates(StatesGroup):
    waiting_appoint_user = State()        # Ожидание юзера для роли
    choosing_clan_to_edit = State()       # Выбор клана
    waiting_edit_member_id = State()      # Ввод ID игрока в клане
    waiting_new_nick_for_member = State() # Ввод нового никнейма
    waiting_announcement_text = State()   # Рассылка новостей
    waiting_proposal_answer = State()     # Ответ на заявку
    waiting_twink_user = State()          # Ожидание ID или тега основы
    waiting_twink_nick = State()          # Ожидание игрового ника твинка


@router.message(F.text == "👔 Для админов")
@router.message(F.text == "👔 Для аgминов")
@router.message(F.text.contains("Для админ"))
@router.message(F.text.contains("Для admin"))
async def cmd_open_admin_panel(message: Message):
    member = await get_member(message.from_user.id)
    if not member or not is_any_admin(member):
        await message.answer("⛔ Доступ заблокирован. Меню только для администрации.")
        return

    role = member.get("role", "member")
    # ВАЖНО: Проверьте, что в конце строки написано user_id=message.from_user.id
    await message.answer(
        f"👔 <b>Панель управления ViGarik Squad</b>\n"
        f"Ваша текущая роль: {ROLE_LABELS.get(role, role)}\n\n"
        f"Выбери необходимое действие на клавиатуре ниже:",
        parse_mode="HTML",
        reply_markup=admin_panel_keyboard(role, user_id=message.from_user.id)
    )


# ─── ЛОГИКА ОТКРЫТИЯ ПАПКИ ПУШ-СИСТЕМЫ ─────────────────────────────────────────

@router.message(F.text == "🎯 Управление пуш-сезоном")
async def open_admin_push_folder(message: Message):
    """Открывает компактное подменю (папку) со всеми кнопками пуш-сезона основы."""
    member = await get_member(message.from_user.id)
    if not member or member.get("role") in ("member", "helper"):
        await message.answer("⛔ У вас нет доступа к управлению пуш-сезоном.")
        return

    await message.answer(
        "📂 <b>Управление пуш-сезоном Основы</b>\n\n"
        "Здесь вы можете запустить опрос целей, проверить должников, "
        "посмотреть статистику с таймерами игроков или прочитать предложки.",
        parse_mode="HTML",
        reply_markup=admin_push_keyboard()
    )


# ─── ЛОГИКА ОТКРЫТИЯ ПАПКИ УПРАВЛЕНИЯ УЧАСТНИКАМИ ─────────────────────────────

@router.message(F.text == "👥 Управление участниками")
async def open_admin_members_folder(message: Message):
    """Открывает подменю (папку) со всеми инструментами управления списками."""
    member = await get_member(message.from_user.id)
    if not member or member.get("role") == "member":
        await message.answer("⛔ У вас нет доступа к управлению участниками.")
        return

    await message.answer(
        "📂 <b>Управление списками и участниками сети</b>\n\n"
        "Выбери необходимое действие:\n"
        "• Изменить профиль/удалить игрока через ID\n"
        "• Посмотреть список участников без привязанных ников\n"
        "• Быстро привязать твинк к существующей основе игрока",
        parse_mode="HTML",
        reply_markup=admin_members_keyboard()
    )


# ─── ПОДСКАЗКА С КЛЮЧЕВЫМИ КОМАНДАМИ ДЛЯ ОБОИХ СОЗДАТЕЛЕЙ ─────────────────────

@router.message(F.text == "⚙️ Системные команды")
async def cmd_show_system_commands_guide(message: Message):
    """Выводит список скрытых текстовых команд Создателям бота (копирование по клику)."""
    # СИНХРОНИЗИРОВАНО: Проверка открыта для обоих ID владельцев
    if message.from_user.id not in (7899153362, 5281584435):
        return

    guide_text = (
        "⚙️ <b>ПАНЕЛЬ СКРЫТЫХ СИСТЕМНЫХ КОМАНД</b>\n\n"
        "Нажмите на любую команду в списке ниже, чтобы мгновенно скопировать её:\n\n"
        "🛠 <b>Развертывание и конфиг:</b>\n"
        "• <code>/SetupBotVigarikThreads</code> — Авто-создание топиков логов в чате и привязка к БД.\n"
        "• <code>/reload_config</code> — Перезагрузка ролей и топиков из config.py на лету.\n"
        "• <code>get_id</code> — Напишите это слово в любом топике, чтобы узнать его точный ID чата.\n\n"
        "🏆 <b>Управление сезонами пуша Основы:</b>\n"
        "• <code>/start_season</code> — Запуск нового сезона (техническая фиксация).\n"
        "• <code>/check_season</code> — Финиш сезона, опрос Brawl Stars API и вывод отчета по штрафникам.\n\n"
        "⚠️ <i>Команды защищены и работают только под вашим Telegram ID. Для выполнения отправьте скопированную команду сообщением в этот чат.</i>"
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
    """
    Секретная команда автоматического развертывания топиков логов.
    Доступна СТРОГО обоим владельцам сети.
    """
    # СИНХРОНИЗИРОВАНО: Теперь оба ID могут разворачивать топики логов
    if message.from_user.id not in (7899153362, 5281584435):
        return

    import config
    from database import set_setting

    chat_id = config.LOGS_CHAT_ID or config.ADMIN_CHAT_ID

    if not chat_id:
        await message.answer(
            "❌ <b>Ошибка настройки:</b>\n"
            "Сначала пропиши ID чата в <code>LOGS_CHAT_ID</code> внутри файла <b>config.py</b>!",
            parse_mode="HTML"
        )
        return

    await message.answer("⏳ <b>Запуск развертывания...</b>\nСоздаю топики логов в административном чате...",
                         parse_mode="HTML")

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
                text=f"📌 Топик успешно инициализирован. Сюда будут поступать логи категории: <b>{name}</b>.",
                parse_mode="HTML"
            )
            results.append(f"✅ {name} — ID темы: <code>{topic.message_thread_id}</code>")

        except Exception as e:
            await message.answer(
                f"❌ Ошибка при создании топика <b>{name}</b>: {e}\nУбедись, что бот добавлен в чат и выдан статус Администратора!",
                parse_mode="HTML")
            return

    report = (
            "🚀 <b>Система логирования успешно настроена!</b>\n\n"
            "Все топики созданы и привязаны к базе данных:\n" + "\n".join(results) +
            "\n\n<i>Перезапуск бота не требуется. Логгер уже начал перехват действий!</i>"
    )
    await message.answer(report, report_type="HTML")


@router.message(F.text == "/get_id")
async def cmd_get_chat_id_live(message: Message):
    """Временная команда для моментального получения ID группы."""
    await message.answer(
        f"🆔 <b>ID этого чата:</b> <code>{message.chat.id}</code>\n"
        f"📌 Скопируй его и вставь в config.py в поле LOGS_CHAT_ID"
    )


@router.callback_query(F.data == "run_username_check")
async def handle_manual_username_check(call: CallbackQuery, bot: Bot):
    # Показываем всплывающее плашечное уведомление
    await call.answer("⏳ Запускаю проверку юзернеймов...", show_alert=False)

    # Отправляем сообщение о начале проверки
    status_msg = await call.message.answer("🔄 <b>Проверка юзернеймов запущена...</b>\nЭто займет несколько секунд.",
                                           parse_mode="HTML")

    try:
        # Запускаем нашу фоновую функцию вручную
        await check_and_update_usernames(bot)

        # Меняем текст сообщения по завершении
        await status_msg.edit_text(
            "✅ <b>Проверка юзернеймов успешно завершена!</b>\nЕсли у кого-то менялся тег — уведомления уже отправлены.",
            parse_mode="HTML")
    except Exception as e:
        await status_msg.edit_text(f"❌ <b>Произошла ошибка при проверке:</b>\n<code>{e}</code>", parse_mode="HTML")