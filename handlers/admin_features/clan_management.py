import re
import random
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command  # ДОБАВЛЕНО: Для корректной ловли /delete
from aiogram.utils.markdown import html_decoration as hd  # ИСПРАВЛЕНО: Безопасное экранирование ников

from database import get_member, upsert_member, get_all_members, get_unregistered_members, get_clan_members, \
    remove_member
from utils.permissions import can_edit_list, is_any_admin
from utils.keyboards import main_menu
from utils.roster_sync import sync_roster_msg
from .base import AdminStates
from config import CLAN_DISPLAY, CLAN_TAGS

# Подключаем наш API-сервис для верификации тегов при ручном изменении
from services.api_service import get_player_profile

router = Router()


@router.message(F.text == "📋 Редактировать список клана")
async def edit_list_select_clan(message: Message, state: FSMContext):
    member = await get_member(message.from_user.id)
    if not member or not can_edit_list(member):
        await message.answer("⛔ Недостаточно прав. Требуется Вице Президент и выше.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏰 Основа (Squad)", callback_data="edit_clan_sel:squad")],
        [InlineKeyboardButton(text="🎓 Академия (Academy)", callback_data="edit_clan_sel:academy")],
        [InlineKeyboardButton(text="⚔️ Ивенты (Events)", callback_data="edit_clan_sel:events")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="edit_list:cancel")]
    ])

    await message.answer("Выбери клан, список которого ты хочешь изменить или пополнить:", reply_markup=kb)
    await state.set_state(AdminStates.choosing_clan_to_edit)


@router.callback_query(F.data.startswith("edit_clan_sel:"), AdminStates.choosing_clan_to_edit)
async def edit_list_show_members(callback: CallbackQuery, state: FSMContext):
    clan = callback.data.split(":")[1]
    await state.update_data(selected_clan=clan)

    members = await get_clan_members(clan)
    all_db = await get_all_members()
    unregistered_in_clan = [m for m in all_db if m.get("clan") == clan and not m.get("game_nick")]

    seen_ids = set()
    combined_members = []
    for m in members + unregistered_in_clan:
        if m.get("user_id") and m["user_id"] not in seen_ids:
            seen_ids.add(m["user_id"])
            combined_members.append(m)

    lines = [f"<b>Редактирование списка: {CLAN_DISPLAY.get(clan, clan).upper()}</b>\n"]

    if not combined_members:
        lines.append("<i>Список сейчас пуст.</i>")
    else:
        for m in combined_members:
            uid = m["user_id"]
            raw_nick = m.get("game_nick") or "(Нет игрового ника ❌)"
            nick = hd.quote(str(raw_nick))

            ptag = m.get("player_tag") or "Нет тега 🚫"
            trophies = m.get("trophies", 0)
            trophies_str = f" | 🏆 {trophies:,}" if trophies > 0 else ""

            uname = f"@{hd.quote(m['username'])}" if m.get("username") else f"ID: {uid}"
            reg_marker = "✅" if m.get("registered") == 1 else "💤"
            lines.append(f"• <code>{uid}</code> | {reg_marker} {uname} | {nick} ({ptag}{trophies_str})")

    # ИСПРАВЛЕНО: Кнопка "Массовое добавление" полностью удалена из списка кнопок
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад к выбору клана", callback_data="edit_clan_back_to_sel")],
        [InlineKeyboardButton(text="❌ Выйти из меню", callback_data="edit_list:cancel")]
    ])

    await callback.message.edit_text(
        "\n".join(
            lines) + "\n\nЧтобы изменить профиль игрока или удалить его, <b>введи его user_id</b> сообщением ниже:",
        parse_mode="HTML",
        reply_markup=kb
    )
    await state.set_state(AdminStates.waiting_edit_member_id)
    await callback.answer()


@router.callback_query(F.data == "edit_clan_back_to_sel", AdminStates.waiting_edit_member_id)
async def edit_clan_back_to_sel(callback: CallbackQuery, state: FSMContext):
    await state.clear()

    member = await get_member(callback.from_user.id)
    if not member or not can_edit_list(member):
        await callback.answer("⛔ Недостаточно прав. Требуется Вице Президент и выше.", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏰 Основа (Squad)", callback_data="edit_clan_sel:squad")],
        [InlineKeyboardButton(text="🎓 Академия (Academy)", callback_data="edit_clan_sel:academy")],
        [InlineKeyboardButton(text="⚔️ Ивенты (Events)", callback_data="edit_clan_sel:events")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="edit_list:cancel")]
    ])

    await callback.message.edit_text(
        "Выбери клан, список которого ты хочешь изменить или пополнить:",
        reply_markup=kb
    )
    await state.set_state(AdminStates.choosing_clan_to_edit)
    await callback.answer()


@router.message(AdminStates.waiting_edit_member_id)
async def edit_list_receive_id(message: Message, state: FSMContext):
    text = message.text.strip()
    data = await state.get_data()
    clan = data.get("selected_clan")

    if not text.lstrip("-").isdigit():
        await message.answer("Введи корректный числовой user_id участника:")
        return

    target_id = int(text)
    target = await get_member(target_id)

    if not target:
        await message.answer("❌ Участник с таким ID не найден в базе данных бота.")
        return

    await state.update_data(edit_target_id=target_id)
    current_nick = hd.quote(str(target.get("game_nick") or "Отсутствует"))
    current_tag = target.get("player_tag") or "Не привязан"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="edit_list:cancel")]
    ])

    await message.answer(
        f"Участник: @{target.get('username') or 'нет'} (ID: <code>{target_id}</code>)\n"
        f"Текущий игровой ник: <b>{current_nick}</b>\n"
        f"Текущий игровой тег: <code>{current_tag}</code>\n\n"
        f"✍️ Напишите для него <b>новый тег игрока Brawl Stars</b> (например, #9PJYV82CC).\n"
        f"Бот автоматически обновит его ник и кубки через API.\n\n"
        f"<i>(Или отправь команду <code>/delete</code> чтобы убрать его из этого клана)</i>",
        parse_mode="HTML",
        reply_markup=kb
    )
    await state.set_state(AdminStates.waiting_new_nick_for_member)


# ИСПРАВЛЕНО: Убрали дублирующийся декоратор
@router.message(AdminStates.waiting_new_nick_for_member)
async def edit_list_set_nick(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    target_id = data.get("edit_target_id")
    clan = data.get("selected_clan")
    editor = await get_member(message.from_user.id)

    # 1. Обработка удаления игрока из базы
    if message.text.strip() == "/delete":
        await remove_member(target_id)

        from utils.admin_logger import log_admin_action
        await log_admin_action(
            bot=bot, admin_id=message.from_user.id,
            admin_name=message.from_user.username or message.from_user.first_name,
            action_text=f"🗑 Полностью удалил из базы участника ID <code>{target_id}</code>.",
            clan_key=clan
        )

        await state.clear()
        await message.answer("🗑 Участник полностью удалён из базы.", reply_markup=main_menu(editor))
        await sync_roster_msg(bot, clan)
        return

    # 2. ИСПРАВЛЕНО: Валидация нового игрового ТЕГА через API вместо ручного ввода текста никнейма
    new_tag = message.text.strip().upper()
    await message.answer("⏳ Проверяем тег игрока через Brawl Stars API...")

    player_data = await get_player_profile(new_tag)
    if not player_data:
        await message.answer("❌ Игрок с таким тегом не найден в игре Brawl Stars. Проверьте тег и введите заново:")
        return

    new_nick = player_data["name"]
    trophies = player_data["trophies"]

    # Сохраняем верифицированные данные
    await upsert_member(
        user_id=target_id,
        game_nick=new_nick,
        player_tag=new_tag,
        trophies=trophies,
        registered=1,
        clan=clan
    )

    from utils.admin_logger import log_admin_action
    await log_admin_action(
        bot=bot, admin_id=message.from_user.id,
        admin_name=message.from_user.username or message.from_user.first_name,
        action_text=f"✏️ Обновил профиль ID <code>{target_id}</code> через API. Новый ник: <b>{hd.quote(new_nick)}</b>, кубки: {trophies:,}.",
        clan_key=clan
    )

    await state.clear()
    await message.answer(f"✅ Данные игрока успешно обновлены!\nНик: <b>{hd.quote(new_nick)}</b>\nКубки: {trophies:,}",
                         parse_mode="HTML", reply_markup=main_menu(editor))
    await sync_roster_msg(bot, clan)



@router.callback_query(F.data == "edit_list:cancel")
async def process_edit_list_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.answer("Редактирование отменено")


@router.message(F.text == "👤 Участники без ников")
async def unregistered_list(message: Message):
    member = await get_member(message.from_user.id)
    if not member or not is_any_admin(member):
        await message.answer("⛔ Нет прав.")
        return

    role = member.get("role", "member")

    if role in ("president", "grand_vice_president", "grand_vice"):
        clan_to_search = None
    else:
        clan_to_search = member.get("clan")

    members = await get_unregistered_members(clan_to_search)

    if not members:
        await message.answer("✅ Все участники успешно внесли свои игровые никнеймы!")
        return

    clan_title = CLAN_DISPLAY.get(clan_to_search, "Все кланы")
    lines = [f"<b>Участники без игрового ника ({clan_title}):</b>\n"]

    for m in members:
        uname = m.get("username")
        # ИСПРАВЛЕНО: Накладываем hd.quote на имена, чтобы HTML-теги в именах не ломали бота
        tg_name = f"@{hd.quote(str(uname))}" if uname else hd.quote(str(m.get("first_name") or "Игрок"))
        captured_clan = CLAN_DISPLAY.get(m.get('clan'), 'Не определен')
        lines.append(f"• {tg_name} — <code>{m['user_id']}</code> <i>(Клан: {captured_clan})</i>")

    await message.answer("\n".join(lines), parse_mode="HTML")


# ─── ДОБАВЛЕНИЕ ТВИНКА (Вице-президент и выше) ───────────────────────────────
@router.message(AdminStates.waiting_twink_nick)
async def add_twink_finalize(message: Message, state: FSMContext, bot: Bot):
    twink_tag = message.text.strip().upper()

    if not twink_tag.startswith("#"):
        twink_tag = f"#{twink_tag}"

    data = await state.get_data()
    editor = await get_member(message.from_user.id)

    await message.answer("⏳ Проверяем профиль твинка и его клуб через Brawl Stars API...")

    from services.api_service import get_player_profile
    player_data = await get_player_profile(twink_tag)

    if not player_data:
        await message.answer("❌ Твинк с таким тегом не найден в игре Brawl Stars. Проверьте тег и введите заново:")
        return

    game_nick = player_data["name"]
    trophies = player_data["trophies"]

    # ИСПРАВЛЕНО: Используем ваше родное рабочее поле вместо стандартного club->tag
    api_club_tag = player_data.get("clan_tag", "").upper()

    # Автоматически сопоставляем тег клуба из вашего API с конфигом CLAN_TAGS
    reversed_tags = {tag.upper(): key for key, tag in CLAN_TAGS.items()}
    clan = reversed_tags.get(api_club_tag)

    # Если твинк состоит в чужом клубе или вообще без клуба
    if not clan:
        await message.answer(
            f"❌ Этот твинк сейчас находится в клубе с тегом <code>{api_club_tag or 'нет тега'}</code>.\n"
            f"Его нет в списке наших официальных кланов сети! Сначала примите твинк в клуб в игре, а затем введите тег заново:",
            parse_mode="HTML"
        )
        return

    import aiosqlite
    from database import DB_PATH
    from aiogram.utils.markdown import html_decoration as hd

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO members (user_id, username, first_name, last_name, game_nick, player_tag, trophies, clan, role, registered)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'member', 1)
            """,
            (
                data.get("t_id"),
                data.get("t_uname"),
                data.get("t_fname"),
                data.get("t_lname"),
                game_nick,
                twink_tag,
                clan
            )
        )
        await db.commit()

    from utils.admin_logger import log_admin_action
    await log_admin_action(
        bot=bot,
        admin_id=message.from_user.id,
        admin_name=message.from_user.username or message.from_user.first_name,
        action_text=f"➕ Добавил твинк через API. Ник: <b>{hd.quote(game_nick)}</b>, кубки: {trophies:,}, определен в клан: {clan.upper()} (по тегу клуба {api_club_tag}), игроку ID <code>{data.get('t_id')}</code>.",
        clan_key=clan
    )

    await state.clear()

    from utils.keyboards import main_menu
    await message.answer(
        f"✅ Твинк успешно добавлен игроку!\n"
        f"Ник в игре: <b>{hd.quote(game_nick)}</b>\n"
        f"Кубки: <code>{trophies:,}</code>\n"
        f"Автоматически занесен в список: <b>{CLAN_DISPLAY.get(clan, clan).upper()}</b>",
        parse_mode="HTML",
        reply_markup=main_menu(editor)
    )

    try:
        from utils.roster_sync import sync_roster_msg
        await sync_roster_msg(bot, clan)
    except ImportError:
        pass
