"""
Модуль контроля сезонных норм пуша.

Нормы по трофеям = трофеи ТОП-1 участника основы / коэффициент.
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

# ─── Нормы пуша: (коэффициент, минимальная лига текстом) ─────────────────────
# Трофеи = трофеи ТОП-1 участника основы / коэффициент.
PUSH_NORMS = {
    "trophies_main": (1.2, "Лега 1"),    # Пуш трофеев
    "trophies_alt": (1.1, "Мифик 1"),    # Пуш трофеев без пуша лиги
    "league_main": (1.3, "Лега 3"),      # Пуш лиги
    "league_alt": (1.35, "Мастер 1"),    # Пуш лиги без пуша кубков
}

GOAL_LABELS = {"trophies": "🏆 Трофеи", "league": "🏅 Лига"}

# Ключ в system_settings (vigarik.db), где лежит снимок стартовых трофеев сезона.
SEASON_START_KEY = "push_season_start"


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def fmt_num(value) -> str:
    """Число с пробелами-разделителями: 150000 -> 150 000."""
    return f"{_safe_int(value):,}".replace(",", " ")


def squad_registered(members: list[dict]) -> list[dict]:
    """Только зарегистрированные участники основы — от них считаются нормы."""
    return [m for m in (members or []) if m.get("clan") == "squad" and m.get("registered") == 1]


def calc_push_minimums(squad_members: list[dict]) -> dict:
    """Считает все трофейные нормы от ТОП-1 участника основы (X)."""
    top = max(squad_members or [], key=lambda m: _safe_int(m.get("trophies")), default=None)
    x = _safe_int(top.get("trophies")) if top else 0
    result = {
        "top_trophies": x,
        "top_nick": str(top.get("game_nick") or top.get("username") or top.get("user_id")) if top else "—",
        "top_user_id": _safe_int(top.get("user_id")) if top else 0,
    }
    for key, (coef, _league) in PUSH_NORMS.items():
        result[key] = int(x / coef) if x > 0 else 0
    return result


async def get_push_minimums() -> dict:
    """Нормы на текущий момент (кубки — по данным последнего обновления в базе)."""
    return calc_push_minimums(squad_registered(await get_all_members()))


# ─── Старт сезона: фиксация трофеев каждого игрока основы ────────────────────

async def record_season_start_trophies() -> dict:
    """Снимает актуальные трофеи КАЖДОГО зарегистрированного игрока основы.

    Пишется для всех, включая тех, кто ещё не выбрал цель пуша.
    Трофеи берём из Brawl Stars API, при недоступном API — последнее значение из базы.
    """
    squad = squad_registered(await get_all_members())

    players: dict = {}
    from_api = 0
    for m in squad:
        uid = _safe_int(m.get("user_id"))
        if uid <= 0:
            continue

        tag = m.get("player_tag")
        trophies = _safe_int(m.get("trophies"))
        source = "db"

        if tag:
            profile = None
            try:
                profile = await get_player_profile(tag)
            except Exception as e:
                logger.debug(f"season start: API недоступен для {tag}: {e}")
            if profile:
                trophies = _safe_int(profile.get("trophies"), trophies)
                source = "api"
                from_api += 1
            await asyncio.sleep(0.12)  # бережём лимиты Brawl Stars API

        players[str(uid)] = {
            "nick": str(m.get("game_nick") or m.get("username") or uid),
            "tag": str(tag or ""),
            "trophies": trophies,
            "source": source,
        }

    now = datetime.now()
    payload = {
        "started_at": now.strftime("%d.%m.%Y %H:%M"),
        "started_iso": now.isoformat(timespec="seconds"),
        "total": len(players),
        "from_api": from_api,
        "players": players,
    }
    try:
        await set_setting(SEASON_START_KEY, json.dumps(payload, ensure_ascii=False))
    except Exception:
        logger.exception("Не удалось сохранить стартовые трофеи сезона")
    logger.info(f"Старт сезона: зафиксировано {payload['total']} игроков, из них по API {from_api}.")
    return payload


async def get_season_start() -> dict:
    """Снимок стартовых трофеев текущего сезона (или пустой dict)."""
    try:
        raw = await get_setting(SEASON_START_KEY)
    except Exception:
        raw = None
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.warning("Не удалось прочитать снимок стартовых трофеев сезона")
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


async def start_new_push_season() -> dict:
    """Старт сезона без рассылки: фиксируем кубки основы как точку отсчёта."""
    return await record_season_start_trophies()


# ─── Финиш сезона ────────────────────────────────────────────────────────────

async def check_season_results(bot: Bot) -> str:
    """Вызывается В КОНЦЕ сезона.

    Автоматически проверяются только трофеи (по актуальным данным Brawl Stars API).
    Для каждого, кто добрал трофеи, указано, какую минимальную лигу проверить вручную.
    """
    goals_map = {}
    for g in await get_push_goals():
        uid = _safe_int(g.get("user_id"))
        if uid:
            goals_map[uid] = g.get("goal")

    squad_members = squad_registered(await get_all_members())
    if not squad_members:
        return "📭 В основном составе сейчас нет зарегистрированных игроков."

    mins = calc_push_minimums(squad_members)
    x = mins["top_trophies"]

    done, failed, api_errors, unvoted = [], [], [], []

    for m in squad_members:
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
                need = PUSH_NORMS["trophies_alt"][1]
            elif trophies >= mins["trophies_main"]:
                need = PUSH_NORMS["trophies_main"][1]
            else:
                need = None
            fail_hint = (f"нужно {fmt_num(mins['trophies_main'])} (+ Лега 1) "
                         f"или {fmt_num(mins['trophies_alt'])} (+ Мифик 1)")
        else:
            if trophies >= mins["league_main"]:
                need = PUSH_NORMS["league_main"][1]
            elif trophies >= mins["league_alt"]:
                need = PUSH_NORMS["league_alt"][1]
            else:
                need = None
            fail_hint = (f"нужно {fmt_num(mins['league_main'])} (+ Лега 3) "
                         f"или {fmt_num(mins['league_alt'])} (+ Мастер 1)")

        label = GOAL_LABELS[goal]
        if need:
            done.append(f"• {nick} ({label}) — {fmt_num(trophies)} 🏆 → проверить лигу: минимум <b>{need}</b>")
        else:
            failed.append(f"• {nick} ({label}) — {fmt_num(trophies)} 🏆, {fail_hint}")

    lines = [
        "📊 <b>ИТОГИ СЕЗОНА ПУША (ОСНОВА)</b>",
        f"👑 ТОП-1 клана (X): {hd.quote(mins['top_nick'])} — <b>{fmt_num(x)}</b> 🏆",
        f"Нормы: X/1.2 = {fmt_num(mins['trophies_main'])} · X/1.1 = {fmt_num(mins['trophies_alt'])} · "
        f"X/1.3 = {fmt_num(mins['league_main'])} · X/1.35 = {fmt_num(mins['league_alt'])}",
        "⚠️ <i>Лигу бот не проверяет — сверьте вручную по пометке «проверить лигу».</i>",
        "━━━━━━━",
        "✅ <b>ТРОФЕИ ВЫПОЛНЕНЫ:</b>",
        *(done or ["  — нет игроков"]),
        "",
        "❌ <b>НЕ ДОБРАЛИ ТРОФЕИ (ШТРАФНИКИ):</b>",
        *(failed or ["  — нет игроков"]),
    ]
    if api_errors:
        lines += ["", "⚠️ <b>ОШИБКА API:</b>", *api_errors]
    if unvoted:
        lines += ["", "❓ <b>НЕ ВЫБИРАЛИ ЦЕЛЬ:</b>", *unvoted]
    return "\n".join(lines)
