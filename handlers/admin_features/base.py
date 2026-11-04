""" Главный модуль админ-панели управления. Включает распределение по папкам меню (Участники, Скримы, пуш-система). Полная синхронизация для двух создателей. """
import asyncio
import logging
import os
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.markdown import html_decoration as hd
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from utils.username_monitor import check_and_update_usernames
from database import get_member, get_setting
from utils.permissions import is_any_admin, has_role
# ИСПРАВЛЕНО: Добавили импорт клавиатур папок участников и скримов (Этап 1 ТЗ)
from utils.keyboards import (
    main_menu, admin_panel_keyboard, admin_push_keyboard, admin_members_keyboard,
    admin_participants_keyboard, admin_scrims_keyboard,
)
from utils.admin_logger import log_admin_action
from league import league_db as db
# Переиспользуем готовые наработки скримов: подписи типов и парсер даты/времени
from league.competitive import SCRIM_TYPE_LABEL, _normalise_scheduled
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

# ─── МАСТЕР СОЗДАНИЯ ТУРНИРА (Этап 2 ТЗ: пошаговый FSM) ──────────────────────
# ShowDown — соло-режим, там лимит считается в игроках и жёстко зафиксирован на 10.
# Для 1V1/2V2/3V3 лимит задаётся в КОМАНДАХ (составах), а не в игроках.
SHOWDOWN_LIMIT = 10

TOURNAMENT_TYPES = {
    "1v1": "🤺 1V1",
    "2v2": "👥 2V2",
    "3v3": "🛡 3V3",
    "showdown": "💀 ShowDown",
}

# Охранники FSM-ввода: эти кнопки меню не должны попадать в число/дату турнира
TOURNAMENT_GUARD_TEXTS = frozenset({
    "👔 Для админов", "👥 Участники", "🤺 Скримы", "📢 Сделать объявление",
    "⚙️ Системные команды", "◀️ Выйти из админки", "🔙 Назад в админку",
    "📜 История игр", "🏆 Создание турнира", "👀 Незакрытые скримы", "📋 Текущие турниры",
    "👥 Управление участниками", "🎯 Управление пуш-сезоном",
    "⚙️ Назначить модерацию", "🔄 Проверить юзернеймы",
    "📋 Редактировать список клана", "👤 Участники без ников", "➕ Добавить твинк",
    "Составы🏆 (BetaTest)", "Составы 🏆 (BetaTest)", "💡 Отправить предложение",
    "🎯 Выбрать цель пуша", "◀️ Назад", "❌ Отмена",
})


class TournamentStates(StatesGroup):
    """Шаги мастера: тип → формат (Бо3/Бо5) → лимит участников → дата и время."""
    choosing_type = State()
    choosing_format = State()
    choosing_limit = State()
    waiting_limit_number = State()
    waiting_datetime = State()


def _tournament_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="trn:cancel")
    ]])


def _tournament_type_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, label in TOURNAMENT_TYPES.items():
        builder.button(text=label, callback_data=f"trn:type:{key}")
    builder.button(text="❌ Отмена", callback_data="trn:cancel")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def _tournament_format_kb() -> InlineKeyboardMarkup:
    """Формат матча — переиспользуем логику BO3/BO5 из старых скримов."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Бо3 (до 2 побед)", callback_data="trn:fmt:BO3"),
            InlineKeyboardButton(text="Бо5 (до 3 побед)", callback_data="trn:fmt:BO5"),
        ],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="trn:back:type")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="trn:cancel")],
    ])


def _tournament_limit_kb(back_to: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✏️ Задать число", callback_data="trn:limit:set"),
            InlineKeyboardButton(text="♾ Без лимита", callback_data="trn:limit:none"),
        ],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"trn:back:{back_to}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="trn:cancel")],
    ])


def _limit_unit(t_type: str, plural: bool = True) -> str:
    """Единица лимита: в ShowDown играют соло-игроки, в остальных типах — команды."""
    if t_type == "showdown":
        return "игроков" if plural else "игроков"
    return "команд" if plural else "команды"


def _limit_label(t_type: str) -> str:
    """Заголовок шага: «Лимит команд» или «Лимит игроков» (ShowDown)."""
    return "Лимит игроков" if t_type == "showdown" else "Лимит команд"


def _limit_text(limit, t_type: str = "") -> str:
    """Человеческая запись лимита. Лимит считается в КОМАНДАХ (составах), не в игроках."""
    if limit is None:
        return "♾ без лимита"
    return f"{int(limit)} {_limit_unit(t_type)}"


def _players_per_team(t_type: str) -> int:
    """Сколько игроков от команды реально выходит на карту."""
    return {"1v1": 1, "2v2": 2, "3v3": 3}.get(t_type, 1)


def _tournament_summary(data: dict) -> str:
    t_key = data.get("t_type")
    lines = [
        "🏆 <b>Черновик турнира</b>",
        "",
        f"• Тип: {TOURNAMENT_TYPES.get(t_key, t_key)}",
    ]
    if t_key != "showdown":
        lines.append(f"• Формат матча: <b>{data.get('t_format', '—')}</b>")
    else:
        lines.append("• Формат матча: не применяется (ShowDown)")
    limit = data.get("t_limit")
    lines.append(f"• {_limit_label(t_key)}: <b>{_limit_text(limit, t_key)}</b>")
    if t_key != "showdown":
        per_team = _players_per_team(t_key)
        note = f"• От каждой команды на карту выходит по {per_team} игрока"
        if limit is not None:
            note += f" (максимум {int(limit) * per_team} игроков на турнире)"
        lines.append(note)
    lines.append(f"• Начало: <b>{hd.quote(str(data.get('t_datetime') or '—'))}</b>")
    return "\n".join(lines)


async def _ask_tournament_limit(call: CallbackQuery, state: FSMContext, back_to: str):
    data = await state.get_data()
    t_key = data.get("t_type") or ""
    per_team = _players_per_team(t_key)
    await state.set_state(TournamentStates.choosing_limit)
    await call.message.edit_text(
        f"👥 <b>Шаг 3. {_limit_label(t_key)}</b>\n\n"
        f"Хотите ли вы задать лимит <b>команд</b> на турнир?\n"
        f"Можно ввести число или оставить турнир без лимита.\n\n"
        f"ℹ️ Лимит считается в <b>командах (составах)</b>, а не в игроках: "
        f"в составе числится до 4 человек, а на карту от команды выходит {per_team}.",
        parse_mode="HTML",
        reply_markup=_tournament_limit_kb(back_to),
    )


async def _ask_tournament_datetime(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.set_state(TournamentStates.waiting_datetime)
    await call.message.edit_text(
        f"{_tournament_summary(data)}\n\n"
        "🗓 <b>Шаг 4. Когда будет турнир?</b>\n\n"
        "Напиши дату и время начала сообщением, например: <code>25.12 19:00</code> "
        "или <code>25.12.2026 19:00</code>.\n"
        "Для отмены отправь /cancel.",
        parse_mode="HTML",
        reply_markup=_tournament_cancel_kb(),
    )


@router.message(F.text == "🏆 Создание турнира")
async def tournament_create_start(message: Message, state: FSMContext):
    """Шаг 1: выбор типа турнира (скрима)."""
    member = await get_member(message.from_user.id)
    if not member or not is_any_admin(member):
        await message.answer("⛔ Доступ только для администрации.")
        return
    await state.clear()
    await state.set_state(TournamentStates.choosing_type)
    await message.answer(
        "🏆 <b>Создание турнира</b>\n\n"
        "⚔️ <b>Шаг 1. Какой тип скрима вы хотите выбрать?</b>\n\n"
        "ℹ️ Для 1V1 / 2V2 / 3V3 лимит задаётся в <b>командах (составах)</b>, не в игроках.\n"
        f"ℹ️ При выборе <b>ShowDown</b> лимит автоматически фиксируется "
        f"на <b>{SHOWDOWN_LIMIT} игроках</b>.",
        parse_mode="HTML",
        reply_markup=_tournament_type_kb(),
    )


@router.callback_query(TournamentStates.choosing_type, F.data.startswith("trn:type:"))
async def tournament_choose_type(call: CallbackQuery, state: FSMContext):
    """Шаг 1 → Шаг 2. ShowDown: лимит = 10 и формат не спрашиваем."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    t_key = call.data.rsplit(":", 1)[-1]
    if t_key not in TOURNAMENT_TYPES:
        await call.answer("Неизвестный тип", show_alert=True)
        return
    await state.update_data(t_type=t_key)
    if t_key == "showdown":
        # Лимит фиксируется автоматически, формат матча к ShowDown не применяется
        await state.update_data(t_limit=SHOWDOWN_LIMIT, t_format=None)
        await call.answer(f"ShowDown: лимит {SHOWDOWN_LIMIT} игроков")
        await _ask_tournament_datetime(call, state)
        return
    await state.update_data(t_limit=None)
    await state.set_state(TournamentStates.choosing_format)
    await call.message.edit_text(
        f"Тип турнира: <b>{TOURNAMENT_TYPES[t_key]}</b>\n\n"
        "🎮 <b>Шаг 2. Какой тип скрима будет между команд?</b>\n\n"
        "Бо3 — побеждает тот, кто первым выиграет 2 карты.\n"
        "Бо5 — побеждает тот, кто первым выиграет 3 карты.",
        parse_mode="HTML",
        reply_markup=_tournament_format_kb(),
    )
    await call.answer()


@router.callback_query(TournamentStates.choosing_format, F.data.startswith("trn:fmt:"))
async def tournament_choose_format(call: CallbackQuery, state: FSMContext):
    """Шаг 2 → Шаг 3."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    fmt = call.data.rsplit(":", 1)[-1]
    if fmt not in ("BO3", "BO5"):
        await call.answer("Неизвестный формат", show_alert=True)
        return
    await state.update_data(t_format=fmt)
    await _ask_tournament_limit(call, state, back_to="format")
    await call.answer(fmt)


@router.callback_query(TournamentStates.choosing_limit, F.data == "trn:limit:none")
async def tournament_limit_none(call: CallbackQuery, state: FSMContext):
    """Шаг 3: безлимитный турнир."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    await state.update_data(t_limit=None)
    await call.answer("Без лимита")
    await _ask_tournament_datetime(call, state)


@router.callback_query(TournamentStates.choosing_limit, F.data == "trn:limit:set")
async def tournament_limit_ask_number(call: CallbackQuery, state: FSMContext):
    """Шаг 3: запрос числа команд (для ShowDown — число игроков)."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    data = await state.get_data()
    t_key = data.get("t_type") or ""
    unit = _limit_unit(t_key)
    await state.set_state(TournamentStates.waiting_limit_number)
    await call.message.edit_text(
        f"👥 <b>Шаг 3. {_limit_label(t_key)}</b>\n\n"
        f"Отправь число <b>{unit}</b> (от 2 до 128) сообщением, например: <code>16</code>.\n"
        f"Для отмены отправь /cancel.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="♾ Без лимита", callback_data="trn:limit:none_input")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="trn:back:limit")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="trn:cancel")],
        ]),
    )
    await call.answer()


@router.callback_query(TournamentStates.waiting_limit_number, F.data == "trn:limit:none_input")
async def tournament_limit_none_from_input(call: CallbackQuery, state: FSMContext):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    await state.update_data(t_limit=None)
    await call.answer("Без лимита")
    await _ask_tournament_datetime(call, state)


# Охранник: команды и кнопки меню никогда не считаются числом/датой
@router.message(
    StateFilter(TournamentStates.waiting_limit_number, TournamentStates.waiting_datetime),
    F.text.startswith("/") | F.text.in_(TOURNAMENT_GUARD_TEXTS),
)
async def tournament_input_guard(message: Message, state: FSMContext):
    """Выход из FSM по /cancel, /start и по нажатию любой кнопки меню."""
    await state.clear()
    text = (message.text or "").strip()
    if text.startswith("/cancel"):
        await message.answer("❌ Создание турнира отменено.", reply_markup=admin_scrims_keyboard())
        return
    await message.answer(
        "❌ Создание турнира прервано (вы нажали другую кнопку/команду).",
        reply_markup=admin_scrims_keyboard(),
    )


@router.message(TournamentStates.waiting_limit_number)
async def tournament_limit_number(message: Message, state: FSMContext):
    """Шаг 3: приём числа КОМАНД (для ShowDown — игроков)."""
    data_state = await state.get_data()
    unit = _limit_unit(data_state.get("t_type") or "")
    text = (message.text or "").strip()
    if not text.isdigit() or not (2 <= int(text) <= 128):
        await message.answer(
            f"❌ Нужно целое число {unit} от 2 до 128. Попробуй ещё раз или отправь /cancel.",
            parse_mode="HTML",
        )
        return
    await state.update_data(t_limit=int(text))
    await state.set_state(TournamentStates.waiting_datetime)
    data = await state.get_data()
    await message.answer(
        f"{_tournament_summary(data)}\n\n"
        "🗓 <b>Шаг 4. Когда будет турнир?</b>\n\n"
        "Напиши дату и время начала, например: <code>25.12 19:00</code> "
        "или <code>25.12.2026 19:00</code>.\n"
        "Для отмены отправь /cancel.",
        parse_mode="HTML",
        reply_markup=_tournament_cancel_kb(),
    )


@router.message(TournamentStates.waiting_datetime)
async def tournament_datetime(message: Message, state: FSMContext):
    """Шаг 4: дата и время начала турнира (парсер переиспользован из скримов)."""
    scheduled = _normalise_scheduled(message.text or "")
    if not scheduled:
        await message.answer(
            "❌ Неверный формат даты. Введи так: <code>25.12 19:00</code> "
            "или <code>25.12.2026 19:00</code>.\nДля отмены отправь /cancel.",
            parse_mode="HTML",
        )
        return
    await state.update_data(t_datetime=scheduled)
    data = await state.get_data()
    await state.set_state(None)
    await message.answer(
        f"{_tournament_summary(data)}\n\n"
        "✅ Все шаги мастера пройдены. Проверь данные и подтверди создание турнира.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Создать турнир", callback_data="trn:confirm")],
            [InlineKeyboardButton(text="🧪 Создать тестовый (без рассылки в ЛС)",
                                  callback_data="trn:confirm_test")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="trn:cancel")],
        ]),
    )


@router.callback_query(F.data.startswith("trn:back:"))
async def tournament_back(call: CallbackQuery, state: FSMContext):
    """Кнопки «Назад» по шагам мастера."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    target = call.data.rsplit(":", 1)[-1]
    if target == "type":
        await state.set_state(TournamentStates.choosing_type)
        await call.message.edit_text(
            "🏆 <b>Создание турнира</b>\n\n"
            "⚔️ <b>Шаг 1. Какой тип скрима вы хотите выбрать?</b>\n\n"
            "ℹ️ Для 1V1 / 2V2 / 3V3 лимит задаётся в <b>командах (составах)</b>, не в игроках.\n"
            f"ℹ️ При выборе <b>ShowDown</b> лимит автоматически фиксируется "
            f"на <b>{SHOWDOWN_LIMIT} игроках</b>.",
            parse_mode="HTML",
            reply_markup=_tournament_type_kb(),
        )
    elif target == "format":
        data = await state.get_data()
        t_key = data.get("t_type")
        await state.set_state(TournamentStates.choosing_format)
        await call.message.edit_text(
            f"Тип турнира: <b>{TOURNAMENT_TYPES.get(t_key, t_key)}</b>\n\n"
            "🎮 <b>Шаг 2. Какой тип скрима будет между команд?</b>",
            parse_mode="HTML",
            reply_markup=_tournament_format_kb(),
        )
    else:  # limit
        data = await state.get_data()
        back_to = "type" if data.get("t_type") == "showdown" else "format"
        await _ask_tournament_limit(call, state, back_to=back_to)
    await call.answer()


@router.callback_query(F.data == "trn:cancel")
async def tournament_cancel(call: CallbackQuery, state: FSMContext):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    await state.clear()
    try:
        await call.message.edit_text("❌ Создание турнира отменено.", reply_markup=None)
    except Exception:
        pass
    await call.message.answer(
        "📂 Скримы и турниры \n\nВыбери необходимое действие:",
        parse_mode="HTML",
        reply_markup=admin_scrims_keyboard(),
    )
    await call.answer("Отменено")


# ─── ЭТАП 3: БАЗА ТУРНИРОВ (идемпотентные миграции в league.db) ──────────────

def init_tournament_tables() -> None:
    """Создаёт таблицы турниров. Безопасно вызывать сколько угодно раз.

    Если таблицы остались от черновой/incompatible версии (например, матч со
    side_a/side_b вместо team_a_id/team_b_id) — пересоздаём их чистыми. Там были
    только тестовые турниры, боевые данные составов/скримов это не затрагивает.
    """
    conn = db.get_connection()
    existing = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    required = {
        "tournaments": {"t_type", "t_format", "team_limit", "starts_at", "status"},
        "tournament_teams": {"tournament_id", "league_id", "leader_id", "name", "tag"},
        "tournament_matches": {"tournament_id", "round_no", "slot_no", "bracket",
                               "team_a_id", "team_b_id", "winner_team_id"},
        "tournament_results": {"match_id", "tournament_id", "reporter_id", "status"},
        "tournament_posts": {"tournament_id", "chat_id", "message_id"},
    }
    for table, need in required.items():
        if table in existing:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if not need.issubset(cols):
                for t in ("tournament_results", "tournament_matches", "tournament_posts",
                          "tournament_teams", "tournaments"):
                    conn.execute(f"DROP TABLE IF EXISTS {t}")
                logger.warning("Турнирные таблицы пересозданы: старая схема несовместима")
                break
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tournaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            t_type TEXT NOT NULL,
            t_format TEXT,
            team_limit INTEGER,
            starts_at TEXT NOT NULL,
            status TEXT DEFAULT 'registration',
            created_by INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS tournament_teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            league_id INTEGER,
            leader_id INTEGER NOT NULL,
            name TEXT DEFAULT '',
            tag TEXT DEFAULT '',
            registered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS tournament_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            clan_key TEXT,
            chat_id INTEGER NOT NULL,
            thread_id INTEGER,
            message_id INTEGER NOT NULL,
            FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS tournament_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            round_no INTEGER NOT NULL,
            slot_no INTEGER NOT NULL,
            bracket TEXT DEFAULT 'main',
            team_a_id INTEGER,
            team_b_id INTEGER,
            winner_team_id INTEGER,
            loser_team_id INTEGER,
            status TEXT DEFAULT 'pending',
            score_text TEXT DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished_at DATETIME,
            FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS tournament_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER NOT NULL,
            tournament_id INTEGER NOT NULL,
            reporter_id INTEGER NOT NULL,
            reporter_team_id INTEGER,
            text_note TEXT DEFAULT '',
            photos TEXT DEFAULT '[]',
            status TEXT DEFAULT 'pending',
            decided_by INTEGER,
            decided_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (match_id) REFERENCES tournament_matches(id) ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tournament_team_unique
            ON tournament_teams (tournament_id, leader_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tournament_match_slot
            ON tournament_matches (tournament_id, bracket, round_no, slot_no);
        CREATE INDEX IF NOT EXISTS idx_tournament_status ON tournaments (status);
        CREATE INDEX IF NOT EXISTS idx_tournament_posts_t ON tournament_posts (tournament_id);
        CREATE INDEX IF NOT EXISTS idx_tournament_matches_t ON tournament_matches (tournament_id, status);
        CREATE INDEX IF NOT EXISTS idx_tournament_results_m ON tournament_results (match_id, status);
    """)
    # Миграция: флаг тестового турнира (без рассылки в ЛС всем игрокам)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(tournaments)").fetchall()}
    if "is_test" not in cols:
        conn.execute("ALTER TABLE tournaments ADD COLUMN is_test INTEGER DEFAULT 0")
    # Миграция: флаг «лидеры новой пары уже уведомлены» (защита от повторных рассылок)
    cols_m = {r["name"] for r in conn.execute("PRAGMA table_info(tournament_matches)").fetchall()}
    if "notified" not in cols_m:
        conn.execute("ALTER TABLE tournament_matches ADD COLUMN notified INTEGER DEFAULT 0")
    conn.commit()
    conn.close()


def _is_test(tournament) -> bool:
    try:
        return int(tournament["is_test"] or 0) == 1
    except Exception:
        return False


def _test_mark(tournament) -> str:
    """Префикс-метка тестового турнира для всех постов и карточек."""
    return "🧪 <b>[ТЕСТ]</b> " if _is_test(tournament) else ""


def _tour_name(tournament) -> str:
    """Публичное имя турнира: дата и время вместо техничного #ID.

    Показываем всем «Турнир 25.12.2026 19:00», а не «Турнир #4».
    """
    starts = str(tournament["starts_at"] or "").strip()
    return starts if starts else f"#{tournament['id']}"


def create_tournament_row(t_type, t_format, team_limit, starts_at, created_by,
                          is_test: int = 0) -> int:
    init_tournament_tables()
    conn = db.get_connection()
    cur = conn.execute(
        "INSERT INTO tournaments (t_type, t_format, team_limit, starts_at, created_by, is_test) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (t_type, t_format, team_limit, starts_at, created_by, int(is_test)),
    )
    conn.commit()
    tid = int(cur.lastrowid)
    conn.close()
    return tid


def get_tournament(tournament_id: int):
    init_tournament_tables()
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
    conn.close()
    return row


def get_tournament_teams(tournament_id: int):
    init_tournament_tables()
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM tournament_teams WHERE tournament_id = ? ORDER BY registered_at, id",
        (tournament_id,),
    ).fetchall()
    conn.close()
    return rows


def add_tournament_team(tournament_id, league_id, leader_id, name, tag) -> bool:
    """Регистрирует команду (или соло-игрока в ShowDown). False — если уже зарегистрирован."""
    init_tournament_tables()
    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT INTO tournament_teams (tournament_id, league_id, leader_id, name, tag) "
            "VALUES (?, ?, ?, ?, ?)",
            (tournament_id, league_id, leader_id, name, tag),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def save_tournament_post(tournament_id, clan_key, chat_id, thread_id, message_id) -> None:
    init_tournament_tables()
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO tournament_posts (tournament_id, clan_key, chat_id, thread_id, message_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (tournament_id, clan_key, chat_id, thread_id, message_id),
    )
    conn.commit()
    conn.close()


def get_tournament_posts(tournament_id: int):
    init_tournament_tables()
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM tournament_posts WHERE tournament_id = ?", (tournament_id,)
    ).fetchall()
    conn.close()
    return rows


# ─── ЭТАП 3: СЕТКА С АВТОПРОХОДАМИ (BYE) ─────────────────────────────────────

def build_bracket_pairs(names: list[str]):
    """Пары первого раунда с автопроходами.

    Доводим число участников до ближайшей степени двойки: недостающие места —
    BYE (автопроход). BYE раздаются верхним посевам, поэтому при нечётном числе
    команд кто-то проходит дальше без игры.
    """
    n = len(names)
    if n < 2:
        return [], 0, max(0, 2 - n)
    size = 1
    while size < n:
        size *= 2
    byes = size - n
    slots = list(names) + [None] * byes
    pairs = [(slots[i], slots[i + 1]) for i in range(0, size, 2)]
    rounds = size.bit_length() - 1
    return pairs, rounds, byes


def _bracket_text(names: list[str]) -> str:
    """Текстовая сетка — запасной вариант, если Pillow недоступен."""
    layout, auto_n = _bracket_layout(names)
    if not layout:
        return "Участников пока недостаточно для сетки."
    playin_pairs = [p for p in layout[0] if p[0] and p[1]]
    lines = [f"Плей-ин / первый круг ({len(playin_pairs)} пар):"]
    for i, (a, b) in enumerate(playin_pairs, 1):
        lines.append(f"{i}. {a} 🆚 {b}")
    if auto_n:
        autos = names[:auto_n]
        lines.append(f"⏭ Сразу в следующий круг ({auto_n}): {', '.join(autos)}")
    return "\n".join(lines)


def _match_by_slot(matches, bracket: str, round_no: int, slot_no: int):
    """Находит матч из списка по ветке/раунду/слоту (для отрисовки победителей)."""
    if not matches:
        return None
    for m in matches:
        if (str(m["bracket"] or "main") == bracket and
                int(m["round_no"]) == int(round_no) and int(m["slot_no"]) == int(slot_no)):
            return m
    return None


def _round_title(pairs_in_round: int, round_idx: int, total_rounds: int,
                 playin: bool) -> str:
    """Заголовок раунда с учётом плей-ина и позиции в дереве.

    Без эмодзи: шрифт DejaVu Sans их не содержит, Pillow рисовал бы «квадраты».
    """
    if playin and round_idx == 0:
        return "ПЛЕЙ-ИН"
    if round_idx == total_rounds - 1:
        return "ФИНАЛ"
    if round_idx == total_rounds - 2:
        return "ПОЛУФИНАЛ"
    names = {4: "1/4", 8: "1/8", 16: "1/16", 32: "1/32", 64: "1/64"}
    return f"{names.get(pairs_in_round, f'1/{pairs_in_round}')} ФИНАЛА"


def render_bracket_image(names: list[str], title: str, matches=None):
    """Рисует сетку турнира (PNG в BytesIO). None — если Pillow не установлен.

    Компактная схема без фейковых матчей с BYE: первый круг — плей-ин только из
    реальных пар, а команды без пары показаны сразу в следующем круге.
    Если передан matches — подмешиваем реальные продвинувшиеся команды и
    подсвечиваем победителей зелёным.
    """
    try:
        from io import BytesIO
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
    except Exception as e:
        logger.warning(f"Pillow недоступен, сетка будет текстовой: {e}")
        return None

    n = len(names)
    if n < 2:
        return None
    size = 1
    while size < n:
        size *= 2
    total_rounds = size.bit_length() - 1
    playin = (n != size)
    playin_matches = n - size // 2 if playin else 0
    autos = size - n

    # ── Данные раундов: [ (label_a, label_b, win_side|None, live) ] ──
    # live=True — матч играбельный ПРЯМО СЕЙЧАС: обе команды известны и итог не записан.
    rounds_data = []
    if matches:
        mains = [m for m in matches if str(m["bracket"] or "main") == "main"]
        by_round = {}
        for m in mains:
            by_round.setdefault(int(m["round_no"]), []).append(m)
        for r in sorted(by_round):
            row_pairs = []
            for m in sorted(by_round[r], key=lambda x: int(x["slot_no"])):
                la = _team_name(m["team_a_id"], "ждёт")
                lb = _team_name(m["team_b_id"], "ждёт")
                win = None
                if m["winner_team_id"]:
                    wid = int(m["winner_team_id"])
                    if m["team_a_id"] and int(m["team_a_id"]) == wid:
                        win = 0
                    elif m["team_b_id"] and int(m["team_b_id"]) == wid:
                        win = 1
                live = (m["status"] == "pending" and m["team_a_id"] and m["team_b_id"]
                        and not m["winner_team_id"])
                row_pairs.append((la, lb, win, bool(live)))
            rounds_data.append(row_pairs)
        while len(rounds_data) < total_rounds:
            idx = len(rounds_data)
            rounds_data.append([("ждёт", "ждёт", None, False)]
                               * max(1, size >> (idx + 1)))
    else:
        layout, _ = _bracket_layout(names)
        for rnd in layout:
            rounds_data.append([(a or "ждёт", b or "ждёт", None, False)
                                for (a, b) in rnd])
    if not rounds_data:
        return None

    third = None
    if matches:
        for _m in matches:
            if str(_m["bracket"] or "main") == "third":
                third = _m
                break

    # ── Пьедестал: 1/2 место из финала, 3/4 — из матча за 3-е место ──
    places = []
    if matches:
        mains = [m for m in matches if str(m["bracket"] or "main") == "main"]
        if mains:
            max_round = max(int(m["round_no"]) for m in mains)
            final_ms = [m for m in mains if int(m["round_no"]) == max_round]
            # Считается, только если последний раунд один и у него есть победитель
            final_done = [m for m in final_ms if m["winner_team_id"]]
            if len(final_ms) == 1 and final_done:
                fm = final_ms[0]
                places.append((1, _team_name(fm["winner_team_id"])))
                if fm["loser_team_id"]:
                    places.append((2, _team_name(fm["loser_team_id"])))
        if third and third["winner_team_id"]:
            places.append((3, _team_name(third["winner_team_id"])))
            if third["loser_team_id"]:
                places.append((4, _team_name(third["loser_team_id"])))
    podium_h = 140 if places else 0   # полоса пьедестала горизонтальная, высота фиксированная

    def _font(size_pt: int):
        for path in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ):
            try:
                return ImageFont.truetype(path, size_pt)
            except Exception:
                continue
        return ImageFont.load_default()

    def _img_text(text: str) -> str:
        """Убирает символы без глифов в DejaVu (все эмодзи), чтобы нет «квадратов»."""
        out = []
        for ch in str(text):
            o = ord(ch)
            if (o < 0x7F or 0x0400 <= o <= 0x04FF          # латиница + кириллица
                    or 0x2010 <= o <= 0x205E               # тире, кавычки, многоточие
                    or 0x2190 <= o <= 0x21FF               # стрелки
                    or 0x2500 <= o <= 0x257F               # box drawing
                    or o in (0x00B7, 0x2022, 0x2713)):     # ·, •, ✓
                out.append(ch)
        return " ".join("".join(out).split())

    # ── геометрия ──
    SS = 2                       # supersampling: рисуем в 2× и сжимаем — гладкие края
    box_w, box_h = 284, 48
    gap_in = 12
    pair_gap = 30
    col_gap = 76
    x0 = 48
    header_h = 100
    y_head = header_h + podium_h  # сетка ниже, если рисуется пьедестал
    y_top = y_head + 26
    match_h = 2 * box_h + gap_in

    ys_per_round: list[list[int]] = []
    for r, pairs_r in enumerate(rounds_data):
        if r == 0:
            ys_per_round.append([y_top + j * (match_h + pair_gap) for j in range(len(pairs_r))])
        else:
            prev = ys_per_round[-1]
            ys_r = []
            for j in range(len(pairs_r)):
                c1 = prev[2 * j] + match_h / 2
                c2 = prev[2 * j + 1] + match_h / 2
                ys_r.append(int((c1 + c2) / 2 - match_h / 2))
            ys_per_round.append(ys_r)
    grid_bottom = ys_per_round[0][-1] + match_h
    third_strip = 172 if third else 0
    width = x0 + total_rounds * (box_w + col_gap) + 26
    height = int(grid_bottom + 92 + third_strip)

    # ── палитра тёмной темы VGS (рассчитана поверх фотофона: линии/рамки ярче) ──
    BG = (13, 17, 30)
    CARD = (30, 37, 58)            # активный слот
    PANEL = (19, 24, 39)           # карточки и ожидающие слоты
    WON_BG = (22, 48, 37)          # победитель
    BORDER = (128, 144, 188)       # обводка боксов — не сливается с фоном
    BORDER_P = (86, 98, 132)       # обводка ожидающих слотов
    CONN = (158, 176, 216)         # соединители — заметно светлее
    DIVIDER = (80, 92, 126)
    GREEN = (53, 211, 127)
    GOLD = (255, 214, 92)
    TXT = (255, 255, 255)          # названия команд — чисто белые
    DIM = (148, 158, 188)
    DIMM = (108, 118, 150)

    F_TITLE = _font(30 * SS)
    F_HEAD = _font(15 * SS)
    F_BOX = _font(19 * SS)
    F_SMALL = _font(13 * SS)

    # ── Фон: арт (assets/bracket_bg.*) cover-кропом, мягкий blur, затемнение ──
    img = None
    for bg_path in ("assets/bracket_bg.png", "assets/bracket_bg.jpg",
                    "assets/bracket_bg.webp", "bracket_bg.png", "bracket_bg.jpg"):
        try:
            if not os.path.exists(bg_path):
                continue
            bg = Image.open(bg_path).convert("RGB")
            bw, bh = bg.size
            sc = max(width * SS / max(bw, 1), height * SS / max(bh, 1))
            bg = bg.resize((int(bw * sc + 0.5), int(bh * sc + 0.5)), Image.LANCZOS)
            left = (bg.width - width * SS) // 2
            top = (bg.height - height * SS) // 2
            bg = bg.crop((left, top, left + width * SS, top + height * SS))
            # слегка сглаживаем арт (убирает шум апскейла)…
            bg = bg.filter(ImageFilter.GaussianBlur(radius=1.6 * SS))
            # …и затемняем сильнее (0.74): фон остаётся декорацией, контент читается
            bg = Image.blend(bg, Image.new("RGB", bg.size, (9, 12, 24)), 0.74)
            img = bg
            break
        except Exception as e:
            logger.warning(f"Фоновая картинка сетки {bg_path} не загружена: {e}")
    if img is None:
        img = Image.new("RGB", (width * SS, height * SS), BG)
    draw = ImageDraw.Draw(img)

    def T(px, py, s, f, fill):
        draw.text((int(px * SS), int(py * SS)), s, font=f, fill=fill)

    def RR(px, py, pw, ph, r, fill, outline=None, wdt=1):
        draw.rounded_rectangle(
            [px * SS, py * SS, (px + pw) * SS, (py + ph) * SS],
            radius=r * SS, fill=fill, outline=outline,
            width=(wdt * SS if outline else 1))

    def LN(x1, y1, x2, y2, fill, wdt=2):
        draw.line([(int(x1 * SS), int(y1 * SS)), (int(x2 * SS), int(y2 * SS))],
                  fill=fill, width=wdt * SS)

    def DISC(px, py, d, fill):
        draw.ellipse([px * SS, py * SS, (px + d) * SS, (py + d) * SS], fill=fill)

    def TW(s, f):
        try:
            return draw.textlength(s, font=f) / SS
        except Exception:
            return len(str(s)) * 7

    def _fit(text: str, limit: int = 21) -> str:
        text = str(text)
        return text if len(text) <= limit else text[: limit - 1] + "…"

    # ── заголовок: «Турнир #N» акцентом, остаток приглушён (без жёлтой плашки) ──
    t_parts = _img_text(title[:70]).split("·", 1)
    T(x0, 20, t_parts[0].strip(), F_TITLE, GOLD)
    if len(t_parts) > 1:
        T(x0 + TW(t_parts[0].strip(), F_TITLE) + 12, 20,
          "· " + t_parts[1].strip(), F_TITLE, DIM)
    LN(x0, 66, width - x0, 66, DIVIDER, 1)

    # ── Полоса «ПОБЕДИТЕЛИ»: медальные диски, место полностью, имя белым ──
    if places:
        T(x0, 84, "ПОБЕДИТЕЛИ", F_HEAD, GOLD)
        card_y = 108
        card_h = 86
        RR(x0, card_y, width - 2 * x0, card_h, 14, PANEL, BORDER_P)
        medal_fill = {1: GOLD, 2: (201, 210, 222), 3: (206, 128, 56)}
        cols = max(len(places), 1)
        col_w = max(230, (width - 2 * x0 - 24) // cols)
        for i, (place, pname) in enumerate(places):
            cx = x0 + 22 + i * col_w
            color = medal_fill.get(place, (150, 160, 185))
            d = 30
            DISC(cx, card_y + 28, d, color)
            num = str(place)
            T(cx + d / 2 - TW(num, F_BOX) / 2, card_y + 28 + d / 2 - 11, num, F_BOX, BG)
            T(cx + d + 14, card_y + 20, f"{place} МЕСТО", F_SMALL, DIMM)
            lim = max(8, int((col_w - d - 40) / 9.2))
            T(cx + d + 14, card_y + 44, _img_text(_fit(pname, lim)), F_BOX, TXT)

    def _box(px: int, py: int, label: str, style: str, won: bool):
        nonlocal img, draw
        if style == "won":
            fill, outline, tc, wdt = WON_BG, GREEN, TXT, 2
        elif style == "live":
            # играется сейчас: мягкое неоновое свечение + тонкая зелёная рамка
            fill, outline, tc, wdt = (24, 46, 37), GREEN, TXT, 3
        elif style in ("lost", "pending"):
            fill, outline, tc, wdt = PANEL, BORDER_P, DIMM, 1
        else:
            fill, outline, tc, wdt = CARD, BORDER, TXT, 1
        if style == "live":
            # свечение рисуется на отдельном слое и размывается — получаем плавную ару,
            # а не жёсткое пятно за боксом
            glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
            gd = ImageDraw.Draw(glow)
            gd.rounded_rectangle(
                [(px - 8) * SS, (py - 8) * SS, (px + box_w + 8) * SS, (py + box_h + 8) * SS],
                radius=20 * SS, fill=(53, 211, 127, 96))
            glow = glow.filter(ImageFilter.GaussianBlur(radius=7 * SS))
            img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
            draw = ImageDraw.Draw(img)
        RR(px, py, box_w, box_h, 13, fill, outline, wdt)
        max_chars = int((box_w - (64 if won else 28)) / 9.2)
        T(px + 16, py + (box_h - 21) // 2,
          _img_text(_fit(label, max_chars)), F_BOX, tc)
        if won:
            d = 18
            DISC(px + box_w - d - 13, py + (box_h - d) // 2, d, GREEN)
            T(px + box_w - 13 - d / 2 - TW("✓", F_BOX) / 2,
              py + box_h / 2 - 11, "✓", F_BOX, BG)

    # Текущий раунд — самый ранний, где есть ИГРАБЕЛЬНЫЙ матч (обе команды есть)
    current_round = None
    if matches:
        pend = [int(m["round_no"]) for m in matches
                if str(m["bracket"] or "main") == "main" and m["status"] == "pending"
                and m["team_a_id"] and m["team_b_id"] and not m["winner_team_id"]]
        if pend:
            current_round = min(pend)

    # ── колонки раундов: заголовки по центру колонки, соединители-уголки ──
    for r, pairs_r in enumerate(rounds_data):
        x = x0 + r * (box_w + col_gap)
        cap = _round_title(len(pairs_r), r, total_rounds, playin)
        is_current = (current_round == r + 1)
        cap_color = GREEN if is_current else DIMM
        cap_x = x + max(0, (box_w - TW(cap, F_HEAD)) / 2)
        T(cap_x, y_head, cap, F_HEAD, cap_color)
        if is_current:
            # зелёная плашка-подчёркивание у играющегося сейчас раунда
            LN(cap_x, y_head + 21, cap_x + TW(cap, F_HEAD), y_head + 21, GREEN, 2)
        ys_r = ys_per_round[r]
        for j, (la, lb, win, live) in enumerate(pairs_r):
            ya, yb = ys_r[j], ys_r[j] + box_h + gap_in
            for k, (lab, yy) in enumerate(((la, ya), (lb, yb))):
                if lab == "ждёт":
                    style, won = "pending", False
                elif win is not None and win == k:
                    style, won = "won", True
                elif win is not None:
                    style, won = "lost", False
                elif live:
                    style, won = "live", False
                else:
                    style, won = "idle", False
                _box(x, yy, lab, style, won)
            if r < total_rounds - 1:
                xm = x + box_w + col_gap // 2
                yc = ys_r[j] + match_h / 2
                tgt = ys_per_round[r + 1][j // 2] + match_h / 2
                LN(x + box_w, yc, xm, yc, CONN)
                LN(xm, min(yc, tgt), xm, max(yc, tgt), CONN)
                LN(xm, tgt, x + box_w + col_gap, tgt, CONN)

    # ── матч за 3-е место: отдельная панель с золотой полосой (отступы шире) ──
    if third:
        strip_y = grid_bottom + 32
        RR(x0, strip_y, min(width - 2 * x0, 2 * box_w + 96), 148, 14, PANEL, BORDER_P)
        RR(x0, strip_y, 6, 148, 3, GOLD)
        T(x0 + 34, strip_y + 20, "МАТЧ ЗА 3-Е МЕСТО", F_HEAD, GOLD)
        done = bool(third["winner_team_id"])
        live3 = (not done) and third["team_a_id"] and third["team_b_id"]
        for k, key in enumerate(("team_a_id", "team_b_id")):
            team = third[key]
            lab = _team_name(team) if team else "ждёт"
            won = bool(done) and team and int(third["winner_team_id"]) == int(team)
            if won:
                style = "won"
            elif live3:
                style = "live"
            else:
                style = "pending" if not team else "idle"
            _box(x0 + 34 + k * (box_w + 16), strip_y + 60, lab, style, bool(won))

    footer = (f"Команд: {n} · плей-ин: {playin_matches} матч. · "
              f"автопроходов сразу: {autos}")
    T(x0, height - 30, footer, F_SMALL, DIMM)
    wm = "VGStats bracket"
    T(width - x0 - TW(wm, F_SMALL), height - 30, wm, F_SMALL, (74, 82, 108))

    # сглаживание: сжимаем с 2× до целевого размера
    img = img.resize((width, height), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ─── ЭТАП 3: ТЕКСТ АНОНСА И РАССЫЛКА ─────────────────────────────────────────

def _tournament_card(tournament, teams) -> str:
    """Пост-анонс турнира: параметры, лимит команд, список участников."""
    t_key = tournament["t_type"]
    limit = tournament["team_limit"]
    lines = [
        f"{_test_mark(tournament)}🏆 <b>АНОНС ТУРНИРА</b>",
        "",
    ]
    if _is_test(tournament):
        lines.append("⚠️ Это <b>тестовый турнир</b> для проверки системы.")
        lines.append("")
    lines.append(f"• Тип: <b>{TOURNAMENT_TYPES.get(t_key, t_key)}</b>")
    if t_key != "showdown":
        lines.append(f"• Формат матча: <b>{tournament['t_format'] or '—'}</b>")
        lines.append(f"• От команды играет: <b>{_players_per_team(t_key)}</b>")
    lines.append(f"• {_limit_label(t_key)}: <b>{_limit_text(limit, t_key)}</b>")
    lines.append(f"• Начало: <b>{hd.quote(str(tournament['starts_at']))}</b>")
    lines.append("")
    unit = _limit_unit(t_key)
    if limit is None:
        lines.append(f"📋 <b>Зарегистрировано {unit}: {len(teams)}</b> (без лимита)")
    else:
        lines.append(f"📋 <b>Зарегистрировано {unit}: {len(teams)} из {int(limit)}</b>")
    if teams:
        for i, t in enumerate(teams, 1):
            tag = f" [{hd.quote(str(t['tag']))}]" if t["tag"] else ""
            lines.append(f"{i}. {hd.quote(str(t['name'] or 'Без названия'))}{tag}")
    else:
        lines.append("Пока никто не зарегистрировался.")
    lines.append("")
    if t_key == "showdown":
        lines.append("Участвовать может любой игрок сети — жми кнопку ниже.")
    else:
        lines.append(
            "Регистрирует команду <b>лидер состава</b>. В состав может входить "
            "любой участник из трёх кланов сети."
        )
    return "\n".join(lines)


def _tournament_reg_kb(tournament_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Зарегистрироваться", callback_data=f"trn:reg:{tournament_id}")
    ]])


# Тестовые посты турниров летят ТОЛЬКО в этот топик клана Squad
# (из ссылки https://t.me/c/2187842577/405196: chat_id = -1002187842577, thread_id = 405196)
TOURNAMENT_TEST_TOPIC = {"chat_id": -1002187842577, "thread_id": 405196}


def _tournament_topics(is_test: bool = False) -> dict:
    """Топики «Турниры» по кланам.

    - Тестовый турнир: только тестовый топик Squad (TOURNAMENT_TEST_TOPIC
      или config.TOURNAMENT_TEST_TOPIC).
    - Боевой турнир: config.TOURNAMENT_TOPICS, если заданы, иначе новостные
      топики из ADMIN_NEWS_TARGETS.
    """
    import config
    if is_test:
        return {"squad": getattr(config, "TOURNAMENT_TEST_TOPIC", None) or TOURNAMENT_TEST_TOPIC}
    topics = getattr(config, "TOURNAMENT_TOPICS", None)
    if topics:
        return topics
    return getattr(config, "ADMIN_NEWS_TARGETS", {}) or {}


async def _publish_tournament_posts(bot: Bot, tournament_id: int):
    """Публикует анонс с картинкой сетки в клановые топики «Турниры»."""
    tournament = get_tournament(tournament_id)
    teams = get_tournament_teams(tournament_id)
    text = _tournament_card(tournament, teams)
    names = [str(t["name"] or "Команда") for t in teams]
    image = render_bracket_image(names, f"{_tour_name(tournament)} · предварительная сетка")
    if image is None and names:
        text += "\n\n<b>Предварительная сетка:</b>\n" + hd.quote(_bracket_text(names))

    ok, failed = [], []
    for clan_key, target in _tournament_topics(is_test=_is_test(tournament)).items():
        chat_id = target.get("chat_id")
        thread_id = target.get("thread_id")
        if not chat_id:
            continue
        try:
            if image is not None:
                from aiogram.types import BufferedInputFile
                image.seek(0)
                photo = BufferedInputFile(image.read(), filename=f"bracket_{tournament_id}.png")
                msg = await bot.send_photo(
                    chat_id=chat_id, message_thread_id=thread_id, photo=photo,
                    caption=text[:1024], parse_mode="HTML",
                    reply_markup=_tournament_reg_kb(tournament_id),
                )
            else:
                msg = await bot.send_message(
                    chat_id=chat_id, message_thread_id=thread_id, text=text,
                    parse_mode="HTML", reply_markup=_tournament_reg_kb(tournament_id),
                )
            save_tournament_post(tournament_id, clan_key, chat_id, thread_id, msg.message_id)
            ok.append(clan_key)
        except Exception as e:
            logger.warning(f"Не удалось опубликовать анонс турнира в {clan_key}: {e}")
            failed.append(clan_key)
    return ok, failed


async def _broadcast_tournament_dm(bot: Bot, tournament_id: int):
    """Рассылает уведомление в ЛС всем зарегистрированным игрокам сети."""
    from database import get_all_members
    tournament = get_tournament(tournament_id)
    t_key = tournament["t_type"]
    base_text = (
        "🏆 <b>Скоро турнир!</b>\n\n"
        f"• Тип: <b>{TOURNAMENT_TYPES.get(t_key, t_key)}</b>\n"
        + (f"• Формат матча: <b>{tournament['t_format'] or '—'}</b>\n" if t_key != "showdown" else "")
        + f"• {_limit_label(t_key)}: <b>{_limit_text(tournament['team_limit'], t_key)}</b>\n"
        f"• Начало: <b>{hd.quote(str(tournament['starts_at']))}</b>\n\n"
    )
    no_team_text = (
        "❗ Для участия нужно иметь <b>состав</b>. Сейчас у тебя его нет.\n"
        "Создай состав в разделе «Составы🏆» — в него может входить "
        "<b>любой участник из трёх кланов</b> сети (Squad, Academy, Events), "
        "после этого лидер сможет зарегистрировать команду на турнир."
    )
    sent = blocked = no_team = 0

    # 🧪 ТЕСТОВЫЙ ТУРНИР: массовую рассылку не делаем, пишем только создателю,
    # чтобы можно было полностью прогнать сценарий без спама игрокам.
    if _is_test(tournament):
        creator = tournament["created_by"]
        if creator:
            try:
                composition = db.get_user_league(int(creator))
            except Exception:
                composition = None
            kb = _tournament_reg_kb(tournament_id) if (
                t_key == "showdown" or (composition and int(composition["leader_id"]) == int(creator))
            ) else None
            try:
                await bot.send_message(
                    int(creator),
                    "🧪 <b>ТЕСТОВЫЙ ТУРНИР</b>\n"
                    "(массовая рассылка в ЛС отключена, это сообщение видишь только ты)\n\n"
                    + base_text
                    + ("Ты лидер состава — можешь зарегистрировать команду:"
                       if kb else no_team_text if not composition else
                       "Зарегистрировать команду может только лидер состава."),
                    parse_mode="HTML", reply_markup=kb,
                )
                sent = 1
            except Exception:
                blocked = 1
        return sent, blocked, no_team

    try:
        members = await get_all_members()
    except Exception as e:
        logger.error(f"Рассылка турнира: не удалось получить участников: {e}")
        return 0, 0, 0
    muted = 0
    for m in members:
        user_id = m.get("user_id")
        if not user_id or int(m.get("registered") or 0) != 1:
            continue
        # Участник мог отключить массовые уведомления командой /notify_off
        try:
            if (await get_setting(f"dm_mute_{int(user_id)}")) == "1":
                muted += 1
                continue
        except Exception:
            pass
        try:
            composition = db.get_user_league(int(user_id))
        except Exception:
            composition = None
        if t_key == "showdown":
            text = base_text + "Это соло-режим: участвовать может любой игрок сети."
            kb = _tournament_reg_kb(tournament_id)
        elif not composition:
            text = base_text + no_team_text
            kb = None
            no_team += 1
        elif int(composition["leader_id"]) == int(user_id):
            text = base_text + "Ты лидер состава — можешь зарегистрировать команду прямо сейчас:"
            kb = _tournament_reg_kb(tournament_id)
        else:
            text = base_text + (
                f"Твой состав: <b>{hd.quote(str(composition['name']))}</b>. "
                "Зарегистрировать команду на турнир может только лидер состава."
            )
            kb = None
        try:
            await bot.send_message(user_id, text, parse_mode="HTML", reply_markup=kb)
            sent += 1
        except Exception:
            blocked += 1
        await asyncio.sleep(0.05)  # бережём лимиты Telegram
    if muted:
        logger.info(f"Рассылка турнира {tournament_id}: пропущено {muted} с /notify_off")
    return sent, blocked, no_team


async def _refresh_tournament_posts(bot: Bot, tournament_id: int):
    """Обновляет анонсы (список участников + сетка) после новой регистрации."""
    tournament = get_tournament(tournament_id)
    teams = get_tournament_teams(tournament_id)
    text = _tournament_card(tournament, teams)
    names = [str(t["name"] or "Команда") for t in teams]
    image = render_bracket_image(names, f"{_tour_name(tournament)} · предварительная сетка")
    for post in get_tournament_posts(tournament_id):
        try:
            if image is not None:
                from aiogram.types import BufferedInputFile, InputMediaPhoto
                image.seek(0)
                media = InputMediaPhoto(
                    media=BufferedInputFile(image.read(), filename=f"bracket_{tournament_id}.png"),
                    caption=text[:1024], parse_mode="HTML",
                )
                await bot.edit_message_media(
                    chat_id=post["chat_id"], message_id=post["message_id"], media=media,
                    reply_markup=_tournament_reg_kb(tournament_id),
                )
            else:
                await bot.edit_message_text(
                    chat_id=post["chat_id"], message_id=post["message_id"], text=text,
                    parse_mode="HTML", reply_markup=_tournament_reg_kb(tournament_id),
                )
        except Exception as e:
            logger.debug(f"Анонс турнира {tournament_id} не обновлён: {e}")


@router.callback_query(F.data.startswith("trn:reg:"))
async def tournament_register(call: CallbackQuery, bot: Bot):
    """Регистрация команды лидером состава (в ShowDown — соло-игроком)."""
    tournament_id = int(call.data.rsplit(":", 1)[-1])
    tournament = get_tournament(tournament_id)
    if not tournament or tournament["status"] != "registration":
        await call.answer("Регистрация на турнир закрыта", show_alert=True)
        return
    teams = get_tournament_teams(tournament_id)
    limit = tournament["team_limit"]
    if limit is not None and len(teams) >= int(limit):
        await call.answer("😔 Все места уже заняты", show_alert=True)
        return
    if any(int(t["leader_id"]) == call.from_user.id for t in teams):
        await call.answer("Ты уже зарегистрирован на этот турнир", show_alert=True)
        return

    if tournament["t_type"] == "showdown":
        member = await get_member(call.from_user.id)
        if not member or int(member.get("registered") or 0) != 1:
            await call.answer("Сначала пройди регистрацию в боте (/start)", show_alert=True)
            return
        name = member.get("game_nick") or member.get("username") or f"ID {call.from_user.id}"
        added = add_tournament_team(tournament_id, None, call.from_user.id, name,
                                    member.get("player_tag") or "")
    else:
        composition = db.get_user_league(call.from_user.id)
        if not composition:
            await call.answer(
                "У тебя нет состава. Создай его в разделе «Составы🏆» — "
                "в состав может входить любой участник из трёх кланов.",
                show_alert=True,
            )
            return
        if int(composition["leader_id"]) != call.from_user.id:
            await call.answer("Регистрирует команду только лидер состава", show_alert=True)
            return
        if any(t["league_id"] and int(t["league_id"]) == int(composition["id"]) for t in teams):
            await call.answer("Твоя команда уже в списке", show_alert=True)
            return
        added = add_tournament_team(tournament_id, int(composition["id"]), call.from_user.id,
                                    composition["name"], composition["tag"])

    if not added:
        await call.answer("Не удалось зарегистрировать (возможно, уже в списке)", show_alert=True)
        return
    await call.answer("✅ Ты в списке участников!", show_alert=True)
    try:
        await bot.send_message(
            call.from_user.id,
            f"✅ Регистрация на турнир #{tournament_id} подтверждена.\n"
            f"Начало: <b>{hd.quote(str(tournament['starts_at']))}</b>. "
            f"Сетка обновлена в клановых топиках «Турниры».",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await _refresh_tournament_posts(bot, tournament_id)


@router.callback_query(F.data.in_({"trn:confirm", "trn:confirm_test"}))
async def tournament_confirm(call: CallbackQuery, state: FSMContext, bot: Bot):
    """Этап 3: сохраняем турнир в БД, рассылаем ЛС и публикуем анонсы в топиках.

    Режим «trn:confirm_test» — тестовый турнир: работает идентично обычному
    (анонсы, регистрация, сетка, матчи, результаты), но массовая рассылка в ЛС
    игрокам не выполняется — уведомление получает только создатель.
    """
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    is_test = 1 if call.data == "trn:confirm_test" else 0
    data = await state.get_data()
    if not data.get("t_type") or not data.get("t_datetime"):
        await state.clear()
        await call.answer("Черновик устарел, начни заново", show_alert=True)
        return
    await state.clear()
    await call.answer("Создаю тестовый турнир..." if is_test else "Создаю турнир...")

    tournament_id = create_tournament_row(
        t_type=data.get("t_type"),
        t_format=data.get("t_format"),
        team_limit=data.get("t_limit"),
        starts_at=data.get("t_datetime"),
        created_by=call.from_user.id,
        is_test=is_test,
    )
    await call.message.edit_text(
        f"{'🧪 <b>ТЕСТОВЫЙ РЕЖИМ</b>' if is_test else ''}\n{_tournament_summary(data)}\n\n"
        f"✅ Турнир <b>#{tournament_id}</b> создан. "
        + ("Рассылка в ЛС игрокам отключена. Публикую анонсы..."
           if is_test else "Рассылаю уведомления и публикую анонсы..."),
        parse_mode="HTML",
        reply_markup=None,
    )

    ok, failed = await _publish_tournament_posts(bot, tournament_id)
    sent, blocked, no_team = await _broadcast_tournament_dm(bot, tournament_id)

    report = [
        f"{'🧪 ' if is_test else ''}🏆 <b>Турнир #{tournament_id} опубликован</b>",
        "",
    ]
    if is_test:
        report.append("⚠️ <b>Тестовый режим:</b> массовая рассылка в ЛС отключена, "
                      "уведомление получил только ты. Все остальные функции "
                      "(регистрация, сетка, старт, результаты, матч за 3-е место) работают штатно.")
        report.append("")
    report += [
        f"📨 Рассылка в ЛС: доставлено <b>{sent}</b>, не доставлено <b>{blocked}</b>",
        f"👥 Из них без состава (попросили создать): <b>{no_team}</b>",
    ]
    if ok:
        report.append("📢 Анонс размещён: " + ", ".join(ok))
    if failed:
        report.append("⚠️ Не удалось разместить: " + ", ".join(failed))
    if not getattr(__import__("config"), "TOURNAMENT_TOPICS", None):
        report.append(
            "\nℹ️ В config.py пока нет <code>TOURNAMENT_TOPICS</code> — анонс ушёл в новостные "
            "топики. Пришли ID топиков «Турниры», и я переключу публикацию на них."
        )
    await call.message.answer("\n".join(report), parse_mode="HTML",
                              reply_markup=admin_scrims_keyboard())
    await log_admin_action(
        bot=bot,
        admin_id=call.from_user.id,
        admin_name=call.from_user.username or call.from_user.first_name or "admin",
        action_text=(
            f"🏆 Создал турнир #{tournament_id}: тип {data.get('t_type')}, "
            f"формат {data.get('t_format') or '—'}, "
            f"лимит {_limit_text(data.get('t_limit'), data.get('t_type') or '')}, "
            f"начало {data.get('t_datetime')}. Рассылка: {sent} доставлено."
        ),
    )


# ═══ ЭТАП 4: ТЕКУЩИЕ ТУРНИРЫ, АВТОСТАРТ И УВЕДОМЛЕНИЯ ЛИДЕРАМ ════════════════

TOURNAMENT_STATUS_LABEL = {
    "registration": "📝 Идёт регистрация",
    "running": "🔥 Турнир идёт",
    "finished": "🏁 Завершён",
    "cancelled": "🚫 Отменён",
}


def get_active_tournaments():
    """Активные турниры: идёт регистрация или турнир уже запущен."""
    init_tournament_tables()
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM tournaments WHERE status IN ('registration', 'running') "
        "ORDER BY starts_at, id"
    ).fetchall()
    conn.close()
    return rows


def set_tournament_limit(tournament_id: int, team_limit) -> None:
    init_tournament_tables()
    conn = db.get_connection()
    conn.execute("UPDATE tournaments SET team_limit = ? WHERE id = ?", (team_limit, tournament_id))
    conn.commit()
    conn.close()


def set_tournament_status(tournament_id: int, status: str) -> bool:
    """Переводит турнир в новый статус. False — если статус уже был изменён (гонка)."""
    init_tournament_tables()
    conn = db.get_connection()
    cur = conn.execute(
        "UPDATE tournaments SET status = ? WHERE id = ? AND status != ?",
        (status, tournament_id, status),
    )
    conn.commit()
    changed = cur.rowcount > 0
    conn.close()
    return changed


def get_due_tournaments():
    """Турниры со статусом registration, у которых наступило время старта."""
    init_tournament_tables()
    now = datetime.now()
    due = []
    conn = db.get_connection()
    rows = conn.execute("SELECT * FROM tournaments WHERE status = 'registration'").fetchall()
    conn.close()
    for row in rows:
        try:
            starts = datetime.strptime(str(row["starts_at"]).strip(), "%d.%m.%Y %H:%M")
        except Exception:
            continue
        if starts <= now:
            due.append(row)
    return due


async def _leader_contact(leader_id: int) -> str:
    """Контакт лидера для связи: @username, иначе ник и ID."""
    try:
        member = await get_member(int(leader_id))
    except Exception:
        member = None
    if member:
        uname = member.get("username")
        if uname and str(uname).lower() != "unknown":
            return f"@{uname}"
        nick = member.get("game_nick") or member.get("first_name")
        if nick:
            return f"{hd.quote(str(nick))} (ID {leader_id})"
    return f"ID {leader_id}"


def _schedule_block(tournament_id: int) -> list[str]:
    """Расписание плей-ина/первого круга по данным матчей: только реальные пары,
    плюс явный список команд, проходящих сразу в следующий круг."""
    matches = get_tournament_matches(tournament_id, "main")
    r1 = [m for m in matches if int(m["round_no"]) == 1]
    lines = []
    for i, m in enumerate(r1, 1):
        a = _team_name(m["team_a_id"], "ожидается…")
        b = _team_name(m["team_b_id"], "ожидается…")
        lines.append(f"{i}. <b>{hd.quote(str(a))}</b> 🆚 <b>{hd.quote(str(b))}</b>")
    # Автопроходы: команды второго круга, которых нет в первом
    r1_ids = {int(m[k]) for m in r1 for k in ("team_a_id", "team_b_id") if m[k]}
    autos = []
    for m in matches:
        if int(m["round_no"]) == 2:
            for k in ("team_a_id", "team_b_id"):
                if m[k] and int(m[k]) not in r1_ids:
                    nm = _team_name(m[k])
                    if nm not in autos:
                        autos.append(nm)
    if autos:
        lines.append(f"⏭ Сразу в следующем круге: <b>{hd.quote(', '.join(autos))}</b>")
    if not r1:
        lines.append("Все команды начинают с первого полного круга сетки.")
    return lines


def _final_card(tournament, teams) -> str:
    """Пост с финальной сеткой и расписанием пар."""
    t_key = tournament["t_type"]
    matches = get_tournament_matches(int(tournament["id"]), "main")
    r1_cnt = len([m for m in matches if int(m["round_no"]) == 1])
    autos = max(0, len(teams) - r1_cnt * 2)
    lines = [
        f"{_test_mark(tournament)}🔥 <b>ТУРНИР {hd.quote(_tour_name(tournament))} НАЧАЛСЯ!</b>",
        "",
        f"• Тип: <b>{TOURNAMENT_TYPES.get(t_key, t_key)}</b>",
    ]
    if t_key != "showdown":
        lines.append(f"• Формат матча: <b>{tournament['t_format'] or '—'}</b>")
    lines.append(f"• Участников: <b>{len(teams)}</b>")
    if autos:
        lines.append(f"• Автопроход в следующий круг: <b>{autos}</b>")
    lines.append(f"• Старт: <b>{hd.quote(str(tournament['starts_at']))}</b>")
    lines.append("")
    lines.append("🗓 <b>Расписание первого игрового этапа:</b>")
    lines.extend(_schedule_block(int(tournament["id"])))
    lines.append("")
    lines.append("Лидерам отправлены контакты соперников в ЛС. Удачи!")
    return "\n".join(lines)


async def start_tournament(bot: Bot, tournament_id: int, manual_by: int = 0):
    """Запуск турнира: финальная сетка в топиках + ЛС участникам и лидерам."""
    tournament = get_tournament(tournament_id)
    if not tournament:
        return None
    teams = get_tournament_teams(tournament_id)
    if len(teams) < 2:
        if set_tournament_status(tournament_id, "cancelled"):
            for t in teams:
                try:
                    await bot.send_message(
                        int(t["leader_id"]),
                        f"😔 Турнир {hd.quote(_tour_name(tournament))} отменён: "
                        f"не набралось участников (зарегистрировано {len(teams)}).",
                    )
                except Exception:
                    pass
        return {"cancelled": True, "teams": len(teams), "notified": 0}
    if not set_tournament_status(tournament_id, "running"):
        return None  # кто-то уже запустил турнир

    names = [str(t["name"] or "Команда") for t in teams]
    by_name = {str(t["name"] or "Команда"): t for t in teams}

    # Создаём матчи: плей-ин только из реальных пар + автопроходы во втором круге
    generate_first_round(tournament_id, teams)
    round1_matches = [m for m in get_tournament_matches(tournament_id, "main")
                      if int(m["round_no"]) == 1]
    autos_count = len(teams) - len(round1_matches) * 2

    # 1) Финальная сетка и расписание в топики с анонсом
    text = _final_card(tournament, teams)
    image = render_bracket_image(names, f"{'🧪 [ТЕСТ] ' if _is_test(tournament) else ''}"
                                        f"{_tour_name(tournament)} · финальная сетка",
                                 matches=get_tournament_matches(tournament_id))
    posts = get_tournament_posts(tournament_id)
    for post in posts:
        try:
            if image is not None:
                from aiogram.types import BufferedInputFile, InputMediaPhoto
                image.seek(0)
                media = InputMediaPhoto(
                    media=BufferedInputFile(image.read(), filename=f"bracket_{tournament_id}.png"),
                    caption=text[:1024], parse_mode="HTML",
                )
                await bot.edit_message_media(chat_id=post["chat_id"],
                                             message_id=post["message_id"],
                                             media=media, reply_markup=None)
            else:
                await bot.edit_message_text(chat_id=post["chat_id"],
                                            message_id=post["message_id"],
                                            text=text, parse_mode="HTML", reply_markup=None)
        except Exception as e:
            logger.debug(f"Не удалось обновить пост турнира {tournament_id}: {e}")
            try:
                await bot.send_message(post["chat_id"], text, parse_mode="HTML",
                                       message_thread_id=post["thread_id"])
            except Exception:
                pass
    if not posts:
        for clan_key, target in _tournament_topics(is_test=_is_test(tournament)).items():
            try:
                await bot.send_message(target.get("chat_id"), text, parse_mode="HTML",
                                       message_thread_id=target.get("thread_id"))
            except Exception:
                pass

    # 2) Персональные уведомления лидерам: соперник + контакт для связи
    fmt = tournament["t_format"] or "BO3"
    fmt_label = "Бо3" if str(fmt).upper() == "BO3" else ("Бо5" if str(fmt).upper() == "BO5" else fmt)
    # Автопроходы новой схемы: команды второго круга, которых нет в первом
    r1_ids = {int(m[k]) for m in round1_matches
              for k in ("team_a_id", "team_b_id") if m[k]}
    auto_names = []
    for m in get_tournament_matches(tournament_id, "main"):
        if int(m["round_no"]) == 2:
            for k in ("team_a_id", "team_b_id"):
                if m[k] and int(m[k]) not in r1_ids:
                    row = _team_row(int(m[k]))
                    if row and str(row["name"]) not in auto_names:
                        auto_names.append(str(row["name"]))
    pairs = []
    for m in round1_matches:
        na = _team_name(m["team_a_id"]) if m["team_a_id"] else None
        nb = _team_name(m["team_b_id"]) if m["team_b_id"] else None
        if na and nb:
            pairs.append((na, nb))

    notified = 0
    for a, b in pairs:
        for mine, rival in ((a, b), (b, a)):
            if not mine:
                continue
            team = by_name.get(mine)
            if not team:
                continue
            leader_id = int(team["leader_id"])
            if rival is None:
                msg = (
                    f"🏆 <b>Турнир {hd.quote(_tour_name(tournament))} начался!</b>\n\n"
                    f"🆓 В первом раунде у вас <b>автопроход (BYE)</b> — соперника нет, "
                    f"вы проходите дальше. Ждите пару следующего раунда."
                )
            else:
                rival_team = by_name.get(rival)
                contact = await _leader_contact(int(rival_team["leader_id"])) if rival_team else "—"
                rival_tag = f" [{hd.quote(str(rival_team['tag']))}]" if rival_team and rival_team["tag"] else ""
                if tournament["t_type"] == "showdown":
                    msg = (
                        f"🏆 <b>Турнир {hd.quote(_tour_name(tournament))} начался!</b>\n\n"
                        f"У вас игра против <b>{hd.quote(str(rival))}</b>{rival_tag}.\n"
                        f"Свяжитесь с соперником: {contact}"
                    )
                else:
                    msg = (
                        f"🏆 <b>Турнир {hd.quote(_tour_name(tournament))} начался!</b>\n\n"
                        f"У вас скрим <b>{fmt_label}</b> против команды "
                        f"<b>{hd.quote(str(rival))}</b>{rival_tag}, "
                        f"спишитесь с их лидером: {contact}\n\n"
                        f"После игры отправьте результат командой /result "
                        f"(фото + счёт)."
                    )
            try:
                await bot.send_message(leader_id, msg, parse_mode="HTML")
                notified += 1
            except Exception:
                pass
            await asyncio.sleep(0.05)

    # Лидерам команд с автопроходом: вы стартуете со следующего круга
    for auto_name in auto_names:
        team = by_name.get(auto_name)
        if not team:
            continue
        try:
            await bot.send_message(
                int(team["leader_id"]),
                f"🏆 <b>Турнир {hd.quote(_tour_name(tournament))} начался!</b>\n\n"
                f"⏭ Ваша команда <b>{hd.quote(auto_name)}</b> проходит "
                f"<b>сразу в следующий круг</b> (соперника на первом этапе нет). "
                f"Пара определится после завершения плей-ина — бот пришлёт уведомление.",
                parse_mode="HTML",
            )
            notified += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)

    # 3) Уведомление остальным участникам составов
    for team in teams:
        if not team["league_id"]:
            continue
        try:
            members = db.get_league_members(int(team["league_id"]), occupied_only=True)
        except Exception:
            continue
        for m in members:
            uid = m["user_id"]
            if not uid or int(uid) == int(team["leader_id"]):
                continue
            try:
                await bot.send_message(
                    int(uid),
                    f"🏆 <b>Турнир {hd.quote(_tour_name(tournament))} начался!</b>\n\n"
                    f"Ваша команда <b>{hd.quote(str(team['name'] or 'Команда'))}</b> участвует. "
                    f"Детали пары уточните у лидера состава.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            await asyncio.sleep(0.05)

    logger.info(f"Турнир {tournament_id} запущен: {len(teams)} участников, {notified} уведомлений")
    return {"cancelled": False, "teams": len(teams), "notified": notified,
            "byes": autos_count}


# ─── ЭТАП 5: МАТЧИ, РЕЗУЛЬТАТЫ, ПРОДВИЖЕНИЕ ПО СЕТКЕ ─────────────────────────

def _m_get(match_id: int):
    init_tournament_tables()
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM tournament_matches WHERE id = ?", (match_id,)).fetchone()
    conn.close()
    return row


def get_tournament_matches(tournament_id: int, bracket: str = None):
    init_tournament_tables()
    conn = db.get_connection()
    if bracket:
        rows = conn.execute(
            "SELECT * FROM tournament_matches WHERE tournament_id = ? AND bracket = ? "
            "ORDER BY round_no, slot_no", (tournament_id, bracket)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tournament_matches WHERE tournament_id = ? "
            "ORDER BY bracket DESC, round_no, slot_no", (tournament_id,)).fetchall()
    conn.close()
    return rows


def _team_row(team_id):
    if not team_id:
        return None
    init_tournament_tables()
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM tournament_teams WHERE id = ?", (int(team_id),)).fetchone()
    conn.close()
    return row


def _team_name(team_id, default: str = "—") -> str:
    row = _team_row(team_id)
    return str(row["name"] or "Команда") if row else default


def create_match(tournament_id, round_no, slot_no, team_a_id, team_b_id,
                 bracket="main", partial=False) -> int:
    """Создаёт матч. BYE (нет соперника) закрывается сразу автопроходом.

    partial=True — матч может быть не укомплектован (сторона ждёт победителя
    плей-ина), его НЕ помечаем как bye.
    """
    init_tournament_tables()
    conn = db.get_connection()
    status = "pending"
    winner = None
    if not partial:
        if team_a_id and not team_b_id:
            status, winner = "bye", team_a_id
        elif team_b_id and not team_a_id:
            status, winner = "bye", team_b_id
    cur = conn.execute(
        "INSERT OR IGNORE INTO tournament_matches "
        "(tournament_id, round_no, slot_no, bracket, team_a_id, team_b_id, winner_team_id, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (tournament_id, round_no, slot_no, bracket, team_a_id, team_b_id, winner, status),
    )
    conn.commit()
    mid = int(cur.lastrowid or 0)
    conn.close()
    return mid


def _bracket_layout(names: list[str]):
    """Раскладка сетки без фейковых матчей с BYE.

    Возвращает список раундов; каждый раунд — список пар (a, b), где a/b —
    название команды или None (место под победителя предыдущего раунда).

    Идея: если команд N < 2^k, то первый круг — это плей-ин только из реальных
    пар (2·(N − 2^(k−1)) команд), а остальные (2^k − N) команд проходят сразу
    в следующий круг. Например, при 10 командах: плей-ин — 2 матча (4 команды),
    а 6 команд стартуют сразу с четвертьфинала.
    """
    n = len(names)
    if n < 2:
        return [], n
    size = 1
    while size < n:
        size *= 2
    total_rounds = size.bit_length() - 1
    playin = n - size // 2          # сколько матчей в первом круге
    auto = n - playin * 2           # команды, проходящие сразу (= size - n)
    autos = list(names[:auto])
    play = list(names[auto:])

    rounds = []
    if playin > 0:
        rounds.append([(play[2 * j], play[2 * j + 1]) for j in range(playin)])
        # второй круг: победители плей-ина (None) + автопроходы
        slots = []
        for j in range(playin):
            slots.append(None)
            slots.append(autos[j] if j < len(autos) else None)
        slots.extend(autos[playin:])
        rounds.append([(slots[2 * i], slots[2 * i + 1]) for i in range(len(slots) // 2)])
        rest = len(slots) // 4
    else:
        rounds.append([(names[2 * j], names[2 * j + 1])
                       for j in range(size // 2)])
        rest = size // 4
    while rest >= 1:
        rounds.append([(None, None)] * rest)
        rest //= 2
    return rounds[:total_rounds], auto


def generate_first_round(tournament_id: int, teams) -> None:
    """Создаёт матчи по новой схеме: плей-ин только из реальных пар,
    автопроходы сразу оказываются в следующем круге (без фейковых BYE-матчей).

    Слоты плей-ина нумеруются нечётными числами (1, 3, 5...), чтобы правило
    продвижения _advance_winner (slot→(slot+1)//2, чётность → сторона) работало
    без изменений: победитель слота 2j-1 встаёт в сторону A матча j след. круга.
    """
    names = [str(t["name"] or "Команда") for t in teams]
    by_name = {str(t["name"] or "Команда"): t for t in teams}
    layout, auto_n = _bracket_layout(names)
    if not layout:
        return
    playin = len(layout[0]) if auto_n else 0

    # Круг 1: только реальные пары (или полный первый круг для степени двойки)
    if playin:
        for j, (a, b) in enumerate(layout[0], 1):
            create_match(tournament_id, 1, 2 * j - 1,
                         by_name[a]["id"], by_name[b]["id"])
    else:
        for slot, (a, b) in enumerate(layout[0], 1):
            create_match(tournament_id, 1, slot, by_name[a]["id"], by_name[b]["id"])

    # Круг 2: предсоздаём матчи: сторона A ждёт победителя плей-ина, сторона B —
    # команда-автопроход (уже известна). Матчи без плей-ин-потомка полные сразу.
    if playin and len(layout) > 1:
        for slot, (a, b) in enumerate(layout[1], 1):
            team_a = by_name[a]["id"] if a else None
            team_b = by_name[b]["id"] if b else None
            create_match(tournament_id, 2, slot, team_a, team_b, partial=True)


def _promote_byes(tournament_id: int) -> None:
    """Продвигает автопроходы, пока появляются полностью укомплектованные пары."""
    for _ in range(12):
        byes = [m for m in get_tournament_matches(tournament_id, "main")
                if m["status"] == "bye" and m["winner_team_id"]]
        if not byes:
            break
        for m in byes:
            _advance_winner(tournament_id, m, int(m["winner_team_id"]), None, silent=True)
            # Автопроход всегда закрываем, чтобы не обрабатывать его повторно
            conn = db.get_connection()
            conn.execute("UPDATE tournament_matches SET status='done', "
                         "finished_at=CURRENT_TIMESTAMP WHERE id=?", (int(m["id"]),))
            conn.commit()
            conn.close()


def _next_slot(round_no: int, slot_no: int) -> tuple[int, int]:
    return round_no + 1, (slot_no + 1) // 2


def _advance_winner(tournament_id, match, winner_id, loser_id, silent=False) -> bool:
    """Записывает победителя в матч следующего раунда. True — если что-то изменилось."""
    init_tournament_tables()
    conn = db.get_connection()
    if match["status"] != "bye":
        conn.execute(
            "UPDATE tournament_matches SET status='done', winner_team_id=?, loser_team_id=?, "
            "finished_at=CURRENT_TIMESTAMP WHERE id=?",
            (winner_id, loser_id, int(match["id"])),
        )
    nr, ns = _next_slot(int(match["round_no"]), int(match["slot_no"]))
    total = conn.execute(
        "SELECT COUNT(*) c FROM tournament_matches WHERE tournament_id=? AND bracket='main' "
        "AND round_no=?", (tournament_id, int(match["round_no"]))).fetchone()["c"]
    changed = False
    if total > 1:  # есть следующий раунд
        nxt = conn.execute(
            "SELECT * FROM tournament_matches WHERE tournament_id=? AND bracket='main' "
            "AND round_no=? AND slot_no=?", (tournament_id, nr, ns)).fetchone()
        side = "team_a_id" if int(match["slot_no"]) % 2 == 1 else "team_b_id"
        if nxt is None:
            conn.execute(
                f"INSERT INTO tournament_matches (tournament_id, round_no, slot_no, bracket, {side}) "
                f"VALUES (?, ?, ?, 'main', ?)", (tournament_id, nr, ns, winner_id))
            changed = True
        elif not nxt[side]:
            conn.execute(f"UPDATE tournament_matches SET {side}=? WHERE id=?",
                         (winner_id, int(nxt["id"])))
            changed = True
    conn.commit()
    conn.close()
    return changed


def _ensure_third_place(tournament_id: int) -> int:
    """Создаёт матч за 3-е место, когда оба полуфинала сыграны."""
    mains = get_tournament_matches(tournament_id, "main")
    if not mains:
        return 0
    last_round = max(int(m["round_no"]) for m in mains)
    semis = [m for m in mains if int(m["round_no"]) == last_round - 1] if last_round >= 2 else []
    if len(semis) != 2 or any(m["status"] not in ("done", "bye") or not m["loser_team_id"]
                              for m in semis):
        return 0
    existing = [m for m in get_tournament_matches(tournament_id, "third")]
    if existing:
        return int(existing[0]["id"])
    return create_match(tournament_id, last_round, 1,
                        int(semis[0]["loser_team_id"]), int(semis[1]["loser_team_id"]),
                        bracket="third")


def _tournament_is_over(tournament_id: int) -> bool:
    mains = get_tournament_matches(tournament_id, "main")
    thirds = get_tournament_matches(tournament_id, "third")
    if not mains:
        return False
    last_round = max(int(m["round_no"]) for m in mains)
    final = [m for m in mains if int(m["round_no"]) == last_round]
    if len(final) != 1 or final[0]["status"] not in ("done", "bye"):
        return False
    if thirds and thirds[0]["status"] not in ("done", "bye"):
        return False
    return True


def _podium(tournament_id: int) -> str:
    mains = get_tournament_matches(tournament_id, "main")
    thirds = get_tournament_matches(tournament_id, "third")
    last_round = max(int(m["round_no"]) for m in mains)
    final = [m for m in mains if int(m["round_no"]) == last_round][0]
    lines = [
        f"🥇 1 место: <b>{hd.quote(_team_name(final['winner_team_id']))}</b>",
        f"🥈 2 место: <b>{hd.quote(_team_name(final['loser_team_id']))}</b>",
    ]
    if thirds and thirds[0]["winner_team_id"]:
        lines.append(f"🥉 3 место: <b>{hd.quote(_team_name(thirds[0]['winner_team_id']))}</b>")
    return "\n".join(lines)


def _bracket_board(tournament_id: int) -> str:
    """Текущее состояние сетки: все раунды, включая матч за 3-е место."""
    lines = []
    mains = get_tournament_matches(tournament_id, "main")
    if mains:
        last_round = max(int(m["round_no"]) for m in mains)
        for rnd in sorted({int(m["round_no"]) for m in mains}):
            in_round = [m for m in mains if int(m["round_no"]) == rnd]
            if rnd == last_round and len(in_round) == 1:
                title = "🏁 Финал"
            elif len(in_round) == 2:
                title = "🥊 Полуфиналы"
            else:
                title = f"Раунд {rnd}"
            lines.append(f"\n<b>{title}:</b>")
            for m in in_round:
                a = _team_name(m["team_a_id"], "ожидается")
                b = _team_name(m["team_b_id"], "ожидается")
                if m["status"] in ("done", "bye") and m["winner_team_id"]:
                    win = _team_name(m["winner_team_id"])
                    score = f" ({hd.quote(str(m['score_text']))})" if m["score_text"] else ""
                    lines.append(f"• {hd.quote(a)} 🆚 {hd.quote(b)} → ✅ <b>{hd.quote(win)}</b>{score}")
                else:
                    lines.append(f"• {hd.quote(a)} 🆚 {hd.quote(b)}")
    thirds = get_tournament_matches(tournament_id, "third")
    if thirds:
        m = thirds[0]
        a, b = _team_name(m["team_a_id"], "ожидается"), _team_name(m["team_b_id"], "ожидается")
        lines.append("\n<b>🥉 Матч за 3-е место:</b>")
        if m["winner_team_id"]:
            lines.append(f"• {hd.quote(a)} 🆚 {hd.quote(b)} → ✅ <b>{hd.quote(_team_name(m['winner_team_id']))}</b>")
        else:
            lines.append(f"• {hd.quote(a)} 🆚 {hd.quote(b)}")
    return "\n".join(lines)


async def _repost_bracket(bot: Bot, tournament_id: int):
    """Обновляет пост сетки. Если редактирование невозможно (лимит 2 суток) —
    удаляет старый пост и публикует новый."""
    tournament = get_tournament(tournament_id)
    teams = get_tournament_teams(tournament_id)
    names = [str(t["name"] or "Команда") for t in teams]
    finished = _tournament_is_over(tournament_id)
    mark = _test_mark(tournament)
    pub = hd.quote(_tour_name(tournament))
    head = (f"{mark}🏁 <b>ТУРНИР {pub} ЗАВЕРШЁН</b>\n\n{_podium(tournament_id)}"
            if finished else f"{mark}🔥 <b>ТУРНИР {pub}</b> — текущая сетка")
    text = f"{head}\n{_bracket_board(tournament_id)}"
    image = render_bracket_image(names, f"{'🧪 [ТЕСТ] ' if _is_test(tournament) else ''}"
                                        f"{_tour_name(tournament)} · сетка",
                                 matches=get_tournament_matches(tournament_id))

    for post in get_tournament_posts(tournament_id):
        edited = False
        try:
            if image is not None:
                from aiogram.types import BufferedInputFile, InputMediaPhoto
                image.seek(0)
                media = InputMediaPhoto(
                    media=BufferedInputFile(image.read(), filename=f"bracket_{tournament_id}.png"),
                    caption=text[:1024], parse_mode="HTML")
                await bot.edit_message_media(chat_id=post["chat_id"],
                                             message_id=post["message_id"], media=media)
            else:
                await bot.edit_message_text(chat_id=post["chat_id"], message_id=post["message_id"],
                                            text=text, parse_mode="HTML")
            edited = True
        except Exception as e:
            logger.info(f"Пост турнира {tournament_id} не отредактирован ({e}), пересоздаю")

        if edited:
            continue
        # Лимит редактирования истёк (2 суток) — удаляем старый пост и шлём новый
        try:
            await bot.delete_message(chat_id=post["chat_id"], message_id=post["message_id"])
        except Exception:
            pass
        try:
            if image is not None:
                from aiogram.types import BufferedInputFile
                image.seek(0)
                photo = BufferedInputFile(image.read(), filename=f"bracket_{tournament_id}.png")
                msg = await bot.send_photo(chat_id=post["chat_id"],
                                           message_thread_id=post["thread_id"],
                                           photo=photo, caption=text[:1024], parse_mode="HTML")
            else:
                msg = await bot.send_message(chat_id=post["chat_id"],
                                             message_thread_id=post["thread_id"],
                                             text=text, parse_mode="HTML")
            conn = db.get_connection()
            conn.execute("UPDATE tournament_posts SET message_id = ? WHERE id = ?",
                         (msg.message_id, int(post["id"])))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Не удалось пересоздать пост сетки турнира {tournament_id}: {e}")


async def _notify_next_pairs(bot: Bot, tournament_id: int):
    """Старт новых пар: рассылает лидерам пары, у которых появились ОБЕ команды.

    Это и есть «автоматический старт следующего круга»: как только у пары
    определились обе команды, капитаны сразу получают уведомление с контактом
    соперника. Пара не ждёт чужих матчей — уведомляем по факту готовности.
    Каждая пара уведомляется один раз (флаг notified в tournament_matches).
    """
    tournament = get_tournament(tournament_id)
    fmt = str(tournament["t_format"] or "BO3").upper()
    fmt_label = "Бо3" if fmt == "BO3" else ("Бо5" if fmt == "BO5" else fmt)
    for m in get_tournament_matches(tournament_id):
        if m["status"] != "pending" or not m["team_a_id"] or not m["team_b_id"]:
            continue
        try:
            if int(m["notified"] or 0):
                continue
        except Exception:
            pass
        stage = "🥉 матч за 3-е место" if m["bracket"] == "third" \
            else f"круг {m['round_no']}"
        for mine, rival in ((m["team_a_id"], m["team_b_id"]), (m["team_b_id"], m["team_a_id"])):
            my_team, rival_team = _team_row(mine), _team_row(rival)
            if not my_team or not rival_team:
                continue
            contact = await _leader_contact(int(rival_team["leader_id"]))
            try:
                    await bot.send_message(
                    int(my_team["leader_id"]),
                    f"⚔️ <b>Турнир {hd.quote(_tour_name(tournament))}</b> — {stage}!\n\n"
                    f"У вас скрим <b>{fmt_label}</b> против команды "
                    f"<b>{hd.quote(str(rival_team['name']))}</b>, "
                    f"спишитесь с их лидером: {contact}\n\n"
                    f"После игры отправьте результат командой /result",
                    parse_mode="HTML")
            except Exception:
                pass
            await asyncio.sleep(0.05)
        conn = db.get_connection()
        conn.execute("UPDATE tournament_matches SET notified = 1 WHERE id = ?",
                     (int(m["id"]),))
        conn.commit()
        conn.close()


def _find_user_pending_match(user_id: int):
    """Ищет незавершённый матч, где пользователь — лидер одной из команд."""
    init_tournament_tables()
    conn = db.get_connection()
    row = conn.execute("""
        SELECT m.*, t.t_format, t.id AS tid FROM tournament_matches m
        JOIN tournaments t ON t.id = m.tournament_id
        LEFT JOIN tournament_teams ta ON ta.id = m.team_a_id
        LEFT JOIN tournament_teams tb ON tb.id = m.team_b_id
        WHERE t.status = 'running' AND m.status = 'pending'
          AND m.team_a_id IS NOT NULL AND m.team_b_id IS NOT NULL
          AND (ta.leader_id = ? OR tb.leader_id = ?)
        ORDER BY m.round_no, m.slot_no LIMIT 1
    """, (user_id, user_id)).fetchone()
    conn.close()
    return row


# ─── Сдача результата лидером ───────────────────────────────────────────────

class TournamentResultStates(StatesGroup):
    waiting_result = State()


@router.message(F.text.in_({"/result", "/результат"}))
async def tournament_result_start(message: Message, state: FSMContext):
    """Лидер отправляет результат матча: фото + текст."""
    match = _find_user_pending_match(message.from_user.id)
    if not match:
        await message.answer("📭 Сейчас у тебя нет активных турнирных матчей, "
                             "по которым нужно сдать результат.")
        return
    a, b = _team_name(match["team_a_id"]), _team_name(match["team_b_id"])
    await state.set_state(TournamentResultStates.waiting_result)
    await state.update_data(result_match_id=int(match["id"]))
    await message.answer(
        f"📸 <b>Результат матча</b>\n\n"
        f"Матч: <b>{hd.quote(a)}</b> 🆚 <b>{hd.quote(b)}</b>\n\n"
        f"Отправь <b>фотографию</b> с результатом игры и подписью к ней "
        f"(например: «2:1, победили мы»).\n"
        f"Можно прислать несколько фото — затем нажми «Готово».\n"
        f"Для отмены отправь /cancel.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Готово, отправить админам",
                                 callback_data="trnres:send")]]),
    )


@router.message(TournamentResultStates.waiting_result, F.text.startswith("/"))
async def tournament_result_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отправка результата отменена.")


@router.message(TournamentResultStates.waiting_result, F.photo)
async def tournament_result_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = list(data.get("result_photos") or [])
    photos.append(message.photo[-1].file_id)
    note = data.get("result_note") or ""
    if message.caption:
        note = (note + "\n" + message.caption).strip()
    await state.update_data(result_photos=photos, result_note=note)
    await message.answer(
        f"📸 Скриншот №{len(photos)} принят. Пришли ещё или нажми «Готово».",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Готово, отправить админам",
                                 callback_data="trnres:send")]]),
    )


@router.message(TournamentResultStates.waiting_result, F.text)
async def tournament_result_text(message: Message, state: FSMContext):
    data = await state.get_data()
    note = ((data.get("result_note") or "") + "\n" + message.text).strip()
    await state.update_data(result_note=note)
    await message.answer(
        "📝 Комментарий сохранён. Пришли фото результата или нажми «Готово».",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Готово, отправить админам",
                                 callback_data="trnres:send")]]),
    )


@router.callback_query(F.data == "trnres:send")
async def tournament_result_send(call: CallbackQuery, state: FSMContext, bot: Bot):
    """Отправляет заявку с результатом в чат администрации."""
    import json
    data = await state.get_data()
    match_id = int(data.get("result_match_id") or 0)
    match = _m_get(match_id)
    if not match or match["status"] != "pending":
        await state.clear()
        await call.answer("Матч уже закрыт", show_alert=True)
        return
    photos = list(data.get("result_photos") or [])
    note = (data.get("result_note") or "").strip()
    if not photos and not note:
        await call.answer("Сначала пришли фото или текст результата", show_alert=True)
        return
    await state.clear()

    team_a, team_b = _team_row(match["team_a_id"]), _team_row(match["team_b_id"])
    reporter_team = team_a if team_a and int(team_a["leader_id"]) == call.from_user.id else team_b

    conn = db.get_connection()
    cur = conn.execute(
        "INSERT INTO tournament_results (match_id, tournament_id, reporter_id, reporter_team_id, "
        "text_note, photos) VALUES (?, ?, ?, ?, ?, ?)",
        (match_id, int(match["tournament_id"]), call.from_user.id,
         int(reporter_team["id"]) if reporter_team else None, note, json.dumps(photos)),
    )
    conn.commit()
    result_id = int(cur.lastrowid)
    conn.close()

    name_a = str(team_a["name"] if team_a else "A")
    name_b = str(team_b["name"] if team_b else "B")
    stage = "🥉 матч за 3-е место" if match["bracket"] == "third" else f"раунд {match['round_no']}"
    caption = (
        f"📨 <b>Результат турнирного матча</b>\n\n"
        f"Турнир #{match['tournament_id']} · {stage}\n"
        f"Матч: <b>{hd.quote(name_a)}</b> 🆚 <b>{hd.quote(name_b)}</b>\n"
        f"Отправил: {await _leader_contact(call.from_user.id)} "
        f"(команда {hd.quote(str(reporter_team['name'] if reporter_team else '—'))})\n\n"
        f"📝 Комментарий: {hd.quote(note) if note else '—'}\n\n"
        f"Кто победил?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Да, победил {name_a[:20]}",
                              callback_data=f"trnres:win:{result_id}:{match['team_a_id']}")],
        [InlineKeyboardButton(text=f"❌ Нет, победил {name_b[:20]}",
                              callback_data=f"trnres:win:{result_id}:{match['team_b_id']}")],
        [InlineKeyboardButton(text="🚫 Отклонить заявку", callback_data=f"trnres:rej:{result_id}")],
    ])
    import config as _cfg
    admin_chat = _cfg.ADMIN_CHAT_ID
    try:
        if photos:
            from aiogram.types import InputMediaPhoto
            if len(photos) > 1:
                await bot.send_media_group(admin_chat,
                                           [InputMediaPhoto(media=p) for p in photos[:10]])
                await bot.send_message(admin_chat, caption, parse_mode="HTML", reply_markup=kb)
            else:
                await bot.send_photo(admin_chat, photos[0], caption=caption[:1024],
                                     parse_mode="HTML", reply_markup=kb)
        else:
            await bot.send_message(admin_chat, caption, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.error(f"Не удалось отправить результат {result_id} админам: {e}")
        await call.message.answer("❌ Не удалось связаться с администрацией. Сообщи админу вручную.")
        await call.answer()
        return
    await call.message.edit_text(
        "✅ Результат отправлен администрации на подтверждение. "
        "Как только админ подтвердит — сетка обновится.", reply_markup=None)
    await call.answer("Отправлено")


# ─── Модерация результата администратором ───────────────────────────────────

@router.callback_query(F.data.startswith("trnres:win:"))
async def tournament_result_confirm(call: CallbackQuery, bot: Bot):
    """Админ подтверждает победителя → продвижение по сетке и обновление постов."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    _, _, result_id, winner_id = call.data.split(":")
    result_id, winner_id = int(result_id), int(winner_id)

    conn = db.get_connection()
    res = conn.execute("SELECT * FROM tournament_results WHERE id = ?", (result_id,)).fetchone()
    conn.close()
    if not res or res["status"] != "pending":
        await call.answer("Заявка уже обработана", show_alert=True)
        return
    match = _m_get(int(res["match_id"]))
    if not match or match["status"] != "pending":
        await call.answer("Матч уже закрыт", show_alert=True)
        return

    loser_id = int(match["team_b_id"]) if winner_id == int(match["team_a_id"]) \
        else int(match["team_a_id"])
    tournament_id = int(match["tournament_id"])

    conn = db.get_connection()
    conn.execute("UPDATE tournament_results SET status='accepted', decided_by=?, "
                 "decided_at=CURRENT_TIMESTAMP WHERE id=?", (call.from_user.id, result_id))
    conn.execute("UPDATE tournament_matches SET score_text=? WHERE id=?",
                 (str(res["text_note"] or "")[:60], int(match["id"])))
    conn.commit()
    conn.close()

    admin_name = call.from_user.username or call.from_user.first_name or "admin"
    await _apply_confirmed_result(bot, tournament_id, match, winner_id, loser_id,
                                  str(res["text_note"] or "")[:60], admin_name,
                                  admin_id=call.from_user.id)
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(
        f"✅ Результат подтверждён: победила <b>{hd.quote(_team_name(winner_id))}</b> "
        f"(админ {hd.quote(admin_name)}).",
        parse_mode="HTML")
    await call.answer("Подтверждено")


async def _apply_confirmed_result(bot: Bot, tournament_id: int, match,
                                  winner_id: int, loser_id: int,
                                  score_text: str, admin_name: str,
                                  admin_id: int = 0) -> None:
    """Общий пайплайн «результат матча подтверждён»: продвижение по сетке,
    автостарт укомплектованных пар следующего круга, матч за 3-е место,
    обновление постов, финал турнира. Работает и для заявок от лидеров,
    и для ручного внесения победителя администратором."""
    conn = db.get_connection()
    conn.execute("UPDATE tournament_matches SET score_text=? WHERE id=?",
                 (score_text, int(match["id"])))
    conn.commit()
    conn.close()

    _advance_winner(tournament_id, match, winner_id, loser_id)
    _promote_byes(tournament_id)
    third_id = _ensure_third_place(tournament_id)

    win_name = _team_name(winner_id)
    lose_name = _team_name(loser_id)

    # Уведомляем обе команды (публичное имя турнира — его дата)
    tour_pub = hd.quote(_tour_name(get_tournament(tournament_id) or
                                   {"id": tournament_id, "starts_at": ""}))
    for team_id, text in (
        (winner_id, f"🎉 Победа в турнире {tour_pub} засчитана! Вы проходите дальше."),
        (loser_id, f"😔 В турнире {tour_pub} засчитано поражение против "
                   f"«{hd.quote(win_name)}»."),
    ):
        row = _team_row(team_id)
        if row:
            try:
                await bot.send_message(int(row["leader_id"]), text, parse_mode="HTML")
            except Exception:
                pass

    if third_id:
        logger.info(f"Турнир {tournament_id}: создан матч за 3-е место #{third_id}")
    await _repost_bracket(bot, tournament_id)
    await _notify_next_pairs(bot, tournament_id)

    if _tournament_is_over(tournament_id):
        set_tournament_status(tournament_id, "finished")
        podium = _podium(tournament_id)
        for t in get_tournament_teams(tournament_id):
            try:
                await bot.send_message(int(t["leader_id"]),
                                       f"🏁 <b>Турнир {hd.quote(_tour_name(tournament))} завершён!</b>\n\n{podium}",
                                       parse_mode="HTML")
            except Exception:
                pass
            await asyncio.sleep(0.05)
        await _repost_bracket(bot, tournament_id)

    await log_admin_action(
        bot=bot, admin_id=admin_id,
        admin_name=admin_name,
        action_text=(f"🏆 Зафиксирован результат матча турнира #{tournament_id}: "
                     f"победа «{win_name}» над «{lose_name}»."),
    )


@router.callback_query(F.data.startswith("trnres:rej:"))
async def tournament_result_reject(call: CallbackQuery, bot: Bot):
    """Админ отклоняет заявку с результатом."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    result_id = int(call.data.rsplit(":", 1)[-1])
    conn = db.get_connection()
    res = conn.execute("SELECT * FROM tournament_results WHERE id = ?", (result_id,)).fetchone()
    if not res or res["status"] != "pending":
        conn.close()
        await call.answer("Заявка уже обработана", show_alert=True)
        return
    conn.execute("UPDATE tournament_results SET status='rejected', decided_by=?, "
                 "decided_at=CURRENT_TIMESTAMP WHERE id=?", (call.from_user.id, result_id))
    conn.commit()
    conn.close()
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("🚫 Заявка с результатом отклонена.")
    await call.answer("Отклонено")
    try:
        await bot.send_message(
            int(res["reporter_id"]),
            "🚫 Ваша заявка с результатом турнирного матча отклонена администрацией. "
            "Проверьте скриншоты и отправьте результат заново через /result.")
    except Exception:
        pass


async def tournament_scheduler_task(bot: Bot) -> None:
    """Фоновая задача: раз в минуту стартует турниры, у которых наступило время."""
    await asyncio.sleep(45)
    while True:
        try:
            for row in get_due_tournaments():
                try:
                    await start_tournament(bot, int(row["id"]))
                except Exception as e:
                    logger.error(f"Ошибка автостарта турнира {row['id']}: {e}")
        except Exception as e:
            logger.error(f"tournament scheduler error: {e}")
        await asyncio.sleep(60)


@router.startup()
async def _launch_tournament_scheduler(bot: Bot = None, **kwargs):
    """Поднимает планировщик турниров вместе с ботом (без правок main.py)."""
    bot = bot or kwargs.get("bot")
    if bot is None:
        return
    init_tournament_tables()
    asyncio.create_task(tournament_scheduler_task(bot))
    logger.info("Планировщик автостарта турниров запущен")


# ─── САНИТАР СОСТАВОВ: один игрок — один состав ──────────────────────────────
# Лечим инвариант, а не точку входа: если кто-то оказался в двух составах,
# оставляем его там, куда он зашёл ПОЗЖЕ, старое членство снимаем автоматически.
# Лидеров своих составов автоматикой НЕ трогаем — о них только предупреждаем.

def _find_duplicate_memberships():
    """Возвращает список членств для снятия (игрок числится в 2+ составах)."""
    conn = db.get_connection()
    rows = conn.execute("""
        SELECT m.id AS row_id, m.user_id, m.league_id, m.slot_index, m.joined_at,
               l.name AS league_name
        FROM league_members m JOIN leagues l ON l.id = m.league_id
        WHERE m.user_id IS NOT NULL
        ORDER BY m.user_id, m.joined_at, m.id
    """).fetchall()
    conn.close()
    by_user = {}
    for r in rows:
        by_user.setdefault(int(r["user_id"]), []).append(r)
    purge, conflicts = [], []
    for uid, lst in by_user.items():
        if len(lst) < 2:
            continue
        keep = max(lst, key=lambda r: (str(r["joined_at"] or ""), int(r["row_id"])))
        for r in lst:
            if int(r["row_id"]) == int(keep["row_id"]):
                continue
            if int(r["slot_index"]) == 1:
                # лидера его же состава не трогаем — предупреждаем админов
                conflicts.append((uid, r["league_name"], keep["league_name"]))
                continue
            purge.append((int(r["row_id"]), uid, int(r["league_id"]),
                          str(r["league_name"]), str(keep["league_name"])))
    return purge, conflicts


def _apply_membership_purge(purge):
    if not purge:
        return set()
    affected = set()
    conn = db.get_connection()
    for row_id, uid, league_id, old_name, new_name in purge:
        conn.execute("DELETE FROM league_members WHERE id = ?", (row_id,))
        conn.execute("DELETE FROM league_deputies WHERE league_id = ? AND user_id = ?",
                     (league_id, uid))
        affected.add(league_id)
    conn.commit()
    conn.close()
    return affected


async def membership_janitor_task(bot: Bot) -> None:
    """Каждые 15 минут вычищает сдвоенные членства в составах."""
    await asyncio.sleep(90)
    while True:
        try:
            purge, conflicts = _find_duplicate_memberships()
            if purge:
                affected = _apply_membership_purge(purge)
                # после снятия проверяем минимум 3 участников у пострадавших составов
                for league_id in affected:
                    try:
                        from league.league_db import enforce_verification_requirement
                        enforce_verification_requirement(league_id)
                    except Exception:
                        pass
                for row_id, uid, old_name, new_name in purge:
                    logger.info(
                        f"Санитар составов: {uid} убран из «{old_name}», оставлен в «{new_name}»")
                    try:
                        await bot.send_message(
                            uid,
                            f"ℹ️ Ты числился сразу в двух составах. Оставлен в "
                            f"«{hd.quote(new_name)}» — из «{hd.quote(old_name)}» "
                            f"тебя убрали автоматически (правило: один игрок — один состав).",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
                    await asyncio.sleep(0.05)
                await log_admin_action(
                    bot=bot, admin_id=0, admin_name="system",
                    action_text=(f"🧹 Санитар составов: вычищено {len(purge)} двойных "
                                 f"членств (игрок оставлен в более позднем составе)."),
                )
            for uid, old_name, new_name in conflicts:
                await log_admin_action(
                    bot=bot, admin_id=0, admin_name="system",
                    action_text=(f"⚠️ Игрок {uid} лидер «{old_name}» и при этом числится в "
                                 f"«{new_name}». Автоматикой лидера не снял — решите вручную."),
                )
        except Exception as e:
            logger.error(f"membership janitor error: {e}")
        await asyncio.sleep(900)


@router.startup()
async def _launch_membership_janitor(bot: Bot = None, **kwargs):
    bot = bot or kwargs.get("bot")
    if bot is None:
        return
    asyncio.create_task(membership_janitor_task(bot))
    logger.info("Санитар составов (один игрок — один состав) запущен")


# ─── UI: «Текущие турниры» ───────────────────────────────────────────────────

class TournamentAdminStates(StatesGroup):
    waiting_new_limit = State()


def _tournaments_list_kb(rows) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for row in rows:
        teams = get_tournament_teams(int(row["id"]))
        mark = "🧪 " if _is_test(row) else ""
        builder.button(
            text=f"{mark}#{row['id']} {TOURNAMENT_TYPES.get(row['t_type'], row['t_type'])} · "
                 f"{len(teams)} уч. · {row['starts_at']}",
            callback_data=f"trnadm:open:{row['id']}",
        )
    builder.button(text="🔄 Обновить", callback_data="trnadm:list")
    builder.adjust(1)
    return builder.as_markup()


def _tournament_admin_card(tournament, teams) -> str:
    t_key = tournament["t_type"]
    lines = [
        f"{_test_mark(tournament)}🏆 <b>Турнир #{tournament['id']}</b>",
        "",
        f"• Статус: <b>{TOURNAMENT_STATUS_LABEL.get(tournament['status'], tournament['status'])}</b>",
        f"• Тип: <b>{TOURNAMENT_TYPES.get(t_key, t_key)}</b>",
    ]
    if t_key != "showdown":
        lines.append(f"• Формат матча: <b>{tournament['t_format'] or '—'}</b>")
    lines.append(f"• {_limit_label(t_key)}: <b>{_limit_text(tournament['team_limit'], t_key)}</b>")
    lines.append(f"• Старт: <b>{hd.quote(str(tournament['starts_at']))}</b>")
    lines.append("")
    unit = _limit_unit(t_key)
    lines.append(f"📋 <b>Поданные составы ({len(teams)} {unit}):</b>")
    if teams:
        for i, t in enumerate(teams, 1):
            tag = f" [{hd.quote(str(t['tag']))}]" if t["tag"] else ""
            when = str(t["registered_at"] or "")[:16]
            lines.append(
                f"{i}. {hd.quote(str(t['name'] or 'Без названия'))}{tag} "
                f"— лидер ID {t['leader_id']} · {hd.quote(when)}"
            )
    else:
        lines.append("Пока никто не подал заявку.")
    return "\n".join(lines)


def _tournament_admin_kb(tournament) -> InlineKeyboardMarkup:
    tid = int(tournament["id"])
    rows = [
        [InlineKeyboardButton(text="✏️ Изменить лимит команд", callback_data=f"trnadm:limit:{tid}")],
        [InlineKeyboardButton(text="🔄 Обновить список", callback_data=f"trnadm:open:{tid}")],
        [InlineKeyboardButton(text="🔁 Обновить пост в топиках", callback_data=f"trnadm:repost:{tid}")],
    ]
    if tournament["status"] == "registration":
        rows.append([InlineKeyboardButton(text="▶️ Запустить турнир сейчас",
                                          callback_data=f"trnadm:start:{tid}")])
        rows.append([InlineKeyboardButton(text="🚫 Отменить турнир",
                                          callback_data=f"trnadm:cancel:{tid}")])
    if tournament["status"] == "running":
        rows.append([InlineKeyboardButton(text="⚖️ Записать итог матча",
                                          callback_data=f"trnadm:res:{tid}")])
        rows.append([InlineKeyboardButton(text="🚫 Отменить турнир",
                                          callback_data=f"trnadm:cancel:{tid}")])
    rows.append([InlineKeyboardButton(text="🗑 Удалить турнир", callback_data=f"trnadm:del:{tid}")])
    rows.append([InlineKeyboardButton(text="◀️ К списку турниров", callback_data="trnadm:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text == "📋 Текущие турниры")
async def admin_current_tournaments(message: Message):
    """Список активных турниров: выбор турнира для управления."""
    member = await get_member(message.from_user.id)
    if not member or not is_any_admin(member):
        await message.answer("⛔ Доступ только для администрации.")
        return
    rows = get_active_tournaments()
    if not rows:
        await message.answer("📭 Активных турниров нет. Создай новый через «🏆 Создание турнира».",
                             reply_markup=admin_scrims_keyboard())
        return
    await message.answer(
        "📋 <b>Текущие турниры</b>\n\nВыбери турнир для управления:",
        parse_mode="HTML", reply_markup=_tournaments_list_kb(rows),
    )


@router.callback_query(F.data == "trnadm:list")
async def admin_tournaments_list(call: CallbackQuery):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    rows = get_active_tournaments()
    if not rows:
        await call.message.edit_text("📭 Активных турниров нет.", reply_markup=None)
        await call.answer()
        return
    await call.message.edit_text(
        "📋 <b>Текущие турниры</b>\n\nВыбери турнир для управления:",
        parse_mode="HTML", reply_markup=_tournaments_list_kb(rows),
    )
    await call.answer()


@router.callback_query(F.data.startswith("trnadm:open:"))
async def admin_tournament_open(call: CallbackQuery):
    """Карточка турнира: параметры + актуальный список поданных составов."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    tournament = get_tournament(tid)
    if not tournament:
        await call.answer("Турнир не найден", show_alert=True)
        return
    teams = get_tournament_teams(tid)
    parts = _split_text(_tournament_admin_card(tournament, teams))
    try:
        await call.message.edit_text(parts[-1] if len(parts) == 1 else parts[0],
                                     parse_mode="HTML",
                                     reply_markup=_tournament_admin_kb(tournament))
    except Exception:
        await call.message.answer(parts[0], parse_mode="HTML",
                                  reply_markup=_tournament_admin_kb(tournament))
    for extra in parts[1:]:
        await call.message.answer(extra, parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("trnadm:limit:"))
async def admin_tournament_limit_ask(call: CallbackQuery, state: FSMContext):
    """Изменение лимита команд у активного турнира."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    tournament = get_tournament(tid)
    if not tournament:
        await call.answer("Турнир не найден", show_alert=True)
        return
    teams = get_tournament_teams(tid)
    unit = _limit_unit(tournament["t_type"])
    await state.set_state(TournamentAdminStates.waiting_new_limit)
    await state.update_data(edit_tournament_id=tid)
    await call.message.edit_text(
        f"✏️ <b>Лимит турнира #{tid}</b>\n\n"
        f"Сейчас: <b>{_limit_text(tournament['team_limit'], tournament['t_type'])}</b>, "
        f"уже подано: <b>{len(teams)}</b>.\n\n"
        f"Отправь новое число <b>{unit}</b> (от {max(2, len(teams))} до 128) "
        f"или нажми «♾ Снять лимит».\nДля отмены отправь /cancel.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="♾ Снять лимит", callback_data=f"trnadm:nolimit:{tid}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"trnadm:open:{tid}")],
        ]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("trnadm:nolimit:"))
async def admin_tournament_no_limit(call: CallbackQuery, state: FSMContext, bot: Bot):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    set_tournament_limit(tid, None)
    await state.clear()
    await _refresh_tournament_posts(bot, tid)
    tournament = get_tournament(tid)
    await call.message.edit_text(
        _tournament_admin_card(tournament, get_tournament_teams(tid)),
        parse_mode="HTML", reply_markup=_tournament_admin_kb(tournament),
    )
    await call.answer("Лимит снят")


@router.message(TournamentAdminStates.waiting_new_limit, F.text.startswith("/")
                | F.text.in_(TOURNAMENT_GUARD_TEXTS))
async def admin_tournament_limit_guard(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Изменение лимита отменено.", reply_markup=admin_scrims_keyboard())


@router.message(TournamentAdminStates.waiting_new_limit)
async def admin_tournament_limit_save(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    tid = int(data.get("edit_tournament_id") or 0)
    tournament = get_tournament(tid)
    if not tournament:
        await state.clear()
        await message.answer("❌ Турнир не найден.", reply_markup=admin_scrims_keyboard())
        return
    teams = get_tournament_teams(tid)
    unit = _limit_unit(tournament["t_type"])
    text = (message.text or "").strip()
    if not text.isdigit() or not (2 <= int(text) <= 128):
        await message.answer(f"❌ Нужно целое число {unit} от 2 до 128. "
                             f"Попробуй ещё раз или отправь /cancel.")
        return
    new_limit = int(text)
    if new_limit < len(teams):
        await message.answer(
            f"❌ Лимит не может быть меньше числа уже поданных заявок ({len(teams)}). "
            f"Введи число от {len(teams)} или отправь /cancel."
        )
        return
    set_tournament_limit(tid, new_limit)
    await state.clear()
    await _refresh_tournament_posts(bot, tid)
    tournament = get_tournament(tid)
    await message.answer(
        f"✅ Лимит турнира #{tid} изменён на <b>{new_limit} {unit}</b>.",
        parse_mode="HTML", reply_markup=admin_scrims_keyboard(),
    )
    await message.answer(
        _tournament_admin_card(tournament, get_tournament_teams(tid)),
        parse_mode="HTML", reply_markup=_tournament_admin_kb(tournament),
    )
    await log_admin_action(
        bot=bot, admin_id=message.from_user.id,
        admin_name=message.from_user.username or message.from_user.first_name or "admin",
        action_text=f"✏️ Изменил лимит турнира #{tid} на {new_limit} {unit}.",
    )


@router.callback_query(F.data.startswith("trnadm:start:"), ~F.data.startswith("trnadm:start_yes:"))
async def admin_tournament_start_ask(call: CallbackQuery):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"✅ Да, запустить #{tid}", callback_data=f"trnadm:start_yes:{tid}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"trnadm:open:{tid}"),
    ]]))
    await call.answer("Подтвердите запуск")


@router.callback_query(F.data.startswith("trnadm:start_yes:"))
async def admin_tournament_start_yes(call: CallbackQuery, bot: Bot):
    """Ручной досрочный старт турнира администратором."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    await call.answer("Запускаю турнир...")
    result = await start_tournament(bot, tid, manual_by=call.from_user.id)
    if result is None:
        await call.message.edit_text(f"⚠️ Турнир #{tid} уже запущен или не найден.", reply_markup=None)
        return
    if result.get("cancelled"):
        await call.message.edit_text(
            f"🚫 Турнир #{tid} отменён: участников недостаточно ({result['teams']}).",
            reply_markup=None,
        )
        return
    await call.message.edit_text(
        f"🔥 <b>Турнир #{tid} запущен!</b>\n\n"
        f"Участников: <b>{result['teams']}</b>, автопроходов: <b>{result.get('byes', 0)}</b>.\n"
        f"Уведомлений лидерам отправлено: <b>{result['notified']}</b>.\n"
        f"Финальная сетка и расписание пар опубликованы в топиках.",
        parse_mode="HTML", reply_markup=None,
    )
    await log_admin_action(
        bot=bot, admin_id=call.from_user.id,
        admin_name=call.from_user.username or call.from_user.first_name or "admin",
        action_text=f"▶️ Досрочно запустил турнир #{tid} ({result['teams']} участников).",
    )


@router.callback_query(F.data.startswith("trnadm:cancel:"))
async def admin_tournament_cancel(call: CallbackQuery, bot: Bot):
    """Отмена турнира администратором с уведомлением участников."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    if not set_tournament_status(tid, "cancelled"):
        await call.answer("Турнир уже закрыт", show_alert=True)
        return
    teams = get_tournament_teams(tid)
    for t in teams:
        try:
            await bot.send_message(int(t["leader_id"]),
                                   f"🚫 Турнир #{tid} отменён администрацией.")
        except Exception:
            pass
    for post in get_tournament_posts(tid):
        try:
            await bot.edit_message_reply_markup(chat_id=post["chat_id"],
                                                message_id=post["message_id"], reply_markup=None)
        except Exception:
            pass
    await call.message.edit_text(f"🚫 Турнир #{tid} отменён. Уведомлено команд: {len(teams)}.",
                                 reply_markup=None)
    await call.answer("Турнир отменён")
    await log_admin_action(
        bot=bot, admin_id=call.from_user.id,
        admin_name=call.from_user.username or call.from_user.first_name or "admin",
        action_text=f"🚫 Отменил турнир #{tid}.",
    )


# ─── ПОЛНОЕ УДАЛЕНИЕ ТУРНИРА ─────────────────────────────────────────────────

def _collect_tournament_for_delete(tournament_id: int) -> dict:
    """Собирает данные ДО удаления: посты в топиках, лидеры команд, имя турнира."""
    init_tournament_tables()
    conn = db.get_connection()
    tour = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
    posts = conn.execute("SELECT * FROM tournament_posts WHERE tournament_id = ?",
                         (tournament_id,)).fetchall()
    leaders = conn.execute("SELECT DISTINCT leader_id FROM tournament_teams WHERE tournament_id = ?",
                           (tournament_id,)).fetchall()
    teams_cnt = conn.execute("SELECT COUNT(*) c FROM tournament_teams WHERE tournament_id = ?",
                             (tournament_id,)).fetchone()["c"]
    conn.close()
    return {
        "tour": tour, "posts": posts,
        "leaders": [int(r["leader_id"]) for r in leaders if r["leader_id"]],
        "teams_cnt": int(teams_cnt),
    }


def _drop_tournament_rows(tournament_id: int) -> None:
    """Удаляет турнир; дети (команды/матчи/результаты/посты) — каскадом.

    FK включены (PRAGMA foreign_keys=ON на каждом соединении league_db),
    поэтому DELETE FROM tournaments сносит tournament_teams/matches/posts,
    а результаты — через матчи.
    """
    conn = db.get_connection()
    conn.execute("DELETE FROM tournament_results WHERE tournament_id = ?", (tournament_id,))
    conn.execute("DELETE FROM tournaments WHERE id = ?", (tournament_id,))
    conn.commit()
    conn.close()


@router.callback_query(F.data.startswith("trnadm:del:"))
async def admin_tournament_delete_ask(call: CallbackQuery):
    """Шаг 1: честное предупреждение перед необратимым удалением."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    pack = _collect_tournament_for_delete(tid)
    if not pack["tour"]:
        await call.answer("Турнир уже удалён", show_alert=True)
        return
    t = pack["tour"]
    await call.message.edit_text(
        f"🗑 <b>Удалить турнир #{tid}?</b>\n\n"
        f"Тип: {TOURNAMENT_TYPES.get(t['t_type'], t['t_type'])} · "
        f"статус: {TOURNAMENT_STATUS_LABEL.get(t['status'], t['status'])}\n"
        f"Зарегистрировано команд: <b>{pack['teams_cnt']}</b>, постов в топиках: <b>{len(pack['posts'])}</b>.\n\n"
        f"⚠️ Действие <b>необратимо</b>: будут удалены запись турнира, все заявки, "
        f"матчи, результаты, сетка. Посты-анонсы в топиках бот попробует удалить. "
        f"Лидерам уйдёт уведомление.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"✅ Да, удалить #{tid} полностью",
                                 callback_data=f"trnadm:del_yes:{tid}"),
            InlineKeyboardButton(text="◀️ Назад к турниру", callback_data=f"trnadm:open:{tid}"),
        ]]),
    )
    await call.answer("Подтвердите удаление")


@router.callback_query(F.data.startswith("trnadm:del_yes:"))
async def admin_tournament_delete_yes(call: CallbackQuery, bot: Bot):
    """Шаг 2: выполняем удаление — посты в топиках, уведомления лидерам, БД, лог."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    pack = _collect_tournament_for_delete(tid)
    if not pack["tour"]:
        await call.answer("Турнир уже удалён", show_alert=True)
        await call.message.edit_text("🗑 Турнир уже удалён.", reply_markup=None)
        return

    posts_deleted = 0
    for post in pack["posts"]:
        try:
            await bot.delete_message(chat_id=post["chat_id"], message_id=post["message_id"])
            posts_deleted += 1
        except Exception as e:
            logger.debug(f"Пост турнира {tid} не удалён из чата: {e}")

    for leader_id in pack["leaders"]:
        try:
            await bot.send_message(
                leader_id,
                f"🗑 Турнир #{tid} удалён администрацией. "
                f"Ваши заявки/матчи в нём аннулированы.",
            )
        except Exception:
            pass
        await asyncio.sleep(0.05)

    teams_cnt = pack["teams_cnt"]
    _drop_tournament_rows(tid)

    await call.message.edit_text(
        f"🗑 Турнир #{tid} удалён полностью.\n"
        f"Удалено постов в топиках: <b>{posts_deleted}</b>, уведомлено лидеров: "
        f"<b>{len(pack['leaders'])}</b>, команд было: <b>{teams_cnt}</b>.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀️ К списку турниров", callback_data="trnadm:list"),
        ]]),
    )
    await call.answer("Удалено")
    await log_admin_action(
        bot=bot, admin_id=call.from_user.id,
        admin_name=call.from_user.username or call.from_user.first_name or "admin",
        action_text=(f"🗑 Удалил турнир #{tid} полностью: команд {teams_cnt}, "
                     f"постов снято {posts_deleted}."),
    )


@router.callback_query(F.data.startswith("trnadm:repost:"))
async def admin_tournament_repost(call: CallbackQuery, bot: Bot):
    """Обновить уже существующую рассылку-пост турнира в топиках.

    На регистрации: перечитывает анонс и дорисовывает кнопку «✅ Зарегистрироваться»
    (лекарство от «в новостях нет кнопки вступления» на старых постах).
    На идущих турнирах: обновляет текущую сетку в тех же постах.
    """
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    tournament = get_tournament(tid)
    if not tournament:
        await call.answer("Турнир не найден", show_alert=True)
        return
    if not get_tournament_posts(tid):
        await call.answer("У турнира нет постов в топиках — анонс не публиковался",
                          show_alert=True)
        return
    if tournament["status"] == "registration":
        await _refresh_tournament_posts(bot, tid)
        await call.answer("Анонс обновлён, кнопка регистрации на месте")
    else:
        await _repost_bracket(bot, tid)
        await call.answer("Сетка в топиках обновлена")


# ─── РУЧНОЕ ВНЕСЕНИЕ ИТОГА МАТЧА АДМИНИСТРАЦИЕЙ ──────────────────────────────

def _pending_full_matches(tournament_id: int):
    """Матчи текущего/ожидаемого круга: обе команды известны, исход не записан."""
    return [m for m in get_tournament_matches(tournament_id)
            if m["status"] == "pending" and m["team_a_id"] and m["team_b_id"]]


@router.callback_query(F.data.startswith("trnadm:res:"))
async def admin_tournament_results_list(call: CallbackQuery):
    """Админ выбирает матч для ручной записи победителя."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    matches = _pending_full_matches(tid)
    builder = InlineKeyboardBuilder()
    for m in matches[:20]:
        stage = "🥉 3-е место" if m["bracket"] == "third" else f"круг {m['round_no']}"
        builder.button(
            text=f"{stage}: {_team_name(m['team_a_id'])} 🆚 {_team_name(m['team_b_id'])}",
            callback_data=f"trnadm:mview:{m['id']}",
        )
    builder.button(text="◀️ Назад к турниру", callback_data=f"trnadm:open:{tid}")
    builder.adjust(1)
    if not matches:
        await call.answer("📭 Нет матчей, ожидающих исхода", show_alert=True)
        return
    await call.message.edit_text(
        "⚖️ <b>Записать итог матча</b>\n\n"
        "Выбери матч — затем отметь победителя. Сетка обновится, а как только у пары "
        "следующего круга определятся обе команды, капитаны автоматически получат уведомление.",
        parse_mode="HTML", reply_markup=builder.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("trnadm:mview:"))
async def admin_match_view(call: CallbackQuery):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    mid = int(call.data.rsplit(":", 1)[-1])
    m = _m_get(mid)
    if not m or m["status"] != "pending" or not m["team_a_id"] or not m["team_b_id"]:
        await call.answer("Матч уже закрыт или не готов", show_alert=True)
        return
    name_a, name_b = _team_name(m["team_a_id"]), _team_name(m["team_b_id"])
    stage = "🥉 матч за 3-е место" if m["bracket"] == "third" else f"круг {m['round_no']}"
    await call.message.edit_text(
        f"⚖️ <b>Турнир #{m['tournament_id']} · {stage}</b>\n\n"
        f"Матч: <b>{hd.quote(name_a)}</b> 🆚 <b>{hd.quote(name_b)}</b>\n\n"
        f"Кто победил?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🏆 Победил {name_a[:20]}",
                                  callback_data=f"trnadm:mset:{mid}:a")],
            [InlineKeyboardButton(text=f"🏆 Победил {name_b[:20]}",
                                  callback_data=f"trnadm:mset:{mid}:b")],
            [InlineKeyboardButton(text="◀️ Назад к матчам",
                                  callback_data=f"trnadm:res:{m['tournament_id']}")],
        ]))
    await call.answer()


@router.callback_query(F.data.startswith("trnadm:mset:"))
async def admin_match_set_winner(call: CallbackQuery, bot: Bot):
    """Администратор вручную фиксирует победителя матча."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    _, _, mid, side = call.data.split(":")
    m = _m_get(int(mid))
    if not m or m["status"] != "pending" or not m["team_a_id"] or not m["team_b_id"]:
        await call.answer("Матч уже закрыт", show_alert=True)
        return
    winner_id = int(m["team_a_id"]) if side == "a" else int(m["team_b_id"])
    loser_id = int(m["team_b_id"]) if side == "a" else int(m["team_a_id"])
    tid = int(m["tournament_id"])
    admin_name = call.from_user.username or call.from_user.first_name or "admin"
    await _apply_confirmed_result(bot, tid, m, winner_id, loser_id, "",
                                  admin_name, admin_id=call.from_user.id)
    tournament = get_tournament(tid)
    status_txt = TOURNAMENT_STATUS_LABEL.get(tournament["status"], tournament["status"])
    await call.message.edit_text(
        f"✅ Победитель записан: <b>{hd.quote(_team_name(winner_id))}</b>.\n"
        f"Сетка обновлена, команды получили уведомления. Статус турнира: {status_txt}.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚖️ Записать ещё итог", callback_data=f"trnadm:res:{tid}")],
            [InlineKeyboardButton(text="◀️ К карточке турнира", callback_data=f"trnadm:open:{tid}")],
        ]))
    await call.answer("Итог записан")

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
