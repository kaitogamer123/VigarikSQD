"""Приглашения игроков в состав."""
import sqlite3
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.markdown import html_decoration as hd
from config import is_hidden_tester
from league.league_db import (
    get_connection as get_db,
    get_user_league,
    has_management_permission,
    lft_disable,
)
from .shared import LeagueStates, _fill_slot
from .ui import _back_row, _input_keyboard, open_settings

router = Router()


# ─── Приглашения ──────────────────────────────────────────────────────────────
@router.message(F.text == "➕ Пригласить игрока")
async def invite_start(message: Message, state: FSMContext):
    if not has_management_permission(message.from_user.id, "can_invite"):
        await message.answer("❌ Нет права приглашать игроков."); return
    await state.set_state(LeagueStates.waiting_invite_target)
    await message.answer(
        "Отправь Telegram ID, игровой тег или точный игровой ник участника:",
        reply_markup=_input_keyboard(),
    )


@router.message(LeagueStates.waiting_invite_target)
async def invite_target(message: Message, state: FSMContext):
    composition = get_user_league(message.from_user.id)
    if not composition or not has_management_permission(message.from_user.id, "can_invite"):
        await state.clear(); return
    raw = (message.text or "").strip(); user = None
    conn_main = sqlite3.connect("vigarik.db"); conn_main.row_factory = sqlite3.Row
    if raw.isdigit():
        user = conn_main.execute("SELECT * FROM members WHERE user_id = ?", (int(raw),)).fetchone()
    else:
        clean = raw.lstrip("#@").upper()
        user = conn_main.execute("""
            SELECT * FROM members WHERE UPPER(REPLACE(player_tag, '#', '')) = ?
            OR LOWER(game_nick) = LOWER(?) OR LOWER(username) = LOWER(?) LIMIT 1
        """, (clean, raw, raw.lstrip("@"))).fetchone()
    conn_main.close()
    if not user:
        await message.answer("❌ Игрок не найден в базе бота."); return
    conn = get_db()
    if conn.execute("SELECT 1 FROM league_members WHERE user_id = ?", (user["user_id"],)).fetchone():
        conn.close(); await message.answer("❌ Игрок уже состоит в составе."); return
    free = conn.execute(
        "SELECT 1 FROM league_members WHERE league_id = ? AND user_id IS NULL LIMIT 1",
        (composition["id"],),
    ).fetchone()
    if not free:
        conn.close()
        await message.answer("❌ В составе нет свободных мест — приглашение не отправлено.")
        await state.clear()
        await open_settings(message)
        return
    try:
        conn.execute("INSERT INTO league_invites (league_id, inviter_id, invitee_id) VALUES (?, ?, ?)",
                     (composition["id"], message.from_user.id, user["user_id"])); conn.commit()
        await message.answer(f"✅ Приглашение для {hd.quote(user['game_nick'] or str(user['user_id']))} создано.",
                             parse_mode="HTML")
    except sqlite3.IntegrityError:
        await message.answer("❌ Этому игроку уже отправлено приглашение.")
    finally:
        conn.close(); await state.clear()
        await open_settings(message)


async def _show_invitations(message: Message, user_id: int, edit: bool = False):
    conn = get_db(); rows = conn.execute("""
        SELECT i.id, l.name, l.tag FROM league_invites i JOIN leagues l ON l.id = i.league_id
        WHERE i.invitee_id = ?
    """, (user_id,)).fetchall(); conn.close()
    buttons = [
        [InlineKeyboardButton(text=f"🏆 {r['name']} [{r['tag']}]", callback_data=f"league:invite_view:{r['id']}")]
        for r in rows
    ]
    buttons.append(_back_row("league:back_root", "◀️ В составы"))
    text = "📥 Приглашения в составы:" if rows else "📭 У тебя нет приглашений в составы."
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


@router.message(F.text.in_({"📥 Приглашения в состав", "📥 Приглашения в лигу"}))
async def invitations(message: Message):
    await _show_invitations(message, message.from_user.id)


@router.callback_query(F.data.in_({"comp:back:invites", "league:back_invites"}))
async def invitations_callback(call: CallbackQuery):
    await _show_invitations(call.message, call.from_user.id, edit=True)
    await call.answer()


@router.callback_query(F.data.startswith("league:invite_view:"))
async def invitation_view(call: CallbackQuery):
    invite_id = int(call.data.rsplit(":", 1)[-1])
    conn = get_db()
    invitation = conn.execute(
        "SELECT id FROM league_invites WHERE id = ? AND invitee_id = ?",
        (invite_id, call.from_user.id),
    ).fetchone()
    conn.close()
    if not invitation:
        await call.answer("Приглашение устарело", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[ 
        InlineKeyboardButton(text="✅ Принять", callback_data=f"league:invite_accept:{invite_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"league:invite_reject:{invite_id}"),
    ], _back_row("comp:back:invites", "◀️ К приглашениям")])
    await call.message.edit_text("Принять приглашение в состав?", reply_markup=kb); await call.answer()


@router.callback_query(F.data.startswith("league:invite_accept:"))
async def invitation_accept(call: CallbackQuery, bot: Bot):
    if get_user_league(call.from_user.id):
        await call.answer("Ты уже состоишь в составе", show_alert=True); return
    invite_id = int(call.data.rsplit(":", 1)[-1]); conn = get_db()
    invite = conn.execute("SELECT * FROM league_invites WHERE id = ? AND invitee_id = ?",
                          (invite_id, call.from_user.id)).fetchone()
    if not invite or not _fill_slot(conn, invite["league_id"], call.from_user.id):
        conn.close(); await call.answer("Нет места или приглашение устарело", show_alert=True); return
    league = conn.execute("SELECT name, tag FROM leagues WHERE id = ?",
                          (invite["league_id"],)).fetchone()
    leader = conn.execute(
        "SELECT * FROM league_members WHERE league_id = ? AND slot_index = 1",
        (invite["league_id"],),
    ).fetchone()
    conn.execute("DELETE FROM league_invites WHERE invitee_id = ?", (call.from_user.id,))
    conn.execute("DELETE FROM league_applications WHERE user_id = ?", (call.from_user.id,))
    conn.commit(); conn.close()
    lft_disable(call.from_user.id)

    team_name = hd.quote(str(league["name"] if league else "состав"))
    team_tag = hd.quote(str(league["tag"] if league else ""))
    leader_tag = (leader["player_tag"] if leader and leader["player_tag"] else "—")
    leader_nick = (leader["game_nick"] if leader and leader["game_nick"] else "—")
    player = sqlite3.connect("vigarik.db"); player.row_factory = sqlite3.Row
    me = player.execute("SELECT game_nick, player_tag FROM members WHERE user_id = ?",
                        (call.from_user.id,)).fetchone()
    player.close()
    my_nick = hd.quote(str(me["game_nick"] if me and me["game_nick"] else call.from_user.first_name))
    my_tag = "@тестер" if is_hidden_tester(call.from_user.id) else (
        me["player_tag"] if me and me["player_tag"] else "—")

    try:
        await call.message.edit_text(
            f"✅ Ты вступил в состав <b>{team_name} [{team_tag}]</b>!\n"
            f"Тег лидера: <b>{hd.quote(str(leader_tag))}</b> ({hd.quote(str(leader_nick))})",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row("league:back_root", "◀️ В составы")]),
        )
    except Exception:
        pass
    await call.answer()
    if leader and leader["user_id"]:
        try:
            await bot.send_message(
                int(leader["user_id"]),
                f"✅ <b>{my_nick}</b> вступил в твой состав. Его тег: <b>{hd.quote(str(my_tag))}</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("league:invite_reject:"))
async def invitation_reject(call: CallbackQuery):
    invite_id = int(call.data.rsplit(":", 1)[-1]); conn = get_db()
    conn.execute("DELETE FROM league_invites WHERE id = ? AND invitee_id = ?", (invite_id, call.from_user.id))
    conn.commit(); conn.close()
    await call.message.edit_text(
        "✅ Приглашение отклонено.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row("comp:back:invites", "◀️ К приглашениям")]),
    ); await call.answer()
