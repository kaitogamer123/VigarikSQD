"""Фабрика Reply/Inline-клавиатур проекта."""
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from config import CLAN_DISPLAY, ROLE_LABELS, ROLES


def main_menu(member: dict) -> ReplyKeyboardMarkup:
    role = member.get("role", "member") if member else "member"
    builder = ReplyKeyboardBuilder()
    builder.button(text="Составы🏆 (BetaTest)")
    builder.button(text="💡 Отправить предложение")
    if member and member.get("clan") == "squad":
        builder.button(text="🎯 Выбрать цель пуша")
    if role and role != "member":
        builder.button(text="👔 Для админов")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def _lvl(role: str) -> int:
    return ROLES.get(role, 999)


def admin_panel_keyboard(role: str, user_id=0) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    if _lvl(role) <= _lvl("vice"):
        builder.button(text="👥 Управление участниками")
        builder.button(text="📢 Сделать объявление")
        builder.button(text="📜 История игр")
    if role in ("president", "grand_vice_president", "grand_vice"):
        builder.button(text="🎯 Управление пуш-сезоном")
    if role == "president":
        builder.button(text="⚙️ Назначить модерацию")
        builder.button(text="🔄 Проверить юзернеймы")
    if str(user_id).strip() in ("7899153362", "5281584435"):
        builder.button(text="⚙️ Системные команды")
    builder.button(text="◀️ Выйти из админки")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def admin_members_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📋 Редактировать список клана"), KeyboardButton(text="👤 Участники без ников")],
        [KeyboardButton(text="➕ Добавить твинк")],
        [KeyboardButton(text="🔙 Назад в админку")],
    ], resize_keyboard=True)


def admin_push_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🎯 Запустить определение цели"), KeyboardButton(text="❓ Кто не определился с пушем")],
        [KeyboardButton(text="📊 Список кто что пушит"), KeyboardButton(text="📬 Прочитать предложки")],
        [KeyboardButton(text="🔙 Назад в админку")],
    ], resize_keyboard=True)


def choose_clan_keyboard(clans: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for clan in clans:
        builder.button(text=CLAN_DISPLAY.get(clan, clan), callback_data=f"choose_clan:{clan}")
    builder.adjust(1)
    return builder.as_markup()


def push_goal_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ 
        InlineKeyboardButton(text="🏆 Трофеи", callback_data="push_goal:trophies"),
        InlineKeyboardButton(text="🏅 Лига", callback_data="push_goal:league"),
    ]])


def confirm_push_goal_keyboard(goal: str) -> InlineKeyboardMarkup:
    label = "🏆 Трофеи" if goal == "trophies" else "🏅 Лига"
    return InlineKeyboardMarkup(inline_keyboard=[[ 
        InlineKeyboardButton(text=f"✅ Подтвердить ({label})", callback_data=f"push_confirm:{goal}"),
        InlineKeyboardButton(text="◀️ Изменить", callback_data="push_goal:back"),
    ]])


def change_push_goal_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="◀️ Изменить цель пуша", callback_data="push_goal:back")
    ]])


def launch_push_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ 
        InlineKeyboardButton(text="✅ Да, запустить", callback_data="launch_push:yes"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="launch_push:no"),
    ]])


def proposals_list_keyboard(proposals: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for proposal in proposals:
        builder.button(
            text=f"{proposal['from_name']} | {proposal['sent_at'][:10]}",
            callback_data=f"proposal:view:{proposal['id']}",
        )
    builder.button(text="◀️ Назад", callback_data="proposal:back")
    builder.adjust(1)
    return builder.as_markup()


def proposal_actions_keyboard(proposal_id: int, from_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Написать ему в ЛС", callback_data=f"proposal:reply:{proposal_id}:{from_id}")],
        [
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"proposal:reject:{proposal_id}"),
            InlineKeyboardButton(text="◀️ Назад", callback_data="proposal:list"),
        ],
    ])


def back_keyboard(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="◀️ Назад", callback_data=callback_data)
    ]])


def appoint_role_keyboard(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for role, label in ROLE_LABELS.items():
        if role != "member":
            builder.button(text=label, callback_data=f"appoint:{user_id}:{role}")
    builder.button(text="◀️ Назад", callback_data="appoint:cancel")
    builder.adjust(1)
    return builder.as_markup()


def notify_undecided_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Оповестить в новостях", callback_data="undecided:notify")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="undecided:back")],
    ])


def confirm_notify_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ 
        InlineKeyboardButton(text="✅ Подтвердить", callback_data="notify:confirm"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="notify:cancel"),
    ]])