"""Общие константы и мелкие помощники админ-панели."""
import logging
import re
from database import get_member, get_setting
from utils.permissions import is_any_admin, has_role

logger = logging.getLogger(__name__)


# ─── МАСТЕР СОЗДАНИЯ ТУРНИРА (Этап 2 ТЗ: пошаговый FSM) ──────────────────────
# ShowDown — соло-режим, там лимит считается в игроках и жёстко зафиксирован на 10.
# Для 1V1/2V2/3V3 лимит задаётся в КОМАНДАХ (составах), а не в игроках.
SHOWDOWN_LIMIT = 10


TOURNAMENT_TYPES = {
    "1v1": "🤺 1V1",
    "2v2": "👥 2V2",
    "3v3": "🛡 3V3",
    "showdown": "💀 ShowDown",
}


# Охранники FSM-ввода: эти кнопки меню не должны попадать в число/дату турнира
TOURNAMENT_GUARD_TEXTS = frozenset({
    "👔 Для админов", "👥 Участники", "🤺 Скримы", "📢 Сделать объявление",
    "⚙️ Системные команды", "◀️ Выйти из админки", "🔙 Назад в админку",
    "📜 История игр", "🏆 Создание турнира", "👀 Незакрытые скримы", "📋 Текущие турниры",
    "👥 Управление участниками", "🎯 Управление пуш-сезоном",
    "⚙️ Назначить модерацию", "🔄 Проверить юзернеймы",
    "📋 Редактировать список клана", "👤 Участники без ников", "➕ Добавить твинк",
    "Составы🏆 (BetaTest)", "Составы 🏆 (BetaTest)", "💡 Отправить предложение",
    "🎯 Выбрать цель пуша", "◀️ Назад", "❌ Отмена",
})


# Соло-режимы: участник регистрируется сам, без состава.
SOLO_TYPES = ("showdown", "1v1")


def _is_solo_type(t_type: str) -> bool:
    """True — турнир соло (1V1 и ShowDown): играет сам игрок, а не состав."""
    return str(t_type or "").lower() in SOLO_TYPES


def _limit_unit(t_type: str, plural: bool = True) -> str:
    """Единица лимита: в соло-режимах считаются игроки, в остальных — команды."""
    if _is_solo_type(t_type):
        return "игроков"
    return "команд" if plural else "команды"


def _limit_label(t_type: str) -> str:
    """Заголовок лимита: «Лимит игроков» для соло-турниров, иначе «Лимит команд»."""
    return "Лимит игроков" if _is_solo_type(t_type) else "Лимит команд"


def _limit_text(limit, t_type: str = "") -> str:
    """Человеческая запись лимита. Лимит считается в КОМАНДАХ (составах), не в игроках."""
    if limit is None:
        return "♾ без лимита"
    return f"{int(limit)} {_limit_unit(t_type)}"


def _players_per_team(t_type: str) -> int:
    """Сколько игроков от команды реально выходит на карту."""
    return {"1v1": 1, "2v2": 2, "3v3": 3}.get(t_type, 1)


def _team_size(team) -> int:
    """Сколько занятых игроков в составе команды (0 — если состава нет)."""
    try:
        league_id = team["league_id"]
    except Exception:
        return 0
    if not league_id:
        return 0
    try:
        from league import league_db as db
        return int(db.count_occupied_members(int(league_id)))
    except Exception:
        return 0


def _is_duo(t_type: str, team) -> bool:
    """2V2-состав ровно из двух игроков — команда играет дуо."""
    return t_type == "2v2" and _team_size(team) == 2


def _is_test(tournament) -> bool:
    try:
        return int(tournament["is_test"] or 0) == 1
    except Exception:
        return False


def _test_mark(tournament) -> str:
    """Префикс-метка тестового турнира для всех постов и карточек."""
    return "🧪 <b>[ТЕСТ]</b> " if _is_test(tournament) else ""


def _tour_name(tournament) -> str:
    """Публичное имя турнира: дата и время вместо техничного #ID.

    Показываем всем «Турнир 25.12.2026 19:00», а не «Турнир #4».
    """
    starts = str(tournament["starts_at"] or "").strip()
    return starts if starts else f"#{tournament['id']}"


TOURNAMENT_STATUS_LABEL = {
    "registration": "📝 Идёт регистрация",
    "running": "🔥 Турнир идёт",
    "finished": "🏁 Завершён",
    "cancelled": "🚫 Отменён",
}


def _split_text(text: str, limit: int = 4000) -> list[str]:
    """Режет текст по строкам, чтобы каждое сообщение Telegram не превышало лимит."""
    parts, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts or [""]


_HTML_TAG_RE = re.compile(r"<(/?)([a-zA-Z][\w-]*)[^>]*>")


def _tg_caption(text: str, limit: int = 1024) -> str:
    """Обрезает подпись к фото под лимит Telegram, не ломая HTML.

    Режет по границе строки, закрывает незакрытые теги и добавляет «…».
    """
    text = str(text or "")
    if len(text) <= limit:
        return text
    budget = limit
    while True:
        budget -= 40
        cut = text[:max(budget, 1)]
        lt, gt = cut.rfind("<"), cut.rfind(">")
        if lt > gt:  # не оставляем обрубок тега
            cut = cut[:lt]
        if "\n" in cut:
            cut = cut.rsplit("\n", 1)[0]
        stack = []
        for m in _HTML_TAG_RE.finditer(cut):
            closing, tag = m.group(1), m.group(2).lower()
            if closing:
                if stack and stack[-1] == tag:
                    stack.pop()
            else:
                stack.append(tag)
        tail = "".join(f"</{t}>" for t in reversed(stack)) + "\n…"
        if len(cut) + len(tail) <= limit or budget <= 1:
            return cut + tail


async def _cb_is_admin(user_id: int) -> bool:
    member = await get_member(user_id)
    return bool(member) and is_any_admin(member)
