import os
import sqlite3
from aiogram import F, Router, Bot
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
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

# 1. Открытие главного меню лиг (с нижней клавиатурой)
@router.message(F.text == "Лиги 💀 (BetaTest)")
async def open_league_root(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    league_data = get_user_league(user_id)

    if not league_data:
        text = "Кажется тебя ещё нету ни в одной лиге. Время вступить в одну из них, или создать свою!"
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🌍 Все лиги")],
                [KeyboardButton(text="📝 Подать заявку в лигу")],
                [KeyboardButton(text="📩 Мои заявки"), KeyboardButton(text="📥 Приглашения в лигу")],
                [KeyboardButton(text="➕ Создать лигу")]
            ],
            resize_keyboard=True
        )
    else:
        is_leader = (league_data["slot_index"] == 1)
        status_str = "Открыт ✅" if league_data["is_open"] == 1 else "Закрыт ❌"
        text = f"🏰 Ваша лига: <b>{league_data['name']} [{league_data['tag']}]</b>\nСтатус набора: {status_str}"

        if is_leader:
            keyboard = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="🌍 Все лиги"), KeyboardButton(text="👥 Состав лиги")],
                    [KeyboardButton(text="📋 Просмотреть заявки")],
                    [KeyboardButton(text="➕ Пригласить игрока"), KeyboardButton(text="🚪 Выгнать участника")],
                    [KeyboardButton(text="👑 Передать лидерство")],
                    [KeyboardButton(text="🔒 Закрыть набор / Открыть набор")]
                ],
                resize_keyboard=True
            )
        else:
            keyboard = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="🌍 Все лиги"), KeyboardButton(text="👥 Состав лиги")],
                    [KeyboardButton(text="🚪 Выйти из лиги")]
                ],
                resize_keyboard=True
            )

    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


# 2. Обработка нажатия на Reply-кнопку создания лиги
@router.message(F.text == "➕ Создать лигу")
async def start_create_league(message: Message, state: FSMContext):
    user_id = message.from_user.id
    league_data = get_user_league(user_id)
    if league_data:
        await message.answer("❌ Вы уже состоите в лиге. Сначала выйдите из текущей.")
        return

    await state.set_state(LeagueStates.waiting_for_league_name)
    await message.answer("Напиши полное название лиги (например, ViGarikSQuaD):")


# 3. Получение названия и запрос тега
@router.message(LeagueStates.waiting_for_league_name)
async def process_league_name(message: Message, state: FSMContext):
    await state.update_data(league_name=message.text.strip())
    await message.answer("Напиши сокращенный тег лиги (лимит 5 символов, пример VGSQD):")
    await state.set_state(LeagueStates.waiting_for_league_tag)


# 4. Получение тега, создание лиги в БД и выдача меню
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
    await open_league_root(message, state)

@router.message(F.text == "🔒 Закрыть набор / Открыть набор")
async def toggle_league_open(message: Message, state: FSMContext):
    user_id = message.from_user.id
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
        await message.answer(f"🔒 Статус набора изменен на: {status_str}")
    else:
        await message.answer("❌ У вас нет прав лидера.")
    conn.close()
    await open_league_root(message, state)
@router.message(F.text == "🌍 Все лиги")
async def show_all_leagues(message: Message, state: FSMContext):
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

    await message.answer("📋 Список всех лиг:", reply_markup=builder.as_markup())


@router.message(F.text == "📝 Подать заявку в лигу")
async def show_leagues_to_apply(message: Message, state: FSMContext):
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

    await message.answer("📋 Выберите лигу для просмотра состава и подачи заявки:", reply_markup=builder.as_markup())



@router.message(LeagueStates.waiting_apply_text)
async def process_apply_text(message: Message, state: FSMContext):
    await state.update_data(apply_text=message.text.strip())

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить заявку", callback_data="league:apply_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="league:back_root")]
    ])
    await message.answer(f"Текст вашей заявки:\n\n<i>{message.text.strip()}</i>\n\nПодтверждаете отправку?",
                         reply_markup=keyboard, parse_mode="HTML")
    await state.set_state(LeagueStates.waiting_apply_confirm)


@router.callback_query(F.data == "league:apply_confirm")
async def confirm_apply(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    league_id = data.get("target_league_id")
    apply_text = data.get("apply_text")
    user_id = callback.from_user.id

    conn = get_db()
    cursor = conn.cursor()

    # 1. Исправлено имя колонки на text_reason
    # 2. Добавлена проверка на случай, если данные в state слетели
    if not league_id or not apply_text:
        await callback.answer("Ошибка: данные заявки утеряны. Попробуйте еще раз.")
        await state.clear()
        return

    cursor.execute("""
        INSERT INTO league_applications (league_id, user_id, text_reason) VALUES (?, ?, ?)
    """, (league_id, user_id, apply_text))

    conn.commit()
    conn.close()

    await state.clear()
    await callback.message.edit_text("✅ Заявка успешно отправлена лидеру лиги!")
    await callback.answer()

@router.message(LeagueStates.waiting_apply_text)
async def process_apply_text(message: Message, state: FSMContext):
    await state.update_data(apply_text=message.text.strip())

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить заявку", callback_data="league:apply_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="league:back_root")]
    ])
    await message.answer(f"Текст вашей заявки:\n\n<i>{message.text.strip()}</i>\n\nПодтверждаете отправку?",
                         reply_markup=keyboard, parse_mode="HTML")
    await state.set_state(LeagueStates.waiting_apply_confirm)


@router.message(F.text == "🚪 Выгнать участника")
async def kick_member_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    league_data = get_user_league(user_id)
    if not league_data or league_data["slot_index"] != 1:
        await message.answer("❌ Доступно только лидеру.")
        return

    await state.set_state(LeagueStates.waiting_kick_slot)
    await message.answer("Введите номер слота участника, которого хотите исключить (2, 3 или 4):")


@router.message(LeagueStates.waiting_kick_slot)
async def process_kick_slot(message: Message, state: FSMContext):
    slot = message.text.strip()
    if slot not in ["2", "3", "4"]:
        await message.answer("❌ Неверный номер слота. Введите 2, 3 или 4:")
        return

    await state.update_data(kick_slot=int(slot))
    await state.set_state(LeagueStates.waiting_kick_confirm)
    await message.answer(f"Вы уверены, что хотите выгнать игрока со слота {slot}? (напиши 'да' для подтверждения)")


@router.message(LeagueStates.waiting_kick_confirm)
async def confirm_kick(message: Message, state: FSMContext):
    if message.text.lower() != "да":
        await state.clear()
        await message.answer("❌ Операция отменена.")
        return

    data = await state.get_data()
    slot = data["kick_slot"]
    user_id = message.from_user.id
    league_data = get_user_league(user_id)

    conn = get_db()
    cursor = conn.cursor()
    # Очищаем слот: заменяем данные на дефолтные
    cursor.execute("""
        UPDATE league_members 
        SET user_id = NULL, game_nick = '-- Свободное место --', player_tag = '', username = '', trophies_record = 0 
        WHERE league_id = ? AND slot_index = ?
    """, (league_data["id"], slot))
    conn.commit()
    conn.close()

    await state.clear()
    await message.answer("✅ Участник успешно исключен!")
    await open_league_root(message, state)


@router.message(F.text == "👑 Передать лидерство")
async def transfer_lead_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    league = get_user_league(user_id)

    if not league or league["slot_index"] != 1:
        await message.answer("❌ Доступно только лидеру лиги.")
        return

    # Получаем состав лиги
    conn = get_db()
    cursor = conn.cursor()
    members = cursor.execute(
        "SELECT * FROM league_members WHERE league_id = ? ORDER BY slot_index ASC",
        (league["id"],)
    ).fetchall()
    conn.close()

    # Формируем красивый список для выбора
    lines = ["Выберите номер слота (2-4), которому хотите передать лидерство:\n"]
    for m in members:
        # Лидера (себя) не предлагаем
        if m["slot_index"] == 1:
            lines.append(f"{m['slot_index']}. {m['game_nick']} — 👑 Лидер")
        elif m["user_id"] is None:
            lines.append(f"{m['slot_index']}. -- Свободное место -- — 👤 Участник")
        else:
            lines.append(f"{m['slot_index']}. {m['game_nick']} — 👤 Участник")

    await state.set_state(LeagueStates.waiting_transfer_slot)
    await message.answer("\n".join(lines))

@router.message(LeagueStates.waiting_transfer_slot)
async def process_transfer_slot_number(message: Message, state: FSMContext):
    slot = message.text.strip()
    if slot not in ["2", "3", "4"]:
        await message.answer("❌ Неверный слот. Введите 2, 3 или 4:")
        return

    await state.update_data(transfer_slot=int(slot))
    await state.set_state(LeagueStates.waiting_transfer_confirm)
    await message.answer(f"Подтвердите передачу прав лидера игроку на слоте {slot}? (напиши 'да')")


@router.message(LeagueStates.waiting_transfer_confirm)
async def confirm_transfer(message: Message, state: FSMContext):
    if message.text.lower() != "да":
        await state.clear()
        await message.answer("❌ Отмена.")
        return

    data = await state.get_data()
    target_slot = data["transfer_slot"]
    user_id = message.from_user.id
    league_data = get_user_league(user_id)

    conn = get_db()
    cursor = conn.cursor()
    # Проверка, есть ли кто-то в слоте
    target_member = cursor.execute(
        "SELECT * FROM league_members WHERE league_id = ? AND slot_index = ?",
        (league_data["id"], target_slot)
    ).fetchone()

    if not target_member or target_member["user_id"] is None:
        await message.answer("❌ Этот слот пуст!")
    else:
        # Меняем роли
        cursor.execute("UPDATE league_members SET role = 'участник' WHERE league_id = ? AND user_id = ?",
                       (league_data["id"], user_id))
        cursor.execute("UPDATE league_members SET role = 'лидер' WHERE league_id = ? AND slot_index = ?",
                       (league_data["id"], target_slot))

        # Меняем слоты местами через временный индекс (99), чтобы база не запуталась
        cursor.execute("UPDATE league_members SET slot_index = 99 WHERE league_id = ? AND slot_index = 1",
                       (league_data["id"],))
        cursor.execute("UPDATE league_members SET slot_index = 1 WHERE league_id = ? AND slot_index = ?",
                       (league_data["id"], target_slot))
        cursor.execute("UPDATE league_members SET slot_index = ? WHERE league_id = ? AND slot_index = 99",
                       (target_slot, league_data["id"]))

        conn.commit()
        await message.answer("👑 Лидерство успешно передано!")

    conn.close()
    await state.clear()
    await open_league_root(message, state)

@router.callback_query(F.data == "league:back_root")
async def back_to_league_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    league_data = get_user_league(user_id)

    if not league_data:
        text = "Кажется тебя ещё нету ни в одной лиге. Время вступить в одну из них, или создать свою!"
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🌍 Все лиги")],
                [KeyboardButton(text="📝 Подать заявку в лигу")],
                [KeyboardButton(text="📩 Мои заявки"), KeyboardButton(text="📥 Приглашения в лигу")],
                [KeyboardButton(text="➕ Создать лигу")]
            ],
            resize_keyboard=True
        )
    else:
        is_leader = (league_data["slot_index"] == 1)
        status_str = "Открыт ✅" if league_data["is_open"] == 1 else "Закрыт ❌"
        text = f"🏰 Ваша лига: <b>{league_data['name']} [{league_data['tag']}]</b>\nСтатус набора: {status_str}"

        if is_leader:
            keyboard = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="🌍 Все лиги"), KeyboardButton(text="👥 Состав лиги")],
                    [KeyboardButton(text="📋 Просмотреть заявки")],
                    [KeyboardButton(text="➕ Пригласить игрока"), KeyboardButton(text="🚪 Выгнать участника")],
                    [KeyboardButton(text="👑 Передать лидерство")],
                    [KeyboardButton(text="🔒 Закрыть набор / Открыть набор")]
                ],
                resize_keyboard=True
            )
        else:
            keyboard = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="🌍 Все лиги"), KeyboardButton(text="👥 Состав лиги")],
                    [KeyboardButton(text="🚪 Выйти из лиги")]
                ],
                resize_keyboard=True
            )

    # Удаляем инлайн-сообщение со списком/кланом и отправляем актуальное сообщение с нижней клавиатурой
    try:
        await callback.message.delete()
    except Exception:
        pass

    await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()
@router.message(F.text == "👥 Состав лиги")
async def view_team_info(message: Message, state: FSMContext):
    user_id = message.from_user.id
    league = get_user_league(user_id)
    if not league:
        await message.answer("❌ Вы не состоите в лиге.")
        return

    conn = get_db()
    cursor = conn.cursor()
    members = cursor.execute("SELECT * FROM league_members WHERE league_id = ? ORDER BY slot_index ASC",
                             (league["id"],)).fetchall()
    conn.close()

    lines = [f"👥 Состав лиги <b>{league['name']}</b>:\n"]
    for m in members:
        status = "👑 Лидер" if m["role"] == "лидер" else "👤 Участник"
        lines.append(f"{m['slot_index']}. {m['game_nick']} — {status}")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.callback_query(F.data.startswith("league:withdraw_app:"))
async def withdraw_application(callback: CallbackQuery, state: FSMContext):
    app_id = int(callback.data.split(":")[-1])
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM league_applications WHERE id = ?", (app_id,))
    conn.commit()
    conn.close()
    await callback.message.edit_text("✅ Заявка отозвана.")
    await callback.answer()


@router.callback_query(F.data.startswith("league:app_detail:"))
async def view_app_detail(callback: CallbackQuery, state: FSMContext):
    app_id = int(callback.data.split(":")[-1])
    conn = get_db()
    cursor = conn.cursor()
    app = cursor.execute("SELECT * FROM league_applications WHERE id = ?", (app_id,)).fetchone()
    conn.close()

    if not app:
        await callback.answer("Заявка не найдена")
        return

    # Исправлено с app['text'] на app['text_reason']
    text = f"📩 Заявка от игрока (ID: {app['user_id']}):\n\n<i>{app['text_reason']}</i>"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принять", callback_data=f"league:accept:{app_id}"),
         InlineKeyboardButton(text="❌ Отклонить", callback_data=f"league:reject:{app_id}")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("league:accept:"))
async def accept_application(callback: CallbackQuery, state: FSMContext):
    app_id = int(callback.data.split(":")[-1])
    conn = get_db()
    cursor = conn.cursor()

    app = cursor.execute("SELECT * FROM league_applications WHERE id = ?", (app_id,)).fetchone()
    if not app:
        conn.close()
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    league_id = app["league_id"]
    user_id = app["user_id"]

    # Проверка: если игрок уже состоит в какой-либо лиге, автоматически сносим его заявку и не пускаем
    existing_league = get_user_league(user_id)
    if existing_league:
        cursor.execute("DELETE FROM league_applications WHERE id = ?", (app_id,))
        conn.commit()
        conn.close()
        await callback.message.edit_text("❌ Игрок уже состоит в лиге! Эта заявка была автоматически удалена.")
        await callback.answer()
        return

    # Ищем свободный слот в лиге (от 2 до 4)
    free_slot = cursor.execute("""
        SELECT * FROM league_members 
        WHERE league_id = ? AND user_id IS NULL AND slot_index > 1 
        ORDER BY slot_index ASC LIMIT 1
    """, (league_id,)).fetchone()

    if not free_slot:
        conn.close()
        await callback.message.edit_text("❌ В лиге нет свободных мест (все слоты заполнены).")
        await callback.answer()
        return

    # Получаем игровые данные игрока из основной базы vigarik.db
    main_conn = sqlite3.connect("vigarik.db")
    main_conn.row_factory = sqlite3.Row
    user_row = main_conn.execute("SELECT * FROM members WHERE user_id = ?", (user_id,)).fetchone()
    main_conn.close()

    game_nick = user_row["game_nick"] if user_row and user_row["game_nick"] else "Игрок"
    player_tag = user_row["player_tag"] if user_row and user_row["player_tag"] else "#N/A"
    username = "None" # Убираем .get() со sqlite3.Row чтобы не было краша
    trophies = user_row["trophies"] if user_row and "trophies" in user_row.keys() else 0

    # Занимаем слот
    cursor.execute("""
        UPDATE league_members 
        SET user_id = ?, game_nick = ?, player_tag = ?, username = ?, trophies_record = ?
        WHERE id = ?
    """, (user_id, game_nick, player_tag, username, trophies, free_slot["id"]))

    # Удаляем все заявки этого игрока
    cursor.execute("DELETE FROM league_applications WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

    await callback.message.edit_text("✅ Заявка принята, игрок добавлен в лигу!")
    await callback.answer()

@router.callback_query(F.data.startswith("league:reject:"))
async def reject_application(callback: CallbackQuery, state: FSMContext):
    app_id = int(callback.data.split(":")[-1])
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM league_applications WHERE id = ?", (app_id,))
    conn.commit()
    conn.close()

    await callback.message.edit_text("❌ Заявка отклонена.")
    await callback.answer()


@router.message(F.text == "📋 Просмотреть заявки")
async def view_league_requests(message: Message, state: FSMContext):
    user_id = message.from_user.id
    league = get_user_league(user_id)
    if not league or league["slot_index"] != 1:
        await message.answer("❌ Доступно только лидеру лиги.")
        return

    conn = get_db()
    cursor = conn.cursor()
    # Вытаскиваем заявки именно из league.db (таблица league_applications, колонка text_reason)
    requests = cursor.execute("""
        SELECT * FROM league_applications
        WHERE league_id = ?
    """, (league["id"],)).fetchall()
    conn.close()

    if not requests:
        await message.answer("📭 На данный момент нет входящих заявок в лигу.")
        return

    # Подтягиваем ники из vigarik.db для красоты
    main_conn = sqlite3.connect("vigarik.db")
    main_conn.row_factory = sqlite3.Row

    builder = InlineKeyboardBuilder()
    for req in requests:
        user_row = main_conn.execute("SELECT game_nick FROM members WHERE user_id = ?", (req["user_id"],)).fetchone()
        nick = user_row["game_nick"] if user_row and user_row["game_nick"] else f"ID: {req['user_id']}"
        builder.button(text=f"📩 Заявка от {nick}", callback_data=f"league:app_detail:{req['id']}")

    main_conn.close()
    builder.adjust(1)

    await message.answer("📋 Список входящих заявок в вашу лигу:", reply_markup=builder.as_markup())

# 1. Просмотр лиги (исправлена кнопка «Обратно к списку» и убрана кнопка подачи заявки, если зашли через "Все лиги")
@router.callback_query(F.data.startswith("league:info:"))
async def view_league_info(callback: CallbackQuery, state: FSMContext):
    data_parts = callback.data.split(":")
    league_id = int(data_parts[2])
    # Если перешли из "Все лиги", в data может передаваться маркер, но проще проверять текущий статус игрока и контекст
    # Сделаем умную проверку: если пользователь уже в лиге, кнопку подачи заявки точно не показываем
    user_id = callback.from_user.id
    user_league = get_user_league(user_id)

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
            current_elo = member_db_row["ranked_elo"] if member_db_row and "ranked_elo" in member_db_row.keys() else 0
            lines.append(f"{slot}. {m['game_nick']} — {role_label} | {trophies}🏆 | {current_elo} Elo Ranked")

    main_conn.close()
    text = "\n".join(lines)

    # Кнопка возврата всегда ведет на список всех лиг
    keyboard_buttons = [
        [InlineKeyboardButton(text="Обратно к списку", callback_data="league:all_list")]
    ]

    # Кнопку "Подать заявку" показываем ТОЛЬКО если пользователь НЕ состоит в лиге и лига открыта
    if not user_league and league["is_open"] == 1:
        keyboard_buttons.append(
            [InlineKeyboardButton(text="Подать заявку", callback_data=f"league:apply_send:{league_id}")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


# Обработчик кнопки "Обратно к списку"
@router.callback_query(F.data == "league:all_list")
async def back_to_all_leagues_list(callback: CallbackQuery, state: FSMContext):
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

    await callback.message.edit_text("📋 Список всех лиг:", reply_markup=builder.as_markup())
    await callback.answer()


# Защита при попытке подать заявку (если уже в лиге)
@router.callback_query(F.data.startswith("league:apply_send:"))
async def ask_apply_reason(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    user_league = get_user_league(user_id)
    if user_league:
        await callback.answer("❌ Вы уже состоите в лиге! Нельзя подавать заявки.", show_alert=True)
        return

    league_id = int(callback.data.split(":")[-1])
    await state.update_data(target_league_id=league_id)
    await state.set_state(LeagueStates.waiting_apply_text)
    await callback.message.answer("Напишите короткую заявку/причину, почему хотите вступить в эту лигу:")
    await callback.answer()


# Защита при попытке пригласить игрока (если лидер пытается пригласить того, кто уже где-то состоит)
@router.message(F.text == "➕ Пригласить игрока")
async def invite_member_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    league_data = get_user_league(user_id)
    if not league_data or league_data["slot_index"] != 1:
        await message.answer("❌ Доступно только лидеру лиги.")
        return

    await state.set_state(LeagueStates.waiting_invite_target)
    await message.answer("Введите тег игрока или его Telegram ID для приглашения:")


@router.message(LeagueStates.waiting_invite_target)
async def process_invite_target(message: Message, state: FSMContext):
    target = message.text.strip()
    inviter_id = message.from_user.id
    league_data = get_user_league(inviter_id)

    # Определяем ID или тег целевого игрока
    target_user_id = None
    if target.isdigit():
        target_user_id = int(target)
    else:
        main_conn = sqlite3.connect("vigarik.db")
        main_conn.row_factory = sqlite3.Row
        target_user = main_conn.execute("SELECT user_id FROM members WHERE player_tag = ? OR game_nick = ?", (target, target)).fetchone()
        main_conn.close()
        if target_user:
            target_user_id = target_user["user_id"]

    if not target_user_id:
        await message.answer("❌ Игрок не найден в базе данных бота!")
        await state.clear()
        await open_league_root(message, state)
        return

    if target_user_id == inviter_id:
        await message.answer("❌ Нельзя пригласить самого себя!")
        await state.clear()
        await open_league_root(message, state)
        return

    conn = get_db()
    cursor = conn.cursor()

    # Проверяем, состоит ли игрок в лиге
    existing_membership = cursor.execute(
        "SELECT * FROM league_members WHERE user_id = ?", (target_user_id,)
    ).fetchone()

    if existing_membership:
        conn.close()
        if existing_membership["league_id"] == league_data["id"]:
            await message.answer("❌ Этот игрок уже находится в вашей лиге!")
        else:
            await message.answer("❌ Этот игрок уже состоит в другой лиге!")
        await state.clear()
        await open_league_root(message, state)
        return

    # Проверяем, отправлено ли уже активное приглашение
    existing_invite = cursor.execute(
        "SELECT * FROM league_invites WHERE league_id = ? AND invitee_id = ?",
        (league_data["id"], target_user_id)
    ).fetchone()

    if existing_invite:
        conn.close()
        await message.answer("❌ Этому игроку уже отправлено приглашение в вашу лигу! Оно еще активно.")
        await state.clear()
        await open_league_root(message, state)
        return

    # Пытаемся записать инвайт (даже при сбое уникальности база не упадет)
    try:
        cursor.execute("""
            INSERT INTO league_invites (league_id, inviter_id, invitee_id) VALUES (?, ?, ?)
        """, (league_data["id"], inviter_id, target_user_id))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        await message.answer("❌ Этому игроку уже отправлено приглашение!")
        await state.clear()
        await open_league_root(message, state)
        return

    conn.close()

    await state.clear()
    await message.answer(f"✅ Приглашение для игрока <code>{target}</code> успешно отправлено!", parse_mode="HTML")
    await open_league_root(message, state)

@router.message(F.text == "📥 Приглашения в лигу")
async def view_league_invites(message: Message, state: FSMContext):
    user_id = message.from_user.id

    conn = get_db()
    cursor = conn.cursor()
    # Вытаскиваем приглашения для этого пользователя
    invites = cursor.execute("""
        SELECT i.*, l.name as league_name, l.tag as league_tag 
        FROM league_invites i
        JOIN leagues l ON i.league_id = l.id
        WHERE i.invitee_id = ?
    """, (user_id,)).fetchall()
    conn.close()

    if not invites:
        await message.answer("📭 У вас нет активных приглашений в лиги.")
        return

    builder = InlineKeyboardBuilder()
    for inv in invites:
        builder.button(
            text=f"🏰 {inv['league_name']} [{inv['league_tag']}]",
            callback_data=f"league:invite_view:{inv['id']}"
        )
    builder.adjust(1)

    await message.answer("📥 Список полученных приглашений в лиги:", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("league:invite_view:"))
async def view_invite_details(callback: CallbackQuery, state: FSMContext):
    invite_id = int(callback.data.split(":")[-1])
    conn = get_db()
    cursor = conn.cursor()

    invite = cursor.execute("""
        SELECT i.*, l.name as league_name, l.tag as league_tag 
        FROM league_invites i
        JOIN leagues l ON i.league_id = l.id
        WHERE i.id = ?
    """, (invite_id,)).fetchone()
    conn.close()

    if not invite:
        await callback.answer("Приглашение не найдено или уже неактуально.", show_alert=True)
        return

    text = f"📥 Приглашение в лигу <b>{invite['league_name']} [{invite['league_tag']}]</b>\n\nХотите принять приглашение?"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принять", callback_data=f"league:invite_accept:{invite_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"league:invite_reject:{invite_id}")
        ],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="league:back_invites")]
    ])

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("league:invite_accept:"))
async def accept_invite(callback: CallbackQuery, state: FSMContext):
    invite_id = int(callback.data.split(":")[-1])
    user_id = callback.from_user.id

    # Проверяем, не вступил ли уже куда-то
    existing_league = get_user_league(user_id)
    if existing_league:
        await callback.answer("❌ Вы уже состоите в лиге! Сначала выйдите из текущей.", show_alert=True)
        return

    conn = get_db()
    cursor = conn.cursor()
    invite = cursor.execute("SELECT * FROM league_invites WHERE id = ?", (invite_id,)).fetchone()

    if not invite:
        conn.close()
        await callback.answer("Приглашение устарело.", show_alert=True)
        return

    league_id = invite["league_id"]

    # Ищем свободный слот (2-4)
    free_slot = cursor.execute("""
        SELECT * FROM league_members 
        WHERE league_id = ? AND user_id IS NULL AND slot_index > 1 
        ORDER BY slot_index ASC LIMIT 1
    """, (league_id,)).fetchone()

    if not free_slot:
        conn.close()
        await callback.message.edit_text("❌ В лиге больше нет свободных мест.")
        await callback.answer()
        return

    # Берем данные игрока из vigarik.db
    main_conn = sqlite3.connect("vigarik.db")
    main_conn.row_factory = sqlite3.Row
    user_row = main_conn.execute("SELECT * FROM members WHERE user_id = ?", (user_id,)).fetchone()
    main_conn.close()

    game_nick = user_row["game_nick"] if user_row and user_row["game_nick"] else "Игрок"
    player_tag = user_row["player_tag"] if user_row and user_row["player_tag"] else "#N/A"
    trophies = user_row["trophies"] if user_row and "trophies" in user_row.keys() else 0

    # Занимаем слот в лиге
    cursor.execute("""
        UPDATE league_members 
        SET user_id = ?, game_nick = ?, player_tag = ?, username = 'None', trophies_record = ?
        WHERE id = ?
    """, (user_id, game_nick, player_tag, trophies, free_slot["id"]))

    # Удаляем все инвайты и заявки этого юзера
    cursor.execute("DELETE FROM league_invites WHERE invitee_id = ?", (user_id,))
    cursor.execute("DELETE FROM league_applications WHERE user_id = ?", (user_id,))

    conn.commit()
    conn.close()

    await callback.message.edit_text("✅ Вы успешно приняли приглашение и вступили в лигу!")
    await callback.answer()


@router.callback_query(F.data.startswith("league:invite_reject:"))
async def reject_invite(callback: CallbackQuery, state: FSMContext):
    invite_id = int(callback.data.split(":")[-1])
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM league_invites WHERE id = ?", (invite_id,))
    conn.commit()
    conn.close()

    await callback.message.edit_text("❌ Приглашение отклонено.")
    await callback.answer()

@router.message(F.text.contains("Выйти из лиги"))
async def text_leave_league(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    conn = get_db()
    cursor = conn.cursor()

    member = cursor.execute(
        "SELECT * FROM league_members WHERE user_id = ?", (user_id,)
    ).fetchone()

    if not member:
        conn.close()
        await message.answer("❌ Вы не состоите в лиге.")
        return

    league_id = member["league_id"]

    if member["role"] == 'лидер':
        cursor.execute("DELETE FROM leagues WHERE id = ?", (league_id,))
        cursor.execute("DELETE FROM league_members WHERE league_id = ?", (league_id,))
        await message.answer("✅ Вы покинули лигу. Так как вы были лидером, лига была удалена.")
    else:
        cursor.execute("""
            UPDATE league_members 
            SET user_id = NULL, game_nick = NULL, player_tag = NULL, username = NULL, trophies_record = 0
            WHERE user_id = ?
        """, (user_id,))
        await message.answer("✅ Вы успешно вышли из лиги.")

    conn.commit()
    conn.close()
