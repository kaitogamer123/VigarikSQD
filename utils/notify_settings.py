"""Флаги отключения ЛС-уведомлений: всё, турниры, скримы.

Ключи в таблице system_settings:
  dm_mute_{uid}             — все массовые уведомления;
  dm_mute_tournaments_{uid} — только турниры;
  dm_mute_scrims_{uid}      — только скримы.

Глобальный флаг перекрывает тематические: если выключено «всё», замолкают оба
раздела, но тематические флаги при этом сохраняются.
"""
import logging

from database import get_setting, set_setting

logger = logging.getLogger(__name__)

KINDS = ("tournaments", "scrims")

# Подсказки в уведомлениях — как отписаться от конкретного раздела.
TOURNAMENT_MUTE_HINT = (
    "\n\n<i>У нас сейчас турнир. Если не интересно — напиши "
    "<code>/notify_off tournaments</code>, и бот больше не будет сообщать тебе о них.</i>"
)
SCRIM_MUTE_HINT = (
    "\n\n<i>Если не интересно — напиши <code>/notify_off scrims</code>, "
    "и бот больше не будет присылать тебе уведомления о скримах.</i>"
)


def _key(user_id: int, kind: str | None = None) -> str:
    suffix = f"_{kind}" if kind else ""
    return f"dm_mute{suffix}_{int(user_id)}"


def _normalise_kind(kind: str | None) -> str | None:
    """Принимает None/'all' → всё, иначе — только tournaments/scrims."""
    if not kind or str(kind).lower() in ("all", "все", "всё"):
        return None
    kind = str(kind).lower()
    if kind not in KINDS:
        raise ValueError(f"unknown notify kind: {kind}")
    return kind


async def is_muted(user_id: int, kind: str | None = None) -> bool:
    """True — пользователю не нужно отправлять уведомления этого типа."""
    try:
        uid = int(user_id or 0)
    except Exception:
        return False
    if not uid:
        return False
    try:
        if (await get_setting(_key(uid))) == "1":
            return True
    except Exception:
        pass
    kind = _normalise_kind(kind) if kind else None
    if not kind:
        return False
    try:
        return (await get_setting(_key(uid, kind))) == "1"
    except Exception:
        return False


async def set_muted(user_id: int, muted: bool, kind: str | None = None) -> None:
    """Ставит/снимает флаг. kind=None — общий флаг «все уведомления»."""
    kind = _normalise_kind(kind)
    await set_setting(_key(user_id, kind), "1" if muted else "0")


async def mute_state(user_id: int) -> dict:
    """Текущее состояние флагов: {'all': bool, 'tournaments': bool, 'scrims': bool}."""
    state = {"all": False, "tournaments": False, "scrims": False}
    for key in ("all",) + KINDS:
        try:
            state[key] = (await get_setting(_key(user_id, None if key == "all" else key))) == "1"
        except Exception:
            state[key] = False
    return state


def state_text(state: dict) -> str:
    def mark(flag: bool) -> str:
        return "🔕 выключены" if flag else "🔔 включены"

    lines = [
        f"• Турниры: {mark(state['all'] or state['tournaments'])}",
        f"• Скримы: {mark(state['all'] or state['scrims'])}",
    ]
    if state["all"]:
        lines.append("• Общий режим «всё выключено»: 🔕")
    return "\n".join(lines)
