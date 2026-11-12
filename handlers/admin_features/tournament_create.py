"""Мастер создания турнира (FSM) и подтверждение."""
import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.markdown import html_decoration as hd
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from database import get_member, get_setting
from utils.permissions import is_any_admin, has_role
from utils.keyboards import (
    main_menu, admin_panel_keyboard, admin_push_keyboard, admin_members_keyboard,
    admin_participants_keyboard, admin_scrims_keyboard,
)
from utils.admin_logger import log_admin_action
from league.competitive import SCRIM_TYPE_LABEL, _normalise_scheduled
from .common import (
    SHOWDOWN_LIMIT,
    TOURNAMENT_GUARD_TEXTS,
    TOURNAMENT_TYPES,
    _cb_is_admin,
    _limit_label,
    _limit_text,
    _limit_unit,
    _players_per_team,
)
from .states import TournamentStates
from .tournament_cards import _broadcast_tournament_dm, _publish_tournament_posts
from .tournament_db import create_tournament_row

logger = logging.getLogger(__name__)
router = Router()


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
