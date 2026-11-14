"""Корневые экраны составов, навигация и клавиатуры."""
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.markdown import html_decoration as hd
from config import INITIAL_ADMINS, is_hidden_tester
from database import get_member
from league.league_db import get_connection as get_db, get_user_league, lft_get
from utils.keyboards import main_menu
from .shared import LeagueStates

router = Router()


def _input_keyboard():
    return _reply([["◀️ Назад", "❌ Отмена"]])


def _back_row(destination: str, label: str = "◀️ Назад"):
    return [InlineKeyboardButton(text=label, callback_data=destination)]


def _row_verified(row) -> bool:
    """Совместимость: колонка может называться is_verified или verified."""
    for key in ("is_verified", "verified"):
        try:
            if int(row[key] or 0) == 1:
                return True
        except (IndexError, KeyError, TypeError, ValueError):
            continue
    return False


def _row_mmr(row) -> int:
    try:
        return int(row["mmr"] or 0)
    except (IndexError, KeyError, TypeError, ValueError):
        return 0


def _reply(rows):
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=text) for text in row] for row in rows],
        resize_keyboard=True,
    )


def _lft_active(user_id: int) -> bool:
    try:
        row = lft_get(int(user_id))
        return bool(row and int(row["is_active"] or 0) == 1)
    except Exception:
        return False


def _root_keyboard(composition, user_id: int):
    if not composition:
        lft_row = "⏹ Остановить поиск" if _lft_active(user_id) else "🔎 Я ищу команду"
        return _reply([
            ["🌍 Все составы", "📝 Подать заявку в состав"],
            ["📩 Мои заявки", "📥 Приглашения в состав"],
            ["➕ Создать состав", "👀 Игроки в поиске"],
            [lft_row],
            ["◀️ Назад в главное меню"],
        ])
    is_leader = int(composition["leader_id"]) == int(user_id)
    is_deputy = bool(composition["is_deputy"])
    if is_leader:
        second = "🏆 Лиги" if _row_verified(composition) else "🛡 Верифицировать состав"
        return _reply([
            ["🌍 Все составы", second],
            ["⚙️ Настройка состава", "📋 Заявки в состав"],
            ["👀 Игроки в поиске", "📜 История матчей"],
            ["◀️ Назад в главное меню"],
        ])
    if is_deputy:
        first = ["👀 Игроки в поиске"]
        if composition["can_review_apps"]:
            first.insert(0, "📋 Заявки в состав")
        return _reply([
            ["🌍 Все составы", "⚙️ Настройка состава"],
            first,
            ["📜 История матчей"],
            ["◀️ Назад в главное меню"],
        ])
    return _reply([
        ["🌍 Все составы", "👥 Участники состава"],
        ["👀 Игроки в поиске", "🚪 Выйти из состава"],
        ["📜 История матчей"],
        ["◀️ Назад в главное меню"],
    ])


def _settings_keyboard(composition, user_id: int):
    is_leader = int(composition["leader_id"]) == int(user_id)
    try:
        notify_on = int(composition["notify_lft"] if composition["notify_lft"] is not None else 1)
    except (IndexError, KeyError, TypeError, ValueError):
        notify_on = 1
    rows = [
        ["👥 Участники состава"] + (
            ["🔒 Закрыть набор / Открыть набор"]
            if is_leader or composition["can_toggle_open"] else []
        ),
        ["👑 Передать лидерство", "🛡 Управление заместителями"] if is_leader else [],
        (["🚪 Выгнать участника"] if is_leader or composition["can_kick"] else [])
        + (["➕ Пригласить игрока"] if is_leader or composition["can_invite"] else []),
        ([f"🔔 Уведомления о поиске: {'Вкл' if notify_on else 'Выкл'}"]
         if is_leader else []),
        ["🚪 Выйти из состава"] + (["💥 Распустить состав"] if is_leader else []),
        ["◀️ Назад в составы"],
    ]
    return _reply([row for row in rows if row])


async def show_root(message: Message, state: FSMContext, user_id: int = None):
    await state.clear()
    user_id = int(user_id or message.from_user.id)
    composition = get_user_league(user_id)
    if not composition:
        text = (
            "🏆 <b>Составы (BetaTest)</b>\n\n"
            "Ты пока не состоишь ни в одном составе. Можно выбрать существующий "
            "состав или создать свой."
        )
    else:
        status = "Открыт ✅" if composition["is_open"] else "Закрыт ❌"
        role = "👑 Лидер" if int(composition["leader_id"]) == user_id else (
            "🛡 Заместитель" if composition["is_deputy"] else "👤 Участник"
        )
        text = (
            f"🏆 <b>Ваш состав: {hd.quote(composition['name'])} "
            f"[{hd.quote(composition['tag'])}]</b>\n"
            f"Статус набора: {status}\nРоль: {role}"
        )
    await message.answer(text, parse_mode="HTML", reply_markup=_root_keyboard(composition, user_id))


@router.message(F.text.in_({"Составы 🏆 (BetaTest)", "Составы🏆 (BetaTest)", "Лиги 💀 (BetaTest)"}))
async def open_compositions(message: Message, state: FSMContext):
    await show_root(message, state)


@router.message(F.text == "◀️ Назад в составы")
async def back_root_text(message: Message, state: FSMContext):
    await show_root(message, state)


@router.message(F.text.in_({"◀️ Назад в главное меню", "Назад в главное меню"}))
async def back_to_main_menu(message: Message, state: FSMContext):
    await state.clear()
    member = await get_member(message.from_user.id)
    await message.answer("Главное меню:", reply_markup=main_menu(member))


@router.message(
    StateFilter(
        LeagueStates.waiting_for_name,
        LeagueStates.waiting_for_tag,
        LeagueStates.waiting_invite_target,
        LeagueStates.waiting_apply_text,
    ),
    F.text.in_({"◀️ Назад", "❌ Отмена"}),
)
async def back_from_composition_input(message: Message, state: FSMContext):
    current = await state.get_state()
    cancelled = message.text == "❌ Отмена"
    if not cancelled and current == LeagueStates.waiting_for_tag.state:
        await state.set_state(LeagueStates.waiting_for_name)
        await message.answer("Введи название состава:", reply_markup=_input_keyboard())
        return

    await state.clear()
    if not cancelled and current == LeagueStates.waiting_apply_text.state:
        await show_root(message, state)
        await _send_all(message)
        return
    if current == LeagueStates.waiting_invite_target.state and get_user_league(message.from_user.id):
        await open_settings(message)
        return
    await show_root(message, state)


@router.callback_query(F.data == "league:back_root")
async def back_root_callback(call: CallbackQuery, state: FSMContext):
    """Совместимость с кнопками в сообщениях, отправленных старой версией."""
    await state.clear()
    try:
        await call.message.delete()
    except Exception:
        pass
    await show_root(call.message, state, user_id=call.from_user.id)
    await call.answer()


@router.callback_query(F.data == "comp:back:settings")
async def back_to_settings_callback(call: CallbackQuery, state: FSMContext):
    composition = get_user_league(call.from_user.id)
    if not composition or not (composition["is_deputy"] or int(composition["leader_id"]) == call.from_user.id):
        await call.answer("Настройки больше недоступны", show_alert=True)
        return
    await state.clear()
    await call.answer()
    await call.message.edit_text("◀️ Возврат в настройки состава.", reply_markup=None)
    await call.message.answer(
        "⚙️ Настройка состава. Выбери действие:",
        reply_markup=_settings_keyboard(composition, call.from_user.id),
    )


@router.message(F.text == "⚙️ Настройка состава")
async def open_settings(message: Message):
    composition = get_user_league(message.from_user.id)
    if not composition or not (composition["is_deputy"] or int(composition["leader_id"]) == message.from_user.id):
        await message.answer("❌ Настройки доступны только лидеру и заместителям.")
        return
    await message.answer(
        "⚙️ <b>Настройка состава</b>\n\nВыбери действие:",
        parse_mode="HTML",
        reply_markup=_settings_keyboard(composition, message.from_user.id),
    )


# ─── Все составы / заявки ────────────────────────────────────────────────────
async def _send_all(message: Message, edit: bool = False, viewer_id: int = None):
    viewer_id = int(viewer_id or getattr(message.from_user, "id", 0) or 0)
    role = str((INITIAL_ADMINS.get(viewer_id) or {}).get("role", ""))
    sees_hidden = is_hidden_tester(viewer_id) or role in ("president", "grand_vice")
    hidden_where = "" if sees_hidden else "WHERE COALESCE(l.hidden, 0) = 0"
    conn = get_db()
    try:
        rows = conn.execute(f"""
            SELECT l.*, (SELECT COUNT(*) FROM league_members m
            WHERE m.league_id = l.id AND m.user_id IS NOT NULL) AS count_members
            FROM leagues l
            {hidden_where}
            ORDER BY COALESCE(l.is_verified, l.verified, 0) DESC,
                     COALESCE(l.mmr, 0) DESC, l.id DESC
        """).fetchall()
    except Exception:
        rows = conn.execute(f"""
            SELECT l.*, (SELECT COUNT(*) FROM league_members m
            WHERE m.league_id = l.id AND m.user_id IS NOT NULL) AS count_members
            FROM leagues l {hidden_where}
            ORDER BY count_members DESC, l.id DESC
        """).fetchall()
    conn.close()
    builder = InlineKeyboardBuilder()
    for row in rows:
        verified = _row_verified(row)
        mmr = _row_mmr(row)
        marker = "🟠" if verified else "⚪"
        base = f"{marker} {row['name']} [{row['tag']}] ({row['count_members']}/4)"
        mmr_part = f" · {mmr}MMR🌟" if verified else ""
        builder.button(text=base + mmr_part, callback_data=f"league:info:{row['id']}")
    builder.button(text="◀️ В составы", callback_data="league:back_root")
    builder.adjust(1)
    text = "🌍 <b>Все составы</b>" if rows else "📭 Составов пока нет."
    if edit:
        await message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())
