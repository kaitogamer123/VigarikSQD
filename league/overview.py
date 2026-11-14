"""Список всех составов и карточка состава."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.markdown import html_decoration as hd
from league.league_db import get_connection as get_db, get_league_members, get_user_league
from .ui import _row_mmr, _row_verified, _send_all

router = Router()


@router.message(F.text.in_({"🌍 Все составы", "🌍 Все лиги", "📝 Подать заявку в состав", "📝 Подать заявку в лигу"}))
async def all_compositions(message: Message):
    await _send_all(message)


@router.callback_query(F.data == "league:all_list")
async def all_compositions_callback(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await _send_all(call.message, edit=True); await call.answer()


def composition_card_text(composition, members) -> str:
    """Карточка состава — общая для «🌍 Все составы» и приглашений из поиска."""
    verified = _row_verified(composition)
    mmr = _row_mmr(composition)
    status_line = f"🟠 Верифицирован · {mmr}MMR🌟" if verified else "⚪ Не верифицирован"
    lines = [f"🏆 <b>{hd.quote(composition['name'])} [{hd.quote(composition['tag'])}]</b>",
             status_line]
    for member in members:
        if not member["user_id"]:
            lines.append(f"{member['slot_index']}. — Свободное место —")
            continue
        role = "👑 Лидер" if member["role"] == "лидер" else (
            "🛡 Заместитель" if member["role"] == "заместитель" else "👤 Участник"
        )
        lines.append(f"{member['slot_index']}. {hd.quote(member['game_nick'] or 'Игрок')} — {role}")
    return "\n".join(lines)


@router.callback_query(F.data.startswith("league:info:"))
async def composition_info(call: CallbackQuery):
    league_id = int(call.data.rsplit(":", 1)[-1])
    conn = get_db(); composition = conn.execute("SELECT * FROM leagues WHERE id = ?", (league_id,)).fetchone()
    members = get_league_members(league_id)
    conn.close()
    if not composition:
        await call.answer("Состав не найден", show_alert=True); return
    buttons = [[InlineKeyboardButton(text="◀️ Ко всем составам", callback_data="league:all_list")]]
    if not get_user_league(call.from_user.id) and composition["is_open"]:
        buttons.insert(0, [InlineKeyboardButton(text="📝 Подать заявку", callback_data=f"league:apply_send:{league_id}")])
    await call.message.edit_text(composition_card_text(composition, members), parse_mode="HTML",
                                 reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)); await call.answer()
