"""Заявки на вступление в состав."""
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
from .ui import _back_row, _input_keyboard, show_root

router = Router()


@router.callback_query(F.data.startswith("league:apply_send:"))
async def apply_start(call: CallbackQuery, state: FSMContext):
    if get_user_league(call.from_user.id):
        await call.answer("Ты уже состоишь в составе", show_alert=True); return
    league_id = int(call.data.rsplit(":", 1)[-1])
    conn = get_db()
    league = conn.execute("SELECT is_open FROM leagues WHERE id = ?", (league_id,)).fetchone()
    free = conn.execute(
        "SELECT 1 FROM league_members WHERE league_id = ? AND user_id IS NULL LIMIT 1",
        (league_id,),
    ).fetchone()
    conn.close()
    if not league or not int(league["is_open"] or 0):
        await call.answer("Набор в этот состав закрыт", show_alert=True); return
    if not free:
        await call.answer("В составе нет свободных мест", show_alert=True); return
    await state.update_data(target_league_id=league_id)
    await state.set_state(LeagueStates.waiting_apply_text)
    await call.message.answer(
        "Напиши коротко, почему хочешь вступить в этот состав:",
        reply_markup=_input_keyboard(),
    ); await call.answer()


@router.message(LeagueStates.waiting_apply_text)
async def apply_text(message: Message, state: FSMContext):
    reason = (message.text or "").strip(); data = await state.get_data()
    if not reason:
        await message.answer("❌ Текст заявки не может быть пустым."); return
    conn = get_db()
    league = conn.execute("SELECT is_open FROM leagues WHERE id = ?",
                          (data.get("target_league_id"),)).fetchone()
    free = conn.execute(
        "SELECT 1 FROM league_members WHERE league_id = ? AND user_id IS NULL LIMIT 1",
        (data.get("target_league_id"),),
    ).fetchone()
    if not league or not int(league["is_open"] or 0) or not free:
        conn.close()
        await state.clear()
        await message.answer("❌ Набор в этот состав уже закрыт или мест нет.")
        await show_root(message, state)
        return
    exists = conn.execute("SELECT 1 FROM league_applications WHERE league_id = ? AND user_id = ?",
                          (data.get("target_league_id"), message.from_user.id)).fetchone()
    if not exists:
        conn.execute("INSERT INTO league_applications (league_id, user_id, text_reason) VALUES (?, ?, ?)",
                     (data.get("target_league_id"), message.from_user.id, reason)); conn.commit()
    conn.close(); await state.clear(); await message.answer("✅ Заявка успешно отправлена в состав!")
    await show_root(message, state)


async def _show_my_apps(message: Message, user_id: int, edit: bool = False):
    conn = get_db(); rows = conn.execute("""
        SELECT a.id, l.name, l.tag FROM league_applications a
        JOIN leagues l ON l.id = a.league_id WHERE a.user_id = ? ORDER BY a.sent_at DESC
    """, (user_id,)).fetchall(); conn.close()
    buttons = [
        [InlineKeyboardButton(text=f"❌ Отозвать: {r['name']} [{r['tag']}]",
                              callback_data=f"league:withdraw_app:{r['id']}")] for r in rows
    ]
    buttons.append(_back_row("league:back_root", "◀️ В составы"))
    text = "📩 Твои заявки:" if rows else "📭 У тебя нет активных заявок."
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


@router.message(F.text == "📩 Мои заявки")
async def my_apps(message: Message):
    await _show_my_apps(message, message.from_user.id)


@router.callback_query(F.data == "comp:back:myapps")
async def my_apps_callback(call: CallbackQuery):
    await _show_my_apps(call.message, call.from_user.id, edit=True)
    await call.answer()


@router.callback_query(F.data.startswith("league:withdraw_app:"))
async def withdraw_app(call: CallbackQuery):
    app_id = int(call.data.rsplit(":", 1)[-1]); conn = get_db()
    conn.execute("DELETE FROM league_applications WHERE id = ? AND user_id = ?", (app_id, call.from_user.id))
    conn.commit(); conn.close()
    await call.message.edit_text(
        "✅ Заявка отозвана.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row("comp:back:myapps", "◀️ К моим заявкам")]),
    ); await call.answer()


# ─── Заявки для управления ────────────────────────────────────────────────────
async def _show_incoming_apps(message: Message, user_id: int, edit: bool = False):
    if not has_management_permission(user_id, "can_review_apps"):
        if edit:
            await message.edit_text("❌ Нет права работать с заявками.")
        else:
            await message.answer("❌ Нет права работать с заявками.")
        return
    composition = get_user_league(user_id); conn = get_db()
    rows = conn.execute("SELECT * FROM league_applications WHERE league_id = ? ORDER BY sent_at",
                        (composition["id"],)).fetchall(); conn.close()
    main = sqlite3.connect("vigarik.db"); main.row_factory = sqlite3.Row
    buttons = []
    for row in rows:
        user = main.execute("SELECT game_nick FROM members WHERE user_id = ?", (row["user_id"],)).fetchone()
        buttons.append([InlineKeyboardButton(
            text=f"📩 {user['game_nick'] if user and user['game_nick'] else row['user_id']}",
            callback_data=f"league:app_detail:{row['id']}")])
    main.close()
    buttons.append(_back_row("comp:back:settings", "◀️ В настройки"))
    text = "📋 Заявки в состав:" if rows else "📭 Входящих заявок нет."
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.message(F.text.in_({"📋 Заявки в состав", "📋 Просмотреть заявки"}))
async def incoming_apps(message: Message):
    await _show_incoming_apps(message, message.from_user.id)


@router.callback_query(F.data == "comp:back:apps")
async def incoming_apps_callback(call: CallbackQuery):
    await _show_incoming_apps(call.message, call.from_user.id, edit=True)
    await call.answer()


@router.callback_query(F.data.startswith("league:app_detail:"))
async def app_detail(call: CallbackQuery):
    if not has_management_permission(call.from_user.id, "can_review_apps"):
        await call.answer("Нет права работать с заявками", show_alert=True); return
    app_id = int(call.data.rsplit(":", 1)[-1]); conn = get_db()
    app = conn.execute("SELECT * FROM league_applications WHERE id = ?", (app_id,)).fetchone(); conn.close()
    composition = get_user_league(call.from_user.id)
    if not app or not composition or app["league_id"] != composition["id"]:
        await call.answer("Заявка устарела", show_alert=True); return
    kb = InlineKeyboardMarkup(inline_keyboard=[[ 
        InlineKeyboardButton(text="✅ Принять", callback_data=f"league:accept:{app_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"league:reject:{app_id}"),
    ], _back_row("comp:back:apps", "◀️ К заявкам")])
    await call.message.edit_text(
        f"📩 Заявка от ID <code>{app['user_id']}</code>:\n\n{hd.quote(app['text_reason'] or 'Без текста')}",
        parse_mode="HTML", reply_markup=kb); await call.answer()


@router.callback_query(F.data.startswith("league:accept:"))
async def app_accept(call: CallbackQuery, bot: Bot):
    if not has_management_permission(call.from_user.id, "can_review_apps"):
        await call.answer("Нет права", show_alert=True); return
    app_id = int(call.data.rsplit(":", 1)[-1]); conn = get_db()
    app = conn.execute("SELECT * FROM league_applications WHERE id = ?", (app_id,)).fetchone()
    composition = get_user_league(call.from_user.id)
    if not app or not composition or app["league_id"] != composition["id"] or not _fill_slot(conn, app["league_id"], app["user_id"]):
        conn.close(); await call.answer("Нет места, игрок уже вступил или заявка устарела", show_alert=True); return
    leader = conn.execute(
        "SELECT * FROM league_members WHERE league_id = ? AND slot_index = 1",
        (app["league_id"],),
    ).fetchone()
    conn.execute("DELETE FROM league_applications WHERE user_id = ?", (app["user_id"],))
    conn.execute("DELETE FROM league_invites WHERE invitee_id = ?", (app["user_id"],))
    conn.commit(); conn.close()
    lft_disable(app["user_id"])

    player = sqlite3.connect("vigarik.db"); player.row_factory = sqlite3.Row
    me = player.execute("SELECT game_nick, player_tag FROM members WHERE user_id = ?",
                        (app["user_id"],)).fetchone()
    player.close()
    my_nick = hd.quote(str(me["game_nick"] if me and me["game_nick"] else app["user_id"]))
    my_tag = "@тестер" if is_hidden_tester(app["user_id"]) else (
        me["player_tag"] if me and me["player_tag"] else "—")
    team_name = hd.quote(str(composition["name"]))
    team_tag = hd.quote(str(composition["tag"]))
    leader_tag = (leader["player_tag"] if leader and leader["player_tag"] else "—")
    leader_nick = (leader["game_nick"] if leader and leader["game_nick"] else "—")

    try:
        await call.message.edit_text(
            "✅ Игрок принят в состав!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row("comp:back:apps", "◀️ К заявкам")]),
        )
    except Exception:
        pass
    await call.answer()
    try:
        await bot.send_message(
            int(app["user_id"]),
            f"✅ Ты вступил в состав <b>{team_name} [{team_tag}]</b>!\n"
            f"Тег лидера: <b>{hd.quote(str(leader_tag))}</b> ({hd.quote(str(leader_nick))})",
            parse_mode="HTML",
        )
    except Exception:
        pass
    if leader and leader["user_id"]:
        try:
            await bot.send_message(
                int(leader["user_id"]),
                f"✅ <b>{my_nick}</b> вступил в твой состав. Его тег: <b>{hd.quote(str(my_tag))}</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("league:reject:"))
async def app_reject(call: CallbackQuery):
    if not has_management_permission(call.from_user.id, "can_review_apps"):
        await call.answer("Нет права", show_alert=True); return
    app_id = int(call.data.rsplit(":", 1)[-1]); conn = get_db()
    composition = get_user_league(call.from_user.id)
    cursor = conn.execute(
        "DELETE FROM league_applications WHERE id = ? AND league_id = ?",
        (app_id, composition["id"]),
    )
    deleted = cursor.rowcount > 0
    conn.commit(); conn.close()
    if not deleted:
        await call.answer("Заявка устарела", show_alert=True)
        return
    await call.message.edit_text(
        "✅ Заявка отклонена.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row("comp:back:apps", "◀️ К заявкам")]),
    ); await call.answer()
