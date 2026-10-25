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
    if member and member.get("clan") == "squad" and member.get("registered") == 1:
        builder.button(text="🎯 Выбрать цель пуша")
    # Этап 5: сдача результатов матчей турниров (лидерам составов)
    builder.button(text="🏁 Мои матчи")
    if role and role != "member":
        builder.button(text="👔 Для админов")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def simple_list_keyboard(rows: list[tuple[str, str]],
                         footer: dict | None = None) -> InlineKeyboardMarkup:
    """Универсальный inline-список: [(текст, callback_data), ...]."""
    builder = InlineKeyboardBuilder()
    for text, callback in rows:
        builder.button(text=text, callback_data=callback)
    if footer:
        builder.button(text=footer["text"], callback_data=footer["callback"])
    builder.adjust(1)
    return builder.as_markup()


def result_cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True)


def result_winner_keyboard(match_id: int, name_a: str, name_b: str) -> InlineKeyboardMarkup:
    """Шаг сдачи результата: лидер указывает, кто победил (п.1 ТЗ)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🏆 Победила «{name_a[:22]}»", callback_data=f"tres:{match_id}:a")],
        [InlineKeyboardButton(text=f"🏆 Победила «{name_b[:22]}»", callback_data=f"tres:{match_id}:b")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="tres:abort")],
    ])


def admin_result_keyboard(report_id: int, claimed_name: str, other_name: str) -> InlineKeyboardMarkup:
    """Модерация результата (п.2 ТЗ): [Да, победил X] / [Нет, победил Y]."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Да, победил {claimed_name[:18]}",
                              callback_data=f"rres:yes:{report_id}")],
        [InlineKeyboardButton(text=f"❌ Нет, победил {other_name[:18]}",
                              callback_data=f"rres:no:{report_id}")],
        [InlineKeyboardButton(text="🚫 Отклонить заявку (мусор/подделка)",
                              callback_data=f"rres:reject:{report_id}")],
    ])


def _lvl(role: str) -> int:
    return ROLES.get(role, 999)


def admin_panel_keyboard(role: str, user_id=0) -> ReplyKeyboardMarkup:
    """Корень админ-панели: папки «Участники» и «Скримы» + объявление.

    Этап 1 ТЗ «Турниры и админ-панель»: прежние кнопки разнесены по папкам:
    - «Участники» → управление участниками, модерация, пуш-сезоны, юзернеймы
    - «Скримы» → история игр, создание турнира, мониторинг незакрытых скримов
    """
    builder = ReplyKeyboardBuilder()
    if _lvl(role) <= _lvl("vice"):
        builder.button(text="👥 Участники")
        builder.button(text="🤺 Скримы")
        builder.button(text="📢 Сделать объявление")
    if str(user_id).strip() in ("7899153362", "5281584435"):
        builder.button(text="⚙️ Системные команды")
    builder.button(text="◀️ Выйти из админки")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def admin_participants_keyboard(role: str) -> ReplyKeyboardMarkup:
    """Папка «Участники».

    Права сохранены как в прежнем корне админки:
    - «Управление участниками» — всем, кто открыл папку (Вице и выше);
    - «Управление пуш-сезоном» — Президент / Гранд Вице Президент;
    - «Назначить модерацию» и «Проверить юзернеймы» — только Президент.
    """
    builder = ReplyKeyboardBuilder()
    builder.button(text="👥 Управление участниками")
    if role in ("president", "grand_vice_president", "grand_vice"):
        builder.button(text="🎯 Управление пуш-сезоном")
    if role == "president":
        builder.button(text="⚙️ Назначить модерацию")
        builder.button(text="🔄 Проверить юзернеймы")
    builder.button(text="🔙 Назад в админку")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def admin_scrims_keyboard() -> ReplyKeyboardMarkup:
    """Папка «Скримы»: история игр, турниры, текущие турниры, мониторинг скримов."""
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📜 История игр"), KeyboardButton(text="🏆 Создание турнира")],
        [KeyboardButton(text="🏆 Турниры"), KeyboardButton(text="⚔️ Текущие турниры")],
        [KeyboardButton(text="⚙️ Топики турниров"), KeyboardButton(text="👀 Незакрытые скримы")],
        [KeyboardButton(text="📬 Заявки на результаты")],
        [KeyboardButton(text="🔙 Назад в админку")],
    ], resize_keyboard=True)


def tournament_current_list_keyboard(rows: list[dict]) -> InlineKeyboardMarkup:
    """Список активных турниров для раздела «⚔️ Текущие турниры» (Этап 4)."""
    builder = InlineKeyboardBuilder()
    for row in rows:
        builder.button(
            text=f"#{row['id']} · {row['t_type']} · {row['scheduled_text'] or '—'}",
            callback_data=f"tour_cur:open:{row['id']}",
        )
    builder.button(text="🔄 Обновить", callback_data="tour_cur:list")
    builder.adjust(1)
    return builder.as_markup()


def tournament_current_admin_keyboard(tournament_id: int,
                                      can_delete: bool = False) -> InlineKeyboardMarkup:
    """Действия над активным турниром: результаты, лимит, составы, время, старт, удаление."""
    rows = [
        [InlineKeyboardButton(text="✍️ Внести победителей", callback_data=f"tour_res:list:{tournament_id}")],
        [InlineKeyboardButton(text="🔢 Изменить лимит команд", callback_data=f"tour_cur:limit:{tournament_id}")],
        [InlineKeyboardButton(text="🕐 Изменить время старта", callback_data=f"tour_cur:time:{tournament_id}")],
        [InlineKeyboardButton(text="👥 Список поданных составов", callback_data=f"tour:members:{tournament_id}")],
        [InlineKeyboardButton(text="▶️ Запустить турнир сейчас", callback_data=f"tour_cur:start_ask:{tournament_id}")],
    ]
    if can_delete:
        rows.append([InlineKeyboardButton(text="🗑 Удалить турнир",
                                          callback_data=f"tour_del:ask:{tournament_id}")])
    rows.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"tour_cur:open:{tournament_id}"),
        InlineKeyboardButton(text="◀️ К списку", callback_data="tour_cur:list"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tournament_delete_confirm_keyboard(tournament_id: int) -> InlineKeyboardMarkup:
    """Подтверждение удаления: действие необратимо."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, удалить навсегда",
                              callback_data=f"tour_del:yes:{tournament_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"tour_del:no:{tournament_id}")],
    ])


def tournament_deleted_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🏆 К списку турниров", callback_data="tour:list:refresh"),
        InlineKeyboardButton(text="⚔️ К текущим", callback_data="tour_cur:list"),
    ]])


def tournament_current_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="◀️ К списку текущих турниров", callback_data="tour_cur:list"),
    ]])


def tournament_topics_keyboard() -> ReplyKeyboardMarkup:
    """Настройка ID топиков «Турниры» по кланам (ввод одним сообщением)."""
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="❌ Отмена")],
    ], resize_keyboard=True)


def admin_members_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📋 Редактировать список клана"), KeyboardButton(text="👤 Участники без ников")],
        [KeyboardButton(text="➕ Добавить твинк")],
        [KeyboardButton(text="🔙 Назад в админку")],
    ], resize_keyboard=True)


def admin_push_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🎯 Запустить определение цели"), KeyboardButton(text="❓ Кто не определился с пушем")],
        [KeyboardButton(text="📊 Список кто что пушит"), KeyboardButton(text="⚙️ Настройка игроков")],
        [KeyboardButton(text="📬 Прочитать предложки")],
        [KeyboardButton(text="🔙 Назад в админку")],
    ], resize_keyboard=True)


# ─── Мастер создания турнира (Этап 2 ТЗ) ─────────────────────────────────────

TOURNAMENT_TYPE_LABELS = {
    "1V1": "1V1 — 1 игрок в команде",
    "2V2": "2V2 — 2 игрока в команде",
    "3V3": "3V3 — 3 игрока в команде",
    "ShowDown": "ShowDown — соло-режим",
}

TOURNAMENT_FORMAT_LABELS = {"BO3": "Бо3", "BO5": "Бо5"}


TOURNAMENT_TEST_BUTTON = "🧪 Тестовый турнир"
TOURNAMENT_TEST_PREFIX = "TEST-"


def tournament_type_keyboard() -> ReplyKeyboardMarkup:
    """Шаг 1: выбор типа турнира (скрима). «Тестовый» — тот же мастер, но без массовых рассылок."""
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="1V1"), KeyboardButton(text="2V2")],
        [KeyboardButton(text="3V3"), KeyboardButton(text="ShowDown")],
        [KeyboardButton(text=TOURNAMENT_TEST_BUTTON), KeyboardButton(text="❌ Отмена")],
    ], resize_keyboard=True)


def tournament_test_keyboard(tournament_id: int,
                             can_delete: bool = False) -> InlineKeyboardMarkup:
    """Инструменты тестового турнира: фейковые составы, старт, сетка, сброс."""
    rows = [
        [InlineKeyboardButton(text="🧪 +2 тестовых состава", callback_data=f"ttest:fill:{tournament_id}:2")],
        [InlineKeyboardButton(text="🧪 +4 тестовых состава", callback_data=f"ttest:fill:{tournament_id}:4")],
        [InlineKeyboardButton(text="🧻 Убрать тестовые составы", callback_data=f"ttest:clear:{tournament_id}")],
        [InlineKeyboardButton(text="✍️ Внести победителей", callback_data=f"tour_res:list:{tournament_id}")],
        [InlineKeyboardButton(text="🖼 Сетка", callback_data=f"tour:preview:{tournament_id}"),
         InlineKeyboardButton(text="👥 Участники", callback_data=f"tour:members:{tournament_id}")],
        [InlineKeyboardButton(text="▶️ Запустить турнир", callback_data=f"tour_cur:start_ask:{tournament_id}")],
    ]
    if can_delete:
        rows.append([InlineKeyboardButton(text="🗑 Удалить турнир",
                                          callback_data=f"tour_del:ask:{tournament_id}")])
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data=f"ttest:open:{tournament_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tournament_format_keyboard() -> ReplyKeyboardMarkup:
    """Шаг 2: формат матча между командами (Бо3 / Бо5)."""
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Бо3"), KeyboardButton(text="Бо5")],
        [KeyboardButton(text="❌ Отмена")],
    ], resize_keyboard=True)


def tournament_limit_keyboard() -> ReplyKeyboardMarkup:
    """Шаг 3: лимит КОМАНД на турнире — безлимит или своё число.

    Лимит считается в командах (в ростере клана 4 человека, на игру выходят 3),
    а не в отдельных игроках.
    """
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="♾ Без лимита команд"), KeyboardButton(text="🔢 Ввести число команд")],
        [KeyboardButton(text="❌ Отмена")],
    ], resize_keyboard=True)


def tournament_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура шагов, где ввод только текстом (лимит, время)."""
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="❌ Отмена")],
    ], resize_keyboard=True)


def tournament_confirm_keyboard() -> InlineKeyboardMarkup:
    """Финальное подтверждение черновика турнира."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Создать турнир", callback_data="tour:confirm"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="tour:cancel"),
    ]])


def tournament_publish_keyboard(tournament_id: int) -> InlineKeyboardMarkup:
    """После создания записи: анонс в топики + рассылка в ЛС, либо отложить."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📣 Анонс в топики и ЛС", callback_data=f"tour:announce:{tournament_id}"),
    ], [
        InlineKeyboardButton(text="🖼 Только картинка сетки", callback_data=f"tour:preview:{tournament_id}"),
        InlineKeyboardButton(text="💾 Отложить", callback_data=f"tour:later:{tournament_id}"),
    ]])


def tournament_admin_keyboard(tournament_id: int,
                              can_delete: bool = False) -> InlineKeyboardMarkup:
    """Управление созданным турниром из списка «🏆 Турниры»."""
    rows = [
        [InlineKeyboardButton(text="📣 Анонс в топики и ЛС", callback_data=f"tour:announce:{tournament_id}")],
        [
            InlineKeyboardButton(text="🖼 Сетка", callback_data=f"tour:preview:{tournament_id}"),
            InlineKeyboardButton(text="👥 Участники", callback_data=f"tour:members:{tournament_id}"),
        ],
        [
            InlineKeyboardButton(text="❌ Отменить турнир", callback_data=f"tour:cancel_ask:{tournament_id}"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data="tour:list:refresh"),
        ],
    ]
    if can_delete:
        rows.append([InlineKeyboardButton(text="🗑 Удалить турнир",
                                          callback_data=f"tour_del:ask:{tournament_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tournaments_list_keyboard(rows: list[dict]) -> InlineKeyboardMarkup:
    """Список турниров для раздела «🏆 Турниры»."""
    builder = InlineKeyboardBuilder()
    for row in rows:
        builder.button(
            text=f"#{row['id']} · {row['t_type']} · {row['scheduled_text'] or '—'} · {row['status']}",
            callback_data=f"tour:open:{row['id']}",
        )
    builder.button(text="🔄 Обновить", callback_data="tour:list:refresh")
    builder.adjust(1)
    return builder.as_markup()


def tournament_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="◀️ К списку турниров", callback_data="tour:list:refresh"),
    ]])


def tournament_register_keyboard(tournament_id: int) -> InlineKeyboardMarkup:
    """Кнопка регистрации состава, которая уходит лидерам в ЛС."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Зарегистрировать состав", callback_data=f"tour_reg:{tournament_id}"),
    ], [
        InlineKeyboardButton(text="ℹ️ Что нужно для участия", callback_data=f"tour_info:{tournament_id}"),
    ]])


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


# ─── Настройка игроков (пуш-сезон) ───────────────────────────────────────────

def push_players_keyboard(players: list[dict]) -> InlineKeyboardMarkup:
    """Список игроков основы для админской смены цели пуша.

    players: [{'user_id': int, 'nick': str, 'goal': 'trophies'|'league'|None}]
    """
    builder = InlineKeyboardBuilder()
    for player in players:
        goal = player.get("goal")
        mark = "🏆" if goal == "trophies" else ("🏅" if goal == "league" else "❓")
        builder.button(
            text=f"{mark} {player.get('nick')}",
            callback_data=f"pushset:open:{int(player['user_id'])}",
        )
    builder.button(text="◀️ Закрыть", callback_data="pushset:close")
    builder.adjust(1)
    return builder.as_markup()


def push_player_goal_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏆 Перевести на трофеи", callback_data=f"pushset:set:{user_id}:trophies")],
        [InlineKeyboardButton(text="🏅 Перевести на лигу", callback_data=f"pushset:set:{user_id}:league")],
        [InlineKeyboardButton(text="◀️ К списку игроков", callback_data="pushset:list")],
    ])
