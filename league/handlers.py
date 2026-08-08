import os
import sqlite3
from aiogram import F, Router, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from league.league_db import get_connection as get_db, init_league_db

# Инициализация базы данных при старте модуля
init_league_db()

router = Router()
DB_PATH = os.path.join("league", "league.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


class LeagueStates(StatesGroup):
    waiting_for_league_name = State()
    waiting_for_league_tag = State()
    waiting_invite_target = State()
    waiting_invite_confirm = State()
    waiting_kick_slot = State()
    waiting_kick_confirm = State()
    waiting_transfer_slot = State()
    waiting_transfer_confirm = State()
    waiting_apply_text = State()
    waiting_apply_confirm = State()


# Вспомогательная функция для получения лиги пользователя
def get_user_league(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT l.*, m.slot_index, m.role as member_role 
        FROM league_members m 
        JOIN leagues l ON m.league_id = l.id 
        WHERE m.user_id = ?
    """, (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res

# 1. Открытие папки "Лиги💀"
@router.message(F.text == "Лиги💀")
async def open_league_root(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    league_data = get_user_league(user_id)

    if not league_data:
        text = "Кажется тебя ещё нету ни в одной лиге. Время вступить в одну из них, или создать свою!"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🌍 Все лиги 💀 (BetaTest)", callback_data="league:all_list")],
            [InlineKeyboardButton(text="📝 Подать заявку в лигу", callback_data="league:apply_list")],
            [InlineKeyboardButton(text="📨 Мои заявки", callback_data="league:my_applications")],
            [InlineKeyboardButton(text="📩 Приглашения в лигу", callback_data="league:invites")],
            [InlineKeyboardButton(text="➕ Создать лигу", callback_data="league:create")]
        ])
        await message.answer(text, reply_markup=keyboard)
    else:
        is_leader = (league_data["slot_index"] == 1)
        status_set = "Открыт ✅" if league_data["is_open"] else "Закрыт ❌"
        text = f"🏰 Ваша лига: <b>{league_data['name']} [{league_data['tag']}]</b>\nСтатус набора: {status_set}"

        if is_leader:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🌍 Все лиги 💀 (BetaTest)", callback_data="league:all_list")],
                [InlineKeyboardButton(text="👥 Состав лиги", callback_data="league:my_team_info")],
                [InlineKeyboardButton(text="📋 Просмотреть заявки", callback_data="league:view_requests")],
                [InlineKeyboardButton(text="➕ Пригласить игрока", callback_data="league:invite_member")],
                [InlineKeyboardButton(text="🚪 Выгнать участника", callback_data="league:kick_member")],
                [InlineKeyboardButton(text="👑 Передать лидерство", callback_data="league:transfer_lead")],
                [InlineKeyboardButton(text="🔒 Закрыть набор / Открыть набор", callback_data="league:toggle_open")]
            ])
        else:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="👥 Состав лиги", callback_data="league:my_team_info")],
                [InlineKeyboardButton(text="🌍 Все лиги 💀 (BetaTest)", callback_data="league:all_list")],
                [InlineKeyboardButton(text="🚪 Выйти из лиги", callback_data="league:leave")]
            ])
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


# --- СОЗДАНИЕ ЛИГИ ---
@router.callback_query(F.data == "league:create")
async def start_create_league(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Напиши полное название лиги (например, ViGarikSQuaD):\n")
    await state.set_state(LeagueStates.waiting_for_league_name)
    await callback.answer()


@router.message(LeagueStates.waiting_for_league_name)
async def process_league_name(message: Message, state: FSMContext):
    await state.update_data(league_name=message.text.strip())
    await message.answer("Напиши сокращенный тег лиги (лимит 5 символов, пример VGSQD):")
    await state.set_state(LeagueStates.waiting_for_league_tag)


@router.message(LeagueStates.waiting_for_league_tag)
async def process_league_tag(message: Message, state: FSMContext):
    tag = message.text.strip()
    if len(tag) > 5:
        await message.answer("❌ Тег слишком длинный! Лимит 5 символов. Попробуй еще раз:")
        return

    data = await state.get_data()
    league_name = data["league_name"]
    user_id = message.from_user.id

    main_conn = sqlite3.connect("vigarik.db")
    main_conn.row_factory = sqlite3.Row
    user_row = main_conn.execute("SELECT * FROM members WHERE user_id = ?", (user_id,)).fetchone()
    main_conn.close()

    game_nick = user_row["game_nick"] if user_row and user_row["game_nick"] else message.from_user.first_name
    player_tag = user_row["player_tag"] if user_row and user_row["player_tag"] else "#N/A"
    username = message.from_user.username or "None"
    trophies = user_row["trophies"] if user_row and "trophies" in user_row.keys() else 0

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO leagues (name, tag, leader_id, is_open) VALUES (?, ?, ?, 1)
    """, (league_name, tag.upper(), user_id))
    league_id = cursor.lastrowid

    cursor.execute("""
        INSERT INTO league_members (league_id, slot_index, user_id, game_nick, player_tag, username, role, trophies_record)
        VALUES (?, 1, ?, ?, ?, ?, 'лидер', ?)
    """, (league_id, user_id, game_nick, player_tag, username, trophies))

    for slot in range(2, 5):
        cursor.execute("""
            INSERT INTO league_members (league_id, slot_index, user_id, game_nick, player_tag, username, role)
            VALUES (?, ?, NULL, '-- Свободное место --', '', '', 'участник')
        """, (league_id, slot))

    conn.commit()
    conn.close()
    await state.clear()

    await message.answer(f"✅ Лига <b>{league_name} [{tag.upper()}]</b> успешно создана!", parse_mode="HTML")

    # --- ОТКРЫТИЕ / ЗАКРЫТИЕ НАБОРА ---
    @router.callback_query(F.data == "league:toggle_open")
    async def toggle_league_open(callback: CallbackQuery):
        user_id = callback.from_user.id
        conn = get_db()
        cursor = conn.cursor()
        league = cursor.execute(
            "SELECT l.* FROM leagues l JOIN league_members m ON l.id = m.league_id WHERE m.user_id = ? AND m.slot_index = 1",
            (user_id,)).fetchone()
        if league:
            new_status = 0 if league["is_open"] == 1 else 1
            cursor.execute("UPDATE leagues SET is_open = ? WHERE id = ?", (new_status, league["id"]))
            conn.commit()
            status_str = "Открыт ✅" if new_status == 1 else "Закрыт ❌"
            await callback.answer(f"Статус набора изменен на: {status_str}", show_alert=True)
        conn.close()

        league_data = get_user_league(user_id)
        status_set = "Открыт ✅" if league_data["is_open"] else "Закрыт ❌"
        text = f"🏰 Ваша лига: <b>{league_data['name']} [{league_data['tag']}]</b>\nСтатус набора: {status_set}"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👥 Состав лиги", callback_data="league:my_team_info")],
            [InlineKeyboardButton(text="📋 Просмотреть заявки", callback_data="league:view_requests")],
            [InlineKeyboardButton(text="Пригласить", callback_data="league:invite_member")],
            [InlineKeyboardButton(text="Выгнать участника", callback_data="league:kick_member")],
            [InlineKeyboardButton(text="Передать лидерство", callback_data="league:transfer_lead")],
            [InlineKeyboardButton(text="🔒 Закрыть набор / Открыть набор", callback_data="league:toggle_open")]
        ])
        try:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        except:
            pass

    @router.callback_query(F.data == "league:all_list")
    async def show_all_leagues(callback: CallbackQuery):
        conn = get_db()
        cursor = conn.cursor()
        leagues = cursor.execute("""
            SELECT l.*, 
            (SELECT COUNT(*) FROM league_members WHERE league_id = l.id AND user_id IS NOT NULL) as count_members
            FROM leagues l
            ORDER BY count_members DESC, l.id DESC
        """).fetchall()
        conn.close()

        builder = InlineKeyboardBuilder()
        for l in leagues:
            count = l["count_members"]
            status_suffix = " (Заполнена)" if count >= 4 else ""
            builder.button(text=f"{l['name']} [{l['tag']}] ({count}/4){status_suffix}",
                           callback_data=f"league:info:{l['id']}")

        builder.button(text="◀️ Назад", callback_data="league:back_root")
        builder.adjust(1)

        text = "📋 Список всех лиг (можно открыть любую и посмотреть состав):"
        await callback.message.edit_text(text, reply_markup=builder.as_markup())
        await callback.answer()

    @router.callback_query(F.data == "league:apply_list")
    async def show_leagues_to_apply(callback: CallbackQuery):
        conn = get_db()
        cursor = conn.cursor()
        leagues = cursor.execute("""
            SELECT l.*, 
            (SELECT COUNT(*) FROM league_members WHERE league_id = l.id AND user_id IS NOT NULL) as count_members
            FROM leagues l
            WHERE l.is_open = 1
            GROUP BY l.id
            HAVING count_members < 4
            ORDER BY count_members DESC
        """).fetchall()
        conn.close()

        builder = InlineKeyboardBuilder()
        for l in leagues:
            count = l["count_members"]
            builder.button(text=f"{l['name']} [{l['tag']}] ({count}/4)", callback_data=f"league:info:{l['id']}")
        builder.button(text="◀️ Назад", callback_data="league:back_root")
        builder.adjust(1)

        text = "📋 Выберите лигу для просмотра состава и подачи заявки:"
        await callback.message.edit_text(text, reply_markup=builder.as_markup())
        await callback.answer()

    @router.callback_query(F.data.startswith("league:info:"))
    async def view_league_info(callback: CallbackQuery):
        league_id = int(callback.data.split(":")[-1])
        conn = get_db()
        cursor = conn.cursor()
        league = cursor.execute("SELECT * FROM leagues WHERE id = ?", (league_id,)).fetchone()
        members = cursor.execute("SELECT * FROM league_members WHERE league_id = ? ORDER BY slot_index ASC",
                                 (league_id,)).fetchall()
        conn.close()

        if not league:
            await callback.answer("Лига не найдена!", show_alert=True)
            return

        lines = [f"Лига - {league['name']} [{league['tag']}]\n"]
        main_conn = sqlite3.connect("vigarik.db")
        main_conn.row_factory = sqlite3.Row

        for m in members:
            slot = m["slot_index"]
            if m["user_id"] is None:
                lines.append(f"{slot}. -- Свободное место --")
            else:
                role_label = "Лидер" if m["role"] == "лидер" else "Участник"
                trophies = m["trophies_record"] if m["trophies_record"] else 0
                member_db_row = main_conn.execute("SELECT ranked_elo FROM members WHERE user_id = ?",
                                                  (m["user_id"],)).fetchone()
                current_elo = member_db_row[
                    "ranked_elo"] if member_db_row and "ranked_elo" in member_db_row.keys() else 0
                lines.append(f"{slot}. {m['game_nick']} — {role_label} | {trophies}🏆 | {current_elo} Elo Ranked")

        main_conn.close()
        text = "\n".join(lines)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Обратно к выбору лиги", callback_data="league:apply_list")],
            [InlineKeyboardButton(text="Подать заявку", callback_data=f"league:apply_send:{league_id}")]
        ])
        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer()


@router.callback_query(F.data.startswith("league:apply_send:"))
async def ask_apply_reason(callback: CallbackQuery, state: FSMContext):
    league_id = int(callback.data.split(":")[-1])
    await state.update_data(apply_league_id=league_id)
    await callback.message.answer("Напиши текстовое объяснение, почему именно тебя должны принять в лигу:")
    await state.set_state(LeagueStates.waiting_apply_text)
    await callback.answer()


@router.message(LeagueStates.waiting_apply_text)
async def process_apply_text(message: Message, state: FSMContext):
    reason = message.text.strip()
    await state.update_data(apply_reason=reason)

    data = await state.get_data()
    league_id = data["apply_league_id"]

    conn = get_db()
    league = conn.execute("SELECT * FROM leagues WHERE id = ?", (league_id,)).fetchone()
    conn.close()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Да", callback_data="league:apply_confirm:yes"),
            InlineKeyboardButton(text="Нет", callback_data="league:apply_confirm:no"),
        ]
    ])

    text = f"Твое сообщение:\n<i>\"{reason}\"</i>\n\nВы уверены, что хотите отправить заявку в лигу <b>\"{league['name']}\"</b>?"
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    await state.set_state(LeagueStates.waiting_apply_confirm)


@router.callback_query(F.data.startswith("league:apply_confirm:"))
async def confirm_apply(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":")[-1]
    data = await state.get_data()

    if action == "no":
        await state.clear()
        await callback.message.edit_text("❌ Отправка заявки отменена.")
        return

    league_id = data["apply_league_id"]
    reason = data["apply_reason"]
    user_id = callback.from_user.id

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO league_applications (league_id, user_id, text_reason)
        VALUES (?, ?, ?)
    """, (league_id, user_id, reason))
    conn.commit()
    conn.close()

    await state.clear()
    await callback.message.edit_text("✅ Заявка успешно отправлена лидеру лиги!")


@router.callback_query(F.data == "league:view_requests")
async def view_league_requests(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    league = cursor.execute(
        "SELECT l.* FROM leagues l JOIN league_members m ON l.id = m.league_id WHERE m.user_id = ? AND m.slot_index = 1",
        (user_id,)).fetchone()

    if not league:
        conn.close()
        await callback.answer("У вас нет прав лидера!", show_alert=True)
        return

    cursor.execute("SELECT * FROM league_applications WHERE league_id = ?", (league["id"],))
    apps = cursor.fetchall()
    conn.close()

    if not apps:
        await callback.message.edit_text("📭 На данный момент нет активных заявок в вашу лигу.",
                                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                             [InlineKeyboardButton(text="◀️ Назад", callback_data="league:back_root")]
                                         ]))
        return

    builder = InlineKeyboardBuilder()
    for app in apps:
        main_conn = sqlite3.connect("vigarik.db")
        main_conn.row_factory = sqlite3.Row
        u_row = main_conn.execute("SELECT game_nick FROM members WHERE user_id = ?", (app["user_id"],)).fetchone()
        main_conn.close()
        nick = u_row["game_nick"] if u_row and u_row["game_nick"] else f"ID:{app['user_id']}"
        date_str = app["sent_at"][:10] if "sent_at" in app.keys() and app["sent_at"] else ""

        builder.button(text=f"{nick} | {date_str}", callback_data=f"league:app_detail:{app['id']}")

    builder.button(text="◀️ Назад", callback_data="league:back_root")
    builder.adjust(1)

    await callback.message.edit_text("📋 Список поступивших заявок:", reply_markup=builder.as_markup())


@router.callback_query(F.data == "league:my_applications")
async def show_my_applications(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = get_db()
    cursor = conn.cursor()

    applications = cursor.execute("""
        SELECT a.id as app_id, a.sent_at, l.name as league_name, l.tag as league_tag
        FROM league_applications a
        JOIN leagues l ON a.league_id = l.id
        WHERE a.user_id = ?
        ORDER BY a.sent_at DESC
    """, (user_id,)).fetchall()
    conn.close()

    if not applications:
        await callback.answer("У вас нет активных заявок.", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for app in applications:
        date_str = app["sent_at"][:10] if app["sent_at"] else ""
        btn_text = f"{app['league_name']} {date_str} {app['league_tag']}"
        builder.button(text=btn_text, callback_data=f"league:my_app_detail:{app['app_id']}")

    builder.button(text="◀️ Назад", callback_data="league:back_root")
    builder.adjust(1)

    text = "📋 Ваши отправленные заявки:"
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("league:my_app_detail:"))
async def view_my_app_detail(callback: CallbackQuery):
    app_id = int(callback.data.split(":")[-1])
    conn = get_db()
    cursor = conn.cursor()

    app = cursor.execute("""
        SELECT a.*, l.name as league_name, l.tag as league_tag
        FROM league_applications a
        JOIN leagues l ON a.league_id = l.id
        WHERE a.id = ?
    """, (app_id,)).fetchone()
    conn.close()

    if not app:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    date_str = app["sent_at"] if "sent_at" in app.keys() else "Неизвестно"
    text = (
        f"🏰 Лига: <b>{app['league_name']} [{app['league_tag']}]</b>\n"
        f"📅 Дата отправки: {date_str}\n\n"
        f"💬 Текст вашей заявки:\n<i>\"{app['text_reason']}\"</i>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ К списку заявок", callback_data="league:my_applications")]
    ])

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "league:kick_member")
async def kick_member_start(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    league = cursor.execute(
        "SELECT l.* FROM leagues l JOIN league_members m ON l.id = m.league_id WHERE m.user_id = ? AND m.slot_index = 1",
        (user_id,)).fetchone()

    if not league:
        conn.close()
        await callback.answer("Недостаточно прав!", show_alert=True)
        return

    members = cursor.execute(
        "SELECT * FROM league_members WHERE league_id = ? AND slot_index IN (2, 3, 4) AND user_id IS NOT NULL",
        (league["id"],)).fetchall()
    conn.close()

    if not members:
        await callback.answer("В лиге нет участников для изгнания!", show_alert=True)
        return

    text = "Список игроков в лиге:\n"
    for m in members:
        text += f"{m['slot_index']}. @{m['username']} {m['game_nick']}\n"
    text += "\nНапиши номер участника (от 2 до 4), которого хочешь выгнать:"

    await callback.message.edit_text(text)
    await state.set_state(LeagueStates.waiting_kick_slot)
    await callback.answer()


@router.message(LeagueStates.waiting_kick_slot)
async def process_kick_slot(message: Message, state: FSMContext):
    try:
        slot = int(message.text.strip())
        if slot not in (2, 3, 4):
            raise ValueError()
    except:
        await message.answer("❌ Неверный номер слота. Напиши цифру от 2 до 4:")
        return

    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    league = cursor.execute(
        "SELECT l.id FROM leagues l JOIN league_members m ON l.id = m.league_id WHERE m.user_id = ? AND m.slot_index = 1",
        (user_id,)).fetchone()

    target = cursor.execute("SELECT * FROM league_members WHERE league_id = ? AND slot_index = ?",
                            (league["id"], slot)).fetchone()
    conn.close()

    if not target or not target["user_id"]:
        await message.answer("❌ В этом слоте никого нет. Выбери другой:")
        return

    await state.update_data(kick_slot=slot, kick_user_id=target["user_id"])

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Да", callback_data="league:kick_confirm:yes"),
            InlineKeyboardButton(text="Нет", callback_data="league:kick_confirm:no"),
        ]
    ])
    await message.answer(f"Вы уверены, что хотите выгнать @{target['username']} {target['game_nick']}?",
                         reply_markup=keyboard)
    await state.set_state(LeagueStates.waiting_kick_confirm)


@router.callback_query(F.data.startswith("league:kick_confirm:"))
async def confirm_kick(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":")[-1]
    data = await state.get_data()

    if action == "no":
        await state.clear()
        await callback.message.edit_text("❌ Изгнание участника отменено.")
        return

    slot = data["kick_slot"]
    user_id = callback.from_user.id

    conn = get_db()
    cursor = conn.cursor()
    league = cursor.execute(
        "SELECT l.id FROM leagues l JOIN league_members m ON l.id = m.league_id WHERE m.user_id = ? AND m.slot_index = 1",
        (user_id,)).fetchone()

    cursor.execute("""
        UPDATE league_members 
        SET user_id = NULL, game_nick = '-- Свободное место --', player_tag = '', username = '', role = 'участник', trophies_record = 0
        WHERE league_id = ? AND slot_index = ?
    """, (league["id"], slot))

    conn.commit()
    conn.close()

    await state.clear()
    await callback.message.edit_text("✅ Участник был успешно выгнан из лиги, слот освобожден.")


@router.callback_query(F.data == "league:transfer_lead")
async def transfer_lead_start(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    league = cursor.execute(
        "SELECT l.* FROM leagues l JOIN league_members m ON l.id = m.league_id WHERE m.user_id = ? AND m.slot_index = 1",
        (user_id,)).fetchone()

    if not league:
        conn.close()
        await callback.answer("Недостаточно прав!", show_alert=True)
        return

    members = cursor.execute(
        "SELECT * FROM league_members WHERE league_id = ? AND slot_index IN (2, 3, 4) AND user_id IS NOT NULL",
        (league["id"],)).fetchall()
    conn.close()

    if not members:
        await callback.answer("В лиге нет участников для передачи лидерства!", show_alert=True)
        return

    text = "Список игроков в лиге:\n"
    for m in members:
        text += f"{m['slot_index']}. @{m['username']} {m['game_nick']}\n"
    text += "\nКому передать лидерку? Напиши номер участника (от 2 до 4):"

    await callback.message.edit_text(text)
    await state.set_state(LeagueStates.waiting_transfer_slot)
    await callback.answer()


@router.message(LeagueStates.waiting_transfer_slot)
async def process_transfer_slot_number(message: Message, state: FSMContext):
    try:
        slot = int(message.text.strip())
        if slot not in (2, 3, 4):
            raise ValueError()
    except:
        await message.answer("❌ Неверный номер слота. Напиши цифру от 2 до 4:")
        return

    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    league = cursor.execute(
        "SELECT l.id FROM leagues l JOIN league_members m ON l.id = m.league_id WHERE m.user_id = ? AND m.slot_index = 1",
        (user_id,)).fetchone()

    target = cursor.execute("SELECT * FROM league_members WHERE league_id = ? AND slot_index = ?",
                            (league["id"], slot)).fetchone()
    conn.close()

    if not target or not target["user_id"]:
        await message.answer("❌ В этом слоте никого нет. Выбери другой:")
        return

    await state.update_data(transfer_slot=slot, target_user_id=target["user_id"])

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Да", callback_data="league:transfer_confirm:yes"),
            InlineKeyboardButton(text="Нет", callback_data="league:transfer_confirm:no"),
        ]
    ])
    await message.answer(f"Вы уверены, что хотите передать лидерку @{target['username']} {target['game_nick']}?",
                         reply_markup=keyboard)
    await state.set_state(LeagueStates.waiting_transfer_confirm)


@router.callback_query(F.data.startswith("league:transfer_confirm:"))
async def confirm_transfer(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":")[-1]
    data = await state.get_data()

    if action == "no":
        await state.clear()
        await callback.message.edit_text("❌ Передача лидерства отменена.")
        return

    target_slot = data["transfer_slot"]
    user_id = callback.from_user.id

    conn = get_db()
    cursor = conn.cursor()
    league = cursor.execute(
        "SELECT l.id FROM leagues l JOIN league_members m ON l.id = m.league_id WHERE m.user_id = ? AND m.slot_index = 1",
        (user_id,)).fetchone()
    league_id = league["id"]

    leader_record = cursor.execute("SELECT * FROM league_members WHERE league_id = ? AND slot_index = 1",
                                   (league_id,)).fetchone()
    target_record = cursor.execute("SELECT * FROM league_members WHERE league_id = ? AND slot_index = ?",
                                   (league_id, target_slot)).fetchone()

    l_uid, l_nick, l_ptag, l_uname, l_troph = leader_record["user_id"], leader_record["game_nick"], leader_record[
        "player_tag"], leader_record["username"], leader_record["trophies_record"]

    cursor.execute("""
        UPDATE league_members 
        SET user_id = ?, game_nick = ?, player_tag = ?, username = ?, role = 'лидер', trophies_record = ?
        WHERE league_id = ? AND slot_index = 1
    """, (target_record["user_id"], target_record["game_nick"], target_record["player_tag"], target_record["username"],
          target_record["trophies_record"], league_id))

    cursor.execute("""
        UPDATE league_members 
        SET user_id = ?, game_nick = ?, player_tag = ?, username = ?, role = 'участник', trophies_record = ?
        WHERE league_id = ? AND slot_index = ?
    """, (l_uid, l_nick, l_ptag, l_uname, l_troph, league_id, target_slot))

    conn.commit()
    conn.close()

    await state.clear()
    await callback.message.edit_text("✅ Лидерство успешно передано! Теперь вы участник лиги.")


@router.callback_query(F.data == "league:back_root")
async def back_to_league_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await open_league_root(callback.message, state)
    await callback.answer()


# --- ПРИГЛАШЕНИЕ ИГРОКА В ЛИГУ ---
@router.callback_query(F.data == "league:invite_member")
async def invite_member_start(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    league = cursor.execute(
        "SELECT l.* FROM leagues l JOIN league_members m ON l.id = m.league_id WHERE m.user_id = ? AND m.slot_index = 1",
        (user_id,)).fetchone()
    conn.close()

    if not league:
        await callback.answer("У вас нет прав лидера!", show_alert=True)
        return

    await callback.message.edit_text("Отправь `@username` или `ID` игрока, которого хочешь пригласить в лигу:")
    await state.set_state(LeagueStates.waiting_invite_target)
    await callback.answer()


@router.message(LeagueStates.waiting_invite_target)
async def process_invite_target(message: Message, state: FSMContext):
    target_raw = message.text.strip().replace("@", "")

    main_conn = sqlite3.connect("vigarik.db")
    main_conn.row_factory = sqlite3.Row
    if target_raw.isdigit():
        target_user = main_conn.execute("SELECT * FROM members WHERE user_id = ?", (int(target_raw),)).fetchone()
    else:
        target_user = main_conn.execute("SELECT * FROM members WHERE username = ?", (target_raw,)).fetchone()
    main_conn.close()

    if not target_user:
        await message.answer("❌ Игрок не найден в базе данных бота. Убедитесь, что он запускал бота.")
        return

    target_user_id = target_user["user_id"]
    user_id = message.from_user.id

    conn = get_db()
    cursor = conn.cursor()
    league = cursor.execute(
        "SELECT l.* FROM leagues l JOIN league_members m ON l.id = m.league_id WHERE m.user_id = ? AND m.slot_index = 1",
        (user_id,)).fetchone()

    # Проверка, есть ли уже в лиге
    in_league = cursor.execute("SELECT * FROM league_members WHERE league_id = ? AND user_id = ?",
                               (league["id"], target_user_id)).fetchone()
    if in_league:
        conn.close()
        await message.answer("❌ Этот игрок уже состоит в вашей лиге!")
        return

    cursor.execute("""
        INSERT INTO league_invites (league_id, target_user_id) VALUES (?, ?)
    """, (league["id"], target_user_id))
    conn.commit()
    conn.close()

    await state.clear()
    await message.answer(f"✅ Приглашение успешно отправлено игроку @{target_raw}!")


# --- СОСТАВ ЛИГИ ---
@router.callback_query(F.data == "league:my_team_info")
async def view_team_info(callback: CallbackQuery):
    user_id = callback.from_user.id
    league_data = get_user_league(user_id)

    if not league_data:
        await callback.answer("Вы не состоите в лиге.", show_alert=True)
        return

    league_id = league_data["id"]
    conn = get_db()
    members = conn.execute("SELECT * FROM league_members WHERE league_id = ? ORDER BY slot_index ASC",
                           (league_id,)).fetchall()
    conn.close()

    lines = [f"🏰 Состав лиги <b>{league_data['name']} [{league_data['tag']}]</b>:\n"]
    main_conn = sqlite3.connect("vigarik.db")
    main_conn.row_factory = sqlite3.Row

    for m in members:
        slot = m["slot_index"]
        if m["user_id"] is None:
            lines.append(f"{slot}. <i>-- Свободное место --</i>")
        else:
            role_label = "👑 Лидер" if m["role"] == "лидер" else "👤 Участник"
            trophies = m["trophies_record"] if m["trophies_record"] else 0
            lines.append(f"{slot}. <b>{m['game_nick']}</b> ({role_label}) — {trophies}🏆")

    main_conn.close()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="league:back_root")]
    ])

    await callback.message.edit_text("\n".join(lines), reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


# --- ОТМЕНА / УДАЛЕНИЕ ЗАЯВКИ ИГРОКОМ ---
@router.callback_query(F.data.startswith("league:withdraw_app:"))
async def withdraw_application(callback: CallbackQuery):
    app_id = int(callback.data.split(":")[-1])
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM league_applications WHERE id = ?", (app_id,))
    conn.commit()
    conn.close()

    await callback.message.edit_text("❌ Ваша заявка была отозвана.")
    await callback.answer()


# --- ДЕТАЛИ И РЕШЕНИЕ ПО ЗАЯВКЕ ЛИДЕРОМ ---
@router.callback_query(F.data.startswith("league:app_detail:"))
async def view_app_detail(callback: CallbackQuery):
    app_id = int(callback.data.split(":")[-1])
    conn = get_db()
    cursor = conn.cursor()
    app = cursor.execute("SELECT * FROM league_applications WHERE id = ?", (app_id,)).fetchone()

    if not app:
        conn.close()
        await callback.answer("Заявка не найдена или уже обработана.", show_alert=True)
        return

    main_conn = sqlite3.connect("vigarik.db")
    main_conn.row_factory = sqlite3.Row
    u_row = main_conn.execute("SELECT * FROM members WHERE user_id = ?", (app["user_id"],)).fetchone()
    main_conn.close()

    nick = u_row["game_nick"] if u_row and "game_nick" in u_row.keys() else f"ID: {app['user_id']}"
    trophies = u_row["trophies"] if u_row and "trophies" in u_row.keys() else 0
    conn.close()

    text = (
        f"📩 Заявка от игрока: <b>{nick}</b>\n"
        f"🏆 Трофеи: {trophies}\n\n"
        f"💬 Текст заявки:\n<i>\"{app['text_reason']}\"</i>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принять", callback_data=f"league:app_accept:{app_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"league:app_reject:{app_id}")
        ],
        [InlineKeyboardButton(text="◀️ К списку заявок", callback_data="league:view_requests")]
    ])

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("league:app_accept:"))
async def accept_application(callback: CallbackQuery):
    app_id = int(callback.data.split(":")[-1])
    conn = get_db()
    cursor = conn.cursor()

    app = cursor.execute("SELECT * FROM league_applications WHERE id = ?", (app_id,)).fetchone()
    if not app:
        conn.close()
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    league_id = app["league_id"]
    target_user_id = app["user_id"]

    free_slot = cursor.execute(
        "SELECT * FROM league_members WHERE league_id = ? AND slot_index IN (2, 3, 4) AND user_id IS NULL ORDER BY slot_index ASC LIMIT 1",
        (league_id,)
    ).fetchone()

    if not free_slot:
        conn.close()
        await callback.answer("❌ В лиге больше нет свободных мест (все слоты заняты)!", show_alert=True)
        return

    main_conn = sqlite3.connect("vigarik.db")
    main_conn.row_factory = sqlite3.Row
    u_row = main_conn.execute("SELECT * FROM members WHERE user_id = ?", (target_user_id,)).fetchone()
    main_conn.close()

    game_nick = u_row["game_nick"] if u_row and u_row["game_nick"] else "Игрок"
    player_tag = u_row["player_tag"] if u_row and u_row["player_tag"] else "#N/A"
    trophies = u_row["trophies"] if u_row and "trophies" in u_row.keys() else 0
    username = u_row["username"] if u_row and "username" in u_row.keys() else ""

    cursor.execute("""
        UPDATE league_members 
        SET user_id = ?, game_nick = ?, player_tag = ?, username = ?, role = 'участник', trophies_record = ?
        WHERE league_id = ? AND slot_index = ?
    """, (target_user_id, game_nick, player_tag, username, trophies, league_id, free_slot["slot_index"]))

    cursor.execute("DELETE FROM league_applications WHERE user_id = ?", (target_user_id,))
    cursor.execute("DELETE FROM league_invites WHERE target_user_id = ?", (target_user_id,))

    conn.commit()
    conn.close()

    await callback.message.edit_text(
        f"✅ Игрок <b>{game_nick}</b> успешно принят в лигу на слот №{free_slot['slot_index']}!", parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("league:app_reject:"))
async def reject_application(callback: CallbackQuery):
    app_id = int(callback.data.split(":")[-1])
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM league_applications WHERE id = ?", (app_id,))
    conn.commit()
    conn.close()

    await callback.message.edit_text("❌ Заявка была отклонена.")
    await callback.answer()
