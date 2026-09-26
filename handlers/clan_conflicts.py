"""Решение конфликтов, когда зарегистрированный игрок входит в чат другого клана."""
import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User
from aiogram.utils.markdown import html_decoration as hd

from config import ADMIN_CHAT_ID, CLAN_DISPLAY, CLAN_TAGS, ROLE_LABELS
from database import get_member
from league.league_db import cancel_pending_departure
from services.api_service import get_player_profile
from utils.clan_account_service import (
    add_twink,
    assign_admin_role,
    create_or_get_conflict,
    get_conflict,
    move_main_account,
    resolve_conflict,
)
from utils.permissions import get_admin_rights_from_file, is_any_admin
from utils.roster_sync import sync_roster_msg

logger = logging.getLogger(__name__)
router = Router()


class ClanConflictState(StatesGroup):
    waiting_twink_tag = State()


def _clean_tag(value) -> str:
    return str(value or "").upper().strip().replace("#", "")


async def _is_admin(user_id: int) -> bool:
    member = await get_member(user_id)
    return is_any_admin(member) or get_admin_rights_from_file(user_id) is not None


def _actions_keyboard(conflict_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🛡 Добавить как администрацию",
            callback_data=f"clconf:admin:{conflict_id}",
        )],
        [InlineKeyboardButton(
            text="➕ Добавить твинк аккаунт",
            callback_data=f"clconf:twink:{conflict_id}",
        )],
        [InlineKeyboardButton(
            text="🔄 Перезаписать аккаунт в новый клан",
            callback_data=f"clconf:move:{conflict_id}",
        )],
        [InlineKeyboardButton(
            text="✅ Ничего, это уже администратор",
            callback_data=f"clconf:ignore:{conflict_id}",
        )],
    ])


async def send_clan_conflict_prompt(bot: Bot, user: User, member: dict, new_clan: str) -> int:
    """Создаёт или повторно использует конфликт и отправляет решение в админ-чат."""
    old_clan = member.get("clan")
    conflict_id = await create_or_get_conflict(
        user.id,
        old_clan,
        new_clan,
        user.username,
    )
    username = f"@{hd.quote(user.username)}" if user.username else f"ID <code>{user.id}</code>"
    old_title = hd.quote(str(CLAN_DISPLAY.get(old_clan, old_clan or "неизвестном клане")))
    new_title = hd.quote(str(CLAN_DISPLAY.get(new_clan, new_clan)))
    text = (
        f"⚠️ Участник {username} зашёл в чат <b>{new_title}</b>, "
        f"но уже зарегистрирован в <b>{old_title}</b>.\n\n"
        "Что с ним делать?"
    )
    if not ADMIN_CHAT_ID:
        logger.error("ADMIN_CHAT_ID не настроен: конфликт входа не отправлен")
        return conflict_id
    try:
        await bot.send_message(
            ADMIN_CHAT_ID,
            text,
            parse_mode="HTML",
            reply_markup=_actions_keyboard(conflict_id),
        )
    except Exception as e:
        logger.error(f"Не удалось отправить конфликт входа в админ-чат: {e}")
    return conflict_id


@router.callback_query(F.data.startswith("clconf:admin:"))
async def choose_admin_role(call: CallbackQuery):
    if not await _is_admin(call.from_user.id):
        await call.answer("Нет административных прав", show_alert=True)
        return
    conflict_id = int(call.data.rsplit(":", 1)[-1])
    conflict = await get_conflict(conflict_id)
    if not conflict or conflict.get("status") != "pending":
        await call.answer("Это решение уже обработано", show_alert=True)
        return
    buttons = []
    for role, label in ROLE_LABELS.items():
        if role == "member":
            continue
        buttons.append([InlineKeyboardButton(
            text=label,
            callback_data=f"clconf:role:{conflict_id}:{role}",
        )])
    buttons.append([InlineKeyboardButton(
        text="◀️ Назад",
        callback_data=f"clconf:back:{conflict_id}",
    )])
    await call.message.edit_text(
        "🛡 <b>Какая у него будет роль?</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await call.answer()


@router.callback_query(F.data.startswith("clconf:back:"))
async def conflict_back(call: CallbackQuery):
    if not await _is_admin(call.from_user.id):
        await call.answer("Нет прав", show_alert=True)
        return
    conflict_id = int(call.data.rsplit(":", 1)[-1])
    conflict = await get_conflict(conflict_id)
    if not conflict or conflict.get("status") != "pending":
        await call.answer("Решение уже обработано", show_alert=True)
        return
    await call.message.edit_text(
        "Выбери действие для участника:",
        reply_markup=_actions_keyboard(conflict_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("clconf:role:"))
async def set_admin_role(call: CallbackQuery, bot: Bot):
    if not await _is_admin(call.from_user.id):
        await call.answer("Нет прав", show_alert=True)
        return
    parts = call.data.split(":", 3)
    conflict_id, role = int(parts[2]), parts[3]
    if role not in ROLE_LABELS or role == "member":
        await call.answer("Неизвестная роль", show_alert=True)
        return
    conflict = await get_conflict(conflict_id)
    if not conflict or conflict.get("status") != "pending":
        await call.answer("Решение уже обработано", show_alert=True)
        return
    await assign_admin_role(conflict["user_id"], role)
    await resolve_conflict(conflict_id, f"admin:{role}", call.from_user.id)
    cancel_pending_departure(conflict["user_id"])
    if conflict.get("old_clan"):
        await sync_roster_msg(bot, conflict["old_clan"], force=True)
    await call.message.edit_text(
        f"✅ Пользователю назначена роль: {ROLE_LABELS[role]}.\n"
        "Основная привязка клана не изменена."
    )
    await call.answer()
    try:
        await bot.send_message(
            conflict["user_id"],
            f"🛡 Тебе назначена роль в боте: {ROLE_LABELS[role]}.",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("clconf:twink:"))
async def twink_start(call: CallbackQuery, state: FSMContext):
    if not await _is_admin(call.from_user.id):
        await call.answer("Нет прав", show_alert=True)
        return
    conflict_id = int(call.data.rsplit(":", 1)[-1])
    conflict = await get_conflict(conflict_id)
    if not conflict or conflict.get("status") != "pending":
        await call.answer("Решение уже обработано", show_alert=True)
        return
    await state.set_state(ClanConflictState.waiting_twink_tag)
    await state.update_data(conflict_id=conflict_id)
    await call.message.answer(
        "➕ Отправь следующим сообщением тег твинк-аккаунта Brawl Stars.\n"
        "Для отмены: /cancel",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀️ Назад к выбору действия", callback_data=f"clconf:twink_back:{conflict_id}")
        ]]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("clconf:twink_back:"))
async def twink_back(call: CallbackQuery, state: FSMContext):
    if not await _is_admin(call.from_user.id):
        await call.answer("Нет прав", show_alert=True)
        return
    conflict_id = int(call.data.rsplit(":", 1)[-1])
    conflict = await get_conflict(conflict_id)
    await state.clear()
    if not conflict or conflict.get("status") != "pending":
        await call.answer("Это решение уже обработано", show_alert=True)
        return
    await call.message.edit_text(
        "Выбери действие для участника:",
        reply_markup=_actions_keyboard(conflict_id),
    )
    await call.answer()


@router.message(ClanConflictState.waiting_twink_tag)
async def twink_finish(message: Message, state: FSMContext, bot: Bot):
    if not await _is_admin(message.from_user.id):
        await state.clear()
        return
    raw = (message.text or "").strip()
    if raw.lower().split("@", 1)[0] == "/cancel":
        await state.clear()
        await message.answer("❌ Добавление твинка отменено.")
        return
    data = await state.get_data()
    conflict = await get_conflict(int(data.get("conflict_id") or 0))
    if not conflict or conflict.get("status") != "pending":
        await state.clear()
        await message.answer("❌ Конфликт уже обработан.")
        return
    await message.answer("⏳ Проверяю твинк через Brawl Stars API...")
    profile = await get_player_profile(raw)
    if not profile:
        await message.answer("❌ Аккаунт не найден. Проверь тег и отправь снова.")
        return
    expected_club = _clean_tag(CLAN_TAGS.get(conflict["new_clan"]))
    actual_club = _clean_tag(profile.get("clan_tag"))
    if not expected_club or actual_club != expected_club:
        await message.answer(
            f"❌ Этот аккаунт не состоит в {CLAN_DISPLAY.get(conflict['new_clan'], conflict['new_clan'])}.\n"
            "Введи другой тег или отправь /cancel."
        )
        return
    try:
        await add_twink(
            conflict["user_id"],
            profile.get("tag") or raw,
            profile.get("name") or "Игрок",
            profile.get("trophies") or 0,
            conflict["new_clan"],
        )
    except ValueError as exc:
        await message.answer(f"❌ {exc} Введи другой тег или отправь /cancel.")
        return
    await resolve_conflict(conflict["id"], "twink", message.from_user.id)
    cancel_pending_departure(conflict["user_id"])
    await state.clear()
    await sync_roster_msg(bot, conflict["new_clan"], force=True)
    await message.answer(
        f"✅ Твинк {hd.quote(profile.get('name') or 'Игрок')} добавлен в "
        f"{hd.quote(CLAN_DISPLAY.get(conflict['new_clan'], conflict['new_clan']))}.",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("clconf:move:"))
async def move_account(call: CallbackQuery, bot: Bot):
    if not await _is_admin(call.from_user.id):
        await call.answer("Нет прав", show_alert=True)
        return
    conflict_id = int(call.data.rsplit(":", 1)[-1])
    conflict = await get_conflict(conflict_id)
    if not conflict or conflict.get("status") != "pending":
        await call.answer("Решение уже обработано", show_alert=True)
        return
    await move_main_account(conflict["user_id"], conflict["new_clan"])
    await resolve_conflict(conflict_id, "move", call.from_user.id)
    cancel_pending_departure(conflict["user_id"])
    for clan in {conflict.get("old_clan"), conflict.get("new_clan")}:
        if clan:
            await sync_roster_msg(bot, clan, force=True)
    await call.message.edit_text(
        f"✅ Аккаунт пользователя перезаписан в "
        f"{hd.quote(CLAN_DISPLAY.get(conflict['new_clan'], conflict['new_clan']))}.",
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data.startswith("clconf:ignore:"))
async def ignore_conflict(call: CallbackQuery):
    if not await _is_admin(call.from_user.id):
        await call.answer("Нет прав", show_alert=True)
        return
    conflict_id = int(call.data.rsplit(":", 1)[-1])
    conflict = await get_conflict(conflict_id)
    if not conflict or conflict.get("status") != "pending":
        await call.answer("Решение уже обработано", show_alert=True)
        return
    await resolve_conflict(conflict_id, "ignore_admin", call.from_user.id)
    cancel_pending_departure(conflict["user_id"])
    await call.message.edit_text(
        "✅ Ничего не изменено. Пользователь считается администрацией с уже существующими правами."
    )
    await call.answer()