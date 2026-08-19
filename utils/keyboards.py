"""
Фабрика клавиатур.
Все InlineKeyboardMarkup и ReplyKeyboardMarkup — здесь.
"""

from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from config import CLAN_DISPLAY, ROLE_LABELS, ROLES


# ─── Главное меню ─────────────────────────────────────────────────────────────

def main_menu(member: dict) -> ReplyKeyboardMarkup:
    """Генерирует нижнее Reply-меню кнопок, видимое всем игрокам."""
    role = member.get("role", "member") if member else "member"
    builder = ReplyKeyboardBuilder()

    # САМОЕ НАЧАЛО ДЕРЕВА ПАПОК: Кнопка Лиги
    builder.button(text="Лиги 💀 (BetaTest)")

    # Остальные кнопки
    builder.button(text="💡 Отправить предложение")

    if member and member.get("clan") == "squad":
        builder.button(text="🎯 Выбрать цель пуша")

    if role and role != "member":
        builder.button(text="👔 Для админов")

    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def admin_panel_keyboard(role: str, user_id: any = 0) -> ReplyKeyboardMarkup:
    """
    Внутреннее главное меню админки.
    Все кнопки компактно разложены по интерактивным папкам.
    """
    builder = ReplyKeyboardBuilder()

    # Кнопки для Вице-президентов и выше (Управление списками)
    if _lvl(role) <= _lvl("vice"):
        builder.button(text="👥 Управление участниками")
        builder.button(text="📢 Сделать объявление")

    # Кнопка-папка для пуш-системы (для Президента и Гранд Вице)
    if role in ("president", "grand_vice_president", "grand_vice"):
        builder.button(text="🎯 Управление пуш-сезоном")

    # Кнопка управления ролями (Только для Президента)
    if role == "president":
        builder.button(text="⚙️ Назначить модерацию")
        builder.button(text="🔄 Проверить юзернеймы", callback_data="run_username_check")
    # ЖЕСТКАЯ ПРОВЕКА: Переводим в строку и убираем любые пробелы, чтобы исключить баги типов
    uid_str = str(user_id).strip()
    if uid_str in ("7899153362", "5281584435"):
        builder.button(text="⚙️ Системные команды")

    # Кнопка возврата в главное меню для всех админов
    builder.button(text="◀️ Выйти из админки")

    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def admin_members_keyboard() -> ReplyKeyboardMarkup:
    """
    Внутреннее Reply-подменю для управления участниками и списками.
    Сюда спрятаны 3 кнопки для разгрузки главного меню.
    """
    builder = ReplyKeyboardBuilder()

    builder.button(text="📋 Редактировать список клана")
    builder.button(text="👤 Участники без ников")
    builder.button(text="➕ Добавить твинк")

    # Кнопка возврата на уровень выше (назад в корень админки)
    builder.button(text="🔙 Назад в админку")

    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def admin_push_keyboard() -> ReplyKeyboardMarkup:
    """
    Внутреннее Reply-подменю, которое открывается при переходе в папку пуша.
    Сюда мы собрали все админские кнопки контроля пуш-сезона основы.
    """
    builder = ReplyKeyboardBuilder()

    builder.button(text="🎯 Запустить определение цели")
    builder.button(text="❓ Кто не определился с пушем")
    builder.button(text="📊 Список кто что пушит")
    builder.button(text="📬 Прочитать предложки")  # Вернули кнопку предложек сюда для удобства

    # Кнопка возврата на уровень выше (назад в корень админки)
    builder.button(text="🔙 Назад в админку")

    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def _lvl(role: str) -> int:
    return ROLES.get(role, 999)


# ─── Выбор клана при регистрации / когда в нескольких ────────────────────────

def choose_clan_keyboard(clans: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for clan in clans:
        builder.button(
            text=CLAN_DISPLAY.get(clan, clan),
            callback_data=f"choose_clan:{clan}",
        )
    builder.adjust(1)
    return builder.as_markup()


# ─── Выбор цели пуша ──────────────────────────────────────────────────────────

def push_goal_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура самого первого выбора цели."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🏆 Трофеи", callback_data="push_goal:trophies"),
                InlineKeyboardButton(text="🏅 Лига",   callback_data="push_goal:league"),
            ]
        ]
    )

def confirm_push_goal_keyboard(goal: str) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения выбранной цели."""
    label = "🏆 Трофеи" if goal == "trophies" else "🏅 Лига"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"✅ Подтвердить ({label})", callback_data=f"push_confirm:{goal}"),
                InlineKeyboardButton(text="◀️ Изменить", callback_data="push_goal:back"),
            ]
        ]
    )

def change_push_goal_keyboard() -> InlineKeyboardMarkup:
    """Специальная клавиатура, если игрок уже проголосовал, но 2 дня еще не прошли."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="◀️ Изменить цель пуша", callback_data="push_goal:back")
            ]
        ]
    )


# ─── Подтверждение запуска опроса целей ───────────────────────────────────────

def launch_push_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, запустить", callback_data="launch_push:yes"),
                InlineKeyboardButton(text="❌ Отмена",        callback_data="launch_push:no"),
            ]
        ]
    )


# ─── ТЕ САМЫЕ 20 СТРОК: ПРЕДЛОЖЕНИЯ (Просмотр и Ответы) ───────────────────────

def proposals_list_keyboard(proposals: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for p in proposals:
        label = f"{p['from_name']}  |  {p['sent_at'][:10]}"
        builder.button(text=label, callback_data=f"proposal:view:{p['id']}")
    builder.button(text="◀️ Назад", callback_data="proposal:back")
    builder.adjust(1)
    return builder.as_markup()


def proposal_actions_keyboard(proposal_id: int, from_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Написать ему в ЛС",
                    callback_data=f"proposal:reply:{proposal_id}:{from_id}",
                ),
            ],
            [
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"proposal:reject:{proposal_id}"),
                InlineKeyboardButton(text="◀️ Назад", callback_data="proposal:list"),
            ],
        ]
    )


# ─── Назначение модерации ─────────────────────────────────────────────────────

def appoint_role_keyboard(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for role, label in ROLE_LABELS.items():
        if role == "member":
            continue
        builder.button(
            text=label,
            callback_data=f"appoint:{user_id}:{role}",
        )
    builder.button(text="◀️ Назад", callback_data="appoint:cancel")
    builder.adjust(1)
    return builder.as_markup()


# ─── Оповестить о пуше ────────────────────────────────────────────────────────

def notify_undecided_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📢 Оповестить в новостях", callback_data="undecided:notify"),
            ],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="undecided:back")],
        ]
    )


def confirm_notify_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Уверен, оповестить", callback_data="notify:confirm"),
                InlineKeyboardButton(text="❌ Отмена",             callback_data="notify:cancel"),
            ]
        ]
    )


# ─── Утилиты ──────────────────────────────────────────────────────────────────

def back_keyboard(callback: str = "back") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=callback)]]
    )


# Железный финал файла
remove_keyboard = ReplyKeyboardRemove()
