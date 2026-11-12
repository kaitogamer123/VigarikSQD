"""Общие константы, состояния и помощники скримов/верификации."""
import re
from datetime import datetime
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.utils.markdown import html_decoration as hd
from database import get_member
from utils.permissions import get_admin_rights_from_file, is_any_admin


SCRIM_TYPE_LABEL = {"normal": "⚔️ Обычный", "random": "🎲 Рандомный", "friendly": "🤝 Дружеский"}


LEAGUES_MENU_BUTTONS = {"⚔️ Лиги", "🏆 Лиги", "Лиги"}


# ─── Кнопка верификации: поддерживаем текущий и устаревший вариант текста ───
VERIFY_BUTTON_TEXTS = {"✅ Верифицировать состав", "🛡 Верифицировать состав"}


# ─── Охрана ввода: команды и кнопки меню никогда не считаются датой/счётом ───
DATETIME_RE = re.compile(r"^\s*\d{1,2}\.\d{1,2}(\.\d{2,4})?\s+\d{1,2}:\d{2}\s*$")


SCORE_RE = re.compile(r"^\s*\d{1,2}\s*[/:\-]\s*\d{1,2}\s*$")


SCRIM_MENU_TEXTS = frozenset({
    "◀️ Назад", "❌ Отмена", "◀️ Назад в составы",
    "◀️ Назад в главное меню", "Назад в главное меню",
    "⚔️ Лиги", "🏆 Лиги", "Лиги",
    "⚔️ Обычный скрим", "🎲 Рандомный скрим", "🤝 Дружеский скрим",
    "🏁 Мои скримы", "📜 История матчей", "📜 История игр",
    "✅ Верифицировать состав", "🛡 Верифицировать состав",
    "🌍 Все составы", "🌍 Все лиги",
    "📝 Подать заявку в состав", "📝 Подать заявку в лигу",
    "📩 Мои заявки", "📥 Приглашения в состав", "📥 Приглашения в лигу",
    "➕ Создать состав", "➕ Создать лигу",
    "⚙️ Настройка состава", "📋 Заявки в состав", "📋 Просмотреть заявки",
    "👥 Участники состава", "👥 Состав лиги",
    "🚪 Выйти из состава", "🚪 Выйти из лиги",
    "🔒 Закрыть набор / Открыть набор",
    "👑 Передать лидерство", "👑 Передать лидерку",
    "🛡 Управление заместителями",
    "🚪 Выгнать участника", "➕ Пригласить игрока", "💥 Распустить состав",
    "Составы 🏆 (BetaTest)", "Составы🏆 (BetaTest)", "Лиги 💀 (BetaTest)",
    "💡 Отправить предложение", "🎯 Выбрать цель пуша", "👔 Для админов",
    "👥 Управление участниками", "📢 Сделать объявление",
    "🎯 Управление пуш-сезоном", "⚙️ Назначить модерацию",
    "🔄 Проверить юзернеймы", "⚙️ Системные команды",
    "◀️ Выйти из админки", "🔙 Назад в админку",
    "📋 Редактировать список клана", "👤 Участники без ников", "➕ Добавить твинк",
})


def _scrim_input_allowed(message: Message) -> bool:
    """True, если текст реально похож на дату или счёт, а не на команду/кнопку."""
    text = (message.text or "").strip()
    if not text or text.startswith("/") or text in SCRIM_MENU_TEXTS:
        return False
    return bool(DATETIME_RE.match(text) or SCORE_RE.match(text))


def _is_cancel_text(message: Message) -> bool:
    text = (message.text or "").strip()
    return bool(text) and text.startswith("/cancel")


class ScrimStates(StatesGroup):
    waiting_datetime = State()
    waiting_score = State()
    waiting_screenshots = State()


def _reply(rows) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in row] for row in rows],
        resize_keyboard=True,
    )


def _leagues_menu() -> ReplyKeyboardMarkup:
    return _reply([
        ["⚔️ Обычный скрим", "🎲 Рандомный скрим"],
        ["🤝 Дружеский скрим"],
        ["🏁 Мои скримы", "📜 История матчей"],
        ["◀️ Назад в составы"],
    ])


def _back_inline(label: str = "◀️ В составы") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, callback_data="league:back_root")
    ]])


async def _is_admin(user_id: int) -> bool:
    try:
        member = await get_member(user_id)
    except Exception:
        member = None
    if member and is_any_admin(member):
        return True
    try:
        return get_admin_rights_from_file(user_id) is not None
    except Exception:
        return False


def _is_leader(composition, user_id: int) -> bool:
    return bool(composition) and int(composition["leader_id"]) == int(user_id)


def _is_verified(composition) -> bool:
    try:
        return int(composition["is_verified"] or 0) == 1
    except Exception:
        return False


def _fmt_dt(raw: str) -> str:
    if not raw:
        return "—"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(str(raw)[:19], fmt).strftime("%d.%m %H:%M")
        except Exception:
            continue
    return str(raw)[:16]


def _parse_score(text: str):
    if not text:
        return None
    t = text.strip().replace(":", "/").replace("-", "/").replace(" ", "/")
    parts = [p for p in t.split("/") if p != ""]
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    a, b = int(parts[0]), int(parts[1])
    if a > 9 or b > 9:
        return None
    return (a, b)


def _valid_score(fmt: str, a: int, b: int) -> bool:
    if a == b:
        return False
    if fmt == "BO3":
        return (a == 2 and b <= 1) or (b == 2 and a <= 1)
    if fmt == "BO5":
        return (a == 3 and b <= 2) or (b == 3 and a <= 2)
    return False


def _fmt_signed(value) -> str:
    v = int(value or 0)
    return f"+{v}" if v > 0 else str(v)


async def _show_root(message: Message, state: FSMContext, user_id: int = None):
    from league.handlers import show_root
    await show_root(message, state, user_id=user_id or message.from_user.id)


def _history_line(row, perspective_league_id: int | None = None) -> str:
    """Строка истории одного завершённого скрима."""
    date = _fmt_dt(row["completed_at"] or row["created_at"])
    typ = SCRIM_TYPE_LABEL.get(row["scrim_type"], row["scrim_type"])
    fmt = row["format"] or ""
    a, b = int(row["challenger_score_a"] or 0), int(row["challenger_score_b"] or 0)
    name_a = f"{row['challenger_name']} [{row['challenger_tag']}]" if row["challenger_name"] else f"#{row['challenger_league_id']}"
    name_b = f"{row['opponent_name']} [{row['opponent_tag']}]" if row["opponent_name"] else "?"
    if perspective_league_id and int(row["opponent_league_id"] or 0) == int(perspective_league_id):
        mine, theirs = b, a
        delta = int(row["mmr_opponent"] or 0)
        rival = name_a
    else:
        mine, theirs = a, b
        delta = int(row["mmr_challenger"] or 0)
        rival = name_b
    result = "✅ Победа" if mine > theirs else ("❌ Поражение" if mine < theirs else "➖ Ничья")
    mmr = f"{_fmt_signed(delta)} MMR🌟" if row["scrim_type"] != "friendly" else "без MMR"
    if perspective_league_id:
        return f"📅 {date} | {typ} {fmt} | vs {hd.quote(rival)} | {mine}:{theirs} {result} | {mmr}"
    return (f"📅 {date} | {typ} {fmt} | {hd.quote(name_a)} {a}:{b} {hd.quote(name_b)} | "
            f"{_fmt_signed(row['mmr_challenger'] or 0)}/{_fmt_signed(row['mmr_opponent'] or 0)} MMR")


def _normalise_scheduled(text: str):
    """'25.12 19:00' -> '25.12.2026 19:00'; '25.12.2026 19:00' -> как есть."""
    try:
        raw = text.strip()
        for fmt in ("%d.%m.%Y %H:%M", "%d.%m %H:%M"):
            try:
                dt = datetime.strptime(raw, fmt)
                if fmt == "%d.%m %H:%M":
                    dt = dt.replace(year=datetime.now().year)
                return dt.strftime("%d.%m.%Y %H:%M")
            except ValueError:
                continue
    except Exception:
        pass
    return None


class _FormatExtraStates(StatesGroup):
    """Отдельный класс, чтобы не мутировать ScrimStates после регистрации."""
    waiting_format = State()


ScrimFormatState = _FormatExtraStates.waiting_format
