""" Главный модуль админ-панели управления. Включает распределение по папкам меню (Участники, Скримы, пуш-система). Полная синхронизация для двух создателей. """
import asyncio
import io
import json
import logging
import math
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    InputMediaPhoto, Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.markdown import html_decoration as hd
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from utils.username_monitor import check_and_update_usernames
from database import get_member, get_all_members, get_setting, set_setting
from utils.permissions import is_any_admin, has_role
# ИСПРАВЛЕНО: Добавили импорт клавиатур папок участников и скримов (Этап 1 ТЗ)
from utils.keyboards import (
    main_menu, admin_panel_keyboard, admin_push_keyboard, admin_members_keyboard,
    admin_participants_keyboard, admin_scrims_keyboard,
    tournament_type_keyboard, tournament_format_keyboard, tournament_limit_keyboard,
    tournament_cancel_keyboard, tournament_confirm_keyboard, tournament_publish_keyboard,
    tournament_admin_keyboard, tournaments_list_keyboard, tournament_back_keyboard,
    tournament_register_keyboard, tournament_topics_keyboard,
    tournament_current_list_keyboard, tournament_current_admin_keyboard,
    tournament_current_back_keyboard,
    simple_list_keyboard, result_cancel_keyboard, result_winner_keyboard,
    admin_result_keyboard, tournament_test_keyboard,
    TOURNAMENT_TYPE_LABELS, TOURNAMENT_FORMAT_LABELS, TOURNAMENT_TEST_BUTTON,
    TOURNAMENT_TEST_PREFIX,
)
from utils.admin_logger import log_admin_action
from league import league_db as db
from league.league_db import get_league, get_user_league
# ПЕРЕИСПОЛЬЗОВАНИЕ: нормализация даты/времени из готового модуля старых скримов
from league.competitive import SCRIM_TYPE_LABEL, _normalise_scheduled

# Pillow нужен для картинки с сеткой. Если библиотеки нет — бот работает,
# но сетку присылает текстом (ничего не падает).
try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except Exception:  # pragma: no cover
    Image = ImageDraw = ImageFont = None
    PIL_AVAILABLE = False
import config
from config import ROLE_LABELS

logger = logging.getLogger(__name__)
router = Router()

class AdminStates(StatesGroup):
    waiting_appoint_user = State()  # Ожидание юзера для роли
    choosing_clan_to_edit = State()  # Выбор клана
    waiting_edit_member_id = State()  # Ввод ID игрока в клане
    waiting_new_nick_for_member = State()  # Ввод нового никнейма
    waiting_announcement_text = State()  # Рассылка новостей
    waiting_proposal_answer = State()  # Ответ на заявку
    waiting_twink_user = State()  # Ожидание ID или тега основы
    waiting_twink_nick = State()  # Ожидание игрового ника твинка

# ══════════════════════════════════════════════════════════════════════════════
# ─── МАСТЕР СОЗДАНИЯ ТУРНИРА (Этап 2 ТЗ: пошаговый FSM) ──────────────────────
# ══════════════════════════════════════════════════════════════════════════════
# ВАЖНО: секция объявлена ВЫШЕ остальных хендлеров роутера, чтобы охранник
# FSM-ввода перехватывал кнопки меню раньше их обычных обработчиков
# (иначе состояние «зависало» бы при нажатии «👥 Участники» посреди мастера).

class TournamentStates(StatesGroup):
    waiting_type = State()          # Шаг 1: тип турнира (1V1 / 2V2 / 3V3 / ShowDown)
    waiting_format = State()        # Шаг 2: формат (Бо3 / Бо5)
    waiting_limit_choice = State()  # Шаг 3: безлимит или своё число
    waiting_limit_value = State()   # Шаг 3а: ввод числа участников
    waiting_datetime = State()      # Шаг 4: дата и время начала
    waiting_confirm = State()       # Шаг 5: подтверждение черновика
    # Этап 4: правка активного турнира
    waiting_new_limit = State()     # новый лимит команд
    waiting_new_time = State()      # новое время старта

# Тип турнира → количество игроков в одной команде.
# ShowDown особый: лимит участников жёстко фиксируется на 10.
TOURNAMENT_TEAM_SIZE = {"1V1": 1, "2V2": 2, "3V3": 3}
TOURNAMENT_SHOWDOWN = "ShowDown"
# Все кнопки шага 1 (включая ShowDown, у которого нет размера команды)
TOURNAMENT_TYPE_BUTTONS = frozenset({"1V1", "2V2", "3V3", TOURNAMENT_SHOWDOWN})
TOURNAMENT_SHOWDOWN_LIMIT = 10          # ShowDown: соло-участники, лимит фиксируется на 10
# Лимит турнира считается в КОМАНДАХ, а не в игроках:
# в ростере клана 4 человека, но на игру выходят 3 (1V1/2V2/3V3).
TOURNAMENT_MIN_TEAMS = 2                # меньше 2 команд сетка не собрать
TOURNAMENT_MAX_TEAMS = 32               # разумный потолок сетки (32 команды)

# Кнопки меню/мастера, которые никогда не считаются ответом на вопрос мастера.
TOURNAMENT_MENU_TEXTS = frozenset({
    "❌ Отмена", "◀️ Назад", "◀️ Назад в админку", "◀️ Выйти из админки", "🔙 Назад в админку",
    "👔 Для админов", "👥 Участники", "🤺 Скримы", "📢 Сделать объявление", "⚙️ Системные команды",
    "👥 Управление участниками", "🎯 Управление пуш-сезоном", "⚙️ Назначить модерацию",
    "🔄 Проверить юзернеймы", "📜 История игр", "👀 Незакрытые скримы", "🏆 Создание турнира",
    "📋 Редактировать список клана", "👤 Участники без ников", "➕ Добавить твинк",
    "🎯 Запустить определение цели", "❓ Кто не определился с пушем", "📊 Список кто что пушит",
    "⚙️ Настройка игроков", "📬 Прочитать предложки", "◀️ Назад в составы",
    "🏆 Турниры", "⚙️ Топики турниров", "⚔️ Текущие турниры",
    "🏁 Мои матчи", "📬 Заявки на результаты",
})

# ИСПРАВЛЕНО: валидные ответы мастера. Охранник FSM не должен перехватывать их
# как «кнопку меню», иначе мастер отменяется на полуслове. Раньше из-за этого
# «🧪 Тестовый турнир» попадал в TOURNAMENT_MENU_TEXTS и отменял создание турнира.
TOURNAMENT_WIZARD_ANSWERS = (
    TOURNAMENT_TYPE_BUTTONS
    | {TOURNAMENT_TEST_BUTTON}
    | set(TOURNAMENT_FORMAT_LABELS.values())
    | {"♾ Без лимита команд", "🔢 Ввести число команд"}
)

# Кнопки, которые после отмены мастера можно сразу открыть повторно (они в этом же модуле)
TOURNAMENT_REDIRECT_HANDLERS = {
    "👥 Участники": "open_admin_participants_folder",
    "👥 Управление участниками": "open_admin_members_folder",
    "🎯 Управление пуш-сезоном": "open_admin_push_folder",
    "👀 Незакрытые скримы": "admin_open_scrims_monitor",
    "🤺 Скримы": "open_admin_scrims_folder",
    "🏆 Турниры": "admin_open_tournaments_list",
    "⚔️ Текущие турниры": "admin_open_current_tournaments",
}


def _fmt_num(value) -> str:
    """Числа с пробелами-разделителями (1000 → 1 000)."""
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def _tournament_summary(data: dict) -> str:
    """Карточка черновика турнира для подтверждения."""
    t_type = data.get("t_type") or "—"
    test_mark = "🧪 <b>ТЕСТОВЫЙ ТУРНИР</b> (без массовой рассылки)\n" if data.get("t_is_test") else ""
    lines = [
        f"{test_mark}🏆 <b>Черновик турнира</b>",
        "",
        f"1️⃣ Тип скрима: <b>{hd.quote(str(t_type))}</b> — {hd.quote(TOURNAMENT_TYPE_LABELS.get(t_type, ''))}",
    ]
    if t_type == TOURNAMENT_SHOWDOWN:
        lines.append("2️⃣ Формат матча: <b>—</b> (в ShowDown карты не считаются по Бо3/Бо5)")
        lines.append(f"3️⃣ Лимит участников: <b>{_fmt_num(TOURNAMENT_SHOWDOWN_LIMIT)}</b> соло (зафиксирован автоматически)")
    else:
        fmt = data.get("t_format")
        team_size = TOURNAMENT_TEAM_SIZE.get(t_type, 1)
        lines.append(f"2️⃣ Формат матча: <b>{hd.quote(TOURNAMENT_FORMAT_LABELS.get(fmt, '—'))}</b>")
        limit = data.get("t_limit")
        if limit:
            lines.append(
                f"3️⃣ Лимит команд: <b>{_fmt_num(limit)}</b> "
                f"(≈ {_fmt_num(int(limit) * team_size)} игроков на игру)"
            )
        else:
            lines.append("3️⃣ Лимит команд: <b>♾ без лимита</b>")
    lines.append(f"4️⃣ Начало турнира: <b>{hd.quote(str(data.get('t_datetime') or '—'))}</b>")
    lines.append("")
    lines.append("Всё верно? Подтверди создание или отмени мастер.")
    return "\n".join(lines)


async def _ask_datetime(message: Message, state: FSMContext) -> None:
    """Шаг 4: дата и время начала турнира."""
    await state.set_state(TournamentStates.waiting_datetime)
    await message.answer(
        "🕐 <b>Шаг 4/4</b> — Когда будет турнир?\n\n"
        "Напиши дату и время начала в формате:\n"
        "• <code>25.12 19:00</code>\n"
        "• <code>25.12.2026 19:00</code>",
        parse_mode="HTML",
        reply_markup=tournament_cancel_keyboard(),
    )


@router.message(F.text == "🏆 Создание турнира")
async def tournament_create_start(message: Message, state: FSMContext):
    """Шаг 1: запуск мастера создания турнира."""
    member = await get_member(message.from_user.id)
    if not member or not has_role(member, "vice"):
        await message.answer("⛔ Создавать турниры могут Вице Президент и выше.")
        return
    await state.clear()
    await state.set_state(TournamentStates.waiting_type)
    await message.answer(
        "🏆 <b>Создание турнира</b> — шаг 1/4\n\n"
        "Какой тип скрима вы хотите выбрать?\n\n"
        "• 1V1 / 2V2 / 3V3 — играют составы команд (в ростере до 4 человек, на игру выходят по типу);\n"
        "• ShowDown — соло-режим, лимит участников автоматически фиксируется на 10;\n"
        f"• <b>{TOURNAMENT_TEST_BUTTON}</b> — тот же мастер, но посты только в админ-чат "
        "и без массовой рассылки в ЛС игрокам (для проверки механики).",
        parse_mode="HTML",
        reply_markup=tournament_type_keyboard(),
    )


@router.message(
    StateFilter(TournamentStates),
    ~F.text.in_(TOURNAMENT_WIZARD_ANSWERS),
    F.text.in_(TOURNAMENT_MENU_TEXTS) | F.text.startswith("/"),
)
async def tournament_state_guard(message: Message, state: FSMContext):
    """Охранник мастера: команды (/start, /cancel) и кнопки меню не глотаются как ввод.

    Мастер аккуратно отменяется, а нажатая кнопка меню (если она из этого модуля)
    сразу открывается заново — пользователь не теряет своё место в админке.
    """
    await state.clear()
    text = (message.text or "").strip()

    if text == "◀️ Выйти из админки" or text.startswith("/cancel"):
        member = await get_member(message.from_user.id)
        await message.answer("❌ Создание турнира отменено.", reply_markup=main_menu(member))
        return
    if text == "🔙 Назад в админку":
        member = await get_member(message.from_user.id)
        role = (member or {}).get("role", "member")
        await message.answer(
            "❌ Создание турнира отменено.\n🔙 Возврат в главное меню админ-панели.",
            reply_markup=admin_panel_keyboard(role, user_id=message.from_user.id),
        )
        return

    handler_name = TOURNAMENT_REDIRECT_HANDLERS.get(text)
    if handler_name:
        await message.answer("❌ Создание турнира отменено.")
        await globals()[handler_name](message)
        return

    if text == "🏆 Создание турнира":
        await tournament_create_start(message, state)
        return

    await message.answer(
        "❌ Создание турнира отменено.",
        reply_markup=admin_scrims_keyboard(),
    )


@router.message(TournamentStates.waiting_type, F.text.in_(TOURNAMENT_TYPE_BUTTONS | {TOURNAMENT_TEST_BUTTON}))
async def tournament_type_chosen(message: Message, state: FSMContext):
    """Шаг 1 принят. ShowDown фиксирует лимит 10 и пропускает шаги 2-3."""
    raw = (message.text or "").strip()
    is_test = raw == TOURNAMENT_TEST_BUTTON
    t_type = "3V3" if is_test else raw
    await state.update_data(t_type=t_type, t_is_test=1 if is_test else 0)
    if is_test:
        await message.answer(
            "🧪 <b>Тестовый режим включён.</b>\n"
            "Посты с сеткой будут публиковаться только в админ-чат, массовая рассылка "
            "в ЛС всем игрокам выполняться не будет. Уведомления о матчах получат только "
            "лидеры записанных составов (в тестовых составах лидер — ты).",
            parse_mode="HTML",
        )

    if t_type == TOURNAMENT_SHOWDOWN:
        await state.update_data(t_format=None, t_limit=TOURNAMENT_SHOWDOWN_LIMIT)
        await message.answer(
            f"📌 Лимит участников автоматически зафиксирован: <b>{_fmt_num(TOURNAMENT_SHOWDOWN_LIMIT)}</b>.",
            parse_mode="HTML",
        )
        await _ask_datetime(message, state)
        return

    await state.set_state(TournamentStates.waiting_format)
    await message.answer(
        "⚔️ <b>Шаг 2/4</b> — Какой тип скрима будет между командами?",
        parse_mode="HTML",
        reply_markup=tournament_format_keyboard(),
    )


@router.message(TournamentStates.waiting_type)
async def tournament_type_wrong(message: Message):
    await message.answer(
        "❌ Выбери тип кнопкой: 1V1, 2V2, 3V3 или ShowDown.\n"
        "Для отмены нажми «❌ Отмена» или отправь /cancel.",
    )


@router.message(TournamentStates.waiting_format, F.text.in_(set(TOURNAMENT_FORMAT_LABELS.values())))
async def tournament_format_chosen(message: Message, state: FSMContext):
    """Шаг 2 принят (переиспользуем форматы Бо3/Бо5 из старых скримов)."""
    raw = (message.text or "").strip()
    fmt = next((key for key, label in TOURNAMENT_FORMAT_LABELS.items() if label == raw), "BO3")
    await state.update_data(t_format=fmt)
    await state.set_state(TournamentStates.waiting_limit_choice)
    await message.answer(
        "👥 <b>Шаг 3/4</b> — Хотите ли вы оставить/изменить лимит <b>команд</b> на турнир?\n\n"
        "Лимит считается в командах: в ростере клана 4 человека, но на игру выходят по типу скрима "
        "(3V3 → 3 игрока, запасной 4-й в лимит не входит).\n\n"
        "• «♾ Без лимита команд» — заявки принимаются без ограничения;\n"
        "• «🔢 Ввести число команд» — жёсткий лимит команд (сетка соберётся под любое число).",
        parse_mode="HTML",
        reply_markup=tournament_limit_keyboard(),
    )


@router.message(TournamentStates.waiting_format)
async def tournament_format_wrong(message: Message):
    await message.answer(
        "❌ Выбери формат кнопкой: Бо3 или Бо5.\n"
        "Для отмены нажми «❌ Отмена» или отправь /cancel.",
    )


@router.message(
    TournamentStates.waiting_limit_choice,
    F.text.in_({"♾ Без лимита команд", "🔢 Ввести число команд"}),
)
async def tournament_limit_choice(message: Message, state: FSMContext):
    """Шаг 3: лимит задаётся в КОМАНДАХ (в ростере 4 человека, играют 3)."""
    data = await state.get_data()
    team_size = TOURNAMENT_TEAM_SIZE.get(data.get("t_type"), 1)
    if (message.text or "").strip() == "♾ Без лимита команд":
        await state.update_data(t_limit=None)
        await _ask_datetime(message, state)
        return
    await state.set_state(TournamentStates.waiting_limit_value)
    await message.answer(
        "🔢 Введи лимит <b>команд</b> одним числом.\n\n"
        f"Тип {hd.quote(str(data.get('t_type')))}: на игру от команды выходят {team_size} игрок(а), "
        f"в ростере команды может быть до 4 человек (запасной не считается в лимит).\n"
        f"Допустимый диапазон: {TOURNAMENT_MIN_TEAMS}–{_fmt_num(TOURNAMENT_MAX_TEAMS)} команд.\n"
        "Для отмены нажми «❌ Отмена» или отправь /cancel.",
        parse_mode="HTML",
        reply_markup=tournament_cancel_keyboard(),
    )


@router.message(TournamentStates.waiting_limit_value)
async def tournament_limit_value(message: Message, state: FSMContext):
    """Ввод числа КОМАНД (не игроков). Кратность не требуется — сетка собирается под любое число."""
    data = await state.get_data()
    team_size = TOURNAMENT_TEAM_SIZE.get(data.get("t_type"), 1)
    raw = (message.text or "").strip().replace(" ", "")
    if not raw.isdigit():
        await message.answer("❌ Нужно целое число команд. Попробуй ещё раз (например: 8).")
        return
    teams = int(raw)
    if teams < TOURNAMENT_MIN_TEAMS or teams > TOURNAMENT_MAX_TEAMS:
        await message.answer(
            f"❌ Лимит должен быть от {TOURNAMENT_MIN_TEAMS} до {_fmt_num(TOURNAMENT_MAX_TEAMS)} команд. "
            "Попробуй ещё раз."
        )
        return
    await state.update_data(t_limit=teams)
    await message.answer(
        f"✅ Лимит: <b>{_fmt_num(teams)}</b> команд "
        f"(≈ {_fmt_num(teams * team_size)} игроков на игру).",
        parse_mode="HTML",
    )
    await _ask_datetime(message, state)


@router.message(TournamentStates.waiting_datetime)
async def tournament_datetime_received(message: Message, state: FSMContext):
    """Шаг 4: нормализуем время готовой функцией из старых скримов и показываем итог."""
    scheduled = _normalise_scheduled(message.text or "")
    if not scheduled:
        await message.answer(
            "❌ Неверный формат даты. Введи так: 25.12 19:00 или 25.12.2026 19:00.\n"
            "Для отмены нажми «❌ Отмена» или отправь /cancel.",
        )
        return
    await state.update_data(t_datetime=scheduled)
    data = await state.get_data()
    await state.set_state(TournamentStates.waiting_confirm)
    await message.answer(
        _tournament_summary(data),
        parse_mode="HTML",
        reply_markup=tournament_confirm_keyboard(),
    )


@router.callback_query(F.data == "tour:confirm")
async def tournament_confirm(call: CallbackQuery, state: FSMContext, bot: Bot):
    """Этап 3, п.1: сохраняем турнир в БД и предлагаем анонс."""
    data = await state.get_data()
    if not data.get("t_type"):
        await state.clear()
        await call.answer("Сессия создания турнира истекла", show_alert=True)
        return
    await state.clear()
    t_type = data.get("t_type")
    fmt = data.get("t_format")
    limit = data.get("t_limit")
    when = data.get("t_datetime")
    admin_name = call.from_user.username or call.from_user.full_name or str(call.from_user.id)

    ensure_tournament_tables()
    is_test = bool(data.get("t_is_test"))
    tournament_id = create_tournament(
        created_by=call.from_user.id,
        created_by_name=str(admin_name),
        t_type=str(t_type),
        t_format=fmt,
        team_limit=limit,
        scheduled_text=str(when or ""),
        bracket_slots=_bracket_slots_for(t_type, limit),
        is_test=is_test,
    )
    summary = _tournament_summary(data)
    await log_admin_action(
        bot=bot,
        admin_id=call.from_user.id,
        admin_name=str(admin_name),
        action_text=(
            f"🏆 Создал {'ТЕСТОВЫЙ ' if is_test else ''}турнир #{tournament_id}: тип {t_type}, "
            f"формат {fmt or '—'}, лимит {f'{limit} команд' if limit else 'без лимита команд'}, "
            f"начало {when}."
        ),
    )
    after_kb = tournament_test_keyboard(tournament_id) if is_test \
        else tournament_publish_keyboard(tournament_id)
    hint = ("Дальше можно добавить тестовые составы и запустить турнир, чтобы проверить сетку."
            if is_test else
            "Дальше можно опубликовать анонс: посты в клановых топиках «Турниры» "
            "и рассылка зарегистрированным игрокам в ЛС.")
    try:
        await call.message.edit_text(
            f"{summary}\n\n"
            f"✅ <b>Турнир #{tournament_id} сохранён в базе.</b>\n{hint}",
            parse_mode="HTML",
            reply_markup=after_kb,
        )
    except Exception:
        await call.message.answer(
            f"✅ Турнир #{tournament_id} сохранён в базе.\n{hint}",
            parse_mode="HTML",
            reply_markup=after_kb,
        )
    await call.message.answer(
        f"👤 Турнир создал: {hd.quote(str(admin_name))}.",
        parse_mode="HTML",
        reply_markup=admin_scrims_keyboard(),
    )
    await call.answer("Турнир сохранён")


@router.callback_query(F.data == "tour:cancel")
async def tournament_cancel_cb(call: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await call.message.edit_text("❌ Создание турнира отменено.", reply_markup=None)
    except Exception:
        pass
    await call.message.answer("📂 Скримы и турниры", reply_markup=admin_scrims_keyboard())
    await call.answer("Отменено")


# ══════════════════════════════════════════════════════════════════════════════
# ─── ТУРНИРЫ (Этап 3 ТЗ): БД, сетка с автопроходами/BYE, анонсы, рассылки ────
# ══════════════════════════════════════════════════════════════════════════════
# Таблицы живут в league/league.db, как и остальная игровая механика.
# Миграция идемпотентная (CREATE TABLE IF NOT EXISTS + PRAGMA table_info),
# сами файлы баз на сервере не трогаем и в гит не добавляем.

TOURNAMENT_SETTINGS_PREFIX = "tournament_topic_"   # + clan_key (academy / squad / events)
TOURNAMENT_CLANS = ("academy", "squad", "events")
TOURNAMENT_DEFAULT_BRACKET_SIZE = 8                # превью-сетка, если лимит не задан
TOURNAMENT_DM_PAUSE = 0.05                         # антиспам-пауза между ЛС

TOURNAMENT_STATUS_LABEL = {
    "registration": "🟢 Регистрация открыта",
    "announced": "📣 Анонсирован",
    "running": "⚔️ Идёт",
    "finished": "🏁 Завершён",
    "cancelled": "🚫 Отменён",
}


class TournamentSettingsStates(StatesGroup):
    """Отдельная группа, чтобы не пересекаться с охранником мастера турниров."""
    waiting_topics = State()


class ResultStates(StatesGroup):
    """Этап 5: сдача результата лидером (фото + текст → выбор победителя)."""
    waiting_photo = State()   # фото с подписью (текстом)
    waiting_winner = State()  # кто победил (кнопки)


_TOURNAMENT_TABLES_READY = False


def ensure_tournament_tables() -> None:
    """Идемпотентная миграция таблиц турниров в league.db (один раз за процесс)."""
    global _TOURNAMENT_TABLES_READY
    if _TOURNAMENT_TABLES_READY:
        return
    conn = db.get_connection()
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS tournaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_by INTEGER NOT NULL,
            created_by_name TEXT DEFAULT '',
            t_type TEXT NOT NULL,
            t_format TEXT,
            team_limit INTEGER,
            scheduled_text TEXT DEFAULT '',
            status TEXT DEFAULT 'registration',
            bracket_slots TEXT DEFAULT '[]',
            published_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS tournament_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            league_id INTEGER NOT NULL,
            team_name TEXT DEFAULT '',
            team_tag TEXT DEFAULT '',
            leader_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (tournament_id, league_id),
            FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_tournament_participants
            ON tournament_participants (tournament_id);
        CREATE TABLE IF NOT EXISTS tournament_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            round INTEGER NOT NULL DEFAULT 1,
            pair_index INTEGER NOT NULL DEFAULT 0,
            side_a TEXT,
            side_b TEXT,
            winner TEXT,
            status TEXT DEFAULT 'scheduled',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (tournament_id, round, pair_index),
            FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_tournament_matches
            ON tournament_matches (tournament_id, round);
        CREATE TABLE IF NOT EXISTS match_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            match_id INTEGER NOT NULL,
            reporter_id INTEGER NOT NULL,
            team_name TEXT DEFAULT '',
            opponent_name TEXT DEFAULT '',
            claimed_winner TEXT DEFAULT '',
            photo_file_id TEXT DEFAULT '',
            comment TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            decided_by INTEGER,
            decided_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_match_reports ON match_reports (status, tournament_id);
        CREATE TABLE IF NOT EXISTS tournament_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            round_no INTEGER NOT NULL,
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (tournament_id, round_no),
            FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS tournament_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            thread_id INTEGER,
            kind TEXT DEFAULT 'bracket',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (tournament_id, chat_id, kind),
            FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
        );
    """)
    cols = {row["name"] for row in cur.execute("PRAGMA table_info(tournaments)").fetchall()}
    if "bracket_slots" not in cols:
        cur.execute("ALTER TABLE tournaments ADD COLUMN bracket_slots TEXT DEFAULT '[]'")
    if "published_at" not in cols:
        cur.execute("ALTER TABLE tournaments ADD COLUMN published_at DATETIME")
    # scheduled_at — машинное время старта для автозапуска (Этап 4, п.2)
    if "scheduled_at" not in cols:
        cur.execute("ALTER TABLE tournaments ADD COLUMN scheduled_at DATETIME")
    # Тестовый режим: фейковые составы, посты только в админ-чат, без массовой рассылки
    if "is_test" not in cols:
        cur.execute("ALTER TABLE tournaments ADD COLUMN is_test INTEGER DEFAULT 0")
    # Этап 5: фиксация обработки матча
    mcols = {row["name"] for row in cur.execute("PRAGMA table_info(tournament_matches)").fetchall()}
    if "resolved_by" not in mcols:
        cur.execute("ALTER TABLE tournament_matches ADD COLUMN resolved_by INTEGER")
    if "resolved_at" not in mcols:
        cur.execute("ALTER TABLE tournament_matches ADD COLUMN resolved_at DATETIME")
    # thread_id нужен, чтобы пересоздавать пост сетки именно в топике, а не в общем чате
    pcols = {row["name"] for row in cur.execute("PRAGMA table_info(tournament_posts)").fetchall()}
    if "thread_id" not in pcols:
        cur.execute("ALTER TABLE tournament_posts ADD COLUMN thread_id INTEGER")
    cur.execute("""
        UPDATE tournaments SET scheduled_at = (
            SELECT substr(scheduled_text, 7, 4) || '-' || substr(scheduled_text, 4, 2) || '-'
                   || substr(scheduled_text, 1, 2) || ' ' || substr(scheduled_text, 12, 5)
        )
        WHERE (scheduled_at IS NULL OR scheduled_at = '')
          AND length(COALESCE(scheduled_text, '')) >= 16
    """)
    conn.commit()
    conn.close()
    _TOURNAMENT_TABLES_READY = True


def _iso_from_scheduled_text(text: str):
    """'25.12.2026 19:00' -> '2026-12-25 19:00' (для сортировки и сравнения в SQL)."""
    raw = (text or "").strip()
    for fmt in ("%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return None


def is_test_tournament(row) -> bool:
    """Тестовый турнир: та же механика, но посты в админ-чат и без массовой рассылки в ЛС."""
    if row is None:
        return False
    try:
        # sqlite3.Row не имеет .get(), обращаемся по ключу напрямую
        return int(row["is_test"] or 0) == 1
    except (KeyError, IndexError, TypeError, ValueError):
        return False


def create_tournament(created_by, created_by_name, t_type, t_format, team_limit,
                      scheduled_text, bracket_slots, is_test: bool = False) -> int:
    """Создаёт запись турнира и возвращает его id."""
    ensure_tournament_tables()
    conn = db.get_connection()
    cur = conn.execute(
        "INSERT INTO tournaments (created_by, created_by_name, t_type, t_format, team_limit, "
        "scheduled_text, scheduled_at, status, bracket_slots, is_test) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'registration', ?, ?)",
        (int(created_by), created_by_name or "", t_type, t_format, team_limit,
         scheduled_text or "", _iso_from_scheduled_text(scheduled_text),
         json.dumps(bracket_slots or [], ensure_ascii=False), 1 if is_test else 0),
    )
    conn.commit()
    new_id = int(cur.lastrowid)
    conn.close()
    return new_id


def get_tournament(tournament_id: int):
    ensure_tournament_tables()
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM tournaments WHERE id = ?", (int(tournament_id),)).fetchone()
    conn.close()
    # dict, а не sqlite3.Row: дальше по коду используются .get()
    return dict(row) if row else None


def get_tournaments(limit: int = 10):
    ensure_tournament_tables()
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM tournaments ORDER BY id DESC LIMIT ?", (int(limit),)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Этап 4: активные турниры, правка лимитов, матчи, автостарт ─────────────

TOURNAMENT_ACTIVE_STATUSES = ("registration", "announced", "running")


def get_active_tournaments():
    """Все текущие (не завершённые и не отменённые) турниры."""
    ensure_tournament_tables()
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM tournaments WHERE status IN (?, ?, ?) ORDER BY scheduled_at IS NULL, "
        "scheduled_at, id DESC",
        TOURNAMENT_ACTIVE_STATUSES,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_due_tournaments(now_iso: str):
    """Турниры, время старта которых уже наступило (для автозапуска)."""
    ensure_tournament_tables()
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM tournaments WHERE status IN (?, ?) AND scheduled_at IS NOT NULL "
        "AND scheduled_at <= ? ORDER BY scheduled_at",
        ("registration", "announced", now_iso),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_tournament_limit(tournament_id: int, team_limit) -> None:
    ensure_tournament_tables()
    conn = db.get_connection()
    conn.execute(
        "UPDATE tournaments SET team_limit = ? WHERE id = ?",
        (int(team_limit) if team_limit else None, int(tournament_id)),
    )
    conn.commit()
    conn.close()


def update_tournament_time(tournament_id: int, scheduled_text: str) -> None:
    ensure_tournament_tables()
    conn = db.get_connection()
    conn.execute(
        "UPDATE tournaments SET scheduled_text = ?, scheduled_at = ? WHERE id = ?",
        (scheduled_text, _iso_from_scheduled_text(scheduled_text), int(tournament_id)),
    )
    conn.commit()
    conn.close()


def update_tournament_bracket(tournament_id: int, slots: list) -> None:
    ensure_tournament_tables()
    conn = db.get_connection()
    conn.execute(
        "UPDATE tournaments SET bracket_slots = ? WHERE id = ?",
        (json.dumps(slots or [], ensure_ascii=False), int(tournament_id)),
    )
    conn.commit()
    conn.close()


def replace_tournament_matches(tournament_id: int, matches: list[dict],
                               round_no: int = 1) -> None:
    """Пересоздаёт пары указанного круга (по умолчанию 1-го)."""
    ensure_tournament_tables()
    conn = db.get_connection()
    conn.execute("DELETE FROM tournament_matches WHERE tournament_id = ? AND round = ?",
                 (int(tournament_id), int(round_no)))
    for idx, m in enumerate(matches):
        conn.execute(
            "INSERT OR IGNORE INTO tournament_matches (tournament_id, round, pair_index, side_a, "
            "side_b, winner, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (int(tournament_id), int(round_no), idx, m.get("side_a"), m.get("side_b"),
             m.get("winner"), m.get("status", "scheduled")),
        )
    conn.commit()
    conn.close()


def get_tournament_matches(tournament_id: int, round_no: int = 1):
    ensure_tournament_tables()
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM tournament_matches WHERE tournament_id = ? AND round = ? ORDER BY pair_index",
        (int(tournament_id), int(round_no)),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_tournament_matches(tournament_id: int):
    """Все матчи турнира (включая матч за 3-4 места с round=0)."""
    ensure_tournament_tables()
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM tournament_matches WHERE tournament_id = ? ORDER BY round, pair_index",
        (int(tournament_id),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_placements(tournament_id: int) -> list[tuple[int, str]]:
    """Итоговые места: [(1, 'TEST-01'), (2, 'TEST-08'), (3, 'TEST-03'), (4, 'TEST-05')]."""
    row = get_tournament(tournament_id)
    if not row:
        return []
    matches = get_all_tournament_matches(tournament_id)
    third = get_third_place_match(tournament_id)
    placements: list[tuple[int, str]] = []

    # финал — строго последний раунд сетки (иначе после полуфинала
    # «1 место» присудили бы победителю полуфинала)
    final_round = _total_rounds(_bracket_size(row))
    final = next(
        (m for m in matches
         if int(m["round"]) == final_round and m["side_a"] and m["side_b"]),
        None,
    )
    if final and final["winner"]:
        loser = final["side_b"] if final["winner"] == final["side_a"] else final["side_a"]
        placements.append((1, _clean_team(final["winner"])))
        if loser:
            placements.append((2, _clean_team(loser)))

    if third and third["winner"] and third["side_a"] and third["side_b"]:
        loser3 = third["side_b"] if third["winner"] == third["side_a"] else third["side_a"]
        placements.append((3, _clean_team(third["winner"])))
        if loser3:
            placements.append((4, _clean_team(loser3)))
    return placements


def placements_text(tournament_id: int) -> str:
    """Текстовая таблица призёров для постов/подтверждений."""
    rows = get_placements(tournament_id)
    if not rows:
        return ""
    return "\n".join(f"{place} место — {name}" for place, name in rows)


def tournament_matches_exist(tournament_id: int) -> bool:
    ensure_tournament_tables()
    conn = db.get_connection()
    row = conn.execute(
        "SELECT 1 FROM tournament_matches WHERE tournament_id = ? LIMIT 1", (int(tournament_id),)
    ).fetchone()
    conn.close()
    return row is not None


def get_match(match_id: int):
    ensure_tournament_tables()
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM tournament_matches WHERE id = ?", (int(match_id),)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_match_winner(match_id: int, winner: str, status: str, admin_id: int) -> None:
    ensure_tournament_tables()
    conn = db.get_connection()
    conn.execute(
        "UPDATE tournament_matches SET winner = ?, status = ?, resolved_by = ?, "
        "resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
        (winner, status, int(admin_id), int(match_id)),
    )
    conn.commit()
    conn.close()


def upsert_match_side(tournament_id: int, round_no: int, pair_index: int, side: str, team: str) -> int:
    """Создаёт/дополняет матч в следующем круге, возвращает его id."""
    ensure_tournament_tables()
    conn = db.get_connection()
    row = conn.execute(
        "SELECT * FROM tournament_matches WHERE tournament_id = ? AND round = ? AND pair_index = ?",
        (int(tournament_id), int(round_no), int(pair_index)),
    ).fetchone()
    if row:
        match_id = int(row["id"])
        col = "side_a" if side == "a" else "side_b"
        conn.execute(f"UPDATE tournament_matches SET {col} = ? WHERE id = ?", (team, match_id))
    else:
        cur = conn.execute(
            "INSERT INTO tournament_matches (tournament_id, round, pair_index, side_a, side_b, "
            "status) VALUES (?, ?, ?, ?, ?, 'scheduled')",
            (int(tournament_id), int(round_no), int(pair_index),
             team if side == "a" else None, team if side == "b" else None),
        )
        match_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return match_id


def create_match_report(tournament_id, match_id, reporter_id, team_name, opponent_name,
                        claimed_winner, photo_file_id, comment) -> int:
    ensure_tournament_tables()
    conn = db.get_connection()
    cur = conn.execute(
        "INSERT INTO match_reports (tournament_id, match_id, reporter_id, team_name, "
        "opponent_name, claimed_winner, photo_file_id, comment) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (int(tournament_id), int(match_id), int(reporter_id), team_name or "", opponent_name or "",
         claimed_winner or "", photo_file_id or "", comment or ""),
    )
    conn.commit()
    report_id = int(cur.lastrowid)
    conn.close()
    return report_id


def get_match_report(report_id: int):
    ensure_tournament_tables()
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM match_reports WHERE id = ?", (int(report_id),)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_pending_reports(limit: int = 15):
    ensure_tournament_tables()
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM match_reports WHERE status = 'pending' ORDER BY id DESC LIMIT ?", (int(limit),)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_match_report_status(report_id: int, status: str, admin_id: int) -> None:
    ensure_tournament_tables()
    conn = db.get_connection()
    conn.execute(
        "UPDATE match_reports SET status = ?, decided_by = ?, decided_at = CURRENT_TIMESTAMP "
        "WHERE id = ?", (status, int(admin_id), int(report_id)),
    )
    conn.commit()
    conn.close()


def has_pending_report(match_id: int) -> bool:
    ensure_tournament_tables()
    conn = db.get_connection()
    row = conn.execute(
        "SELECT 1 FROM match_reports WHERE match_id = ? AND status = 'pending' LIMIT 1",
        (int(match_id),),
    ).fetchone()
    conn.close()
    return row is not None


# ─── Посты с сеткой (Этап 5, п.3: обновление с учётом лимита 2 дня) ─────────

def save_bracket_post(tournament_id: int, chat_id: int, message_id: int,
                      kind: str = "bracket", thread_id=None) -> None:
    ensure_tournament_tables()
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO tournament_posts (tournament_id, chat_id, message_id, thread_id, kind) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (tournament_id, chat_id, kind) DO UPDATE SET message_id = excluded.message_id, "
        "thread_id = excluded.thread_id, created_at = CURRENT_TIMESTAMP",
        (int(tournament_id), int(chat_id), int(message_id),
         int(thread_id) if thread_id else None, kind),
    )
    conn.commit()
    conn.close()


def get_bracket_posts(tournament_id: int, kind: str = "bracket"):
    ensure_tournament_tables()
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM tournament_posts WHERE tournament_id = ? AND kind = ?",
        (int(tournament_id), kind),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_bracket_post(post_id: int) -> None:
    ensure_tournament_tables()
    conn = db.get_connection()
    conn.execute("DELETE FROM tournament_posts WHERE id = ?", (int(post_id),))
    conn.commit()
    conn.close()


# ─── Продвижение по сетке + матч за 3-е место (Этап 5, п.3-4) ───────────────

def _bracket_size(row) -> int:
    """Размер сетки: ближайшая степень двойки от числа зарегистрированных команд."""
    count = len(get_tournament_participants(row["id"]))
    size = 1
    while size < max(2, count):
        size *= 2
    return size


def _total_rounds(size: int) -> int:
    return max(1, int(math.log2(max(2, size))))


def _third_place_needed(size: int) -> bool:
    """Матч за 3-е место имеет смысл, когда есть полуфиналы (4+ команды)."""
    return _total_rounds(size) >= 2


def _propagate_auto_advances(tournament_id: int) -> int:
    """Продвигает победителей матчей с BYE (resolved_at ещё пустой) в следующий круг."""
    row = get_tournament(tournament_id)
    total = _total_rounds(_bracket_size(row))
    moved = 0
    for round_no in range(1, total + 1):
        for m in get_tournament_matches(tournament_id, round_no):
            if m["status"] != "bye" or not m["winner"] or m["resolved_at"] is not None:
                continue
            advance_winner(tournament_id, round_no, m["pair_index"], m["winner"])
            conn = db.get_connection()
            conn.execute(
                "UPDATE tournament_matches SET resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
                (int(m["id"]),),
            )
            conn.commit()
            conn.close()
            moved += 1
    return moved


def advance_winner(tournament_id: int, round_no: int, pair_index: int, winner: str) -> dict:
    """Продвигает победителя в следующий круг (или фиксирует конец турнира)."""
    row = get_tournament(tournament_id)
    total = _total_rounds(_bracket_size(row))
    info = {"next_match_id": None, "tournament_finished": False, "next_opponent": None}

    if round_no >= total:
        info["tournament_finished"] = True
        return info

    next_round = round_no + 1
    # предвариловка: каждый победитель идёт в свою пару основной сетки
    # (плей-ин матч 1 → матч 1, матч 2 → матч 2), а не по два в один
    participants = get_tournament_participants(tournament_id)
    names = [p["team_name"] or "" for p in participants]
    plan = _seed_plan(names, row["t_type"]) if names else {"playin_pairs": []}
    is_playin = bool(plan.get("playin_pairs")) and round_no == 1 and \
        int(pair_index) < len(plan["playin_pairs"])
    if is_playin:
        # куда именно встал слот «Победитель пары N» в первой колонке основной сетки
        main_col = _first_main_column(plan)
        label = f"Победитель пары {int(pair_index) + 1}"
        if label in main_col:
            pos = main_col.index(label)
            next_pair, side = pos // 2, ("a" if pos % 2 == 0 else "b")
        else:
            next_pair, side = int(pair_index), "a"
    else:
        next_pair = int(pair_index) // 2
        side = "a" if int(pair_index) % 2 == 0 else "b"
    match_id = upsert_match_side(tournament_id, next_round, next_pair, side, winner)
    info["next_match_id"] = match_id
    nxt = get_match(match_id)
    if nxt and nxt["side_a"] and nxt["side_b"]:
        info["next_opponent"] = nxt["side_b"] if side == "a" else nxt["side_a"]
    return info


def register_semifinal_loser(tournament_id: int, round_no: int, loser: str) -> bool:
    """Этап 5, п.4: проигравший в полуфинале уходит в матч за 3-4 места (round = 0).

    Возвращает True, когда обе команды матча за 3-е место уже известны.
    """
    row = get_tournament(tournament_id)
    size = _bracket_size(row)
    if not _third_place_needed(size):
        return False
    if round_no != _total_rounds(size) - 1:   # не полуфинал
        return False
    side = "b" if _third_has_side(tournament_id, "a") else "a"
    match_id = upsert_match_side(tournament_id, 0, 0, side, loser)
    m = get_match(match_id)
    return bool(m and m["side_a"] and m["side_b"])


def _third_has_side(tournament_id: int, side: str) -> bool:
    col = "side_a" if side == "a" else "side_b"
    conn = db.get_connection()
    row = conn.execute(
        f"SELECT {col} AS v FROM tournament_matches WHERE tournament_id = ? AND round = 0 "
        "AND pair_index = 0", (int(tournament_id),),
    ).fetchone()
    conn.close()
    return bool(row and row["v"])


def get_third_place_match(tournament_id: int):
    conn = db.get_connection()
    row = conn.execute(
        "SELECT * FROM tournament_matches WHERE tournament_id = ? AND round = 0 AND pair_index = 0",
        (int(tournament_id),),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def set_tournament_status(tournament_id: int, status: str) -> None:
    ensure_tournament_tables()
    conn = db.get_connection()
    conn.execute("UPDATE tournaments SET status = ? WHERE id = ?", (status, int(tournament_id)))
    conn.commit()
    conn.close()


def mark_tournament_published(tournament_id: int) -> None:
    ensure_tournament_tables()
    conn = db.get_connection()
    conn.execute(
        "UPDATE tournaments SET status = CASE WHEN status = 'registration' THEN 'announced' "
        "ELSE status END, published_at = CURRENT_TIMESTAMP WHERE id = ?",
        (int(tournament_id),),
    )
    conn.commit()
    conn.close()


def add_tournament_participant(tournament_id: int, league_id: int, team_name, team_tag, leader_id) -> bool:
    """Регистрирует состав. False — если состав уже в списке."""
    ensure_tournament_tables()
    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT INTO tournament_participants (tournament_id, league_id, team_name, team_tag, leader_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (int(tournament_id), int(league_id), team_name or "", team_tag or "", int(leader_id or 0)),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def get_tournament_participants(tournament_id: int):
    ensure_tournament_tables()
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM tournament_participants WHERE tournament_id = ? ORDER BY id",
        (int(tournament_id),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_test_participants(tournament_id: int) -> int:
    """Сколько в турнире фейковых тестовых составов (TEST-*)."""
    ensure_tournament_tables()
    conn = db.get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM tournament_participants WHERE tournament_id = ? "
        "AND team_name LIKE ?",
        (int(tournament_id), f"{TOURNAMENT_TEST_PREFIX}%"),
    ).fetchone()
    conn.close()
    return int(row["c"] or 0) if row else 0


def add_test_participants(tournament_id: int, amount: int, admin_id: int) -> int:
    """Тестовые составы с leader_id = админ: все уведомления приходят ему в ЛС.

    Так проверяется весь поток (приглашения, пары, сдача результата) без спама игрокам.
    """
    ensure_tournament_tables()
    added = 0
    conn = db.get_connection()
    existing = conn.execute(
        "SELECT COUNT(*) AS c FROM tournament_participants WHERE tournament_id = ?",
        (int(tournament_id),),
    ).fetchone()
    start = int(existing["c"] or 0)
    conn.close()
    for i in range(1, int(amount) + 1):
        name = f"{TOURNAMENT_TEST_PREFIX}{start + i:02d}"
        if add_tournament_participant(tournament_id, 900000 + start + i, name, "TST", int(admin_id)):
            added += 1
    return added


def clear_test_participants(tournament_id: int) -> int:
    """Убирает тестовые составы и пересобирает слоты заново."""
    ensure_tournament_tables()
    conn = db.get_connection()
    cur = conn.execute(
        "DELETE FROM tournament_participants WHERE tournament_id = ? AND team_name LIKE ?",
        (int(tournament_id), f"{TOURNAMENT_TEST_PREFIX}%"),
    )
    conn.commit()
    removed = cur.rowcount or 0
    conn.close()
    return removed


def get_participant_leaders(tournament_id: int):
    """Лидеры зарегистрированных составов (для тестовой рассылки вместо массовой)."""
    ensure_tournament_tables()
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT DISTINCT leader_id FROM tournament_participants "
        "WHERE tournament_id = ? AND leader_id IS NOT NULL AND leader_id > 0",
        (int(tournament_id),),
    ).fetchall()
    conn.close()
    return [int(r["leader_id"]) for r in rows]


async def get_tournament_topics() -> dict:
    """Читает ID топиков «Турниры» из настроек vigarik.db (config.py не трогаем)."""
    topics = {}
    for clan in TOURNAMENT_CLANS:
        try:
            raw = await get_setting(f"{TOURNAMENT_SETTINGS_PREFIX}{clan}")
        except Exception:
            raw = None
        if raw and str(raw).strip().lstrip("-").isdigit():
            topics[clan] = int(str(raw).strip())
    return topics


async def save_tournament_topic(clan_key: str, topic_id: int) -> None:
    await set_setting(f"{TOURNAMENT_SETTINGS_PREFIX}{clan_key}", str(int(topic_id)))


async def _test_publish_targets() -> list[tuple[int, int | None, str]]:
    """🧪 Тестовый турнир: посты идут ТОЛЬКО в топик основы (Squad).

    ID топика берётся из настроек «⚙️ Топики турниров» (ключ tournament_topic_squad).
    Если он не задан — падаем в админ-чат, чтобы не потерять пост.
    """
    chat_id = (config.CLAN_CHATS.get("squad") or {}).get("chat_id")
    raw_topic = await get_setting(f"{TOURNAMENT_SETTINGS_PREFIX}squad")
    if chat_id and raw_topic and str(raw_topic).strip().lstrip("-").isdigit():
        return [(int(chat_id), int(str(raw_topic).strip()), "🧪 основа (Squad)")]
    if config.ADMIN_CHAT_ID:
        return [(int(config.ADMIN_CHAT_ID), None,
                 "🧪 админ-чат (топик основы не задан — ⚙️ Топики турниров)")]
    return []


# ─── Сетка: слоты и автопроходы/BYE ─────────────────────────────────────────

def _seed_plan(names: list, t_type: str) -> dict:
    """Схема посева БЕЗ пачки BYE в первом круге.

    10 команд → не сетка на 16 с 6 автопроходами, а предварительный раунд
    (4 команды играют 2 матча) + основная сетка на 8, куда входят 6 команд
    напрямую и 2 победителя предвариловки.

    BYE остаётся только там, где команд реально не хватает: например,
    15 команд → сетка 16 и ровно 1 автопроход в 1/8.
    """
    names = [str(n) for n in (names or [])]
    n = len(names)
    if t_type == TOURNAMENT_SHOWDOWN:
        return {"solo": True, "names": names, "main_size": n, "playin_pairs": [],
                "direct": names, "total_rounds": 0}
    if n < 2:
        return {"solo": False, "names": names, "main_size": 2, "playin_pairs": [],
                "direct": names, "total_rounds": 1}
    next_pow = 1
    while next_pow < n:
        next_pow *= 2
    prev_pow = next_pow // 2
    byes = next_pow - n
    # много «пустых» мест — лучше крошечная предвариловка, чем 6 автопроходов
    use_playin = n >= 5 and byes > next_pow // 4
    if use_playin:
        main_size = prev_pow
        playin_count = n - main_size          # столько матчей в предвариловке
        playin_teams = names[:playin_count * 2]
        direct = names[playin_count * 2:]
        pairs = [(playin_teams[2 * i], playin_teams[2 * i + 1])
                 for i in range(playin_count)]
        return {"solo": False, "names": names, "main_size": main_size,
                "playin_pairs": pairs, "direct": direct,
                "total_rounds": 1 + int(math.log2(main_size))}
    # обычная сетка, BYE раскидываем в первом круге (их тут мало)
    slots: list = []
    byes_left = byes
    idx = 0
    for name in names:
        slots.append(name)
        if byes_left > 0:
            slots.append("BYE")
            byes_left -= 1
    while byes_left > 0:
        slots.append("BYE")
        byes_left -= 1
    return {"solo": False, "names": names, "main_size": next_pow,
            "playin_pairs": [], "direct": slots,
            "total_rounds": int(math.log2(next_pow))}


def _first_main_column(plan: dict) -> list:
    """Первая колонка основной сетки: слоты предвариловки + команды без BYE."""
    if plan.get("solo"):
        return list(plan["names"])
    playin = plan.get("playin_pairs") or []
    size = max(2, int(plan.get("main_size") or 2))
    direct = list(plan.get("direct") or [])
    col: list = ["BYE"] * size
    if playin:
        # победителей предвариловки разводим по разным половинам сетки,
        # чтобы W1 и W2 не встретились в первом же матче основной сетки
        step = max(1, size // max(1, len(playin)))
        for i in range(len(playin)):
            pos = i * step
            while pos < size and col[pos] != "BYE":
                pos += 1
            if pos >= size:
                pos = next((j for j in range(size) if col[j] == "BYE"), 0)
            col[pos] = f"Победитель пары {i + 1}"
    for name in direct:
        if name == "BYE":
            continue
        pos = next((i for i, v in enumerate(col) if v == "BYE"), None)
        if pos is None:
            break
        col[pos] = name
    return col


def _bracket_slots_for(t_type: str, team_limit) -> list[str]:
    """Список слотов превью-сетки под лимит (или под дефолт), с учётом BYE.

    ShowDown — соло, слотов ровно лимит (10). Для командных типов слотов всегда
    степень двойки: недостающие позиции заполняются BYE (автопроход).
    """
    if t_type == TOURNAMENT_SHOWDOWN:
        return [f"Слот {i}" for i in range(1, TOURNAMENT_SHOWDOWN_LIMIT + 1)]
    target = int(team_limit) if team_limit else TOURNAMENT_DEFAULT_BRACKET_SIZE
    size = 1
    while size < target:
        size *= 2
    slots = []
    byes = size - target
    # до регистрации показываем честные «свободные слоты», а не выдуманные команды
    real = [f"Свободный слот {i}" for i in range(1, target + 1)]
    # BYE распределяем в конец пар, чтобы «живые» команды встречались между собой
    ordered: list[str] = []
    insert_positions = set()
    idx = size - 1
    while byes > 0 and idx >= 0:
        insert_positions.add(idx)
        byes -= 1
        idx -= 2
    pos = 0
    for i in range(size):
        if i in insert_positions:
            ordered.append("BYE")
        else:
            ordered.append(real[pos] if pos < len(real) else "BYE")
            pos += 1
    slots = ordered
    return slots


def _bracket_pairs_text(slots: list[str]) -> list[str]:
    """Текстовое представление первого круга: A vs B, с пометкой автопрохода."""
    lines = []
    for i in range(0, len(slots), 2):
        a = slots[i]
        b = slots[i + 1] if i + 1 < len(slots) else "BYE"
        if a == "BYE" and b == "BYE":
            continue
        if a == "BYE":
            lines.append(f"{b} → автопроход (BYE)")
        elif b == "BYE":
            lines.append(f"{a} → автопроход (BYE)")
        elif str(a).startswith("Победитель пары"):
            lines.append(f"{b} → ждёт победителя предварительного раунда")
        elif str(b).startswith("Победитель пары"):
            lines.append(f"{a} → ждёт победителя предварительного раунда")
        else:
            lines.append(f"{a} vs {b}")
    return lines


def _load_font(size: int):
    """Шрифт с поддержкой кириллицы; при отсутствии — встроенный PIL-шрифт."""
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def _clean_team(text) -> str:
    """Убирает мусор из названия состава: 'TEST-03 [] TEST-05' → 'TEST-03 vs TEST-05'.

    Появлялось из-за пустых тегов («[]») и эмодзи-крестика, который шрифт не рисует.
    """
    raw = str(text or "")
    # убираем только эмодзи/пиктограммы (шрифт DejaVu их не рисует);
    # буквы, цифры, «№», тире и кавычки сохраняем
    cleaned = []
    for ch in raw:
        code = ord(ch)
        if ch in {"🆚", "⚔", "⚔️"}:
            cleaned.append(" vs ")
            continue
        if (
            0x1F000 <= code <= 0x1FAFF      # эмодзи
            or 0x2600 <= code <= 0x27BF     # символы/дингбаты
            or 0xFE00 <= code <= 0xFE0F     # variation selectors
            or 0x1F1E6 <= code <= 0x1F1FF   # флаги
            or code in (0x2192, 0x2190)     # стрелки
        ):
            continue
        cleaned.append(ch)
    out = "".join(cleaned).replace("[]", "")
    out = " ".join(out.split())
    return out.strip()


def _fit_text(draw, text, font, max_width: int) -> str:
    """Обрезает подпись по пикселям, чтобы она не вылезала из бокса."""
    text = _clean_team(text) or "—"
    try:
        if draw.textlength(text, font=font) <= max_width:
            return text
        while len(text) > 1 and draw.textlength(text + "…", font=font) > max_width:
            text = text[:-1]
        return text + "…"
    except Exception:
        return text[:28]


def _round_title(total: int, round_no: int) -> str:
    """1/8 финала / 1/4 финала / Полуфинал / Финал."""
    total = max(1, int(total))
    round_no = max(1, int(round_no))
    if round_no >= total:
        return "Финал"
    if round_no == total - 1:
        return "Полуфинал"
    return f"1/{2 ** (total - round_no)} финала"


def render_bracket_image(columns, title: str, subtitle: str = "",
                         third=None, solo: bool = False, labels=None,
                         playin=None, placements=None) -> bytes | None:
    """Красивая сетка: колонки по раундам, ровные линии, реальные названия команд.

    columns[0] — участники, columns[r] — победители раунда r.
    BYE подсвечивается серым, известные победители — зелёным.
    """
    if not PIL_AVAILABLE:
        return None
    try:
        columns = [list(col) for col in (columns or [])]
        if not columns:
            columns = [["Свободный слот", "Свободный слот"]]
        # колонки уже подготовлены вызывающим кодом: [участники, раунд 1, раунд 2, ...]
        # BYE больше не раздувает первый круг: предвариловка показывается отдельной колонкой
        solo = bool(solo) or len(columns) == 1
        total_rounds = max(0, len(columns) - 1)

        def stage_label(col_index: int) -> str:
            if labels and col_index < len(labels) and labels[col_index]:
                return labels[col_index]
            if col_index <= 0:
                return "Участники ShowDown" if solo else "Участники"
            from_end = len(columns) - col_index   # 1 = последняя колонка = финал
            if from_end == 1:
                return "Финал"
            if from_end == 2:
                return "Полуфинал"
            if from_end == 3:
                return "1/4 финала"
            if from_end == 4:
                return "1/8 финала"
            return f"1/{2 ** (from_end - 1)} финала"

        box_w, box_h, v_gap, gap_x, pad = 264, 58, 20, 66, 42
        row_h = box_h + v_gap
        placements = [(int(p), str(n)) for p, n in (placements or [])]
        # таблица призёров над сеткой: тёмный фон + 4 плитки в ряд
        tile_gap = 16
        tile_h = 92
        panel_h = (40 + tile_h + 22) if placements else 0
        header_h = 136 + panel_h
        base_h = header_h + len(columns[0]) * row_h + pad
        third_h = 132 if (third and (third.get("side_a") or third.get("side_b"))) else 0
        playin_h = 0
        if playin:
            playin_h = 76 + 26 * math.ceil(len(playin) / 2) + 30
        height = max(560, base_h + third_h + playin_h + (36 if third_h else 0))
        width = max(1000, pad * 2 + len(columns) * box_w + (len(columns) - 1) * gap_x)

        img = Image.new("RGB", (width, height), (15, 20, 32))
        draw = ImageDraw.Draw(img)
        f_title = _load_font(38)
        f_sub = _load_font(19)
        f_round = _load_font(18)
        f_slot = _load_font(20)
        f_small = _load_font(16)
        f_third = _load_font(22)

        # фон-шапка: только заголовок, без серой строки-подписи
        draw.rectangle([0, 0, width, 92], fill=(22, 29, 46))
        draw.rectangle([0, 90, width, 94], fill=(255, 209, 102))
        draw.text((pad, 24), _fit_text(draw, title, f_title, width - pad * 2),
                  font=f_title, fill=(255, 214, 102))

        # ── таблица победителей над сеткой (тёмная, плитки по местам) ─────────
        if placements:
            py0 = 94
            draw.rectangle([0, py0, width, py0 + panel_h], fill=(19, 25, 38))
            draw.line([(0, py0 + panel_h - 2), (width, py0 + panel_h - 2)],
                      fill=(255, 209, 102), width=2)

            # 1 — золото, 2 — серебро, 3 — бронза, 4 — тёмная бронза
            medal = {
                1: {"accent": (255, 209, 102), "fill": (45, 39, 21), "label": "1 МЕСТО"},
                2: {"accent": (203, 213, 225), "fill": (35, 41, 54), "label": "2 МЕСТО"},
                3: {"accent": (251, 146, 60), "fill": (46, 33, 21), "label": "3 МЕСТО"},
                4: {"accent": (180, 144, 110), "fill": (35, 31, 28), "label": "4 МЕСТО"},
            }
            tiles = placements[:4]
            tile_w = (width - pad * 2 - tile_gap * (len(tiles) - 1)) / max(1, len(tiles))
            for i, (place, name) in enumerate(tiles):
                tx = pad + i * (tile_w + tile_gap)
                ty = py0 + 26
                style = medal.get(place, {"accent": (150, 165, 195), "fill": (28, 35, 49),
                                          "label": f"{place} МЕСТО"})
                draw.rounded_rectangle(
                    [tx, ty, tx + tile_w, ty + tile_h], radius=14,
                    fill=style["fill"], outline=style["accent"], width=2,
                )
                # цветной маркер места
                draw.rounded_rectangle(
                    [tx + 12, ty + 12, tx + 96, ty + 32], radius=6,
                    fill=style["accent"],
                )
                marker_text = _fit_text(draw, style["label"], f_small, 76)
                draw.text((tx + 17, ty + 16), marker_text, font=f_small, fill=(24, 24, 24))
                draw.text((tx + 12, ty + 50),
                          _fit_text(draw, name, f_slot, tile_w - 24),
                          font=f_slot, fill=(238, 242, 250))

        top = header_h
        col_positions: list[list[float]] = []
        for c, col in enumerate(columns):
            if c == 0:
                centers = [top + i * row_h + box_h / 2 for i in range(len(col))]
            else:
                prev = col_positions[c - 1]
                centers = []
                for i in range(len(col)):
                    a = prev[2 * i] if 2 * i < len(prev) else prev[-1]
                    b = prev[2 * i + 1] if 2 * i + 1 < len(prev) else a
                    centers.append((a + b) / 2)
            col_positions.append(centers)

            x = pad + c * (box_w + gap_x)
            round_label = stage_label(c)
            label_w = draw.textlength(round_label, font=f_round)
            draw.text((x + (box_w - label_w) / 2, top - 30), round_label,
                      font=f_round, fill=(120, 150, 210))

        # линии между колонками
        for c in range(1, len(columns)):
            x_prev = pad + (c - 1) * (box_w + gap_x) + box_w
            x_cur = pad + c * (box_w + gap_x)
            mid_x = x_prev + gap_x / 2
            for i, y_cur in enumerate(col_positions[c]):
                y_a = col_positions[c - 1][2 * i] if 2 * i < len(col_positions[c - 1]) else y_cur
                y_b = col_positions[c - 1][2 * i + 1] if 2 * i + 1 < len(col_positions[c - 1]) else y_a
                draw.line([(x_prev, y_a), (mid_x, y_a)], fill=(70, 92, 136), width=2)
                draw.line([(x_prev, y_b), (mid_x, y_b)], fill=(70, 92, 136), width=2)
                draw.line([(mid_x, y_a), (mid_x, y_b)], fill=(70, 92, 136), width=2)
                draw.line([(mid_x, y_cur), (x_cur, y_cur)], fill=(70, 92, 136), width=2)

        # боксы
        placeholder_words = ("Победитель", "Свободный слот", "Слот ", "ждёт соперника", "?")
        for c, col in enumerate(columns):
            x = pad + c * (box_w + gap_x)
            for i, name in enumerate(col):
                cy = col_positions[c][i]
                name = str(name or "—")
                is_bye = name.strip().upper() == "BYE"
                is_placeholder = any(w in name for w in placeholder_words) or name.strip() == "—"
                is_winner = (not is_placeholder) and (not is_bye) and c > 0
                if is_bye:
                    fill, outline, color = (24, 30, 44), (52, 64, 88), (110, 122, 146)
                elif is_placeholder:
                    fill, outline, color = (26, 33, 49), (64, 80, 110), (126, 141, 168)
                elif is_winner:
                    fill, outline, color = (21, 52, 44), (52, 211, 153), (209, 250, 229)
                else:
                    fill, outline, color = (30, 38, 57), (88, 108, 146), (234, 240, 250)
                draw.rounded_rectangle(
                    [x, cy - box_h / 2, x + box_w, cy + box_h / 2],
                    radius=11, fill=fill, outline=outline, width=2,
                )
                prefix = ""
                if is_bye:
                    prefix = "BYE · "
                label = _fit_text(draw, prefix + name, f_slot, box_w - 26)
                draw.text((x + 13, cy - 12), label, font=f_slot, fill=color)

        # чемпион
        if not solo and len(columns) > 1:
            champ = _clean_team(columns[-1][0] if columns[-1] else "")
            if champ and not any(w in champ for w in placeholder_words) and champ.upper() != "BYE":
                x = pad + (len(columns) - 1) * (box_w + gap_x)
                cy = col_positions[-1][0]
                draw.text((x, cy - box_h / 2 - 28), "ЧЕМПИОН",
                          font=f_small, fill=(255, 214, 102))

        # предварительный раунд: показываем отдельным блоком под сеткой
        if playin:
            y0 = header_h + len(columns[0]) * row_h + 4
            draw.text((pad, y0), "ПРЕДВАРИТЕЛЬНЫЙ РАУНД", font=f_third,
                      fill=(120, 200, 255))
            for i, (a, b) in enumerate(playin):
                px = pad + (i % 2) * (box_w + gap_x)
                py = y0 + 34 + (i // 2) * 30
                draw.text((px, py),
                          _fit_text(draw, f"{_clean_team(a)} vs {_clean_team(b)}",
                                    f_slot, box_w + gap_x - 20),
                          font=f_slot, fill=(190, 205, 230))

        # матч за 3-4 места
        if third_h:
            y0 = header_h + len(columns[0]) * row_h + 4
            if playin:
                y0 += playin_h
            box3_w = min(width - pad * 2, box_w * 2 + gap_x)
            draw.rounded_rectangle(
                [pad, y0, pad + box3_w, y0 + 104], radius=14,
                fill=(43, 32, 20), outline=(245, 158, 11), width=2,
            )
            draw.text((pad + 18, y0 + 12), "МАТЧ ЗА 3-4 МЕСТА",
                      font=f_third, fill=(253, 186, 116))
            a = _clean_team(third.get("side_a") or "ждём проигравшего полуфинала")
            b = _clean_team(third.get("side_b") or "ждём проигравшего полуфинала")
            winner = _clean_team(third.get("winner") or "")
            draw.text((pad + 18, y0 + 50),
                      _fit_text(draw, f"{a}  vs  {b}", f_slot, box3_w - 36),
                      font=f_slot, fill=(245, 230, 210))
            if winner:
                draw.text((pad + 18, y0 + 76),
                          _fit_text(draw, f"Победитель: {winner}", f_small, box3_w - 36),
                          font=f_small, fill=(74, 222, 128))

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:  # noqa: BLE001
        logger.error(f"render_bracket_image error: {e}")
        return None


def _bracket_columns_for(row, participants=None):
    """Собирает колонки сетки с РЕАЛЬНЫМИ названиями команд из регистрации."""
    if participants is None:
        participants = get_tournament_participants(row["id"])
    solo = row["t_type"] == TOURNAMENT_SHOWDOWN
    first = _build_bracket_slots(participants, row["t_type"]) if participants else []
    if not first:
        try:
            first = json.loads(row["bracket_slots"] or "[]")
        except Exception:
            first = []
    # None / пустая строка — это BYE, а не «None» в названии команды
    first = ["BYE" if (x is None or not str(x).strip() or str(x).strip().upper() == "BYE")
             else str(x).strip() for x in first]
    if solo:
        names = [p["team_name"] or "—" for p in participants] or first
        return ([names] if names else [["Свободный слот"] * TOURNAMENT_SHOWDOWN_LIMIT]), None, []
    names = [p["team_name"] or "—" for p in participants]
    plan = _seed_plan(names, row["t_type"]) if names else None
    playin = []
    if plan and plan["playin_pairs"]:
        # предвариловка рисуется отдельным блоком, а сетка остаётся ровной
        playin = plan["playin_pairs"]
        first = _first_main_column(plan)
        columns = [first]
        total = plan["total_rounds"]           # включает предвариловку
        start_round = 2                        # основная сетка начинается со 2-го круга
    else:
        first = _build_bracket_slots(participants, row["t_type"]) if participants else []
        first = ["BYE" if (x is None or not str(x).strip()) else str(x).strip() for x in first]
        if not first:
            first = ["Свободный слот", "Свободный слот"]
        size = 1
        while size < max(2, len(first)):
            size *= 2
        first = (first + ["BYE"] * size)[:size]
        columns = [first]
        total = max(1, int(math.log2(size)))
        start_round = 1

    for r in range(start_round, total + 1):
        matches = {m["pair_index"]: m for m in get_tournament_matches(row["id"], r)}
        col = []
        pairs_count = max(1, len(columns[-1]) // 2)
        # в основной сетке после предвариловки пар становится больше
        if matches:
            pairs_count = max(pairs_count, max(matches) + 1)
        for i in range(pairs_count):
            m = matches.get(i) or {}
            if m.get("winner"):
                col.append(str(m["winner"]))
            elif m.get("side_a") and m.get("side_b"):
                # явная пара, чтобы в боксе не было «[ ]» и «vs vs»
                col.append(f"{m['side_a']} vs {m['side_b']}")
            elif m.get("side_a") or m.get("side_b"):
                col.append(f"{m.get('side_a') or m.get('side_b')} · ждёт соперника")
            else:
                col.append(f"Победитель {_stage_label(row, r)} №{i + 1}")
        columns.append(col)
    return columns, get_third_place_match(row["id"]), playin


def _bracket_subtitle(row, participants=None) -> str:
    if participants is None:
        participants = get_tournament_participants(row["id"])
    fmt = TOURNAMENT_FORMAT_LABELS.get(row["t_format"], "—") if row["t_format"] else "—"
    return (f"Тип: {row['t_type']} · Формат: {fmt} · Лимит: {_limit_text(row)} · "
            f"Начало: {row['scheduled_text'] or '—'} · Команд: {len(participants)}"
            + (" · ТЕСТ" if is_test_tournament(row) else ""))


def _render_for_row(row, title_suffix: str = "", participants=None) -> bytes | None:
    columns, third, playin = _bracket_columns_for(row, participants)
    placements = [] if row["t_type"] == TOURNAMENT_SHOWDOWN else get_placements(int(row["id"]))
    if row["t_type"] == TOURNAMENT_SHOWDOWN:
        labels = ["Участники ShowDown"]
    else:
        # первая колонка = сетка, дальше по раундам.
        # при предвариловке основная сетка начинается со 2-го раунда (round=2)
        offset = 1 if playin else 0
        labels = ["Участники"] + [
            _stage_label(row, r + offset) for r in range(1, len(columns))
        ]
    # когда призёры уже известны — сверху таблица, а не «актуальная сетка»
    suffix = " · Победители" if placements else title_suffix
    return render_bracket_image(
        columns,
        f"Турнир #{row['id']} · {row['t_type']}{suffix}",
        "",  # серая строка-подпись убрана по просьбе
        third=third,
        solo=(row["t_type"] == TOURNAMENT_SHOWDOWN),
        labels=labels,
        playin=playin,
        placements=placements,
    )


# ─── Тексты анонса ───────────────────────────────────────────────────────────

def _limit_text(row) -> str:
    if row["t_type"] == TOURNAMENT_SHOWDOWN:
        return f"{_fmt_num(TOURNAMENT_SHOWDOWN_LIMIT)} соло-участников"
    if row["team_limit"]:
        team_size = TOURNAMENT_TEAM_SIZE.get(row["t_type"], 1)
        return (f"{_fmt_num(row['team_limit'])} команд "
                f"(≈ {_fmt_num(int(row['team_limit']) * team_size)} игроков на игру)")
    return "♾ без лимита команд"


def _participants_block(participants, limit_rows: int = 20) -> str:
    if not participants:
        return "Пока никто не зарегистрирован — стань первым!"
    lines = []
    for i, p in enumerate(participants[:limit_rows], start=1):
        tag = (p["team_tag"] or "").strip()
        lines.append(f"{i}. {hd.quote(p['team_name'] or '—')}" + (f" [{hd.quote(tag)}]" if tag else ""))
    if len(participants) > limit_rows:
        lines.append(f"... и ещё {len(participants) - limit_rows}")
    return "\n".join(lines)


def _announcement_text(row, participants, for_clan_post: bool = True) -> str:
    typ = TOURNAMENT_TYPE_LABELS.get(row["t_type"], row["t_type"])
    fmt = TOURNAMENT_FORMAT_LABELS.get(row["t_format"], "—") if row["t_format"] else "—"
    status = TOURNAMENT_STATUS_LABEL.get(row["status"], row["status"])
    lines = [
        "🏆 <b>ТУРНИР VI GARIK SQUAD</b>",
        "",
        f"• Тип скрима: <b>{hd.quote(str(typ))}</b>",
        f"• Формат: <b>{hd.quote(str(fmt))}</b>" if row["t_format"] else "• Формат: <b>—</b>",
        f"• Лимит: <b>{_limit_text(row)}</b>",
        f"• Начало: <b>{hd.quote(str(row['scheduled_text'] or '—'))}</b>",
        f"• Статус: {hd.quote(str(status))}",
        "",
        "👥 <b>Участники</b>",
        _participants_block(participants),
    ]
    if for_clan_post:
        # реальные пары: матчи турнира, а до старта — зарегистрированные команды
        matches = get_tournament_matches(row["id"], 1)
        if matches:
            pairs = _pairs_schedule_text(matches).split("\n")
        else:
            columns, _third, _playin = _bracket_columns_for(row, participants)
            pairs = _bracket_pairs_text(columns[0])
        if pairs:
            lines += ["", "🗺 <b>Сетка (1-й круг)</b>"]
            lines += [f"• {hd.quote(p)}" for p in pairs[:16]]
            if len(pairs) > 16:
                lines.append(f"... и ещё {len(pairs) - 16}")
    lines += [
        "",
        "📌 В ростере команды может быть до 4 человек, на игру выходят по типу скрима — "
        "запасной в лимит не входит. Участвовать могут составы из любого из трёх кланов сети "
        "(Squad / Academy / Events).",
        "Регистрация — по кнопке ниже (только для лидера состава).",
    ]
    return "\n".join(lines)


def _announcement_caption(row) -> str:
    """Короткая подпись к картинке (лимит Telegram — 1024 символа)."""
    typ = TOURNAMENT_TYPE_LABELS.get(row["t_type"], row["t_type"])
    return (
        f"🏆 <b>Турнир #{row['id']} — {hd.quote(str(typ))}</b>\n"
        f"🕐 {hd.quote(str(row['scheduled_text'] or '—'))}\n"
        f"👥 Лимит: {_limit_text(row)}"
    )


def _no_composition_text() -> str:
    return (
        "⚠️ <b>У тебя нет состава</b>\n\n"
        "Чтобы участвовать в турнире, нужно сформировать состав: он может включать "
        "любых участников из трёх кланов сети (Squad / Academy / Events).\n\n"
        "Открой раздел «Составы🏆 (BetaTest)» в меню и нажми «➕ Создать состав», "
        "затем пригласи 3+ игроков. После этого регистрация на турнир станет доступна."
    )


# ─── Публикация анонса: топики + рассылка в ЛС ───────────────────────────────

async def _post_to_clan_topics(bot: Bot, row, participants) -> tuple[list[str], list[str]]:
    """Пост-анонс с картинкой сетки в топик «Турниры» каждого клана."""
    sent, failed = [], []
    # реальные названия команд из регистрации (а не «Команда 1»)
    png = _render_for_row(row, " · анонс", participants)
    # 🧪 Тестовый турнир: посты уходят только в топик основы (Squad)
    if is_test_tournament(row):
        targets = await _test_publish_targets()
        if not targets:
            return sent, ["не задан топик основы (⚙️ Топики турниров) и ADMIN_CHAT_ID"]
    else:
        topics = await get_tournament_topics()
        if not topics:
            return sent, ["топики не настроены (⚙️ Топики турниров)"]
        targets = [
            ((config.CLAN_CHATS.get(clan) or {}).get("chat_id"), topic, f"{clan} (топик {topic})")
            for clan, topic in topics.items()
        ]
    for chat_id, topic_id, label in targets:
        if not chat_id or (not topic_id and not is_test_tournament(row)):
            failed.append(f"{label}: не задан chat_id/топик")
            continue
        try:
            if png:
                post = await bot.send_photo(
                    chat_id=chat_id,
                    message_thread_id=topic_id,
                    photo=BufferedInputFile(png, filename=f"tournament_{row['id']}_bracket.png"),
                    caption=_announcement_caption(row),
                    parse_mode="HTML",
                )
                save_bracket_post(row["id"], chat_id, post.message_id, thread_id=topic_id)
                await bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=topic_id,
                    text=_announcement_text(row, participants),
                    parse_mode="HTML",
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=topic_id,
                    text=_announcement_text(row, participants),
                    parse_mode="HTML",
                )
            sent.append(label)
        except Exception as e:
            logger.warning(f"tournament announce to {label} failed: {e}")
            failed.append(f"{label}: {e}")
            # 🧪 fallback: топик основы мог быть указан неверно — не теряем пост
            if is_test_tournament(row) and config.ADMIN_CHAT_ID and chat_id != config.ADMIN_CHAT_ID:
                try:
                    await bot.send_message(
                        int(config.ADMIN_CHAT_ID),
                        f"⚠️ Не смог отправить в топик основы (ID {topic_id}): {e}\n"
                        "Проверь ID через «⚙️ Топики турниров». Пост анонса ниже.",
                        parse_mode="HTML",
                    )
                    await bot.send_message(
                        int(config.ADMIN_CHAT_ID),
                        _announcement_text(row, participants),
                        parse_mode="HTML",
                    )
                    sent.append("🧪 админ-чат (fallback)")
                except Exception as e2:
                    failed.append(f"fallback: {e2}")
    return sent, failed


async def _dm_registered_players(bot: Bot, row) -> dict:
    """Рассылка всем зарегистрированным: приглашение + проверка наличия состава.

    🧪 Тестовый турнир: массовая рассылка не выполняется вообще — приглашения
    получают только лидеры составов, уже записанных в этот турнир.
    """
    stats = {"total": 0, "with_team": 0, "without_team": 0, "not_leader": 0,
             "invited": 0, "skipped": 0, "failed": 0, "mode": "full"}
    if is_test_tournament(row):
        stats["mode"] = "test"
        for leader_id in get_participant_leaders(row["id"]):
            stats["total"] += 1
            try:
                await bot.send_message(
                    int(leader_id),
                    "🧪 <b>ТЕСТ</b> · Открыта регистрация на турнир #"
                    f"{row['id']}\n"
                    f"• Тип: {hd.quote(TOURNAMENT_TYPE_LABELS.get(row['t_type'], row['t_type']))}\n"
                    f"• Лимит: {_limit_text(row)}\n"
                    f"• Начало: {hd.quote(str(row['scheduled_text'] or '—'))}\n\n"
                    "Массовая рассылка в тестовом режиме отключена: это сообщение получили "
                    "только лидеры составов, записанных в турнир.",
                    parse_mode="HTML",
                    reply_markup=tournament_register_keyboard(row["id"]),
                )
                stats["with_team"] += 1
                stats["invited"] += 1
                await asyncio.sleep(TOURNAMENT_DM_PAUSE)
            except Exception:
                stats["failed"] += 1
        return stats
    try:
        members = await get_all_members()
    except Exception as e:
        logger.error(f"tournament dm: get_all_members failed: {e}")
        return stats
    already_ids = {int(p["league_id"]) for p in get_tournament_participants(row["id"])}

    for m in members:
        stats["total"] += 1
        uid = m.get("user_id")
        if not uid or m.get("registered") != 1:
            stats["skipped"] += 1
            continue
        composition = None
        try:
            composition = get_user_league(int(uid))
        except Exception:
            composition = None
        try:
            if not composition:
                stats["without_team"] += 1
                await bot.send_message(int(uid), _no_composition_text(), parse_mode="HTML")
            elif int(composition["leader_id"]) == int(uid):
                stats["with_team"] += 1
                if int(composition["id"]) in already_ids:
                    text = (
                        f"🏆 <b>Турнир #{row['id']}</b> — твой состав "
                        f"{hd.quote(composition['name'])} [{hd.quote(composition['tag'])}] "
                        f"уже зарегистрирован.\n"
                        f"🕐 {hd.quote(str(row['scheduled_text'] or '—'))}"
                    )
                else:
                    text = (
                        f"🏆 <b>Открыта регистрация на турнир #{row['id']}</b>\n\n"
                        f"• Тип: {hd.quote(TOURNAMENT_TYPE_LABELS.get(row['t_type'], row['t_type']))}\n"
                        f"• Формат: {hd.quote(TOURNAMENT_FORMAT_LABELS.get(row['t_format'], '—'))}\n"
                        f"• Лимит: {_limit_text(row)}\n"
                        f"• Начало: {hd.quote(str(row['scheduled_text'] or '—'))}\n\n"
                        f"Твой состав: {hd.quote(composition['name'])} "
                        f"[{hd.quote(composition['tag'])}] (MMR {int(composition['mmr'] or 0)}🌟)."
                    )
                await bot.send_message(
                    int(uid), text, parse_mode="HTML",
                    reply_markup=tournament_register_keyboard(row["id"]),
                )
                stats["invited"] += 1
            else:
                stats["not_leader"] += 1
                await bot.send_message(
                    int(uid),
                    "🏆 Открыта регистрация на турнир сети! Регистрировать состав может только "
                    "его лидер — передай ему эту информацию.",
                    parse_mode="HTML",
                )
            await asyncio.sleep(TOURNAMENT_DM_PAUSE)
        except Exception:
            stats["failed"] += 1
    return stats


async def _send_preview(call: CallbackQuery, row, as_caption: bool = False):
    """Картинка сетки с реальными командами (или текстовая сетка без Pillow)."""
    png = _render_for_row(row)
    if png:
        await call.message.answer_photo(
            photo=BufferedInputFile(png, filename=f"tournament_{row['id']}_bracket.png"),
            caption=_announcement_caption(row) if as_caption else None,
            parse_mode="HTML",
        )
        return
    columns, _third, _playin = _bracket_columns_for(row)
    pairs = [f"{_clean_team(a) or '—'} vs {_clean_team(b) or '—'}"
             for a, b in zip(columns[0][::2], columns[0][1::2])]
    text = "\n".join([f"🗺 <b>Сетка турнира #{row['id']}</b>"] + [f"• {hd.quote(p)}" for p in pairs])
    text += "\n\nℹ️ Библиотека Pillow не установлена — картинка сетки недоступна. Установи: pip install Pillow"
    await call.message.answer(text, parse_mode="HTML")


# ─── Этап 4: сборка сетки, автостарт, уведомления капитанам ─────────────────

def _build_bracket_slots(participants, t_type: str) -> list:
    """Первая колонка основной сетки по факту регистрации (без пачки BYE).

    10 команд → предварительный раунд на 4 команды, дальше сетка на 8:
    слоты предвариловки + 6 команд напрямую. BYE остаются только при малом
    числе пустых мест (например, 15 команд → 16 слотов и 1 автопроход).
    """
    names = [p["team_name"] or f"#{p['league_id']}" for p in participants]
    if t_type == TOURNAMENT_SHOWDOWN:
        return names  # соло: слот = участник
    plan = _seed_plan(names, t_type)
    return _first_main_column(plan)


def _bracket_plan_matches(participants, t_type: str):
    """Матчи предварительного раунда (если нужен) и первая колонка основной сетки."""
    names = [p["team_name"] or f"#{p['league_id']}" for p in participants]
    plan = _seed_plan(names, t_type)
    return plan, _first_main_column(plan)


def _bracket_matches(slots: list, t_type: str) -> list[dict]:
    """Пары 1-го круга. Для ShowDown — один «мачт» со всеми соло-участниками."""
    if t_type == TOURNAMENT_SHOWDOWN:
        return [{"side_a": s, "side_b": None, "winner": None, "status": "solo"} for s in slots]
    matches = []
    for i in range(0, len(slots), 2):
        a = slots[i] if i < len(slots) else None
        b = slots[i + 1] if i + 1 < len(slots) else None
        if a is None and b is None:
            continue
        if a is None:
            matches.append({"side_a": None, "side_b": b, "winner": b, "status": "bye"})
        elif b is None:
            matches.append({"side_a": a, "side_b": None, "winner": a, "status": "bye"})
        else:
            matches.append({"side_a": a, "side_b": b, "winner": None, "status": "scheduled"})
    return matches


async def _leader_mention(leader_id) -> str:
    """@username лидера для уведомления капитану (или ID, если юзернейма нет)."""
    try:
        m = await get_member(int(leader_id))
    except Exception:
        m = None
    uname = (m or {}).get("username") if m else None
    if uname and str(uname).lower() not in ("unknown", "none"):
        return f"@{uname}"
    return f"ID {leader_id}"


def _stage_label(row, round_no: int) -> str:
    """Человекочитаемая стадия: 1/8 финала, полуфинал, финал, матч за 3-4 места."""
    if int(round_no) == 0:
        return "Матч за 3-4 места"
    total = _total_rounds(_bracket_size(row))
    # предварительный раунд (play-in) появляется, когда команд не хватает до сетки
    participants = get_tournament_participants(row["id"])
    names = [p["team_name"] or "" for p in participants]
    plan = _seed_plan(names, row["t_type"]) if names else {"playin_pairs": []}
    has_playin = bool(plan.get("playin_pairs"))
    if has_playin:
        if int(round_no) == 1:
            return "Предварительный раунд"
        if int(round_no) >= total:
            return "Финал"
        if int(round_no) == total - 1:
            return "Полуфинал"
        return f"1/{_fmt_num(2 ** (total - int(round_no)))} финала"
    if int(round_no) >= total:
        return "Финал"
    if int(round_no) == total - 1:
        return "Полуфинал"
    return f"1/{_fmt_num(2 ** (total - int(round_no)))} финала"


# ─── Автостарт следующего круга (Этап 5+) ───────────────────────────────────

def mark_round_started(tournament_id: int, round_no: int) -> None:
    ensure_tournament_tables()
    conn = db.get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO tournament_rounds (tournament_id, round_no) VALUES (?, ?)",
        (int(tournament_id), int(round_no)),
    )
    conn.commit()
    conn.close()


def get_started_rounds(tournament_id: int) -> set:
    ensure_tournament_tables()
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT round_no FROM tournament_rounds WHERE tournament_id = ?",
        (int(tournament_id),),
    ).fetchall()
    conn.close()
    return {int(r["round_no"]) for r in rows}


def _round_resolved(matches) -> bool:
    """Раунд закрыт: у каждого матча есть победитель (или это BYE)."""
    if not matches:
        return False
    return all(bool(m["winner"]) or m["status"] == "bye" for m in matches)


async def _round_publish_targets(row):
    if is_test_tournament(row):
        return await _test_publish_targets()
    topics = await get_tournament_topics()
    return [
        ((config.CLAN_CHATS.get(clan) or {}).get("chat_id"), topic, f"{clan} (топик {topic})")
        for clan, topic in topics.items()
    ]


async def _dm_new_pairs(bot: Bot, row, pairs, stage: str) -> int:
    """ЛС капитанам новых пар: «У вас скрим Бо3 против …, спишитесь с лидером…»."""
    participants = {p["team_name"]: p for p in get_tournament_participants(row["id"])}
    fmt = TOURNAMENT_FORMAT_LABELS.get(row["t_format"], "Бо3") if row["t_format"] else "Бо3"
    sent = 0
    for a_name, b_name in pairs:
        a, b = participants.get(a_name), participants.get(b_name)
        if not a or not b or not a.get("leader_id") or not b.get("leader_id"):
            continue
        for me, opp in ((a, b), (b, a)):
            try:
                mention = await _leader_mention(opp["leader_id"])
                await bot.send_message(
                    int(me["leader_id"]),
                    f"⚔️ <b>{hd.quote(stage)}</b> · у вас скрим {hd.quote(fmt)} против команды "
                    f"{hd.quote(str(opp['team_name']))}, спишитесь с их лидером: "
                    f"<b>{hd.quote(mention)}</b>.\n"
                    f"• Турнир #{row['id']}",
                    parse_mode="HTML",
                )
                sent += 1
            except Exception:
                pass
            await asyncio.sleep(TOURNAMENT_DM_PAUSE)
    return sent


async def _announce_round_start(bot: Bot, row, round_no: int, matches) -> None:
    """Публикует старт круга в топик и присылает пары капитанам."""
    pairs = [(m["side_a"], m["side_b"]) for m in matches
             if m["side_a"] and m["side_b"] and not m["winner"]]
    if not pairs:
        return
    stage = _stage_label(row, round_no)
    text = (
        f"🚨 <b>Старт: {hd.quote(stage)}</b> · турнир #{row['id']}\n\n"
        + "\n".join(f"• {hd.quote(a)} 🆚 {hd.quote(b)}" for a, b in pairs)
        + "\n\nСдайте результат через «🏁 Мои матчи» или его внесёт администрация."
    )
    for chat_id, thread_id, _label in await _round_publish_targets(row):
        if not chat_id:
            continue
        try:
            await bot.send_message(chat_id=chat_id, message_thread_id=thread_id,
                                   text=text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"round announce failed: {e}")
    await _dm_new_pairs(bot, row, pairs, stage)
    mark_round_started(row["id"], round_no)


async def _auto_start_rounds(bot: Bot, row) -> list[str]:
    """Как только все матчи круга закрыты — следующий круг стартует сам."""
    notes = []
    tid = int(row["id"])
    started = get_started_rounds(tid)
    total = _total_rounds(_bracket_size(row))
    finished_all = True
    for r in range(1, total + 1):
        matches = get_tournament_matches(tid, r)
        if not matches:
            if not _round_resolved(get_tournament_matches(tid, r - 1)):
                finished_all = False
            break
        if not _round_resolved(matches):
            finished_all = False
            ready = [m for m in matches if m["side_a"] and m["side_b"]]
            if ready and r not in started:
                await _announce_round_start(bot, row, r, matches)
                notes.append(f"▶️ Автостарт: {_stage_label(row, r)}")
            break
    third = get_third_place_match(tid)
    if third and third["side_a"] and third["side_b"] and not third["winner"] \
            and 0 not in started:
        await _announce_round_start(bot, row, 0, [third])
        notes.append("▶️ Автостарт: матч за 3-4 места")
    if finished_all and third and third["winner"]:
        set_tournament_status(tid, "finished")
    return notes


def _row_val(row, key: str, default=None):
    """Безопасное чтение поля из dict или sqlite3.Row (у Row нет .get())."""
    if row is None:
        return default
    try:
        value = row[key]
        return default if value is None else value
    except (KeyError, IndexError, TypeError):
        return default


def _pairs_schedule_text(matches) -> str:
    lines = []
    for i, m in enumerate(matches, start=1):
        a, b = _row_val(m, "side_a"), _row_val(m, "side_b")
        if _row_val(m, "status", "scheduled") == "bye":
            lines.append(f"{_fmt_num(i)}. {hd.quote(str(b or a))} → автопроход (BYE)")
        elif _row_val(m, "status", "scheduled") == "solo":
            lines.append(f"{_fmt_num(i)}. {hd.quote(str(a))} — соло-слот ShowDown")
        else:
            lines.append(f"{_fmt_num(i)}. {hd.quote(str(a))} 🆚 {hd.quote(str(b))}")
    return "\n".join(lines) if lines else "Пар пока нет."


def _tournament_start_text(row, participants, matches) -> str:
    typ = TOURNAMENT_TYPE_LABELS.get(row["t_type"], row["t_type"])
    fmt = TOURNAMENT_FORMAT_LABELS.get(row["t_format"], "—") if row["t_format"] else "—"
    lines = [
        "🚨 <b>ТУРНИР НАЧАЛСЯ!</b>",
        "",
        f"• Турнир: <b>#{row['id']} — {hd.quote(str(typ))}</b>",
        f"• Формат: <b>{hd.quote(str(fmt))}</b>",
        f"• Время: <b>{hd.quote(str(row['scheduled_text'] or '—'))}</b>",
        f"• Участников: <b>{_fmt_num(len(participants))}</b>",
        "",
        "🗺 <b>Расписание пар (1-й круг)</b>",
        _pairs_schedule_text(matches),
        "",
        "Победители пар проходят дальше — следующий круг запустит администрация.",
    ]
    return "\n".join(lines)


async def _post_tournament_start(bot: Bot, row, participants, matches) -> tuple[list[str], list[str]]:
    """Финальный пост с сеткой и парами: кланам — в топик «Турниры», тесту — в админ-чат."""
    sent, failed = [], []
    png = _render_for_row(row, " · СТАРТ", participants)
    text = _tournament_start_text(row, participants, matches)
    caption = _bracket_status_caption(row)
    # 🧪 Тестовый турнир: посты старта — только в топик основы (Squad)
    if is_test_tournament(row):
        targets = await _test_publish_targets()
        if not targets:
            return sent, ["не задан топик основы (⚙️ Топики турниров) и ADMIN_CHAT_ID"]
    else:
        topics = await get_tournament_topics()
        if not topics:
            return sent, ["топики не настроены (⚙️ Топики турниров)"]
        targets = [
            ((config.CLAN_CHATS.get(clan) or {}).get("chat_id"), topic, f"{clan} (топик {topic})")
            for clan, topic in topics.items()
        ]
    for chat_id, thread_id, label in targets:
        if not chat_id:
            failed.append(f"{label}: нет chat_id")
            continue
        try:
            # пост с сеткой — именно его потом обновляем по мере результатов
            if png:
                post = await bot.send_photo(
                    chat_id=chat_id,
                    message_thread_id=thread_id,
                    photo=BufferedInputFile(png, filename=f"tournament_{row['id']}_start.png"),
                    caption=caption[:1024],
                    parse_mode="HTML",
                )
                save_bracket_post(row["id"], chat_id, post.message_id, thread_id=thread_id)
            await bot.send_message(
                chat_id=chat_id, message_thread_id=thread_id, text=text, parse_mode="HTML",
            )
            sent.append(label)
        except Exception as e:
            logger.warning(f"tournament start post to {label} failed: {e}")
            failed.append(f"{label}: {e}")
            if is_test_tournament(row) and config.ADMIN_CHAT_ID and chat_id != config.ADMIN_CHAT_ID:
                try:
                    await bot.send_message(
                        int(config.ADMIN_CHAT_ID),
                        f"⚠️ Не смог отправить сетку в топик основы (ID {thread_id}): {e}\n"
                        "Проверь ID через «⚙️ Топики турниров».",
                        parse_mode="HTML",
                    )
                    await bot.send_message(
                        int(config.ADMIN_CHAT_ID), text, parse_mode="HTML",
                    )
                    sent.append("🧪 админ-чат (fallback)")
                except Exception as e2:
                    failed.append(f"fallback: {e2}")
    return sent, failed


async def start_tournament(bot: Bot, row) -> str:
    """Старт турнира: сетка + пары, пост в топики, ЛС участникам и капитанам."""
    ensure_tournament_tables()
    tid = int(row["id"])
    participants = get_tournament_participants(tid)
    if not participants:
        set_tournament_status(tid, "cancelled")
        return f"⛔ Турнир #{tid} отменён: никто не зарегистрировался."

    if row["t_type"] == TOURNAMENT_SHOWDOWN and len(participants) > TOURNAMENT_SHOWDOWN_LIMIT:
        participants = participants[:TOURNAMENT_SHOWDOWN_LIMIT]

    plan, first_col = _bracket_plan_matches(participants, row["t_type"])
    if plan["solo"]:
        matches = [{"side_a": s, "side_b": None, "winner": None, "status": "solo"}
                   for s in plan["names"]]
        replace_tournament_matches(tid, matches)
        update_tournament_bracket(tid, plan["names"])
    elif plan["playin_pairs"]:
        # предварительный раунд: только матчи между командами, без «BYE vs BYE»
        matches = [{"side_a": a, "side_b": b, "winner": None, "status": "scheduled"}
                   for a, b in plan["playin_pairs"]]
        replace_tournament_matches(tid, matches)
        # основная сетка: команды напрямую + слоты под победителей предвариловки
        main_col = _first_main_column(plan)
        main_matches = []
        i = 0
        while i < len(main_col):
            a = main_col[i]
            b = main_col[i + 1] if i + 1 < len(main_col) else "BYE"
            a = None if str(a).startswith("Победитель пары") else a
            b = None if str(b).startswith("Победитель пары") else b
            if a is None and b is None:
                pass
            elif a is None:
                main_matches.append({"side_a": None, "side_b": b, "winner": None, "status": "scheduled"})
            elif b is None:
                main_matches.append({"side_a": a, "side_b": None, "winner": a, "status": "bye"})
            else:
                main_matches.append({"side_a": a, "side_b": b, "winner": None, "status": "scheduled"})
            i += 2
        replace_tournament_matches(tid, main_matches, round_no=2)
        update_tournament_bracket(tid, main_col)
    else:
        matches = _bracket_matches(plan["direct"], row["t_type"])
        replace_tournament_matches(tid, matches)
        update_tournament_bracket(tid, first_col)
    # победители BYE-матчей сразу продвигаются в следующий круг
    _propagate_auto_advances(tid)
    mark_round_started(tid, 1)   # первый круг объявлен постом старта
    set_tournament_status(tid, "running")

    # в посте старта показываем и предвариловку, и реальные пары основной сетки
    post_matches = list(matches)
    if plan["playin_pairs"]:
        post_matches += [m for m in get_tournament_matches(tid, 2)
                         if m["side_a"] and m["side_b"]]
    topics_sent, topics_failed = await _post_tournament_start(bot, row, participants, post_matches)

    # ── ЛС капитанам (п.3 ТЗ): соперник + @username его лидера
    by_name = {p["team_name"]: p for p in participants}
    notified, not_leader, failed_dm = 0, 0, 0
    fmt = TOURNAMENT_FORMAT_LABELS.get(row["t_format"], "Бо3") if row["t_format"] else "Бо3"
    if row["t_type"] == TOURNAMENT_SHOWDOWN:
        for i, p in enumerate(participants, start=1):
            try:
                await bot.send_message(
                    int(p["leader_id"]),
                    f"🚨 <b>ShowDown турнир #{tid} начался!</b>\n"
                    f"• Твой слот: <b>{_fmt_num(i)}</b> из {_fmt_num(len(participants))}\n"
                    f"• Состав: {hd.quote(p['team_name'] or '—')}\n"
                    f"• Время: {hd.quote(str(row['scheduled_text'] or '—'))}\n\n"
                    f"Удачи! Скриншоты результата отправляй администрации.",
                    parse_mode="HTML",
                )
                notified += 1
                await asyncio.sleep(TOURNAMENT_DM_PAUSE)
            except Exception:
                failed_dm += 1
    else:
        for m in matches:
            if m.get("status") == "bye":
                try:
                    p = by_name.get(m.get("winner"))
                    if p and p["leader_id"]:
                        await bot.send_message(
                            int(p["leader_id"]),
                            f"⚔️ <b>Турнир #{tid}: у вас автопроход (BYE)</b> в 1-м круге.\n"
                            f"Состав {hd.quote(p['team_name'] or '—')} сразу проходит дальше — "
                            f"ждите соперника в следующем круге.",
                            parse_mode="HTML",
                        )
                        notified += 1
                        await asyncio.sleep(TOURNAMENT_DM_PAUSE)
                except Exception:
                    failed_dm += 1
                continue
            a, b = by_name.get(m.get("side_a")), by_name.get(m.get("side_b"))
            if not a or not b:
                continue
            try:
                opp_b = await _leader_mention(b["leader_id"])
                await bot.send_message(
                    int(a["leader_id"]),
                    f"⚔️ У вас скрим {hd.quote(fmt)} против команды "
                    f"{hd.quote(str(b['team_name'] or '—'))}, спишитесь с их лидером: "
                    f"<b>{hd.quote(opp_b)}</b>.\n"
                    f"• Турнир #{tid} · {hd.quote(str(row['scheduled_text'] or '—'))}\n"
                    f"• Тип: {hd.quote(TOURNAMENT_TYPE_LABELS.get(row['t_type'], row['t_type']))}",
                    parse_mode="HTML",
                )
                notified += 1
            except Exception:
                failed_dm += 1
            await asyncio.sleep(TOURNAMENT_DM_PAUSE)
            try:
                opp_a = await _leader_mention(a["leader_id"])
                await bot.send_message(
                    int(b["leader_id"]),
                    f"⚔️ У вас скрим {hd.quote(fmt)} против команды "
                    f"{hd.quote(str(a['team_name'] or '—'))}, спишитесь с их лидером: "
                    f"<b>{hd.quote(opp_a)}</b>.\n"
                    f"• Турнир #{tid} · {hd.quote(str(row['scheduled_text'] or '—'))}\n"
                    f"• Тип: {hd.quote(TOURNAMENT_TYPE_LABELS.get(row['t_type'], row['t_type']))}",
                    parse_mode="HTML",
                )
                notified += 1
            except Exception:
                failed_dm += 1
            await asyncio.sleep(TOURNAMENT_DM_PAUSE)

    report = (
        f"🚀 <b>Турнир #{tid} запущен</b>\n"
        f"• участников: {_fmt_num(len(participants))}\n"
        f"• пар 1-го круга: {_fmt_num(len(matches))}\n"
        f"• посты в топиках: ✅ {len(topics_sent)} / ❌ {len(topics_failed)}\n"
        f"• уведомлений капитанам/участникам: {notified}, ошибок: {failed_dm}"
    )
    if topics_failed:
        report += "\n❌ Не отправлено: " + "; ".join(topics_failed)
    return report


async def tournament_scheduler_task(bot: Bot) -> None:
    """Этап 4, п.2: автозапуск турниров в назначенное время."""
    await asyncio.sleep(20)
    logger.info("tournament scheduler started")
    while True:
        try:
            now_iso = datetime.now().strftime("%Y-%m-%d %H:%M")
            for row in get_due_tournaments(now_iso):
                try:
                    report = await start_tournament(bot, row)
                    logger.info(f"auto-start tournament #{row['id']}: ok")
                    await log_admin_action(
                        bot=bot,
                        admin_id=int(row["created_by"] or 0),
                        admin_name=row["created_by_name"] or "auto",
                        action_text=f"⏰ Автозапуск турнира #{row['id']} по расписанию.",
                    )
                    try:
                        await bot.send_message(int(row["created_by"]), report, parse_mode="HTML")
                    except Exception:
                        pass
                except Exception as e:
                    logger.error(f"auto-start tournament #{row['id']} failed: {e}")
        except Exception as e:
            logger.error(f"tournament scheduler error: {e}")
        await asyncio.sleep(30)


@router.startup()
async def on_tournament_scheduler_start(**kwargs):
    """Старт фоновой задачи без правок main.py (Router.startup пробрасывается диспетчером).

    Бот достаётся из kwargs (aiogram передаёт bot в startup), с фоллбеком на
    текущий контекст — чтобы запуск не зависел от версии aiogram.
    """
    bot = kwargs.get("bot")
    if bot is None:
        try:
            bot = Bot.get_current()
        except Exception:
            bot = None
    if bot is None:
        logger.warning("tournament scheduler: bot not available on startup")
        return
    try:
        asyncio.create_task(tournament_scheduler_task(bot))
        logger.info("tournament scheduler task created")
    except Exception as e:
        logger.error(f"Не удалось запустить планировщик турниров: {e}")


# ─── Раздел «⚔️ Текущие турниры» (Этап 4, п.1) ──────────────────────────────

@router.message(F.text == "⚔️ Текущие турниры")
async def admin_open_current_tournaments(message: Message):
    member = await get_member(message.from_user.id)
    if not member or not has_role(member, "vice"):
        await message.answer("⛔ Доступ только для Вице Президент и выше.")
        return
    rows = get_active_tournaments()
    if not rows:
        await message.answer(
            "⚔️ Активных турниров нет.\nСоздай новый через «🏆 Создание турнира».",
            reply_markup=admin_scrims_keyboard(),
        )
        return
    lines = ["⚔️ <b>Текущие турниры</b>", ""]
    for r in rows:
        when = r["scheduled_text"] or "—"
        soon = " ⏰" if r["scheduled_at"] and r["scheduled_at"] <= datetime.now().strftime("%Y-%m-%d %H:%M") else ""
        lines.append(
            f"{'🧪 ' if is_test_tournament(r) else ''}#{r['id']} · {hd.quote(str(r['t_type']))} · "
            f"{hd.quote(str(when))}{soon} · "
            f"{hd.quote(TOURNAMENT_STATUS_LABEL.get(r['status'], r['status']))} · "
            f"👥 {_fmt_num(len(get_tournament_participants(r['id'])))}"
        )
    lines += ["", "⏰ — время старта уже наступило, турнир запустится автоматически."]
    await message.answer("\n".join(lines), parse_mode="HTML",
                         reply_markup=tournament_current_list_keyboard(rows))


@router.callback_query(F.data == "tour_cur:list")
async def admin_current_tournaments_refresh(call: CallbackQuery):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    rows = get_active_tournaments()
    try:
        await call.message.delete()
    except Exception:
        pass
    if not rows:
        await call.message.answer("⚔️ Активных турниров нет.", reply_markup=admin_scrims_keyboard())
        await call.answer("Обновлено")
        return
    lines = ["⚔️ <b>Текущие турниры</b>", ""]
    for r in rows:
        lines.append(
            f"#{r['id']} · {hd.quote(str(r['t_type']))} · "
            f"{hd.quote(str(r['scheduled_text'] or '—'))} · "
            f"{hd.quote(TOURNAMENT_STATUS_LABEL.get(r['status'], r['status']))} · "
            f"👥 {_fmt_num(len(get_tournament_participants(r['id'])))}"
        )
    await call.message.answer("\n".join(lines), parse_mode="HTML",
                              reply_markup=tournament_current_list_keyboard(rows))
    await call.answer("Обновлено")


@router.callback_query(F.data.startswith("tour_cur:open:"))
async def admin_current_tournament_card(call: CallbackQuery):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    row = get_tournament(int(call.data.rsplit(":", 1)[-1]))
    if not row:
        await call.answer("Турнир не найден", show_alert=True)
        return
    participants = get_tournament_participants(row["id"])
    matches = get_tournament_matches(row["id"], 1)
    auto = ""
    if row["scheduled_at"] and row["status"] in ("registration", "announced"):
        auto = "\n⏰ Автозапуск по расписанию включён."
    winners = placements_text(row["id"])
    text = (
        f"⚔️ <b>Текущий турнир #{row['id']}</b>\n"
        f"• Тип: {hd.quote(TOURNAMENT_TYPE_LABELS.get(row['t_type'], row['t_type']))}\n"
        f"• Формат: {hd.quote(TOURNAMENT_FORMAT_LABELS.get(row['t_format'], '—'))}\n"
        f"• Лимит: {_limit_text(row)}\n"
        f"• Начало: {hd.quote(str(row['scheduled_text'] or '—'))}\n"
        f"• Статус: {hd.quote(TOURNAMENT_STATUS_LABEL.get(row['status'], row['status']))}\n\n"
        f"👥 Поданных составов: <b>{_fmt_num(len(participants))}</b>\n"
        f"{_participants_block(participants, 15)}\n\n"
        f"🗺 Пар 1-го круга: {_fmt_num(len(matches))}{auto}"
        + (f"\n\n🏅 <b>Победители</b>\n{hd.quote(winners)}" if winners else "")
    )
    try:
        await call.message.edit_text(text, parse_mode="HTML",
                                     reply_markup=tournament_current_admin_keyboard(row["id"]))
    except Exception:
        await call.message.answer(text, parse_mode="HTML",
                                  reply_markup=tournament_current_admin_keyboard(row["id"]))
    await call.answer()


@router.callback_query(F.data.startswith("tour_cur:limit:"))
async def admin_current_limit_ask(call: CallbackQuery, state: FSMContext):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    row = get_tournament(tid)
    if not row:
        await call.answer("Турнир не найден", show_alert=True)
        return
    await state.update_data(edit_tournament_id=tid)
    await state.set_state(TournamentStates.waiting_new_limit)
    cur = row["team_limit"]
    await call.message.answer(
        f"🔢 Текущий лимит турнира #{tid}: <b>{_fmt_num(cur) if cur else '♾ без лимита'}</b> команд.\n\n"
        f"Введи новое число команд ({TOURNAMENT_MIN_TEAMS}–{_fmt_num(TOURNAMENT_MAX_TEAMS)}) "
        f"или <code>0</code>, чтобы убрать лимит.\n"
        "Для отмены — /cancel или «❌ Отмена».",
        parse_mode="HTML",
        reply_markup=tournament_cancel_keyboard(),
    )
    await call.answer()


@router.message(TournamentStates.waiting_new_limit)
async def admin_current_limit_save(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    tid = data.get("edit_tournament_id")
    raw = (message.text or "").strip().replace(" ", "")
    if not raw.isdigit():
        await message.answer("❌ Нужно целое число команд (или 0 — без лимита). Попробуй ещё раз.")
        return
    value = int(raw)
    if value == 0:
        update_tournament_limit(tid, None)
        new_txt = "♾ без лимита команд"
    elif TOURNAMENT_MIN_TEAMS <= value <= TOURNAMENT_MAX_TEAMS:
        update_tournament_limit(tid, value)
        new_txt = f"{_fmt_num(value)} команд"
    else:
        await message.answer(
            f"❌ Лимит должен быть 0 (без лимита) или от {TOURNAMENT_MIN_TEAMS} "
            f"до {_fmt_num(TOURNAMENT_MAX_TEAMS)}. Попробуй ещё раз."
        )
        return
    await state.clear()
    row = get_tournament(tid)
    participants = get_tournament_participants(tid)
    over = ""
    if row["team_limit"] and len(participants) > int(row["team_limit"]):
        over = (f"\n⚠️ Уже подано {_fmt_num(len(participants))} составов — "
                "лишние будут отклоняться, но уже зарегистрированные не удаляются.")
    await log_admin_action(
        bot=bot, admin_id=message.from_user.id,
        admin_name=message.from_user.username or message.from_user.full_name or "admin",
        action_text=f"🔢 Изменил лимит турнира #{tid} на {new_txt}.",
    )
    await message.answer(
        f"✅ Лимит турнира #{tid} обновлён: <b>{new_txt}</b>.{over}\n\n"
        f"👥 Составов: {_fmt_num(len(participants))}",
        parse_mode="HTML",
        reply_markup=tournament_current_back_keyboard(),
    )


@router.callback_query(F.data.startswith("tour_cur:time:"))
async def admin_current_time_ask(call: CallbackQuery, state: FSMContext):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    row = get_tournament(tid)
    if not row:
        await call.answer("Турнир не найден", show_alert=True)
        return
    await state.update_data(edit_tournament_id=tid)
    await state.set_state(TournamentStates.waiting_new_time)
    await call.message.answer(
        f"🕐 Текущее время турнира #{tid}: <b>{hd.quote(str(row['scheduled_text'] or '—'))}</b>\n\n"
        "Введи новое время (например: <code>25.12 19:00</code> или <code>25.12.2026 19:00</code>).\n"
        "Для отмены — /cancel или «❌ Отмена».",
        parse_mode="HTML",
        reply_markup=tournament_cancel_keyboard(),
    )
    await call.answer()


@router.message(TournamentStates.waiting_new_time)
async def admin_current_time_save(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    tid = data.get("edit_tournament_id")
    scheduled = _normalise_scheduled(message.text or "")
    if not scheduled:
        await message.answer(
            "❌ Неверный формат. Введи так: 25.12 19:00 или 25.12.2026 19:00."
        )
        return
    update_tournament_time(tid, scheduled)
    await state.clear()
    await log_admin_action(
        bot=bot, admin_id=message.from_user.id,
        admin_name=message.from_user.username or message.from_user.full_name or "admin",
        action_text=f"🕐 Изменил время старта турнира #{tid} на {scheduled}.",
    )
    await message.answer(
        f"✅ Время старта турнира #{tid} обновлено: <b>{hd.quote(scheduled)}</b>.\n"
        "Автозапуск сработает в это время.",
        parse_mode="HTML",
        reply_markup=tournament_current_back_keyboard(),
    )


@router.callback_query(F.data.startswith("tour_cur:start_ask:"))
async def admin_current_start_ask(call: CallbackQuery):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"✅ Да, запустить #{tid}", callback_data=f"tour_cur:start_yes:{tid}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"tour_cur:open:{tid}"),
    ]]))
    await call.answer("Турнир стартует: сетка + ЛС капитанам")


@router.callback_query(F.data.startswith("tour_cur:start_yes:"))
async def admin_current_start_yes(call: CallbackQuery, bot: Bot):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    row = get_tournament(tid)
    if not row:
        await call.answer("Турнир не найден", show_alert=True)
        return
    if row["status"] in ("finished", "cancelled"):
        await call.answer("Турнир уже закрыт", show_alert=True)
        return
    await call.answer("Запускаю турнир...")
    status_msg = await call.message.answer("⏳ Собираю сетку, публикую посты и рассылаю капитанам...")
    report = await start_tournament(bot, row)
    await log_admin_action(
        bot=bot, admin_id=call.from_user.id,
        admin_name=call.from_user.username or call.from_user.full_name or "admin",
        action_text=f"▶️ Запустил турнир #{tid} вручную.",
    )
    try:
        await status_msg.edit_text(report, parse_mode="HTML")
    except Exception:
        await call.message.answer(report, parse_mode="HTML")
    await call.answer()


# ─── Обновление поста с сеткой (Этап 5, п.3) ────────────────────────────────

async def _upsert_bracket_post(bot: Bot, row, caption: str) -> tuple[int, int]:
    """Обновляет пост с сеткой в топиках. Возвращает (обновлено, создано заново).

    Если Telegram не даёт редактировать сообщение (лимит 48 часов) — старый пост
    удаляется и публикуется новый с актуальной картинкой.
    """
    updated, recreated = 0, 0
    # приоритет — сохранённая сетка турнира (в ней уже учтены BYE при старте)
    try:
        slots = list(json.loads(row["bracket_slots"] or "[]"))
    except Exception:
        slots = []
    if not slots:
        slots = _build_bracket_slots(get_tournament_participants(row["id"]), row["t_type"])
    png = _render_for_row(row, " · актуальная сетка")
    import config
    for post in get_bracket_posts(row["id"]):
        chat_id = int(post["chat_id"])
        try:
            if png:
                try:
                    await bot.edit_message_media(
                        chat_id=chat_id,
                        message_id=int(post["message_id"]),
                        media=InputMediaPhoto(
                            media=BufferedInputFile(png, filename=f"tournament_{row['id']}_bracket.png"),
                            caption=caption[:1024],
                            parse_mode="HTML",
                        ),
                    )
                except TelegramBadRequest as e:
                    msg = (e.message or "").lower()
                    if any(key in msg for key in (
                        "message can't be edited", "message is not modified",
                        "message to edit not found", "message_id_invalid",
                        "message can't be deleted",
                    )):
                        raise
                    if "there is no photo" in msg or "no photo" in msg:
                        await bot.edit_message_caption(
                            chat_id=chat_id, message_id=int(post["message_id"]),
                            caption=caption[:1024], parse_mode="HTML",
                        )
                    else:
                        raise
            else:
                await bot.edit_message_caption(
                    chat_id=chat_id, message_id=int(post["message_id"]),
                    caption=caption[:1024], parse_mode="HTML",
                )
            updated += 1
        except Exception as e:
            # Лимит редактирования (48 ч) или пост удалён — создаём новый пост
            logger.info(f"bracket post #{post['id']} needs recreation: {e}")
            try:
                await bot.delete_message(chat_id, int(post["message_id"]))
            except Exception:
                pass
            try:
                text = (
                    f"{caption}\n\n"
                    f"{_pairs_schedule_text(get_tournament_matches(row['id'], 1))}"
                )
                # тот же топик, где был старый пост (а не общий чат)
                thread_id = post.get("thread_id")
                if png:
                    sent = await bot.send_photo(
                        chat_id=chat_id,
                        message_thread_id=thread_id,
                        photo=BufferedInputFile(png, filename=f"tournament_{row['id']}_bracket.png"),
                        caption=text[:1024], parse_mode="HTML",
                    )
                else:
                    sent = await bot.send_message(
                        chat_id=chat_id,
                        message_thread_id=thread_id,
                        text=text, parse_mode="HTML",
                    )
                save_bracket_post(row["id"], chat_id, sent.message_id, thread_id=thread_id)
                recreated += 1
            except Exception as e2:
                logger.warning(f"bracket post recreation failed in {chat_id}: {e2}")
    return updated, recreated


def _clan_key_by_chat(chat_id: int) -> str | None:
    import config
    for key, data in (config.CLAN_CHATS or {}).items():
        if int(data.get("chat_id") or 0) == int(chat_id):
            return key
    return None


def _bracket_status_caption(row) -> str:
    typ = TOURNAMENT_TYPE_LABELS.get(row["t_type"], row["t_type"])
    lines = [
        f"🗺 <b>Актуальная сетка — турнир #{row['id']} ({hd.quote(str(typ))})</b>",
        f"🕐 {hd.quote(str(row['scheduled_text'] or '—'))} · "
        f"{hd.quote(TOURNAMENT_STATUS_LABEL.get(row['status'], row['status']))}",
    ]
    matches = get_tournament_matches(row["id"], 1)
    third = get_third_place_match(row["id"])
    lines += ["", "<b>Пары 1-го круга</b>", _pairs_schedule_text(matches)]
    if third and third["side_a"]:
        lines += ["", "<b>Матч за 3-4 места</b>"]
        if third["winner"]:
            lines.append(f"Победитель: {hd.quote(str(third['winner']))}")
        else:
            lines.append(f"{hd.quote(str(third['side_a']))} vs "
                         f"{hd.quote(str(third['side_b'] or 'ждём проигравшего полуфинала'))}")
    winners = placements_text(int(row["id"]))
    if winners:
        lines += ["", "<b>Победители</b>", hd.quote(winners)]
    return "\n".join(lines)


# ─── Ручной ввод победителей администрацией ─────────────────────────────────

def _open_matches_for_admin(tid: int) -> list[dict]:
    """Все матчи турнира, где ещё нет победителя (включая матч за 3-4 места)."""
    row = get_tournament(tid)
    if not row:
        return []
    total = _total_rounds(_bracket_size(row))
    out = []
    for r in range(1, total + 1):
        for m in get_tournament_matches(tid, r):
            if m["winner"] or m["status"] == "bye":
                continue
            out.append(m)
    third = get_third_place_match(tid)
    if third and not third["winner"] and third["side_a"] and third["side_b"]:
        out.append(third)
    return out


def _match_title(row, m) -> str:
    stage = _stage_label(row, int(m["round"]))
    a = m["side_a"] or "ждёт соперника"
    b = m["side_b"] or "ждёт соперника"
    return f"{stage}: {a} 🆚 {b}"


@router.callback_query(F.data.startswith("tour_res:list:"))
async def admin_results_list(call: CallbackQuery):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    row = get_tournament(tid)
    if not row:
        await call.answer("Турнир не найден", show_alert=True)
        return
    matches = _open_matches_for_admin(tid)
    if not matches:
        await call.answer("Все матчи уже закрыты", show_alert=True)
        await call.message.edit_text(
            "✅ Незакрытых матчей нет — сетка заполнена.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ К турниру", callback_data=f"tour_cur:open:{tid}"),
            ]]),
        )
        return
    rows = []
    for m in matches[:20]:
        label = _match_title(row, m)
        if m["side_a"] and m["side_b"]:
            rows.append((f"✍️ {label}", f"tour_res:pick:{m['id']}"))
        else:
            rows.append((f"⏳ {label}", f"tour_cur:open:{tid}"))
    rows.append(("◀️ К турниру", f"tour_cur:open:{tid}"))
    await call.message.edit_text(
        f"✍️ <b>Внести победителей · турнир #{tid}</b>\n\n"
        "Выбери матч, затем укажи победителя. Когда все матчи круга будут закрыты, "
        "следующий круг стартует автоматически.",
        parse_mode="HTML",
        reply_markup=simple_list_keyboard(rows),
    )
    await call.answer()


@router.callback_query(F.data.startswith("tour_res:pick:"))
async def admin_result_pick(call: CallbackQuery):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    m = get_match(int(call.data.rsplit(":", 1)[-1]))
    if not m:
        await call.answer("Матч не найден", show_alert=True)
        return
    row = get_tournament(int(m["tournament_id"]))
    if not row:
        await call.answer("Турнир не найден", show_alert=True)
        return
    if m["winner"]:
        await call.answer("Победитель уже записан", show_alert=True)
        return
    a, b = str(m["side_a"] or "—"), str(m["side_b"] or "—")
    await call.message.edit_text(
        f"✍️ <b>{hd.quote(_stage_label(row, int(m['round'])))}</b>\n"
        f"{hd.quote(a)} 🆚 {hd.quote(b)}\n\nКто победил?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🏆 {a[:20]}", callback_data=f"tour_res:win:{m['id']}:a")],
            [InlineKeyboardButton(text=f"🏆 {b[:20]}", callback_data=f"tour_res:win:{m['id']}:b")],
            [InlineKeyboardButton(text="◀️ К матчам", callback_data=f"tour_res:list:{m['tournament_id']}")],
        ]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("tour_res:win:"))
async def admin_result_win(call: CallbackQuery, bot: Bot):
    """Админ вручную записывает победителя: та же логика, что при подтверждении фото."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    parts = call.data.split(":")
    if len(parts) < 4:
        await call.answer("Некорректная кнопка", show_alert=True)
        return
    match_id, side = int(parts[2]), parts[3]
    m = get_match(match_id)
    if not m:
        await call.answer("Матч не найден", show_alert=True)
        return
    row = get_tournament(int(m["tournament_id"]))
    if not row:
        await call.answer("Турнир не найден", show_alert=True)
        return
    if m["winner"]:
        await call.answer("Победитель уже записан", show_alert=True)
        return
    winner = m["side_a"] if side == "a" else m["side_b"]
    loser = m["side_b"] if side == "a" else m["side_a"]
    if not winner:
        await call.answer("У матча ещё нет этого участника", show_alert=True)
        return

    is_third = int(m["round"]) == 0
    set_match_winner(match_id, str(winner), "confirmed", call.from_user.id)
    notes = []
    if is_third:
        set_tournament_status(int(m["tournament_id"]), "finished")
        notes.append("🥉 Матч за 3-4 места закрыт, турнир завершён.")
    else:
        info = advance_winner(int(m["tournament_id"]), int(m["round"]), int(m["pair_index"]), str(winner))
        if info["tournament_finished"]:
            notes.append("🏁 Финал закрыт.")
            if not get_third_place_match(int(m["tournament_id"])):
                set_tournament_status(int(m["tournament_id"]), "finished")
        if register_semifinal_loser(int(m["tournament_id"]), int(m["round"]), str(loser)):
            notes.append("🥉 Сформирован матч за 3-4 места.")
        _propagate_auto_advances(int(m["tournament_id"]))
        notes.extend(await _auto_start_rounds(bot, row))

    updated, recreated = await _upsert_bracket_post(bot, row, _bracket_status_caption(row))
    notes.append(f"🗺 Сетка: обновлено {updated}, создано заново {recreated}.")
    placement_rows = placements_text(int(m["tournament_id"]))
    if placement_rows:
        notes.append("🏅 Победители:\n" + placement_rows)

    for p in get_tournament_participants(int(m["tournament_id"])):
        if p["team_name"] not in (winner, loser) or not p["leader_id"]:
            continue
        try:
            await bot.send_message(
                int(p["leader_id"]),
                f"{'✅' if p['team_name'] == winner else '❌'} <b>Результат внесён администрацией</b>\n"
                f"• {hd.quote(str(p['team_name']))}: "
                f"{'победа, проходите дальше' if p['team_name'] == winner else 'поражение'}\n"
                f"• Турнир #{m['tournament_id']}",
                parse_mode="HTML",
            )
        except Exception:
            pass

    await log_admin_action(
        bot=bot, admin_id=call.from_user.id,
        admin_name=call.from_user.username or call.from_user.full_name or "admin",
        action_text=(f"✍️ Внёс результат вручную: турнир #{m['tournament_id']}, "
                     f"матч {m['side_a']} vs {m['side_b']}, победил {winner}."),
    )
    await call.message.edit_text(
        f"✅ Победитель записан: <b>{hd.quote(str(winner))}</b>.\n"
        + "\n".join(notes),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✍️ Ещё матчи", callback_data=f"tour_res:list:{m['tournament_id']}"),
            InlineKeyboardButton(text="◀️ К турниру", callback_data=f"tour_cur:open:{m['tournament_id']}"),
        ]]),
    )
    await call.answer("Победитель записан")


# ─── Кнопки и хендлеры раздела «🏆 Турниры» ─────────────────────────────────

@router.message(F.text == "🏆 Турниры")
async def admin_open_tournaments_list(message: Message):
    member = await get_member(message.from_user.id)
    if not member or not has_role(member, "vice"):
        await message.answer("⛔ Доступ только для Вице Президент и выше.")
        return
    rows = get_tournaments(limit=15)
    if not rows:
        await message.answer(
            "🏆 Турниров пока нет.\nСоздай первый через «🏆 Создание турнира».",
            reply_markup=admin_scrims_keyboard(),
        )
        return
    lines = ["🏆 <b>Последние турниры</b>", ""]
    for r in rows:
        mark = "🧪 " if is_test_tournament(r) else ""
        lines.append(
            f"{mark}#{r['id']} · {hd.quote(str(r['t_type']))} · "
            f"{hd.quote(str(r['scheduled_text'] or '—'))} · "
            f"{hd.quote(TOURNAMENT_STATUS_LABEL.get(r['status'], r['status']))} · "
            f"👥 {len(get_tournament_participants(r['id']))}"
        )
    await message.answer("\n".join(lines), parse_mode="HTML",
                         reply_markup=tournaments_list_keyboard(rows))


@router.callback_query(F.data == "tour:list:refresh")
async def admin_tournaments_refresh(call: CallbackQuery):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    rows = get_tournaments(limit=15)
    try:
        await call.message.delete()
    except Exception:
        pass
    if not rows:
        await call.message.answer("🏆 Турниров пока нет.", reply_markup=admin_scrims_keyboard())
        await call.answer()
        return
    lines = ["🏆 <b>Последние турниры</b>", ""]
    for r in rows:
        lines.append(
            f"#{r['id']} · {hd.quote(str(r['t_type']))} · "
            f"{hd.quote(str(r['scheduled_text'] or '—'))} · "
            f"{hd.quote(TOURNAMENT_STATUS_LABEL.get(r['status'], r['status']))} · "
            f"👥 {len(get_tournament_participants(r['id']))}"
        )
    await call.message.answer("\n".join(lines), parse_mode="HTML",
                              reply_markup=tournaments_list_keyboard(rows))
    await call.answer("Обновлено")


@router.callback_query(F.data.startswith("tour:open:"))
async def admin_tournament_open(call: CallbackQuery):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    row = get_tournament(int(call.data.rsplit(":", 1)[-1]))
    if not row:
        await call.answer("Турнир не найден", show_alert=True)
        return
    participants = get_tournament_participants(row["id"])
    text = (
        f"{'🧪 ' if is_test_tournament(row) else ''}<b>Турнир #{row['id']}</b>\n"
        f"• Тип: {hd.quote(TOURNAMENT_TYPE_LABELS.get(row['t_type'], row['t_type']))}\n"
        f"• Формат: {hd.quote(TOURNAMENT_FORMAT_LABELS.get(row['t_format'], '—'))}\n"
        f"• Лимит: {_limit_text(row)}\n"
        f"• Начало: {hd.quote(str(row['scheduled_text'] or '—'))}\n"
        f"• Статус: {hd.quote(TOURNAMENT_STATUS_LABEL.get(row['status'], row['status']))}\n\n"
        f"👥 Участников: <b>{len(participants)}</b>\n"
        f"{_participants_block(participants)}"
    )
    kb = tournament_test_keyboard(row["id"]) if is_test_tournament(row) \
        else tournament_admin_keyboard(row["id"])
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await call.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("ttest:"))
async def admin_tournament_test_tools(call: CallbackQuery, bot: Bot):
    """Инструменты тестового турнира: фейковые составы, сброс, возврат к карточке."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    parts = call.data.split(":")
    action = parts[1]
    tid = int(parts[2])
    row = get_tournament(tid)
    if not row:
        await call.answer("Турнир не найден", show_alert=True)
        return
    if not is_test_tournament(row):
        await call.answer("Это не тестовый турнир", show_alert=True)
        return
    if action == "fill":
        amount = int(parts[3])
        added = add_test_participants(tid, amount, call.from_user.id)
        await call.answer(f"Добавлено {added} тестовых составов")
    elif action == "clear":
        removed = clear_test_participants(tid)
        await call.answer(f"Удалено {removed} тестовых составов")
    elif action == "open":
        await call.answer()
    participants = get_tournament_participants(tid)
    matches = get_tournament_matches(tid, 1)
    third = get_third_place_match(tid)
    text = (
        f"🧪 <b>Тестовый турнир #{tid}</b>\n"
        f"• Тип: {hd.quote(TOURNAMENT_TYPE_LABELS.get(row['t_type'], row['t_type']))} · "
        f"{hd.quote(TOURNAMENT_FORMAT_LABELS.get(row['t_format'], '—'))}\n"
        f"• Лимит: {_limit_text(row)}\n"
        f"• Начало: {hd.quote(str(row['scheduled_text'] or '—'))}\n"
        f"• Статус: {hd.quote(TOURNAMENT_STATUS_LABEL.get(row['status'], row['status']))}\n\n"
        f"👥 Составов: <b>{_fmt_num(len(participants))}</b> "
        f"(из них тестовых: {_fmt_num(count_test_participants(tid))})\n"
        f"{_participants_block(participants, 15)}\n\n"
        f"🗺 Пар 1-го круга: {_fmt_num(len(matches))}"
        + (f"\n🥉 Матч за 3-4 места: {hd.quote(str(third['side_a'] or '—'))} 🆚 "
           f"{hd.quote(str(third['side_b'] or '—'))}" if third and third["side_a"] else "")
        + "\n\nТестовые составы имеют лидера-тебя: все уведомления придут в твои ЛС."
    )
    try:
        await call.message.edit_text(text, parse_mode="HTML",
                                     reply_markup=tournament_test_keyboard(tid))
    except Exception:
        await call.message.answer(text, parse_mode="HTML",
                                  reply_markup=tournament_test_keyboard(tid))


@router.callback_query(F.data.startswith("tour:members:"))
async def admin_tournament_members(call: CallbackQuery):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    row = get_tournament(int(call.data.rsplit(":", 1)[-1]))
    if not row:
        await call.answer("Турнир не найден", show_alert=True)
        return
    participants = get_tournament_participants(row["id"])
    text = f"👥 <b>Участники турнира #{row['id']}</b> ({len(participants)}):\n\n" + _participants_block(participants, 40)
    await call.message.answer(text, parse_mode="HTML", reply_markup=tournament_back_keyboard())
    await call.answer()


@router.callback_query(F.data.startswith("tour:preview:"))
async def admin_tournament_preview(call: CallbackQuery):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    row = get_tournament(int(call.data.rsplit(":", 1)[-1]))
    if not row:
        await call.answer("Турнир не найден", show_alert=True)
        return
    await _send_preview(call, row)
    await call.answer("Сетка готова")


@router.callback_query(F.data.startswith("tour:later:"))
async def admin_tournament_later(call: CallbackQuery):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    await call.message.edit_text(
        "💾 Анонс отложен. Опубликуешь позже через раздел «🏆 Турниры».",
        reply_markup=None,
    )
    await call.message.answer("📂 Скримы и турниры", reply_markup=admin_scrims_keyboard())
    await call.answer()


@router.callback_query(F.data.startswith("tour:announce:"))
async def admin_tournament_announce(call: CallbackQuery, bot: Bot):
    """Этап 3, п.2-3: рассылка в ЛС + посты в клановых топиках «Турниры»."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tournament_id = int(call.data.rsplit(":", 1)[-1])
    row = get_tournament(tournament_id)
    if not row:
        await call.answer("Турнир не найден", show_alert=True)
        return
    if row["status"] in ("cancelled", "finished"):
        await call.answer("Турнир уже закрыт", show_alert=True)
        return
    participants = get_tournament_participants(tournament_id)
    await call.answer("Публикую анонс...", show_alert=False)
    status_msg = await call.message.answer("⏳ Публикую анонс в топики и рассылаю приглашения в ЛС...")

    topics_sent, topics_failed = await _post_to_clan_topics(bot, row, participants)
    dm = await _dm_registered_players(bot, row)
    mark_tournament_published(tournament_id)

    report = [
        f"📣 <b>Анонс турнира #{tournament_id} отправлен</b>",
        "",
        f"🗂 Топики кланов: ✅ {len(topics_sent)}",
    ]
    for t in topics_sent:
        report.append(f"  • {hd.quote(t)}")
    if topics_failed:
        report.append(f"  ❌ не отправлено: {len(topics_failed)}")
        for t in topics_failed:
            report.append(f"  • {hd.quote(t)}")
    report += [
        "",
        f"📨 Рассылка в ЛС: зарегистрированных {dm['total']}, пропущено {dm['skipped']}",
        f"  • лидеров составов пригласили: {dm['with_team']} (отправлено {dm['invited']})",
        f"  • без состава (нужно создать): {dm['without_team']}",
        f"  • не лидеры (уведомлены): {dm['not_leader']}",
        f"  • ошибок доставки: {dm['failed']}",
        "",
        "Если топики не настроены — задай их через «⚙️ Топики турниров» и повтори анонс.",
    ]
    try:
        await status_msg.edit_text("\n".join(report), parse_mode="HTML")
    except Exception:
        await call.message.answer("\n".join(report), parse_mode="HTML")

    await log_admin_action(
        bot=bot,
        admin_id=call.from_user.id,
        admin_name=call.from_user.username or call.from_user.full_name or "admin",
        action_text=(
            f"📣 Опубликовал анонс турнира #{tournament_id}: топики {len(topics_sent)}, "
            f"приглашений в ЛС {dm['invited']}, без состава {dm['without_team']}."
        ),
    )
    await call.answer()


@router.callback_query(F.data.startswith("tour:cancel_ask:"))
async def admin_tournament_cancel_ask(call: CallbackQuery):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tournament_id = int(call.data.rsplit(":", 1)[-1])
    await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"✅ Да, отменить #{tournament_id}",
                             callback_data=f"tour:cancel_yes:{tournament_id}"),
        InlineKeyboardButton(text="❌ Нет", callback_data=f"tour:open:{tournament_id}"),
    ]]))
    await call.answer("Подтверди отмену турнира")


@router.callback_query(F.data.startswith("tour:cancel_yes:"))
async def admin_tournament_cancel_yes(call: CallbackQuery, bot: Bot):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tournament_id = int(call.data.rsplit(":", 1)[-1])
    row = get_tournament(tournament_id)
    if not row:
        await call.answer("Турнир не найден", show_alert=True)
        return
    set_tournament_status(tournament_id, "cancelled")
    for p in get_tournament_participants(tournament_id):
        if p["leader_id"]:
            try:
                await bot.send_message(
                    int(p["leader_id"]),
                    f"🚫 Турнир #{tournament_id} отменён администрацией. "
                    f"Состав {hd.quote(p['team_name'] or '')} снят с участия.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
    await log_admin_action(
        bot=bot,
        admin_id=call.from_user.id,
        admin_name=call.from_user.username or call.from_user.full_name or "admin",
        action_text=f"🚫 Отменил турнир #{tournament_id}.",
    )
    await call.message.edit_text(f"🚫 Турнир #{tournament_id} отменён.", reply_markup=None)
    await call.answer("Турнир отменён")


@router.message(F.text == "⚙️ Топики турниров")
async def admin_tournament_topics_start(message: Message, state: FSMContext):
    """Настройка ID топиков «Турниры» (хранятся в vigarik.db, config.py не трогаем)."""
    member = await get_member(message.from_user.id)
    if not member or not has_role(member, "vice"):
        await message.answer("⛔ Доступ только для Вице Президент и выше.")
        return
    current = await get_tournament_topics()
    lines = [
        "⚙️ <b>Топики «Турниры»</b>",
        "",
        "В каждом клане создай топик «Турниры», напиши в нём слово <code>get_id</code> "
        "и скопируй ID топика.",
        "",
        "Текущие настройки:",
    ]
    for clan in TOURNAMENT_CLANS:
        label = {"academy": "🎓 Academy", "squad": "👑 Squad", "events": "🎉 Events"}[clan]
        shown = current.get(clan) or "не задан"
        lines.append(f"• {label}: <code>{hd.quote(str(shown))}</code>")
    lines += [
        "",
        "Пришли одним сообщением в формате:",
        "<code>academy=33272 squad=129460 events=5361</code>",
        "(можно указать только часть кланов, разделитель — пробел, запятая или точка с запятой)",
        "",
        "Для выхода отправь /cancel или нажми «❌ Отмена».",
    ]
    await state.set_state(TournamentSettingsStates.waiting_topics)
    await message.answer("\n".join(lines), parse_mode="HTML",
                         reply_markup=tournament_topics_keyboard())


@router.message(TournamentSettingsStates.waiting_topics, F.text.startswith("/"))
@router.message(TournamentSettingsStates.waiting_topics, F.text == "❌ Отмена")
async def admin_tournament_topics_cancel(message: Message, state: FSMContext):
    await state.clear()
    member = await get_member(message.from_user.id)
    await message.answer(
        "❌ Настройка топиков отменена.",
        reply_markup=admin_scrims_keyboard() if member and has_role(member, "vice") else main_menu(member),
    )


@router.message(TournamentSettingsStates.waiting_topics)
async def admin_tournament_topics_save(message: Message, state: FSMContext, bot: Bot):
    """Парсим 'academy=123 squad=456 events=789' либо ссылку t.me/c/<chat>/<thread>.

    Ссылка на топик основы вида https://t.me/c/2187842577/405196 сохраняется как squad:
    chat_id берётся из config.CLAN_CHATS, а 405196 — message_thread_id топика.
    """
    import re as _re
    raw = message.text or ""
    found = dict(_re.findall(r"(academy|squad|events)\s*[=:]\s*(-?\d+)", raw, flags=_re.IGNORECASE))
    if not found:
        link = _re.search(r"t\.me/c/(\d+)(?:/(\d+))?", raw)
        if link:
            # для тестового режима нужен именно топик основы
            found = {"squad": link.group(2) or link.group(1)}
    if not found:
        await message.answer(
            "❌ Не нашёл ни одного ID.\n\n"
            "Формат: <code>academy=33272 squad=129460 events=5361</code>\n"
            "Или ссылка на топик основы: <code>https://t.me/c/2187842577/405196</code>\n\n"
            "⚠️ Второе число в ссылке должно быть ID топика (message_thread_id), "
            "а не ID отдельного сообщения. Надёжнее — написать <code>get_id</code> "
            "внутри нужного топика.",
            parse_mode="HTML",
        )
        return
    saved = []
    for clan, value in found.items():
        await save_tournament_topic(clan.lower(), int(value))
        saved.append(f"{clan.lower()} → {value}")
    await state.clear()
    await log_admin_action(
        bot=bot,
        admin_id=message.from_user.id,
        admin_name=message.from_user.username or message.from_user.full_name or "admin",
        action_text=f"⚙️ Обновил топики «Турниры»: {', '.join(saved)}.",
    )
    await message.answer(
        "✅ <b>Топики турниров сохранены:</b>\n" + "\n".join(f"• {hd.quote(s)}" for s in saved),
        parse_mode="HTML",
        reply_markup=admin_scrims_keyboard(),
    )


# ─── Регистрация состава на турнир (кнопка из ЛС) ───────────────────────────

@router.callback_query(F.data.startswith("tour_info:"))
async def tournament_info_cb(call: CallbackQuery):
    row = get_tournament(int(call.data.rsplit(":", 1)[-1]))
    if not row:
        await call.answer("Турнир не найден", show_alert=True)
        return
    participants = get_tournament_participants(row["id"])
    await call.message.answer(
        _announcement_text(row, participants, for_clan_post=False), parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data.startswith("tour_reg:"))
async def tournament_register_cb(call: CallbackQuery, bot: Bot):
    """Регистрация состава лидером. Участвовать могут составы из всех трёх кланов."""
    row = get_tournament(int(call.data.rsplit(":", 1)[-1]))
    if not row:
        await call.answer("Турнир не найден", show_alert=True)
        return
    if row["status"] in ("cancelled", "finished"):
        await call.answer("Регистрация закрыта", show_alert=True)
        return
    composition = get_user_league(call.from_user.id)
    if not composition:
        await call.message.answer(_no_composition_text(), parse_mode="HTML")
        await call.answer("Нет состава", show_alert=True)
        return
    if int(composition["leader_id"]) != call.from_user.id:
        await call.answer("Регистрировать состав может только его лидер", show_alert=True)
        return
    participants = get_tournament_participants(row["id"])
    if any(int(p["league_id"]) == int(composition["id"]) for p in participants):
        await call.answer("Твой состав уже зарегистрирован", show_alert=True)
        return
    limit = row["team_limit"]
    if row["t_type"] != TOURNAMENT_SHOWDOWN and limit and len(participants) >= int(limit):
        await call.message.answer(
            f"❌ Лимит турнира исчерпан: {_fmt_num(limit)} команд. "
            "Напиши администрации, если нужно место.",
            parse_mode="HTML",
        )
        await call.answer("Лимит исчерпан", show_alert=True)
        return
    if row["t_type"] == TOURNAMENT_SHOWDOWN and limit and len(participants) >= int(limit):
        await call.message.answer(
            f"❌ Лимит турнира исчерпан: {_fmt_num(limit)} соло-участников.",
            parse_mode="HTML",
        )
        await call.answer("Лимит исчерпан", show_alert=True)
        return
    ok = add_tournament_participant(
        row["id"], int(composition["id"]), composition["name"], composition["tag"], call.from_user.id,
    )
    if not ok:
        await call.answer("Состав уже в списке", show_alert=True)
        return
    participants = get_tournament_participants(row["id"])
    place = next((i for i, p in enumerate(participants, start=1)
                  if int(p["league_id"]) == int(composition["id"])), len(participants))
    await call.message.answer(
        f"✅ <b>Состав зарегистрирован на турнир #{row['id']}!</b>\n"
        f"• {hd.quote(composition['name'])} [{hd.quote(composition['tag'])}]\n"
        f"• Позиция в сетке: <b>{_fmt_num(place)}</b> из "
        f"{_fmt_num(limit) if limit else '∞'}\n"
        f"• Начало: {hd.quote(str(row['scheduled_text'] or '—'))}\n\n"
        "Не опаздывай: к моменту начала в составе должно быть нужное число игроков.",
        parse_mode="HTML",
    )
    await call.answer("Зарегистрировано!")


# ══════════════════════════════════════════════════════════════════════════════
# ─── ЭТАП 5: сдача результата лидером и модерация админом ────────────────────
# ══════════════════════════════════════════════════════════════════════════════

RESULT_MENU_TEXTS = frozenset({
    "❌ Отмена", "◀️ Назад", "🏁 Мои матчи", "👑 Для админов", "👔 Для админов",
    "Составы🏆 (BetaTest)", "💡 Отправить предложение", "🎯 Выбрать цель пуша",
})


def _my_pending_matches(user_id: int) -> list[dict]:
    """Матчи (включая матч за 3-е место), где играет команда этого лидера."""
    composition = get_user_league(user_id)
    if not composition:
        return []
    my_name = composition["name"]
    out = []
    for row in get_active_tournaments():
        if row["status"] not in ("running", "announced"):
            continue
        for m in get_tournament_matches(row["id"], 1):
            if m["winner"] or m["status"] not in ("scheduled",):
                continue
            if my_name not in (m["side_a"], m["side_b"]):
                continue
            opp = m["side_b"] if m["side_a"] == my_name else m["side_a"]
            out.append({
                "tournament": row, "match": m, "opponent": opp or "—",
                "round": m["round"], "is_third": m["round"] == 0,
            })
        third = get_third_place_match(row["id"])
        if third and not third["winner"] and third["status"] == "scheduled" \
                and my_name in (third["side_a"], third["side_b"]):
            opp = third["side_b"] if third["side_a"] == my_name else third["side_a"]
            out.append({
                "tournament": row, "match": third, "opponent": opp or "—",
                "round": 0, "is_third": True,
            })
    return out


@router.message(F.text == "🏁 Мои матчи")
async def my_tournament_matches(message: Message, state: FSMContext):
    """Вход лидера: список его матчей в идущих турнирах (для сдачи результата)."""
    await state.clear()
    rows = _my_pending_matches(message.from_user.id)
    if not rows:
        await message.answer(
            "🏁 Сейчас у твоего состава нет активных матчей.\n\n"
            "Матчи появляются после старта турнира. Результат сдаётся только "
            "лидером состава: фото + кто победил."
        )
        return
    keyboard_rows = []
    for item in rows:
        label = _stage_label(item["tournament"], item["round"])
        keyboard_rows.append((
            f"#{item['tournament']['id']} {hd.quote(item['tournament']['t_type'])} · "
            f"{hd.quote(str(item['opponent']))} · {label}",
            f"myres:{item['match']['id']}",
        ))
    await message.answer(
        "🏁 <b>Твои матчи</b>\n\nВыбери матч и сдай результат (фото + кто победил).",
        parse_mode="HTML",
        reply_markup=simple_list_keyboard(keyboard_rows),
    )


@router.callback_query(F.data.startswith("myres:"))
async def result_submit_start(call: CallbackQuery, state: FSMContext):
    """Шаг 1 сдачи результата: просим фото с подписью."""
    match_id = int(call.data.split(":")[1])
    m = get_match(match_id)
    if not m:
        await call.answer("Матч не найден", show_alert=True)
        return
    composition = get_user_league(call.from_user.id)
    if not composition or composition["name"] not in (m["side_a"], m["side_b"]):
        await call.answer("Сдавать результат может только лидер играющей команды", show_alert=True)
        return
    if m["winner"]:
        await call.answer("Результат матча уже подтверждён", show_alert=True)
        return
    if has_pending_report(match_id):
        await call.answer("По этому матчу заявка уже на модерации", show_alert=True)
        return
    await state.update_data(res_match_id=match_id)
    await state.set_state(ResultStates.waiting_photo)
    await call.message.answer(
        "📸 <b>Сдача результата</b>\n\n"
        "Пришли <b>одно фото</b> (скриншот итога матча) и напиши к нему подпись — "
        "короткий текст: счёт и кто победил.\n\n"
        "Например подпись: <code>2:1, победила VSG</code>\n"
        "Для отмены — /cancel или «❌ Отмена».",
        parse_mode="HTML",
        reply_markup=result_cancel_keyboard(),
    )
    await call.answer()


@router.message(ResultStates.waiting_photo, F.text.startswith("/"))
@router.message(ResultStates.waiting_photo, F.text.in_(RESULT_MENU_TEXTS))
async def result_submit_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Сдача результата отменена.")


@router.message(ResultStates.waiting_photo, F.photo)
async def result_photo_received(message: Message, state: FSMContext):
    """Фото получено: сохраняем во временное состояние, просим указать победителя."""
    data = await state.get_data()
    match_id = data.get("res_match_id")
    m = get_match(int(match_id)) if match_id else None
    if not m:
        await state.clear()
        await message.answer("❌ Матч не найден, начни заново через «🏁 Мои матчи».")
        return
    caption = (message.caption or "").strip()
    if not caption:
        await message.answer(
            "❌ Нужен текст к фото (подпись): счёт и кто победил. Пришли фото ещё раз с подписью."
        )
        return
    await state.update_data(res_photo=message.photo[-1].file_id, res_comment=caption)
    await state.set_state(ResultStates.waiting_winner)
    await message.answer(
        "✅ Фото и текст приняты. Теперь укажи, кто победил в этом матче:",
        reply_markup=result_winner_keyboard(int(m["id"]), str(m["side_a"]), str(m["side_b"])),
    )


@router.message(ResultStates.waiting_photo)
async def result_photo_wrong(message: Message):
    await message.answer("❌ Нужно фото (с подписью). Пришли скриншот итога матча одним сообщением.")


@router.callback_query(ResultStates.waiting_winner, F.data == "tres:abort")
async def result_winner_abort(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.answer("❌ Сдача результата отменена.")
    await call.answer("Отменено")


@router.callback_query(ResultStates.waiting_winner, F.data.startswith("tres:"))
async def result_winner_chosen(call: CallbackQuery, state: FSMContext, bot: Bot):
    """Заявка сохранена и ушла в админ-чат с кнопками подтверждения (п.2 ТЗ)."""
    _, match_id_raw, side = call.data.split(":")
    data = await state.get_data()
    m = get_match(int(match_id_raw))
    if not m:
        await state.clear()
        await call.answer("Матч не найден", show_alert=True)
        return
    if m["winner"]:
        await state.clear()
        await call.answer("Результат уже подтверждён", show_alert=True)
        return
    composition = get_user_league(call.from_user.id)
    if not composition:
        await state.clear()
        await call.answer("Ты не в составе", show_alert=True)
        return
    claimed = m["side_a"] if side == "a" else m["side_b"]
    other = m["side_b"] if side == "a" else m["side_a"]
    row = get_tournament(int(m["tournament_id"]))
    report_id = create_match_report(
        tournament_id=int(m["tournament_id"]),
        match_id=int(m["id"]),
        reporter_id=call.from_user.id,
        team_name=composition["name"],
        opponent_name=other,
        claimed_winner=claimed,
        photo_file_id=data.get("res_photo") or "",
        comment=data.get("res_comment") or "",
    )
    await state.clear()
    is_third = int(m["round"]) == 0
    stage = _stage_label(row, int(m["round"]))
    admin_text = (
        f"📥 <b>Заявка на результат #{report_id}</b>\n"
        f"🏆 Турнир #{m['tournament_id']} · {hd.quote(TOURNAMENT_TYPE_LABELS.get(row['t_type'], row['t_type']))}\n"
        f"⚔️ Стадия: {hd.quote(stage)}\n"
        f"🆚 {hd.quote(str(m['side_a'] or '—'))} vs {hd.quote(str(m['side_b'] or '—'))}\n"
        f"👤 Сдал: {hd.quote(call.from_user.full_name)} "
        f"(@{call.from_user.username}) / {composition['name']}\n"
        f"💬 Текст от лидера: {hd.quote(str(data.get('res_comment') or '—'))}\n"
        f"🏁 Заявлен победитель: <b>{hd.quote(str(claimed or '—'))}</b>"
    )
    kb = admin_result_keyboard(report_id, str(claimed or "—"), str(other or "—"))
    sent_ok = False
    if data.get("res_photo"):
        try:
            await bot.send_photo(
                chat_id=config.ADMIN_CHAT_ID,
                photo=data["res_photo"],
                caption=admin_text,
                parse_mode="HTML",
                reply_markup=kb,
            )
            sent_ok = True
        except Exception as e:
            logger.warning(f"report photo to admin chat failed: {e}")
    if not sent_ok:
        await bot.send_message(
            config.ADMIN_CHAT_ID, admin_text, parse_mode="HTML", reply_markup=kb,
        )
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(
        "📨 <b>Заявка отправлена администрации.</b>\n"
        "После проверки админ подтвердит победителя — сетка обновится автоматически.",
        parse_mode="HTML",
        reply_markup=None,
    )
    await call.answer("Отправлено на модерацию")


# ─── Модерация результатов (п.2 ТЗ) ─────────────────────────────────────────

@router.message(F.text == "📬 Заявки на результаты")
async def admin_pending_reports(message: Message):
    member = await get_member(message.from_user.id)
    if not member or not is_any_admin(member):
        await message.answer("⛔ Доступ только для администрации.")
        return
    rows = get_pending_reports()
    if not rows:
        await message.answer("📬 Новых заявок на результаты нет.", reply_markup=admin_scrims_keyboard())
        return
    keyboard_rows = []
    for r in rows:
        keyboard_rows.append((
            f"#{r['id']} · {hd.quote(str(r['team_name'] or '—'))} → {hd.quote(str(r['claimed_winner'] or '—'))}",
            f"rres:view:{r['id']}",
        ))
    await message.answer(
        f"📬 <b>Заявки на результаты</b> ({len(rows)}):\n\nОткрой заявку и прими решение.",
        parse_mode="HTML",
        reply_markup=simple_list_keyboard(keyboard_rows),
    )


@router.callback_query(F.data.startswith("rres:view:"))
async def admin_report_view(call: CallbackQuery):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    r = get_match_report(int(call.data.rsplit(":", 1)[-1]))
    if not r:
        await call.answer("Заявка не найдена", show_alert=True)
        return
    m = get_match(int(r["match_id"]))
    text = (
        f"📥 <b>Заявка #{r['id']}</b> · статус {hd.quote(r['status'])}\n"
        f"🏆 Турнир #{r['tournament_id']}\n"
        f"🆚 {hd.quote(str(m['side_a'] or '—'))} vs {hd.quote(str(m['side_b'] or '—'))}\n"
        f"👤 Сдал: ID {r['reporter_id']} ({hd.quote(str(r['team_name'] or '—'))})\n"
        f"💬 Текст: {hd.quote(str(r['comment'] or '—'))}\n"
        f"🏁 Заявлен победитель: <b>{hd.quote(str(r['claimed_winner'] or '—'))}</b>"
    )
    if r["photo_file_id"]:
        try:
            await call.message.answer_photo(
                photo=r["photo_file_id"], caption=text, parse_mode="HTML",
                reply_markup=admin_result_keyboard(r["id"], str(r["claimed_winner"] or "—"),
                                                   str(m["side_b"] if m["side_a"] == r["claimed_winner"] else m["side_a"] or "—")),
            )
            await call.answer()
            return
        except Exception:
            pass
    await call.message.answer(text, parse_mode="HTML", reply_markup=admin_result_keyboard(
        r["id"], str(r["claimed_winner"] or "—"),
        str(m["side_b"] if m["side_a"] == r["claimed_winner"] else m["side_a"] or "—"),
    ))
    await call.answer()


async def _apply_result(call: CallbackQuery, report_id: int, winner: str, status: str,
                        bot: Bot) -> None:
    """Подтверждение результата: продвижение по сетке + обновление постов + уведомления."""
    r = get_match_report(report_id)
    if not r:
        await call.answer("Заявка не найдена", show_alert=True)
        return
    if r["status"] != "pending":
        await call.answer("Заявка уже обработана", show_alert=True)
        return
    m = get_match(int(r["match_id"]))
    if not m:
        await call.answer("Матч не найден", show_alert=True)
        return
    row = get_tournament(int(m["tournament_id"]))
    loser = m["side_b"] if m["side_a"] == winner else m["side_a"]
    is_third = int(m["round"]) == 0

    set_match_winner(int(m["id"]), winner, "confirmed", call.from_user.id)
    set_match_report_status(report_id, status, call.from_user.id)

    notes = []
    if is_third:
        # финал утешительной ветки: турнир завершён
        set_tournament_status(int(m["tournament_id"]), "finished")
        notes.append("🥉 Матч за 3-4 места закрыт, турнир завершён.")
    else:
        info = advance_winner(int(m["tournament_id"]), int(m["round"]), int(m["pair_index"]), winner)
        if info["tournament_finished"]:
            notes.append("🏁 Это был финал — турнир завершён (остался матч за 3-е место).")
            if not get_third_place_match(int(m["tournament_id"])):
                set_tournament_status(int(m["tournament_id"]), "finished")
        else:
            nxt = get_match(int(info["next_match_id"])) if info["next_match_id"] else None
            if nxt and nxt["side_a"] and nxt["side_b"]:
                notes.append(f"⏭ Победитель идёт в матч против {hd.quote(str(info['next_opponent']))}.")
            else:
                notes.append("⏭ Победитель ждёт соперника из соседней пары.")
        # проигравший полуфинал → в матч за 3-4 места (саб-ветка)
        if register_semifinal_loser(int(m["tournament_id"]), int(m["round"]), loser):
            third = get_third_place_match(int(m["tournament_id"]))
            notes.append(
                f"🥉 Сформирован матч за 3-4 места: {hd.quote(str(third['side_a']))} 🆚 "
                f"{hd.quote(str(third['side_b']))}."
            )
        _propagate_auto_advances(int(m["tournament_id"]))
        # если все матчи круга закрыты — следующий круг стартует автоматически
        notes.extend(await _auto_start_rounds(bot, get_tournament(int(m["tournament_id"]))))

    # обновляем посты с сеткой (с обработкой лимита редактирования 48 ч)
    updated, recreated = await _upsert_bracket_post(bot, row, _bracket_status_caption(row))
    notes.append(f"🗺 Посты с сеткой: обновлено {updated}, создано заново {recreated}.")
    placement_rows = placements_text(int(m["tournament_id"]))
    if placement_rows:
        notes.append("🏅 Победители:\n" + placement_rows)

    # уведомления лидерам
    for p in get_tournament_participants(int(m["tournament_id"])):
        if p["team_name"] not in (winner, loser) or not p["leader_id"]:
            continue
        won = p["team_name"] == winner
        try:
            await bot.send_message(
                int(p["leader_id"]),
                (f"✅ <b>Результат подтверждён администрацией.</b>\n"
                 f"Состав {hd.quote(p['team_name'])} — {'победа, проходите дальше!' if won else 'поражение, вы выбыли.'}\n"
                 f"🏆 Турнир #{m['tournament_id']}") + ("\n" + "\n".join(notes) if won else ""),
                parse_mode="HTML",
            )
        except Exception:
            pass

    await log_admin_action(
        bot=bot, admin_id=call.from_user.id,
        admin_name=call.from_user.username or call.from_user.full_name or "admin",
        action_text=(
            f"🏁 Подтвердил результат #{report_id} (турнир #{m['tournament_id']}, "
            f"матч {m['side_a']} vs {m['side_b']}): победил {winner}."
        ),
    )
    try:
        await call.message.edit_text(
            f"✅ Заявка #{report_id} обработана. Победитель: <b>{hd.quote(winner)}</b>.\n"
            + "\n".join(notes),
            parse_mode="HTML", reply_markup=None,
        )
    except Exception:
        await call.message.answer(
            f"✅ Заявка #{report_id} обработана. Победитель: <b>{hd.quote(winner)}</b>.",
            parse_mode="HTML",
        )
    await call.answer("Результат записан")


@router.callback_query(F.data.startswith("rres:yes:"))
async def admin_result_yes(call: CallbackQuery, bot: Bot):
    """[Да, победил X] — подтверждаем победителя, указанного лидером."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    r = get_match_report(int(call.data.rsplit(":", 1)[-1]))
    if not r:
        await call.answer("Заявка не найдена", show_alert=True)
        return
    await _apply_result(call, int(r["id"]), str(r["claimed_winner"] or ""), "confirmed", bot)


@router.callback_query(F.data.startswith("rres:no:"))
async def admin_result_no(call: CallbackQuery, bot: Bot):
    """[Нет, победил Y] — победил соперник, указанный в заявке."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    report_id = int(call.data.rsplit(":", 1)[-1])
    r = get_match_report(report_id)
    if not r:
        await call.answer("Заявка не найдена", show_alert=True)
        return
    m = get_match(int(r["match_id"]))
    other = m["side_b"] if m["side_a"] == r["claimed_winner"] else m["side_a"]
    if not other:
        await call.answer("Не удалось определить второго участника", show_alert=True)
        return
    await _apply_result(call, report_id, str(other), "confirmed_swapped", bot)


@router.callback_query(F.data.startswith("rres:reject:"))
async def admin_result_reject(call: CallbackQuery, bot: Bot):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    report_id = int(call.data.rsplit(":", 1)[-1])
    r = get_match_report(report_id)
    if not r or r["status"] != "pending":
        await call.answer("Заявка уже обработана", show_alert=True)
        return
    set_match_report_status(report_id, "rejected", call.from_user.id)
    await log_admin_action(
        bot=bot, admin_id=call.from_user.id,
        admin_name=call.from_user.username or call.from_user.full_name or "admin",
        action_text=f"🚫 Отклонил заявку на результат #{report_id} (турнир #{r['tournament_id']}).",
    )
    try:
        await bot.send_message(
            int(r["reporter_id"]),
            f"🚫 Твоя заявка на результат #{report_id} отклонена администрацией. "
            "Свяжись с админами, если считаешь это ошибкой.",
        )
    except Exception:
        pass
    await call.message.edit_text(f"🚫 Заявка #{report_id} отклонена.", reply_markup=None)
    await call.answer("Заявка отклонена")


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
        f"👔 Панель управления ViGarik Squad \n"
        f"Ваша текущая роль: {ROLE_LABELS.get(role, role)}\n\n"
        f"Выбери необходимое действие на клавиатуре ниже:",
        parse_mode="HTML",
        reply_markup=admin_panel_keyboard(role, user_id=message.from_user.id)
    )

# ─── ПАПКА «УЧАСТНИКИ» (Этап 1 ТЗ) ────────────────────────────────────────────
@router.message(F.text == "👥 Участники")
async def open_admin_participants_folder(message: Message):
    """Открывает папку «Участники»: управление списками, модерация, пуш-сезон, проверка юзернеймов."""
    member = await get_member(message.from_user.id)
    if not member or not has_role(member, "vice"):
        await message.answer("⛔ У вас нет доступа к разделу «Участники».")
        return
    await message.answer(
        "📂 Участники сети \n\n"
        "Выбери необходимое действие:\n"
        "• Управление списками и твинками\n"
        "• Управление пуш-сезоном (Президент / Гранд Вице)\n"
        "• Назначить модерацию (Президент)\n"
        "• Проверить юзернеймы (Президент)",
        parse_mode="HTML",
        reply_markup=admin_participants_keyboard(member.get("role", "member"))
    )

# ─── ПАПКА «СКРИМЫ» (Этап 1 ТЗ) ──────────────────────────────────────────────
@router.message(F.text == "🤺 Скримы")
async def open_admin_scrims_folder(message: Message):
    """Открывает папку «Скримы»: история игр, мониторинг незакрытых скримов, создание турнира."""
    member = await get_member(message.from_user.id)
    if not member or not has_role(member, "vice"):
        await message.answer("⛔ У вас нет доступа к разделу «Скримы».")
        return
    await message.answer(
        "📂 Скримы и турниры \n\n"
        "Выбери необходимое действие:\n"
        "• История последних игр сети\n"
        "• Мониторинг незакрытых скримов\n"
        "• Создание турнира (в разработке)",
        parse_mode="HTML",
        reply_markup=admin_scrims_keyboard()
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
        "📂 Управление пуш-сезоном Основы \n\n"
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
        "📂 Управление списками и участниками сети \n\n"
        "Выбери необходимое действие:\n"
        "• Изменить профиль/удалить игрока через ID\n"
        "• Посмотреть список участников без привязанных ников\n"
        "• Быстро привязать твинк к существующей основе игрока",
        parse_mode="HTML",
        reply_markup=admin_members_keyboard()
    )

# ─── МОНИТОРИНГ НЕЗАКРЫТЫХ СКРИМОВ (Этап 1 ТЗ, п.3) ─────────────────────────
SCRIM_STATUS_LABEL = {
    "searching": "🔍 Поиск соперника (рандомный скрим)",
    "invited": "📨 Приглашение отправлено, ждём ответа",
    "scheduled": "📅 Игра назначена, счёт ещё не отправлен",
    "awaiting_scores": "⏳ Ожидаем подтверждения счёта",
}


def _get_all_active_scrims():
    """Все незакрытые скримы (status IN SCRIM_ACTIVE_STATUSES), свежие сверху."""
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM scrims WHERE status IN (?, ?, ?, ?) ORDER BY created_at DESC",
        tuple(db.SCRIM_ACTIVE_STATUSES),
    ).fetchall()
    conn.close()
    return rows


def _side_report_line(scrim, side: str) -> str:
    """Строка по стороне скрима: отправлен ли итоговый счёт и сколько скриншотов."""
    if side == "challenger":
        name = scrim["challenger_name"] or f"#{scrim['challenger_league_id']}"
        score_a, score_b = scrim["challenger_score_a"], scrim["challenger_score_b"]
    else:
        name = scrim["opponent_name"] or "соперник"
        score_a, score_b = scrim["opponent_score_a"], scrim["opponent_score_b"]
    if score_a is None or score_b is None:
        score_txt = "❌ итоговый счёт не отправлен"
    else:
        score_txt = f"✅ счёт {int(score_a)}:{int(score_b)}"
    shots = db.get_scrim_shots_count(int(scrim["id"]), side)
    return f"• {hd.quote(str(name))}: {score_txt}; 📸 скриншотов: {int(shots)}"


def _monitor_text(rows) -> str:
    """Текст мониторинга: по каждому незакрытому скриму видно, кто не оставил счёт."""
    if not rows:
        return (
            "👀 <b>Незакрытые скримы</b>\n\n"
            "✅ Сейчас нет открытых/незакрытых скримов — все игры завершены, отменены или сгорели."
        )
    lines = ["👀 <b>Незакрытые скримы</b>\n"]
    for s in rows:
        label = SCRIM_TYPE_LABEL.get(s["scrim_type"], s["scrim_type"])
        status = SCRIM_STATUS_LABEL.get(s["status"], s["status"])
        created = str(s["created_at"] or "")[:16]
        opp_name = s["opponent_name"] or "поиск..."
        opp_tag = s["opponent_tag"] or "?"
        lines.append(
            f"<b>#{s['id']}</b> {label} · {hd.quote(str(s['format']))} · {hd.quote(str(s['scheduled_text'] or '—'))}\n"
            f"👑 {hd.quote(str(s['challenger_name'] or '?'))} [{hd.quote(str(s['challenger_tag'] or '?'))}] 🆚 "
            f"{hd.quote(str(opp_name))} [{hd.quote(str(opp_tag))}]\n"
            f"Статус: {hd.quote(str(status))} (создан {hd.quote(created)})"
        )
        if s["status"] in ("scheduled", "awaiting_scores"):
            lines.append(_side_report_line(s, "challenger"))
            if s["opponent_league_id"]:
                lines.append(_side_report_line(s, "opponent"))
            else:
                lines.append("• соперник ещё не найден")
        elif s["status"] == "searching":
            lines.append("• ⏳ Соперник подбирается автоматически (рандомный скрим)")
        else:
            lines.append("• ⏳ Приглашение ожидает ответа соперника")
        lines.append("")
    lines.append(
        "Чтобы <b>принудительно закрыть</b> скрим (без зачёта результата и без смены MMR), "
        "нажми «🚫 Закрыть» под нужной карточкой."
    )
    return "\n".join(lines)


def _monitor_keyboard(rows) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for s in rows[:15]:
        builder.button(text=f"🚫 Закрыть #{s['id']}", callback_data=f"adm_scrim:close:{s['id']}")
    builder.button(text="🔄 Обновить", callback_data="adm_scrim:refresh")
    builder.button(text="◀️ Назад", callback_data="adm_scrim:back")
    builder.adjust(1)
    return builder.as_markup()


def _split_text(text: str, limit: int = 4000) -> list[str]:
    """Режет текст по строкам, чтобы каждое сообщение Telegram не превышало лимит."""
    parts, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts or [""]


async def _cb_is_admin(user_id: int) -> bool:
    member = await get_member(user_id)
    return bool(member) and is_any_admin(member)


async def _refresh_monitor(call: CallbackQuery):
    """Обновляет сообщение мониторинга (edit_text, при неудаче — удалить и отправить заново)."""
    rows = _get_all_active_scrims()
    text = _monitor_text(rows)
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=_monitor_keyboard(rows))
        return
    except Exception:
        pass
    try:
        await call.message.delete()
    except Exception:
        pass
    parts = _split_text(text)
    for part in parts[:-1]:
        await call.message.answer(part, parse_mode="HTML")
    await call.message.answer(parts[-1], parse_mode="HTML", reply_markup=_monitor_keyboard(rows))


@router.message(F.text == "👀 Незакрытые скримы")
async def admin_open_scrims_monitor(message: Message):
    """Этап 1 ТЗ, п.3: отдельная кнопка в разделе «Скримы» со списком незакрытых скримов."""
    member = await get_member(message.from_user.id)
    if not member or not is_any_admin(member):
        await message.answer("⛔ Доступ только для администрации.")
        return
    rows = _get_all_active_scrims()
    parts = _split_text(_monitor_text(rows))
    for part in parts[:-1]:
        await message.answer(part, parse_mode="HTML")
    await message.answer(parts[-1], parse_mode="HTML", reply_markup=_monitor_keyboard(rows))


@router.callback_query(F.data.startswith("adm_scrim:close_yes:"))
async def admin_scrim_close_yes(call: CallbackQuery, bot: Bot):
    """Принудительное закрытие скрима администратором (с подтверждением)."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    scrim_id = int(call.data.rsplit(":", 1)[-1])
    scrim = db.get_scrim(scrim_id)
    if not scrim or scrim["status"] not in db.SCRIM_ACTIVE_STATUSES:
        await call.answer("Скрим уже закрыт", show_alert=True)
        return
    db.set_scrim_status(scrim_id, "cancelled")
    admin_name = call.from_user.username or call.from_user.first_name or str(call.from_user.id)
    opp_name = scrim["opponent_name"] or "соперник не найден"
    notify_text = (
        f"🚫 Администратор {hd.quote(str(admin_name))} принудительно закрыл скрим #{scrim_id} "
        f"({hd.quote(str(scrim['challenger_name'] or '?'))} 🆚 {hd.quote(str(opp_name))}).\n"
        f"Результат не засчитан, MMR не изменился."
    )
    for leader_id in {int(scrim["challenger_leader_id"] or 0), int(scrim["opponent_leader_id"] or 0)}:
        if leader_id:
            try:
                await bot.send_message(leader_id, notify_text, parse_mode="HTML")
            except Exception:
                pass
    await log_admin_action(
        bot=bot,
        admin_id=call.from_user.id,
        admin_name=call.from_user.username or call.from_user.first_name or "admin",
        action_text=(
            f"🚫 Принудительно закрыл незакрытый скрим #{scrim_id} "
            f"({scrim['challenger_name'] or '?'} vs {opp_name}). Статус: cancelled, MMR не менялся."
        ),
    )
    await call.message.edit_text(
        f"🚫 Скрим #{scrim_id} принудительно закрыт администратором {hd.quote(str(admin_name))}.\n"
        f"Статус: <b>cancelled</b>. Результат не засчитан, MMR не изменился. Лидерам отправлены уведомления.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔄 Обновить", callback_data="adm_scrim:refresh"),
            InlineKeyboardButton(text="◀️ Назад", callback_data="adm_scrim:back"),
        ]]),
    )
    await call.answer("Скрим закрыт")


@router.callback_query(F.data == "adm_scrim:close_no")
async def admin_scrim_close_no(call: CallbackQuery):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    await _refresh_monitor(call)
    await call.answer("Отменено")


@router.callback_query(F.data == "adm_scrim:refresh")
async def admin_scrim_refresh(call: CallbackQuery):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    await _refresh_monitor(call)
    await call.answer("Обновлено")


@router.callback_query(F.data == "adm_scrim:back")
async def admin_scrim_back(call: CallbackQuery):
    """Возврат из мониторинга в папку «Скримы»."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.message.answer(
        "📂 Скримы и турниры \n\n"
        "Выбери необходимое действие:",
        parse_mode="HTML",
        reply_markup=admin_scrims_keyboard()
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm_scrim:close:"))
async def admin_scrim_close_ask(call: CallbackQuery):
    """Запрос подтверждения перед принудительным закрытием скрима."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    scrim_id = int(call.data.rsplit(":", 1)[-1])
    scrim = db.get_scrim(scrim_id)
    if not scrim or scrim["status"] not in db.SCRIM_ACTIVE_STATUSES:
        await call.answer("Скрим уже закрыт", show_alert=True)
        return
    await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"✅ Да, закрыть #{scrim_id}", callback_data=f"adm_scrim:close_yes:{scrim_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="adm_scrim:close_no"),
    ]]))
    await call.answer("Подтвердите принудительное закрытие")

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
    """Показывает ID чата и текущего топика (нужен для настройки топиков «Турниры»)."""
    thread_id = message.message_thread_id
    await message.answer(
        f"🆔 ID этого чата: {message.chat.id} \n"
        f"📌 ID текущего топика: {thread_id if thread_id else 'General (без топика)'} \n\n"
        "Для топика «Турниры» скопируй ID топика и внеси через «⚙️ Топики турниров».",
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
