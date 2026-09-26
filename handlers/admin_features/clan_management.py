"""Управление списками кланов: просмотр, редактирование, удаление, твинки."""
import logging

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.utils.markdown import html_decoration as hd

from database import (
    get_member,
    upsert_member,
    get_all_members,
    get_unregistered_members,
    get_clan_members,
    remove_member,
)
from utils.permissions import (
    can_appoint_admins,
    can_edit_list,
    get_admin_rights_from_file,
    is_any_admin,
)
from utils.keyboards import main_menu
from utils.roster_sync import sync_roster_msg
from utils.clan_account_service import (
    add_twink,
    delete_twink,
    get_clan_twinks,
    get_twink_by_tag,
    set_twink_role,
)
from .base import AdminStates
from config import CLAN_DISPLAY, CLAN_TAGS, ROLE_LABELS, ROLES
from services.api_service import get_player_profile

logger = logging.getLogger(__name__)
router = Router()


def _norm_tag(value: str) -> str:
    return str(value or "").strip().upper().replace("#", "")


async def _can_edit_clan(user_id: int) -> bool:
    member = await get_member(user_id)
    return can_edit_list(member) or can_edit_list(get_admin_rights_from_file(user_id))


async def _can_set_twink_role(user_id: int) -> bool:
    member = await get_member(user_id)
    return can_appoint_admins(member) or can_appoint_admins(get_admin_rights_from_file(user_id))


def _twink_role_label(role: str) -> str:
    return "👑 Лидер клана" if role == "president" else str(ROLE_LABELS.get(role, role))


def _twink_detail_text(twink: dict) -> str:
    nick = hd.quote(str(twink.get("game_nick") or "Без ника"))
    tag = hd.quote(str(twink.get("player_tag") or ""))
    role = twink.get("role") or "member"
    role_label = hd.quote(_twink_role_label(role))
    trophies = int(twink.get("trophies") or 0)
    return (
        f"🧬 Твинк: {nick} (<code>{tag}</code>)\n"
        f"🏆 Кубки: {trophies:,}\n"
        f"👤 Владелец TG ID: <code>{twink['owner_user_id']}</code>\n"
        f"🎖 Роль в ростере: {role_label}\n\n"
        "Нажми «Изменить роль» или отправь /delete для удаления твинка.\n"
        "Роль твинка не выдаёт его владельцу права администратора бота."
    )


def _twink_detail_keyboard(can_assign: bool) -> InlineKeyboardMarkup:
    rows = []
    if can_assign:
        rows.append([InlineKeyboardButton(
            text="🎖 Изменить роль твинка", callback_data="edit_twink_role:choose"
        )])
    rows.append([InlineKeyboardButton(
        text="◀️ К выбору клана", callback_data="edit_clan_back_to_sel"
    )])
    rows.append([InlineKeyboardButton(text="❌ Выйти из меню", callback_data="edit_list:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text == "📋 Редактировать список клана")
async def edit_list_select_clan(message: Message, state: FSMContext):
    if not await _can_edit_clan(message.from_user.id):
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
    if not await _can_edit_clan(callback.from_user.id):
        await callback.answer("⛔ Недостаточно прав.", show_alert=True)
        return
    clan = callback.data.split(":")[1]
    if clan not in CLAN_DISPLAY:
        await callback.answer("Клан не найден", show_alert=True)
        return
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

    # Твинки хранятся в отдельной таблице member_twinks — подтягиваем их тоже.
    try:
        twinks = await get_clan_twinks(clan)
    except Exception as e:
        logger.error(f"Не удалось загрузить твинков клана {clan}: {e}")
        twinks = []

    lines = [f"<b>Редактирование списка: {hd.quote(str(CLAN_DISPLAY.get(clan, clan)).upper())}</b>\n"]

    if not combined_members and not twinks:
        lines.append("<i>Список сейчас пуст.</i>")
    else:
        lines.append("<b>— Основные аккаунты —</b>")
        if not combined_members:
            lines.append("<i>нет</i>")
        for m in combined_members:
            uid = m["user_id"]
            raw_nick = m.get("game_nick") or "(Нет игрового ника ❌)"
            nick = hd.quote(str(raw_nick))
            ptag = m.get("player_tag") or "Нет тега 🚫"
            trophies = m.get("trophies", 0) or 0
            trophies_str = f" | 🏆 {trophies:,}" if trophies > 0 else ""
            uname = f"@{hd.quote(str(m['username']))}" if m.get("username") else f"ID: {uid}"
            reg_marker = "✅" if m.get("registered") == 1 else "💤"
            lines.append(f"• <code>{uid}</code> | {reg_marker} {uname} | {nick} ({hd.quote(str(ptag))}{trophies_str})")

        lines.append("")
        lines.append("<b>— Твинки 🧬 —</b>")
        if not twinks:
            lines.append("<i>нет</i>")
        for t in twinks:
            nick = hd.quote(str(t.get("game_nick") or "Без ника"))
            ptag = hd.quote(str(t.get("player_tag") or "???"))
            trophies = t.get("trophies", 0) or 0
            trophies_str = f" | 🏆 {trophies:,}" if trophies > 0 else ""
            owner = t.get("owner_user_id")
            owner_name = t.get("username") or t.get("first_name") or f"ID {owner}"
            role = t.get("role") or "member"
            role_label = hd.quote(_twink_role_label(role))
            lines.append(
                f"• 🧬 {nick} (<code>{ptag}</code>{trophies_str}) | "
                f"роль: {role_label} | владелец: {hd.quote(str(owner_name))} "
                f"(ID <code>{owner}</code>)"
            )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад к выбору клана", callback_data="edit_clan_back_to_sel")],
        [InlineKeyboardButton(text="❌ Выйти из меню", callback_data="edit_list:cancel")]
    ])

    footer = (
        "\n\nЧтобы изменить основу — введи числовой <b>user_id</b>."
        "\nЧтобы изменить роль или удалить твинка — введи его игровой "
        "<b>тег</b> (например <code>#ABC123</code>)."
    )
    await state.set_state(AdminStates.waiting_edit_member_id)
    # Split between complete HTML lines, never in the middle of a tag or a twink entry.
    chunks = []
    current = ""
    for line in lines:
        addition = ("\n" if current else "") + line
        if current and len(current) + len(addition) + len(footer) > 3900:
            chunks.append(current)
            current = f"<i>Редактирование списка (продолжение)</i>\n{line}"
        else:
            current += addition
    chunks.append(current + footer)

    await callback.message.edit_text(chunks[0], parse_mode="HTML", reply_markup=kb)
    for chunk in chunks[1:]:
        await callback.message.answer(chunk, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "edit_clan_back_to_sel")
async def edit_clan_back_to_sel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await _can_edit_clan(callback.from_user.id):
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
    if not await _can_edit_clan(message.from_user.id):
        await state.clear()
        await message.answer("⛔ Права на редактирование списка больше не действуют.")
        return
    text = (message.text or "").strip()
    data = await state.get_data()
    clan = data.get("selected_clan")

    # Ветка твинка: ввели игровой тег.
    if text.startswith("#") or (not text.lstrip("-").isdigit() and len(_norm_tag(text)) >= 3):
        twink = await get_twink_by_tag(text)
        if not twink:
            await message.answer("❌ Твинк с таким тегом не найден. Введи тег из списка выше (с #) или числовой user_id основы.")
            return
        if twink.get("clan") != clan:
            await message.answer("❌ Этот твинк относится к другому клану. Выбери его клан в редакторе.")
            return
        await state.update_data(
            edit_target_type="twink", edit_target_tag=twink["player_tag"], edit_target_id=None
        )
        await state.set_state(AdminStates.waiting_new_nick_for_member)
        await message.answer(
            _twink_detail_text(twink),
            parse_mode="HTML",
            reply_markup=_twink_detail_keyboard(await _can_set_twink_role(message.from_user.id)),
        )
        return

    if not text.lstrip("-").isdigit():
        await message.answer("Введи числовой user_id основы или игровой тег твинка (с #):")
        return

    target_id = int(text)
    target = await get_member(target_id)
    if not target:
        await message.answer("❌ Участник с таким ID не найден в базе данных бота.")
        return

    await state.update_data(edit_target_type="member", edit_target_id=target_id, edit_target_tag=None)
    current_nick = hd.quote(str(target.get("game_nick") or "Отсутствует"))
    current_tag = target.get("player_tag") or "Не привязан"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="edit_list:cancel")]
    ])
    await message.answer(
        f"Участник: @{target.get('username') or 'нет'} (ID: {target_id})\n"
        f"Текущий игровой ник: {current_nick}\n"
        f"Текущий игровой тег: {hd.quote(str(current_tag))}\n\n"
        f"✍️ Напиши для него новый тег игрока Brawl Stars (например, #9PJYV82CC).\n"
        f"Бот автоматически обновит его ник и кубки через API.\n\n"
        f"(Или отправь команду /delete чтобы убрать его из этого клана)",
        parse_mode="HTML",
        reply_markup=kb
    )
    await state.set_state(AdminStates.waiting_new_nick_for_member)


async def _twink_from_edit_session(callback: CallbackQuery, state: FSMContext):
    if not await _can_edit_clan(callback.from_user.id):
        await callback.answer("⛔ Нет доступа к редактированию клана.", show_alert=True)
        return None
    if await state.get_state() != AdminStates.waiting_new_nick_for_member.state:
        await callback.answer("Сессия устарела. Открой редактор списка заново.", show_alert=True)
        return None
    data = await state.get_data()
    if data.get("edit_target_type") != "twink" or not data.get("edit_target_tag"):
        await callback.answer("Сначала выбери твинка по игровому тегу.", show_alert=True)
        return None
    twink = await get_twink_by_tag(data["edit_target_tag"])
    if not twink or twink.get("clan") != data.get("selected_clan"):
        await callback.answer("Твинк уже удалён или перемещён. Открой список заново.", show_alert=True)
        return None
    return twink


@router.callback_query(F.data == "edit_twink_role:choose")
async def choose_twink_role(callback: CallbackQuery, state: FSMContext):
    twink = await _twink_from_edit_session(callback, state)
    if not twink:
        return
    if not await _can_set_twink_role(callback.from_user.id):
        await callback.answer("⛔ Назначать роли может только президент.", show_alert=True)
        return

    current = twink.get("role") or "member"
    rows = [
        [InlineKeyboardButton(
            text=f"{'✅ ' if role == current else ''}{_twink_role_label(role)}",
            callback_data=f"edit_twink_role:set:{role}",
        )]
        for role in sorted(ROLES, key=ROLES.get)
    ]
    rows.append([InlineKeyboardButton(text="◀️ Назад к твинку", callback_data="edit_twink_role:back")])
    await callback.message.edit_text(
        f"🎖 Выбери роль для {hd.quote(str(twink.get('game_nick') or 'твинка'))} "
        f"(<code>{hd.quote(str(twink['player_tag']))}</code>).\n"
        "Роль изменит только его место в ростере; права владельца в боте не изменятся.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data == "edit_twink_role:back")
async def back_to_twink_detail(callback: CallbackQuery, state: FSMContext):
    twink = await _twink_from_edit_session(callback, state)
    if not twink:
        return
    await callback.message.edit_text(
        _twink_detail_text(twink),
        parse_mode="HTML",
        reply_markup=_twink_detail_keyboard(await _can_set_twink_role(callback.from_user.id)),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_twink_role:set:"))
async def save_twink_role(callback: CallbackQuery, state: FSMContext, bot: Bot):
    twink = await _twink_from_edit_session(callback, state)
    if not twink:
        return
    if not await _can_set_twink_role(callback.from_user.id):
        await callback.answer("⛔ Назначать роли может только президент.", show_alert=True)
        return
    role = callback.data.removeprefix("edit_twink_role:set:")
    if role not in ROLES:
        await callback.answer("Неизвестная роль.", show_alert=True)
        return
    if (twink.get("role") or "member") == role:
        await callback.answer("Эта роль уже назначена.")
        return

    clan = twink["clan"]
    tag = twink["player_tag"]
    if not await set_twink_role(tag, clan, role):
        await callback.answer("Твинк уже удалён или перемещён. Открой список заново.", show_alert=True)
        return
    await callback.answer("Роль сохранена; обновляю ростер...")

    roster_updated = False
    try:
        roster_updated = await sync_roster_msg(bot, clan, force=True)
    except Exception:
        logger.exception("Не удалось обновить ростер %s после смены роли твинка %s", clan, tag)

    try:
        from utils.admin_logger import log_admin_action
        await log_admin_action(
            bot=bot,
            admin_id=callback.from_user.id,
            admin_name=callback.from_user.username or callback.from_user.first_name,
            action_text=(
                f"Назначил твинку {hd.quote(str(twink.get('game_nick') or 'Игрок'))} "
                f"({hd.quote(str(tag))}) роль {_twink_role_label(role)} в ростере."
            ),
            clan_key=clan,
        )
    except Exception:
        logger.exception("Не удалось записать смену роли твинка %s в админ-лог", tag)

    updated = await get_twink_by_tag(tag)
    result = "✅ Ростер обновлён." if roster_updated else "⚠️ Ростер пока не обновился; проверь логи бота."
    await callback.message.edit_text(
        f"✅ Роль твинка изменена на {hd.quote(_twink_role_label(role))}.\n{result}\n\n"
        + _twink_detail_text(updated or {**twink, "role": role}),
        parse_mode="HTML",
        reply_markup=_twink_detail_keyboard(True),
    )


@router.message(AdminStates.waiting_new_nick_for_member)
async def edit_list_set_nick(message: Message, state: FSMContext, bot: Bot):
    if not await _can_edit_clan(message.from_user.id):
        await state.clear()
        await message.answer("⛔ Права на редактирование списка больше не действуют.")
        return
    data = await state.get_data()
    target_type = data.get("edit_target_type", "member")
    clan = data.get("selected_clan")
    editor = await get_member(message.from_user.id)
    text = (message.text or "").strip()

    # ── Удаление твинка ──
    if target_type == "twink":
        if text != "/delete":
            await message.answer("Для изменения роли нажми кнопку под сообщением о твинке. Для удаления отправь /delete.")
            return
        tag = data.get("edit_target_tag")
        twink = await get_twink_by_tag(tag)
        if not twink or twink.get("clan") != clan:
            await message.answer("❌ Твинк не найден в этом клане. Вернись к списку и выбери его снова.")
            return
        ok = await delete_twink(tag)
        from utils.admin_logger import log_admin_action
        await log_admin_action(
            bot=bot,
            admin_id=message.from_user.id,
            admin_name=message.from_user.username or message.from_user.first_name,
            action_text=f"🗑 Удалил твинк {hd.quote(str(tag))} из клана {hd.quote(str(clan))}.",
            clan_key=clan
        )
        await state.clear()
        await message.answer(
            f"🗑 Твинк <code>{hd.quote(str(tag))}</code> удалён из клана." if ok else "❌ Твинк уже отсутствует в базе.",
            parse_mode="HTML",
            reply_markup=main_menu(editor)
        )
        try:
            await sync_roster_msg(bot, clan, force=True)
        except Exception as e:
            logger.error(f"Ростер не обновился после удаления твинка: {e}")
        return

    # ── Удаление основы ──
    target_id = data.get("edit_target_id")
    if text == "/delete":
        await remove_member(target_id)
        from utils.admin_logger import log_admin_action
        await log_admin_action(
            bot=bot,
            admin_id=message.from_user.id,
            admin_name=message.from_user.username or message.from_user.first_name,
            action_text=f"🗑 Полностью удалил из базы участника ID {target_id}.",
            clan_key=clan
        )
        await state.clear()
        await message.answer("🗑 Участник полностью удалён из базы.", reply_markup=main_menu(editor))
        await sync_roster_msg(bot, clan)
        return

    # ── Смена тега основы через API ──
    new_tag = text.strip().upper()
    await message.answer("⏳ Проверяем тег игрока через Brawl Stars API...")
    player_data = await get_player_profile(new_tag)
    if not player_data:
        await message.answer("❌ Игрок с таким тегом не найден в игре Brawl Stars. Проверьте тег и введите заново:")
        return
    new_nick = player_data["name"]
    trophies = player_data["trophies"]

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
        bot=bot,
        admin_id=message.from_user.id,
        admin_name=message.from_user.username or message.from_user.first_name,
        action_text=f"✏️ Обновил профиль ID {target_id} через API. Новый ник: {hd.quote(new_nick)}, кубки: {trophies:,}.",
        clan_key=clan
    )
    await state.clear()
    await message.answer(
        f"✅ Данные игрока успешно обновлены!\nНик: {hd.quote(new_nick)}\nКубки: {trophies:,}",
        parse_mode="HTML",
        reply_markup=main_menu(editor)
    )
    await sync_roster_msg(bot, clan)


@router.callback_query(F.data == "edit_list:cancel")
async def process_edit_list_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
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
    lines = [f"Участники без игрового ника ({hd.quote(str(clan_title))}):\n"]
    for m in members:
        uname = m.get("username")
        tg_name = f"@{hd.quote(str(uname))}" if uname else hd.quote(str(m.get("first_name") or "Игрок"))
        captured_clan = CLAN_DISPLAY.get(m.get("clan"), "Не определен")
        lines.append(f"• {tg_name} — {m['user_id']} (Клан: {hd.quote(str(captured_clan))})")
    await message.answer("\n".join(lines), parse_mode="HTML")
