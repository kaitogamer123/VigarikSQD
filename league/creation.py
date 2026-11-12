"""Создание состава (FSM)."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram.utils.markdown import html_decoration as hd
from league.league_db import get_connection as get_db, get_user_league
from .shared import LeagueStates, _main_member
from .ui import _input_keyboard, show_root

router = Router()


# ─── Создание состава ────────────────────────────────────────────────────────
@router.message(F.text.in_({"➕ Создать состав", "➕ Создать лигу"}))
async def create_start(message: Message, state: FSMContext):
    if get_user_league(message.from_user.id):
        await message.answer("❌ Ты уже состоишь в составе.")
        return
    await state.set_state(LeagueStates.waiting_for_name)
    await message.answer("Напиши полное название нового состава:", reply_markup=_input_keyboard())


@router.message(LeagueStates.waiting_for_name)
async def create_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not 2 <= len(name) <= 40:
        await message.answer("❌ Название должно содержать от 2 до 40 символов.")
        return
    await state.update_data(composition_name=name)
    await state.set_state(LeagueStates.waiting_for_tag)
    await message.answer("Напиши сокращённый тег состава (до 5 символов):", reply_markup=_input_keyboard())


@router.message(LeagueStates.waiting_for_tag)
async def create_tag(message: Message, state: FSMContext):
    tag = (message.text or "").strip().upper()
    if not 1 <= len(tag) <= 5:
        await message.answer("❌ Тег должен содержать от 1 до 5 символов.")
        return
    data = await state.get_data()
    user_id = message.from_user.id
    user = _main_member(user_id)
    conn = get_db()
    if conn.execute("SELECT 1 FROM leagues WHERE UPPER(tag) = UPPER(?)", (tag,)).fetchone():
        conn.close(); await message.answer("❌ Такой тег уже занят."); return
    cur = conn.execute("INSERT INTO leagues (name, tag, leader_id, is_open) VALUES (?, ?, ?, 1)",
                       (data["composition_name"], tag, user_id))
    league_id = cur.lastrowid
    conn.execute("""
        INSERT INTO league_members
            (league_id, slot_index, user_id, game_nick, player_tag, username, role,
             trophies_record, joined_at)
        VALUES (?, 1, ?, ?, ?, ?, 'лидер', ?, CURRENT_TIMESTAMP)
    """, (league_id, user_id,
          user["game_nick"] if user and user["game_nick"] else message.from_user.first_name,
          user["player_tag"] if user and user["player_tag"] else "#N/A",
          message.from_user.username or "",
          user["trophies"] if user and "trophies" in user.keys() else 0))
    for slot in range(2, 5):
        conn.execute("""
            INSERT INTO league_members (league_id, slot_index, user_id, game_nick, role)
            VALUES (?, ?, NULL, '-- Свободное место --', 'участник')
        """, (league_id, slot))
    conn.commit(); conn.close(); await state.clear()
    await message.answer(f"✅ Состав {hd.quote(data['composition_name'])} [{hd.quote(tag)}] успешно создан!",
                         parse_mode="HTML")
    await show_root(message, state)
