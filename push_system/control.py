""" Модуль контроля сезонных норм пуша.
Нормы по трофеям = трофеи ТОП-1 участника клана / коэффициент (коэффициенты — по кланам:
у Основы свои, у Академии свои; тексты анкет для игроков — в utils/formatting.py).
Минималки по лиге бот НЕ проверяет — администрация сверяет их вручную,
в отчёте для каждого игрока написано, какую лигу проверить.
"""
import asyncio
import json
import logging
from datetime import datetime

from aiogram import Bot
from aiogram.utils.markdown import html_decoration as hd

from database import get_all_members, get_push_goals, get_setting, set_setting
from services.api_service import get_player_profile

logger = logging.getLogger(__name__)

RANKED_LABELS = {
    1: "Бронза I", 2: "Бронза II", 3: "Бронза III",
    4: "Серебро I", 5: "Серебро II", 6: "Серебро III",
    7: "Золото I", 8: "Золото II", 9: "Золото III",
    10: "Алмаз I", 11: "Алмаз II", 12: "Алмаз III",
    13: "Мифик I", 14: "Мифик II", 15: "Мифик III",
    16: "Легенда I", 17: "Легенда II", 18: "Легенда III",
    19: "Мастер",
}

# Кланы, для которых работает пуш-сезон (Основа + Академия)
PUSH_CLANS = ("squad", "academy")

# ─── Нормы пуша: (коэффициент, минимальная лига текстом) ─────────────────────
# Трофеи = трофеи ТОП-1 участника КЛАНА / коэффициент.
PUSH_NORMS_BY_CLAN = {
    # Основа — сезонные коэффициенты (выдал @dyadya_karlson)
    "squad": {
        "trophies_main": (1.35, "Лега 1"),    # Пуш трофеев
        "trophies_alt": (1.25, "Мифик 2"),    # Пуш трофеев без пуша лиги
        "league_main": (1.45, "Лега 3"),      # Пуш лиги
        "league_alt": (1.55, "Мастер 1"),     # Пуш лиги без пуша кубков
    },
    # Академия — свои коэффициенты (мягче)
    "academy": {
        "trophies_main": (1.4, "Мифик 3"),    # Пуш трофеев
        "trophies_alt": (1.3, "Мифик 1"),     # Пуш трофеев без пуша лиги
        "league_main": (1.5, "Лега 2"),       # Пуш лиги
        "league_alt": (1.6, "Лега 3"),        # Пуш лиги без пуша кубков
    },
}

# Обратная совместимость: старый импорт PUSH_NORMS = нормы основы
PUSH_NORMS = PUSH_NORMS_BY_CLAN["squad"]

GOAL_LABELS = {"trophies": "🏆 Трофеи", "league": "🏅 Лига"}

# Ключ в system_settings (vigarik.db), где лежит снимок стартовых трофеев сезона.
SEASON_START_KEY = "push_season_start"


def _season_key(clan: str) -> str:
    """Ключ снимка сезона: у основы исторический общий ключ, у академии — свой."""
    return SEASON_START_KEY if clan == "squad" else f"{SEASON_START_KEY}_{clan}"


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def fmt_num(value) -> str:
    """Число с пробелами-разделителями: 150000 -> 150 000."""
    return f"{_safe_int(value):,}".replace(",", " ")


def clan_registered(members: list[dict], clan: str) -> list[dict]:
    """Зарегистрированные участники указанного клана — от них считаются нормы."""
    return [m for m in (members or []) if m.get("clan") == clan and m.get("registered") == 1]


def squad_registered(members: list[dict]) -> list[dict]:
    """Совместимость со старым кодом: зарегистрированная основа."""
    return clan_registered(members, "squad")


def calc_push_minimums(squad_members: list[dict], clan: str = "squad") -> dict:
    """Считает все трофейные нормы от ТОП-1 участника клана (X)."""
    norms = PUSH_NORMS_BY_CLAN.get(clan, PUSH_NORMS)
    top = max(squad_members or [], key=lambda m: _safe_int(m.get("trophies")), default=None)
    x = _safe_int(top.get("trophies")) if top else 0
    result = {
        "clan": clan,
        "top_trophies": x,
        "top_nick": str(top.get("game_nick") or top.get("username") or top.get("user_id")) if top else "—",
        "top_user_id": _safe_int(top.get("user_id")) if top else 0,
    }
    for key, (coef, _league) in norms.items():
        result[key] = int(x / coef) if x > 0 else 0
    return result


async def get_push_minimums(clan: str = "squad") -> dict:
    """Нормы на текущий момент (кубки — по данным последнего обновления в базе)."""
    return calc_push_minimums(clan_registered(await get_all_members(), clan), clan)


# ─── Старт сезона: фиксация трофеев каждого игрока клана ─────────────────────

async def record_season_start_trophies(clan: str = "squad") -> dict:
    """Снимает актуальные трофеи КАЖДОГО зарегистрированного игрока клана.
    Пишется для всех, включая тех, кто ещё не выбрал цель пуша.
    Трофеи берём из Brawl Stars API, при недоступном API — последнее значение из базы.
    """
    members = clan_registered(await get_all_members(), clan)
    players: dict = {}
    from_api = 0
    for m in members:
        uid = _safe_int(m.get("user_id"))
        if uid <= 0:
            continue
        trophies = None
        tag = m.get("player_tag")
        if tag:
            try:
                profile = await get_player_profile(tag)
            except Exception:
                profile = None
            if profile:
                try:
                    trophies = int(profile.get("trophies") or 0)
                    from_api += 1
                except (TypeError, ValueError):
                    trophies = None
        if trophies is None:
            trophies = _safe_int(m.get("trophies"), 0)
        players[str(uid)] = {"trophies": trophies}
        await asyncio.sleep(0.12)  # бережём лимиты Brawl Stars API

    snapshot = {
        "clan": clan,
        "started_iso": datetime.now().isoformat(timespec="seconds"),
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total": len(players),
        "from_api": from_api,
        "players": players,
    }
    await set_setting(_season_key(clan), json.dumps(snapshot))
    logger.info(f"Старт сезона пуша зафиксирован ({clan}): {len(players)} игроков, из API: {from_api}")
    return snapshot


async def get_season_start(clan: str = "squad") -> dict:
    """Снимок стартовых трофеев текущего сезона клана (или пустой dict)."""
    try:
        raw = await get_setting(_season_key(clan))
    except Exception:
        raw = None
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.warning(f"Не удалось прочитать снимок стартовых трофеев сезона ({clan})")
        return {}


def season_start_trophies(snapshot: dict, user_id) -> int | None:
    """Стартовые трофеи игрока из снимка (None — игрока нет в снимке)."""
    entry = (snapshot.get("players") or {}).get(str(user_id))
    if not entry:
        return None
    try:
        return int(entry.get("trophies"))
    except (TypeError, ValueError):
        return None


async def start_new_push_season(clan: str = "squad") -> dict:
    """Старт сезона без рассылки: фиксируем кубки клана как точку отсчёта."""
    return await record_season_start_trophies(clan)


# ─── Финиш сезона ────────────────────────────────────────────────────────────

async def check_season_results(bot: Bot, clan: str = "squad") -> str:
    """Вызывается В КОНЦЕ сезона. Автоматически проверяются только трофеи
    (по актуальным данным Brawl Stars API). Для каждого, кто добрал трофеи,
    указано, какую минимальную лигу проверить вручную.
    """
    goals_map = {}
    for g in await get_push_goals():
        uid = _safe_int(g.get("user_id"))
        if uid:
            goals_map[uid] = g.get("goal")

    members = clan_registered(await get_all_members(), clan)
    if not members:
        return "📭 В этом клане сейчас нет зарегистрированных игроков."

    norms = PUSH_NORMS_BY_CLAN.get(clan, PUSH_NORMS)
    mins = calc_push_minimums(members, clan)
    x = mins["top_trophies"]
    clan_title = clan.upper()

    done, failed, api_errors, unvoted = [], [], [], []
    for m in members:
        uid = _safe_int(m.get("user_id"))
        nick = hd.quote(str(m.get("game_nick") or m.get("username") or f"ID {uid}"))

        # ТОП-1 — точка отсчёта норм, он выполняет их автоматически.
        if uid == mins["top_user_id"]:
            done.append(f"• 👑 {nick} — ТОП-1 клана ({fmt_num(x)} 🏆)")
            continue

        goal = goals_map.get(uid)
        if goal not in GOAL_LABELS:
            unvoted.append(f"• {nick}")
            continue

        tag = m.get("player_tag")
        profile = await get_player_profile(tag) if tag else None
        await asyncio.sleep(0.12)
        if not profile:
            api_errors.append(f"• {nick} — профиль недоступен, проверить вручную")
            continue
        trophies = _safe_int(profile.get("trophies"))

        if goal == "trophies":
            if trophies >= mins["trophies_alt"]:
                need = norms["trophies_alt"][1]
            elif trophies >= mins["trophies_main"]:
                need = norms["trophies_main"][1]
            else:
                need = None
            fail_hint = (f"нужно {fmt_num(mins['trophies_main'])} (+ {norms['trophies_main'][1]}) "
                         f"или {fmt_num(mins['trophies_alt'])} (+ {norms['trophies_alt'][1]})")
        else:
            if trophies >= mins["league_main"]:
                need = norms["league_main"][1]
            elif trophies >= mins["league_alt"]:
                need = norms["league_alt"][1]
            else:
                need = None
            fail_hint = (f"нужно {fmt_num(mins['league_main'])} (+ {norms['league_main'][1]}) "
                         f"или {fmt_num(mins['league_alt'])} (+ {norms['league_alt'][1]})")

        label = GOAL_LABELS[goal]
        if need:
            done.append(f"• {nick} ({label}) — {fmt_num(trophies)} 🏆 → проверить лигу: минимум {need} ")
        else:
            failed.append(f"• {nick} ({label}) — {fmt_num(trophies)} 🏆, {fail_hint}")

    norm_line = " · ".join(
        f"X/{norms[key][0]} = {fmt_num(mins[key])}" for key in
        ("trophies_main", "trophies_alt", "league_main", "league_alt")
    )
    lines = [
        f"📊 ИТОГИ СЕЗОНА ПУША ({clan_title}) ",
        f"👑 ТОП-1 клана (X): {hd.quote(mins['top_nick'])} — {fmt_num(x)} 🏆",
        f"Нормы: {norm_line}",
        "⚠️ Лигу бот не проверяет — сверьте вручную по пометке «проверить лигу». ",
        "━━━━━━━",
        "✅ ТРОФЕИ ВЫПОЛНЕНЫ: ",
        *(done or [" — нет игроков"]),
        "",
        "❌ НЕ ДОБРАЛИ ТРОФЕИ (ШТРАФНИКИ): ",
        *(failed or [" — нет игроков"]),
    ]
    if api_errors:
        lines += ["", "⚠️ ОШИБКА API: ", *api_errors]
    if unvoted:
        lines += ["", "❓ НЕ ВЫБИРАЛИ ЦЕЛЬ: ", *unvoted]
    return "\n".join(lines)
