"""Система составов. Внутреннее историческое имя модуля/callback `league` сохранено."""
import sqlite3

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.markdown import html_decoration as hd

from database import get_member
from league.league_db import (
    DEPUTY_PERMISSIONS, dissolve_league, get_connection as get_db,
    get_league_members, get_user_league, has_management_permission,
    init_league_db, leave_league, remove_deputy, set_deputy,
    toggle_deputy_permission, transfer_leadership,
)
from utils.keyboards import main_menu

init_league_db()
router = Router()

PERMISSION_LABELS = {
    "can_review_apps": "📋 Работа с заявками",
    "can_invite": "➕ Приглашение игроков",
    "can_kick": "🚪 Исключение участников",
    "can_toggle_open": "🔒 Управление набором",
}


class LeagueStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_tag = State()
    waiting_invite_target = State()
    waiting_apply_text = State()


def _input_keyboard():
    return _reply([["◀️ Назад", "❌ Отмена"]])


def _back_row(destination: str, label: str = "◀️ Назад"):
    return [InlineKeyboardButton(text=label, callback_data=destination)]


def _reply(rows):
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=text) for text in row] for row in rows],
        resize_keyboard=True,
    )


def _root_keyboard(composition, user_id: int):
    if not composition:
        return _reply([
            ["🌍 Все составы"],
            ["📝 Подать заявку в состав"],
            ["📩 Мои заявки", "📥 Приглашения в состав"],
            ["➕ Создать состав"],
            ["◀️ Назад в главное меню"],
        ])
    is_leader = int(composition["leader_id"]) == int(user_id)
    is_deputy = bool(composition["is_deputy"])
    try:
        verified = int(composition["is_verified"] or 0) == 1
    except Exception:
        verified = False
    if is_leader:
        leagues_btn = "⚔️ Лиги" if verified else "✅ Верифицировать состав"
        return _reply([
            ["🌍 Все составы", leagues_btn],
            ["⚙️ Настройка состава", "📋 Заявки в состав"],
            ["📜 История матчей"],
            ["◀️ Назад в главное меню"],
        ])
    if is_deputy:
        rows = [["🌍 Все составы"], ["⚙️ Настройка состава"]]
        if composition["can_review_apps"]:
            rows.append(["📋 Заявки в состав"])
        rows.append(["📜 История матчей"])
        rows.append(["◀️ Назад в главное меню"])
        return _reply(rows)
    return _reply([
        ["🌍 Все составы"],
        ["👥 Участники состава", "🚪 Выйти из состава"],
        ["📜 История матчей"],
        ["◀️ Назад в главное меню"],
    ])


def _settings_keyboard(composition, user_id: int):
    is_leader = int(composition["leader_id"]) == int(user_id)
    rows = [
        ["👥 Участники состава"] + (
            ["🔒 Закрыть набор / Открыть набор"]
            if is_leader or composition["can_toggle_open"] else []
        ),
        ["👑 Передать лидерство", "🛡 Управление заместителями"] if is_leader else [],
        (["🚪 Выгнать участника"] if is_leader or composition["can_kick"] else [])
        + (["➕ Пригласить игрока"] if is_leader or composition["can_invite"] else []),
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
        try:
            verified = int(composition["is_verified"] or 0) == 1
        except Exception:
            verified = False
        try:
            mmr = int(composition["mmr"] or 0)
        except Exception:
            mmr = 0
        verify_line = "🟠 Верифицирован ✅" if verified else "⚪️ Не верифицирован"
        text = (
            f"🏆 <b>Ваш состав: {hd.quote(composition['name'])} "
            f"[{hd.quote(composition['tag'])}]</b>\n"
            f"Статус набора: {status}\nРоль: {role}\n"
            f"{verify_line} | 🌟 MMR: {mmr}"
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


def _main_member(user_id: int):
    conn = sqlite3.connect("vigarik.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM members WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row


def _fill_slot(conn, league_id: int, user_id: int) -> bool:
    if conn.execute("SELECT 1 FROM league_members WHERE user_id = ?", (user_id,)).fetchone():
        return False
    slot = conn.execute(
        "SELECT * FROM league_members WHERE league_id = ? AND user_id IS NULL ORDER BY slot_index LIMIT 1",
        (league_id,),
    ).fetchone()
    if not slot:
        return False
    user = _main_member(user_id)
    conn.execute("""
        UPDATE league_members SET user_id = ?, game_nick = ?, player_tag = ?, username = ?,
            role = 'участник', trophies_record = ?, joined_at = CURRENT_TIMESTAMP WHERE id = ?
    """, (
        user_id,
        user["game_nick"] if user and user["game_nick"] else "Игрок",
        user["player_tag"] if user and user["player_tag"] else "#N/A",
        user["username"] if user and user["username"] else "",
        user["trophies"] if user and "trophies" in user.keys() else 0,
        slot["id"],
    ))
    return True


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


# ─── Все составы / заявки ────────────────────────────────────────────────────
async def _send_all(message: Message, edit: bool = False):
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT l.*, (SELECT COUNT(*) FROM league_members m
            WHERE m.league_id = l.id AND m.user_id IS NOT NULL) AS count_members
            FROM leagues l ORDER BY COALESCE(l.is_verified, 0) DESC, COALESCE(l.mmr, 0) DESC, l.id DESC
        """).fetchall()
    except Exception:
        rows = conn.execute("""
            SELECT l.*, (SELECT COUNT(*) FROM league_members m
            WHERE m.league_id = l.id AND m.user_id IS NOT NULL) AS count_members
            FROM leagues l ORDER BY count_members DESC, l.id DESC
        """).fetchall()
    conn.close()
    builder = InlineKeyboardBuilder()
    for row in rows:
        try:
            verified = int(row["is_verified"] or 0) == 1
        except Exception:
            verified = False
        try:
            mmr = int(row["mmr"] or 0)
        except Exception:
            mmr = 0
        mark = "🟠 " if verified else ""
        name = str(row['name'])
        if len(name) > 18:
            name = name[:17] + "…"
        builder.button(text=f"{mark}{name} [{row['tag']}] | {mmr}MMR🌟",
                       callback_data=f"league:info:{row['id']}")
    builder.button(text="◀️ В составы", callback_data="league:back_root")
    builder.adjust(1)
    text = ("🌍 <b>Все составы</b>\n🟠 — верифицирован (может играть скримы)"
            if rows else "📭 Составов пока нет.")
    if edit:
        await message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())


@router.message(F.text.in_({"🌍 Все составы", "🌍 Все лиги", "📝 Подать заявку в состав", "📝 Подать заявку в лигу"}))
async def all_compositions(message: Message):
    await _send_all(message)


@router.callback_query(F.data == "league:all_list")
async def all_compositions_callback(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await _send_all(call.message, edit=True); await call.answer()


@router.callback_query(F.data.startswith("league:info:"))
async def composition_info(call: CallbackQuery):
    league_id = int(call.data.rsplit(":", 1)[-1])
    conn = get_db(); composition = conn.execute("SELECT * FROM leagues WHERE id = ?", (league_id,)).fetchone()
    members = get_league_members(league_id)
    conn.close()
    if not composition:
        await call.answer("Состав не найден", show_alert=True); return
    try:
        verified = int(composition["is_verified"] or 0) == 1
    except Exception:
        verified = False
    try:
        mmr = int(composition["mmr"] or 0)
    except Exception:
        mmr = 0
    lines = [f"🏆 <b>{hd.quote(composition['name'])} [{hd.quote(composition['tag'])}]</b>",
             f"{'🟠 Верифицирован ✅' if verified else '⚪️ Не верифицирован'} | 🌟 MMR: {mmr}"]
    for member in members:
        if not member["user_id"]:
            lines.append(f"{member['slot_index']}. — Свободное место —")
            continue
        role = "👑 Лидер" if member["role"] == "лидер" else (
            "🛡 Заместитель" if member["role"] == "заместитель" else "👤 Участник"
        )
        lines.append(f"{member['slot_index']}. {hd.quote(member['game_nick'] or 'Игрок')} — {role}")
    buttons = [[InlineKeyboardButton(text="◀️ Ко всем составам", callback_data="league:all_list")]]
    if not get_user_league(call.from_user.id) and composition["is_open"]:
        buttons.insert(0, [InlineKeyboardButton(text="📝 Подать заявку", callback_data=f"league:apply_send:{league_id}")])
    await call.message.edit_text("\n".join(lines), parse_mode="HTML",
                                 reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)); await call.answer()


@router.callback_query(F.data.startswith("league:apply_send:"))
async def apply_start(call: CallbackQuery, state: FSMContext):
    if get_user_league(call.from_user.id):
        await call.answer("Ты уже состоишь в составе", show_alert=True); return
    await state.update_data(target_league_id=int(call.data.rsplit(":", 1)[-1]))
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


# ─── Просмотр участников / набор ─────────────────────────────────────────────
@router.message(F.text.in_({"👥 Участники состава", "👥 Состав лиги"}))
async def view_members(message: Message):
    composition = get_user_league(message.from_user.id)
    if not composition:
        await message.answer("❌ Ты не состоишь в составе."); return
    lines = [f"👥 <b>Участники состава {hd.quote(composition['name'])}</b>"]
    for member in get_league_members(composition["id"]):
        if not member["user_id"]:
            lines.append(f"{member['slot_index']}. — Свободное место —"); continue
        role = "👑 Лидер" if member["role"] == "лидер" else (
            "🛡 Заместитель" if member["role"] == "заместитель" else "👤 Участник"
        )
        lines.append(f"{member['slot_index']}. {hd.quote(member['game_nick'] or 'Игрок')} — {role}")
    is_manager = bool(composition["is_deputy"] or int(composition["leader_id"]) == message.from_user.id)
    destination = "comp:back:settings" if is_manager else "league:back_root"
    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row(destination)]),
    )


@router.message(F.text == "🔒 Закрыть набор / Открыть набор")
async def toggle_open(message: Message, state: FSMContext):
    if not has_management_permission(message.from_user.id, "can_toggle_open"):
        await message.answer("❌ Нет права управлять набором."); return
    composition = get_user_league(message.from_user.id); value = 0 if composition["is_open"] else 1
    conn = get_db(); conn.execute("UPDATE leagues SET is_open = ? WHERE id = ?", (value, composition["id"]))
    conn.commit(); conn.close()
    await message.answer(f"✅ Набор теперь {'открыт' if value else 'закрыт'}.")
    await open_settings(message)


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
async def invitation_accept(call: CallbackQuery):
    if get_user_league(call.from_user.id):
        await call.answer("Ты уже состоишь в составе", show_alert=True); return
    invite_id = int(call.data.rsplit(":", 1)[-1]); conn = get_db()
    invite = conn.execute("SELECT * FROM league_invites WHERE id = ? AND invitee_id = ?",
                          (invite_id, call.from_user.id)).fetchone()
    if not invite or not _fill_slot(conn, invite["league_id"], call.from_user.id):
        conn.close(); await call.answer("Нет места или приглашение устарело", show_alert=True); return
    conn.execute("DELETE FROM league_invites WHERE invitee_id = ?", (call.from_user.id,))
    conn.execute("DELETE FROM league_applications WHERE user_id = ?", (call.from_user.id,))
    conn.commit(); conn.close()
    await call.message.edit_text(
        "✅ Ты вступил в состав!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row("league:back_root", "◀️ В составы")]),
    ); await call.answer()


@router.callback_query(F.data.startswith("league:invite_reject:"))
async def invitation_reject(call: CallbackQuery):
    invite_id = int(call.data.rsplit(":", 1)[-1]); conn = get_db()
    conn.execute("DELETE FROM league_invites WHERE id = ? AND invitee_id = ?", (invite_id, call.from_user.id))
    conn.commit(); conn.close()
    await call.message.edit_text(
        "✅ Приглашение отклонено.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row("comp:back:invites", "◀️ К приглашениям")]),
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
async def app_accept(call: CallbackQuery):
    if not has_management_permission(call.from_user.id, "can_review_apps"):
        await call.answer("Нет права", show_alert=True); return
    app_id = int(call.data.rsplit(":", 1)[-1]); conn = get_db()
    app = conn.execute("SELECT * FROM league_applications WHERE id = ?", (app_id,)).fetchone()
    composition = get_user_league(call.from_user.id)
    if not app or not composition or app["league_id"] != composition["id"] or not _fill_slot(conn, app["league_id"], app["user_id"]):
        conn.close(); await call.answer("Нет места, игрок уже вступил или заявка устарела", show_alert=True); return
    conn.execute("DELETE FROM league_applications WHERE user_id = ?", (app["user_id"],))
    conn.execute("DELETE FROM league_invites WHERE invitee_id = ?", (app["user_id"],))
    conn.commit(); conn.close()
    await call.message.edit_text(
        "✅ Игрок принят в состав!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row("comp:back:apps", "◀️ К заявкам")]),
    ); await call.answer()


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


# ─── Исключение участника ─────────────────────────────────────────────────────
async def _show_kick_choices(message: Message, user_id: int, edit: bool = False):
    composition = get_user_league(user_id)
    if not composition or not has_management_permission(user_id, "can_kick"):
        if edit:
            await message.edit_text("❌ Нет права исключать участников.")
        else:
            await message.answer("❌ Нет права исключать участников.")
        return
    buttons = [[InlineKeyboardButton(text=f"🚪 {m['game_nick']}",
                                     callback_data=f"comp:kick:{m['user_id']}")]
               for m in get_league_members(composition["id"], True)
               if int(m["user_id"]) not in {int(composition["leader_id"]), user_id}]
    buttons.append(_back_row("comp:back:settings", "◀️ В настройки"))
    text = "Кого исключить из состава?" if len(buttons) > 1 else "📭 Некого исключать."
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.message(F.text == "🚪 Выгнать участника")
async def kick_choose(message: Message):
    await _show_kick_choices(message, message.from_user.id)


@router.callback_query(F.data == "comp:back:kick")
async def kick_choose_callback(call: CallbackQuery):
    await _show_kick_choices(call.message, call.from_user.id, edit=True)
    await call.answer()


@router.callback_query(F.data.startswith("comp:kick:"))
async def kick_confirm(call: CallbackQuery):
    target = int(call.data.rsplit(":", 1)[-1])
    if not has_management_permission(call.from_user.id, "can_kick"):
        await call.answer("Нет права", show_alert=True); return
    kb = InlineKeyboardMarkup(inline_keyboard=[[ 
        InlineKeyboardButton(text="✅ Да, исключить", callback_data=f"comp:kickyes:{target}"),
    ], _back_row("comp:back:kick")])
    await call.message.edit_text("Вы уверены, что хотите исключить участника?", reply_markup=kb); await call.answer()


@router.callback_query(F.data.startswith("comp:kickyes:"))
async def kick_yes(call: CallbackQuery, bot: Bot):
    target = int(call.data.rsplit(":", 1)[-1]); composition = get_user_league(call.from_user.id)
    if not composition or not has_management_permission(call.from_user.id, "can_kick"):
        await call.answer("Нет права", show_alert=True); return
    if target == int(composition["leader_id"]):
        await call.answer("Нельзя исключить лидера", show_alert=True); return
    conn = get_db(); row = conn.execute("SELECT game_nick FROM league_members WHERE league_id = ? AND user_id = ?",
                                       (composition["id"], target)).fetchone()
    conn.close()
    if not row or not leave_league(target):
        await call.answer("Участник уже вышел", show_alert=True); return
    await call.message.edit_text(
        f"✅ {hd.quote(row['game_nick'] or str(target))} исключён из состава.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row("comp:back:settings", "◀️ В настройки")]),
    ); await call.answer()
    try: await bot.send_message(target, f"🚪 Ты исключён из состава {composition['name']}.")
    except Exception: pass


# ─── Роспуск / выход / передача ───────────────────────────────────────────────
@router.message(F.text == "💥 Распустить состав")
async def dissolve_confirm(message: Message):
    composition = get_user_league(message.from_user.id)
    if not composition or int(composition["leader_id"]) != message.from_user.id:
        await message.answer("❌ Только лидер может распустить состав."); return
    kb = InlineKeyboardMarkup(inline_keyboard=[[ 
        InlineKeyboardButton(text="✅ Да, распустить", callback_data="comp:dissolve:yes"),
    ], _back_row("comp:back:settings")])
    await message.answer("⚠️ Вы уверены, что хотите распустить состав? Это действие необратимо.", reply_markup=kb)


@router.callback_query(F.data == "comp:dissolve:yes")
async def dissolve_yes(call: CallbackQuery):
    composition = get_user_league(call.from_user.id)
    if not composition or int(composition["leader_id"]) != call.from_user.id:
        await call.answer("Только лидер может распустить состав", show_alert=True); return
    name = composition["name"]; dissolve_league(composition["id"])
    await call.message.edit_text(
        f"✅ Состав {hd.quote(name)} успешно распущен.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row("league:back_root", "◀️ В составы")]),
    ); await call.answer()


def _member_choice(composition, prefix: str):
    back = "comp:back:settings"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"👤 {m['game_nick']}", callback_data=f"{prefix}:{m['user_id']}")]
        for m in get_league_members(composition["id"], True)
        if int(m["user_id"]) != int(composition["leader_id"])
    ] + [_back_row(back, "◀️ В настройки")])


async def _show_transfer_choices(message: Message, user_id: int, edit: bool = False):
    composition = get_user_league(user_id)
    if not composition or int(composition["leader_id"]) != user_id:
        if edit:
            await message.edit_text("❌ Только лидер может передать лидерство.")
        else:
            await message.answer("❌ Только лидер может передать лидерство.")
        return
    if len(get_league_members(composition["id"], True)) <= 1:
        text = "❌ В составе нет участника, которому можно передать лидерство."
        markup = InlineKeyboardMarkup(inline_keyboard=[_back_row("comp:back:settings", "◀️ В настройки")])
    else:
        text = "Кому передать лидерство?"
        markup = _member_choice(composition, "comp:transfer")
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.message(F.text.in_({"👑 Передать лидерство", "👑 Передать лидерку"}))
async def transfer_choose(message: Message):
    await _show_transfer_choices(message, message.from_user.id)


@router.callback_query(F.data == "comp:back:transfer")
async def transfer_choose_callback(call: CallbackQuery):
    await _show_transfer_choices(call.message, call.from_user.id, edit=True)
    await call.answer()


@router.callback_query(F.data.startswith("comp:transfer:"))
async def transfer_confirm(call: CallbackQuery):
    target = int(call.data.rsplit(":", 1)[-1])
    kb = InlineKeyboardMarkup(inline_keyboard=[[ 
        InlineKeyboardButton(text="✅ Да", callback_data=f"comp:transferyes:{target}"),
    ], _back_row("comp:back:transfer")])
    await call.message.edit_text("Подтвердить передачу лидерства этому участнику?", reply_markup=kb); await call.answer()


@router.callback_query(F.data.startswith("comp:transferyes:"))
async def transfer_yes(call: CallbackQuery, bot: Bot):
    target = int(call.data.rsplit(":", 1)[-1]); composition = get_user_league(call.from_user.id)
    if not composition or int(composition["leader_id"]) != call.from_user.id:
        await call.answer("Нет права", show_alert=True); return
    if not transfer_leadership(composition["id"], call.from_user.id, target):
        await call.answer("Передача не выполнена", show_alert=True); return
    await call.message.edit_text(
        "✅ Лидерство успешно передано.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row("league:back_root", "◀️ В составы")]),
    ); await call.answer()
    try: await bot.send_message(target, f"👑 Тебе передано лидерство составом {composition['name']}.")
    except Exception: pass


async def _show_leave_prompt(message: Message, user_id: int, edit: bool = False):
    composition = get_user_league(user_id)
    if not composition:
        if edit:
            await message.edit_text("❌ Ты не состоишь в составе.")
        else:
            await message.answer("❌ Ты не состоишь в составе.")
        return
    is_leader = int(composition["leader_id"]) == user_id
    occupied = get_league_members(composition["id"], True)
    if not is_leader:
        kb = InlineKeyboardMarkup(inline_keyboard=[[ 
            InlineKeyboardButton(text="✅ Да, выйти", callback_data="comp:leave:yes"),
        ], _back_row("league:back_root", "◀️ В составы")])
        text = "Вы уверены, что хотите выйти из состава?"
    elif len(occupied) == 1:
        kb = InlineKeyboardMarkup(inline_keyboard=[[ 
            InlineKeyboardButton(text="✅ Да, распустить", callback_data="comp:dissolve:yes"),
        ], _back_row("comp:back:settings", "◀️ В настройки")])
        text = "Ты единственный участник. Выйти можно только распустив состав. Продолжить?"
    else:
        kb = _member_choice(composition, "comp:leaveto")
        text = "Кому передать состав перед выходом?"
    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


@router.message(F.text.in_({"🚪 Выйти из состава", "🚪 Выйти из лиги"}))
async def leave_start(message: Message):
    await _show_leave_prompt(message, message.from_user.id)


@router.callback_query(F.data == "comp:back:leave")
async def leave_start_callback(call: CallbackQuery):
    await _show_leave_prompt(call.message, call.from_user.id, edit=True)
    await call.answer()


@router.callback_query(F.data == "comp:leave:yes")
async def leave_yes(call: CallbackQuery):
    if not leave_league(call.from_user.id):
        await call.answer("Не удалось выйти", show_alert=True); return
    await call.message.edit_text(
        "✅ Ты успешно вышел из состава.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row("league:back_root", "◀️ В составы")]),
    ); await call.answer()


@router.callback_query(F.data.startswith("comp:leaveto:"))
async def leader_leave_confirm(call: CallbackQuery):
    target = int(call.data.rsplit(":", 1)[-1])
    kb = InlineKeyboardMarkup(inline_keyboard=[[ 
        InlineKeyboardButton(text="✅ Передать и выйти", callback_data=f"comp:leaveleadyes:{target}"),
    ], _back_row("comp:back:leave")])
    await call.message.edit_text("Передать этому участнику состав и выйти?", reply_markup=kb); await call.answer()


@router.callback_query(F.data.startswith("comp:leaveleadyes:"))
async def leader_leave_yes(call: CallbackQuery, bot: Bot):
    target = int(call.data.rsplit(":", 1)[-1]); composition = get_user_league(call.from_user.id)
    if not composition or int(composition["leader_id"]) != call.from_user.id:
        await call.answer("Нет права", show_alert=True); return
    if not transfer_leadership(composition["id"], call.from_user.id, target, remove_old=True):
        await call.answer("Не удалось передать состав", show_alert=True); return
    await call.message.edit_text(
        "✅ Состав передан, ты успешно вышел.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row("league:back_root", "◀️ В составы")]),
    ); await call.answer()
    try: await bot.send_message(target, f"👑 Тебе передан состав {composition['name']} после выхода лидера.")
    except Exception: pass


@router.callback_query(F.data == "comp:cancel")
async def cancel_action(call: CallbackQuery):
    composition = get_user_league(call.from_user.id)
    is_manager = bool(composition and (composition["is_deputy"] or int(composition["leader_id"]) == call.from_user.id))
    destination = "comp:back:settings" if is_manager else "league:back_root"
    await call.message.edit_text(
        "❌ Действие отменено.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row(destination)]),
    ); await call.answer()


# ─── Заместители ──────────────────────────────────────────────────────────────
def _deputy_panel(league_id: int, user_id: int):
    member = next((m for m in get_league_members(league_id, True) if int(m["user_id"]) == user_id), None)
    if not member:
        return None
    rows = []
    for permission in DEPUTY_PERMISSIONS:
        marker = "✅" if member[permission] else "❌"
        rows.append([InlineKeyboardButton(text=f"{marker} {PERMISSION_LABELS[permission]}",
                                          callback_data=f"comp:depperm:{user_id}:{permission}")])
    rows.append([InlineKeyboardButton(text="🗑 Снять заместителя", callback_data=f"comp:depremove:{user_id}")])
    rows.append([InlineKeyboardButton(text="◀️ К списку", callback_data="comp:deputies:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _deputy_list_keyboard(composition, leader_id: int):
    buttons = []
    for member in get_league_members(composition["id"], True):
        if int(member["user_id"]) == leader_id:
            continue
        marker = "🛡" if member["role"] == "заместитель" else "👤"
        buttons.append([InlineKeyboardButton(text=f"{marker} {member['game_nick']}",
                                             callback_data=f"comp:deputy:{member['user_id']}")])
    buttons.append(_back_row("comp:back:settings", "◀️ В настройки"))
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(F.text == "🛡 Управление заместителями")
async def deputies(message: Message):
    composition = get_user_league(message.from_user.id)
    if not composition or int(composition["leader_id"]) != message.from_user.id:
        await message.answer("❌ Назначать заместителей может только лидер."); return
    members = get_league_members(composition["id"], True)
    text = ("🛡 Выбери участника для назначения или настройки заместителя:"
            if len(members) > 1 else "📭 В составе нет участников для назначения.")
    await message.answer(text, reply_markup=_deputy_list_keyboard(composition, message.from_user.id))


@router.callback_query(F.data == "comp:deputies:list")
async def deputies_callback(call: CallbackQuery):
    composition = get_user_league(call.from_user.id)
    if not composition or int(composition["leader_id"]) != call.from_user.id:
        await call.answer("Нет права", show_alert=True); return
    members = get_league_members(composition["id"], True)
    text = "Выбери участника:" if len(members) > 1 else "📭 В составе нет участников для назначения."
    await call.message.edit_text(
        text,
        reply_markup=_deputy_list_keyboard(composition, call.from_user.id),
    ); await call.answer()


@router.callback_query(F.data.startswith("comp:deputy:"))
async def deputy_select(call: CallbackQuery, bot: Bot):
    target = int(call.data.rsplit(":", 1)[-1]); composition = get_user_league(call.from_user.id)
    if not composition or int(composition["leader_id"]) != call.from_user.id:
        await call.answer("Нет права", show_alert=True); return
    target_row = next((m for m in get_league_members(composition["id"], True)
                       if int(m["user_id"]) == target), None)
    if not target_row:
        await call.answer("Участник не найден", show_alert=True); return
    newly = target_row["role"] != "заместитель"
    if newly and not set_deputy(composition["id"], target):
        await call.answer("Не удалось назначить", show_alert=True); return
    text = (
        f"✅ {hd.quote(target_row['game_nick'])} назначен заместителем.\n\n"
        if newly else f"🛡 Настройка заместителя {hd.quote(target_row['game_nick'])}.\n\n"
    ) + "Какие права хотите выдать заместителю?\n✅ — выдано, ❌ — не выдано. Нажатие переключает право."
    await call.message.edit_text(text, parse_mode="HTML",
                                 reply_markup=_deputy_panel(composition["id"], target)); await call.answer()
    if newly:
        try: await bot.send_message(target, f"🛡 Ты назначен заместителем состава {composition['name']}.")
        except Exception: pass


@router.callback_query(F.data.startswith("comp:depperm:"))
async def deputy_permission(call: CallbackQuery):
    parts = call.data.split(":", 3); target, permission = int(parts[2]), parts[3]
    composition = get_user_league(call.from_user.id)
    if not composition or int(composition["leader_id"]) != call.from_user.id:
        await call.answer("Нет права", show_alert=True); return
    value = toggle_deputy_permission(composition["id"], target, permission)
    if value is None:
        await call.answer("Заместитель не найден", show_alert=True); return
    await call.message.edit_reply_markup(reply_markup=_deputy_panel(composition["id"], target))
    await call.answer("Право выдано ✅" if value else "Право отозвано ❌")


@router.callback_query(F.data.startswith("comp:depremove:"))
async def deputy_remove_confirm(call: CallbackQuery):
    target = int(call.data.rsplit(":", 1)[-1])
    composition = get_user_league(call.from_user.id)
    if not composition or int(composition["leader_id"]) != call.from_user.id:
        await call.answer("Нет права", show_alert=True); return
    kb = InlineKeyboardMarkup(inline_keyboard=[[ 
        InlineKeyboardButton(text="✅ Да, снять", callback_data=f"comp:depremoveyes:{target}"),
        InlineKeyboardButton(text="❌ Нет", callback_data=f"comp:deputy:{target}"),
    ]])
    await call.message.edit_text("Снять этого заместителя? История назначения будет удалена.", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("comp:depremoveyes:"))
async def deputy_remove_yes(call: CallbackQuery, bot: Bot):
    target = int(call.data.rsplit(":", 1)[-1]); composition = get_user_league(call.from_user.id)
    if not composition or int(composition["leader_id"]) != call.from_user.id:
        await call.answer("Нет права", show_alert=True); return
    remove_deputy(composition["id"], target)
    await call.message.edit_text(
        "✅ Заместитель снят. Память о назначении удалена.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row("comp:deputies:list", "◀️ К заместителям")]),
    ); await call.answer()
    try: await bot.send_message(target, f"ℹ️ Ты больше не заместитель состава {composition['name']}.")
    except Exception: pass