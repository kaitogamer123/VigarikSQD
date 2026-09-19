"""
Личный профиль Brawl Stars: команда /profileBS.
Старые клановые команды /TrophHour /Trophday /TropWeek /TrophMonth удалены.
Игрок видит свои кубки, прирост за час/день/неделю/всё время и историю боёв.
"""
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.markdown import html_decoration as hd

from config import CLAN_DISPLAY
from database import get_member
from services.api_service import get_player_battlelog, get_player_profile

logger = logging.getLogger(__name__)
router = Router()

GAME_DB_PATH = "game_clans.db"

MODE_RU = {
    "gemGrab": "Захват кристаллов",
    "brawlBall": "Броулбол",
    "soloShowdown": "Столкновение",
    "duoShowdown": "Дуо-столкновение",
    "heist": "Ограбление",
    "bounty": "Награда",
    "hotZone": "Горячая зона",
    "knockout": "Нокаут",
    "wipeout": "На вылет",
    "duels": "Дуэли",
    "payload": "Груз",
    "siege": "Осада",
    "basketBrawl": "Баскетбой",
    "volleyBrawl": "Волейбой",
    "holdTheTrophy": "Удержи трофей",
    "trophyThieves": "Похитители трофеев",
    "paintBrawl": "Краскобой",
    "knockout5v5": "Нокаут 5на5",
    "gemGrab5v5": "Кристаллы 5на5",
    "brawlBall5v5": "Броулбол 5на5",
    "wipeout5v5": "На вылет 5на5",
    "hotZone5v5": "Горячая зона 5на5",
    "soloRanked": "Ранкед",
    "teamRanked": "Ранкед",
    "ranked": "На кубки",
}


def profile_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏱ Час", callback_data="pbs:hour"),
            InlineKeyboardButton(text="📅 День", callback_data="pbs:day"),
        ],
        [
            InlineKeyboardButton(text="📆 Неделя", callback_data="pbs:week"),
            InlineKeyboardButton(text="🏆 Всё время", callback_data="pbs:all"),
        ],
        [InlineKeyboardButton(text="🎮 История боёв", callback_data="pbs:battles")],
        [InlineKeyboardButton(text="🔄 Обновить профиль", callback_data="pbs:home")],
    ])


def _norm_tag(tag: str) -> str:
    return (tag or "").strip().upper().replace("#", "")


def _parse_battle_time(raw: str):
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw)[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except Exception:
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except Exception:
            return None


def _human_ago(battle_dt) -> str:
    if not battle_dt:
        return "недавно"
    now = datetime.now(timezone.utc)
    if battle_dt.tzinfo is None:
        battle_dt = battle_dt.replace(tzinfo=timezone.utc)
    diff = now - battle_dt
    minutes = int(diff.total_seconds() // 60)
    if minutes < 1:
        return "только что"
    if minutes < 60:
        return f"{minutes} мин назад"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} ч назад"
    days = hours // 24
    return f"{days} дн. назад"


def _fmt_signed(value: int) -> str:
    value = int(value or 0)
    if value > 0:
        return f"+{value}"
    return str(value)


def _mode_name(item: dict) -> str:
    battle = item.get("battle") or {}
    event = item.get("event") or {}
    mode = event.get("mode") or battle.get("mode") or "unknown"
    return MODE_RU.get(mode, str(mode))


def _map_name(item: dict) -> str:
    event = item.get("event") or {}
    name = event.get("map")
    return name if name else "Случайная карта"


def _trophy_change(item: dict, player_tag: str) -> int:
    battle = item.get("battle") or {}
    if battle.get("trophyChange") is not None:
        try:
            return int(battle.get("trophyChange") or 0)
        except (TypeError, ValueError):
            return 0
    my = _norm_tag(player_tag)
    for p in battle.get("players") or []:
        if _norm_tag(p.get("tag")) == my:
            try:
                return int(p.get("trophyChange") or 0)
            except (TypeError, ValueError):
                return 0
    return 0


def _battle_result_emoji(item: dict) -> str:
    battle = item.get("battle") or {}
    result = (battle.get("result") or "").lower()
    if result == "victory":
        return "✅"
    if result == "defeat":
        return "❌"
    if result == "draw":
        return "🤝"
    rank = battle.get("rank")
    if rank:
        return f"#{rank}"
    return "⚔️"


def _filter_battles(items: list, hours: int = None, days: int = None) -> list:
    now = datetime.now(timezone.utc)
    out = []
    for item in items:
        t = _parse_battle_time(item.get("battleTime"))
        if not t:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        delta = now - t
        if hours is not None and delta > timedelta(hours=hours):
            continue
        if days is not None and delta > timedelta(days=days):
            continue
        out.append(item)
    return out


def _summarize_battles(items: list, player_tag: str) -> dict:
    wins = losses = draws = other = 0
    trop = 0
    for item in items:
        trop += _trophy_change(item, player_tag)
        battle = item.get("battle") or {}
        result = (battle.get("result") or "").lower()
        if result == "victory":
            wins += 1
        elif result == "defeat":
            losses += 1
        elif result == "draw":
            draws += 1
        else:
            other += 1
    return {
        "count": len(items),
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "other": other,
        "trophies": trop,
    }


async def _game_db_stats(player_tag: str) -> dict:
    """Читает прирост кубков из game_clans.db, если сборщик на сервере пишет туда данные."""
    tag = _norm_tag(player_tag)
    if not tag:
        return {}
    try:
        async with aiosqlite.connect(GAME_DB_PATH, timeout=10.0) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT trophies, trophies_hour_diff, trophies_day_diff,
                       trophies_week_diff, trophies_month_diff, last_played_at, game_nick
                FROM clan_players
                WHERE REPLACE(UPPER(player_tag), '#', '') = ?
                LIMIT 1
                """,
                (tag,),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else {}
    except Exception as e:
        logger.warning(f"Не удалось прочитать game_clans.db: {e}")
        return {}


async def _load_bundle(user_id: int):
    member = await get_member(user_id)
    if not member or not member.get("registered"):
        return None, None, None, None, "unregistered"
    tag = member.get("player_tag")
    if not tag:
        return member, None, None, None, "no_tag"

    profile = await get_player_profile(tag)
    battles = await get_player_battlelog(tag)
    game_stats = await _game_db_stats(tag)
    return member, profile, battles, game_stats, "ok"


def _header(member: dict, profile: dict) -> str:
    nick = (profile or {}).get("name") or member.get("game_nick") or member.get("first_name") or "Игрок"
    tag = (profile or {}).get("tag") or member.get("player_tag") or "—"
    if tag and not str(tag).startswith("#"):
        tag = f"#{tag}"
    clan_key = member.get("clan")
    clan_title = CLAN_DISPLAY.get(clan_key, clan_key) if clan_key else None
    api_clan = (profile or {}).get("clan_name")
    clan_line = api_clan or (clan_title.upper() if clan_title else "нет клуба")
    trophies = (profile or {}).get("trophies")
    if trophies is None:
        trophies = member.get("trophies") or 0
    highest = (profile or {}).get("highest_trophies") or trophies
    level = (profile or {}).get("exp_level") or "—"
    brawlers = (profile or {}).get("brawlers_count")
    brawlers_str = str(brawlers) if brawlers else "—"

    return (
        f"🎮 Профиль Brawl Stars\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Ник: {hd.quote(str(nick))}\n"
        f"🏷️ Тег: {hd.quote(str(tag))}\n"
        f"🏰 Клуб: {hd.quote(str(clan_line))}\n"
        f"🏆 Кубки: {int(trophies):,}\n"
        f"🥇 Рекорд: {int(highest):,}\n"
        f"⭐ Уровень: {level}   🧸 Бравлеров: {brawlers_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━"
    ).replace(",", " ")


def _period_text(period: str, member: dict, profile: dict, battles: list, game_stats: dict) -> str:
    tag = member.get("player_tag")
    trophies_now = (profile or {}).get("trophies")
    if trophies_now is None:
        trophies_now = member.get("trophies") or 0

    titles = {
        "hour": ("⏱ Статистика за последний час", 1, None),
        "day": ("📅 Статистика за последний день", 24, None),
        "week": ("📆 Статистика за неделю", None, 7),
        "all": ("🏆 Статистика за всё время", None, None),
    }
    title, hours, days = titles[period]

    if period == "all":
        trio = (profile or {}).get("trio_wins") or 0
        solo = (profile or {}).get("solo_wins") or 0
        duo = (profile or {}).get("duo_wins") or 0
        highest = (profile or {}).get("highest_trophies") or trophies_now
        month_diff = game_stats.get("trophies_month_diff")
        extra_month = ""
        if month_diff is not None:
            extra_month = f"\n📈 Прирост за месяц (трекер клана): {_fmt_signed(month_diff)}"
        return (
            f"{_header(member, profile)}\n\n"
            f"{title}\n"
            f"🏆 Текущие кубки: {int(trophies_now):,}\n"
            f"🥇 Максимум кубков: {int(highest):,}\n"
            f"🥇 Побед 3на3: {int(trio):,}\n"
            f"🧍 Побед соло: {int(solo):,}\n"
            f"👥 Побед дуо: {int(duo):,}"
            f"{extra_month}\n\n"
            f"👇 Выбери другой период:"
        ).replace(",", " ")

    filtered = _filter_battles(battles or [], hours=hours, days=days)
    summary = _summarize_battles(filtered, tag)

    tracker_map = {
        "hour": game_stats.get("trophies_hour_diff"),
        "day": game_stats.get("trophies_day_diff"),
        "week": game_stats.get("trophies_week_diff"),
    }
    tracker_val = tracker_map.get(period)
    tracker_line = ""
    if tracker_val is not None:
        tracker_line = f"\n🗂 Трекер клана: {_fmt_signed(tracker_val)} кубков"

    if summary["count"] == 0:
        hint = "API отдаёт только последние ~25 боёв. Если давно не играл — список будет пустым."
        return (
            f"{_header(member, profile)}\n\n"
            f"{title}\n"
            f"📭 За этот период боёв в истории API нет.\n"
            f"{hint}"
            f"{tracker_line}\n\n"
            f"👇 Выбери другой период:"
        )

    winrate = round(summary["wins"] / summary["count"] * 100) if summary["count"] else 0
    trop_line = _fmt_signed(summary["trophies"])
    trop_emoji = "📈" if summary["trophies"] >= 0 else "📉"

    return (
        f"{_header(member, profile)}\n\n"
        f"{title}\n"
        f"🎮 Боёв: {summary['count']}\n"
        f"✅ Побед: {summary['wins']}   ❌ Поражений: {summary['losses']}   🤝 Ничьих: {summary['draws']}\n"
        f"📊 Винрейт: {winrate}%\n"
        f"{trop_emoji} Кубки по боям: {trop_line}"
        f"{tracker_line}\n\n"
        f"ℹ️ Считается по последним боям из Brawl Stars API.\n"
        f"👇 Выбери другой период:"
    )


def _battles_text(member: dict, profile: dict, battles: list) -> str:
    tag = member.get("player_tag")
    lines = [_header(member, profile), "", "🎮 История последних боёв"]
    if not battles:
        lines.append("📭 История боёв пуста. Сыграй матч и нажми «Обновить профиль».")
        lines.append("")
        lines.append("👇 Выбери период статистики:")
        return "\n".join(lines)

    for item in battles[:12]:
        emoji = _battle_result_emoji(item)
        mode = _mode_name(item)
        mp = _map_name(item)
        trop = _fmt_signed(_trophy_change(item, tag))
        ago = _human_ago(_parse_battle_time(item.get("battleTime")))
        battle = item.get("battle") or {}
        btype = battle.get("type") or ""
        type_mark = " 🏅" if "rank" in str(btype).lower() else ""
        lines.append(
            f"{emoji} {hd.quote(mode)} — {trop} 🏆 · {hd.quote(str(mp))}{type_mark}\n"
            f"    └ {ago}"
        )

    lines.append("")
    lines.append("👇 Выбери период статистики:")
    return "\n".join(lines)


def _home_text(member: dict, profile: dict) -> str:
    return (
        f"{_header(member, profile)}\n\n"
        f"📊 Нажми кнопку ниже, чтобы посмотреть прирост кубков\n"
        f"за час, день, неделю, всё время или историю боёв."
    )


async def _answer_error(target, kind: str):
    texts = {
        "unregistered": "❌ Сначала пройди регистрацию через /start и привяжи свой игровой тег.",
        "no_tag": "❌ У тебя в профиле бота нет тега Brawl Stars. Напиши /start и пройди верификацию.",
        "fail": "❌ Не удалось загрузить профиль. Попробуй ещё раз через минуту.",
    }
    text = texts.get(kind, texts["fail"])
    if isinstance(target, CallbackQuery):
        await target.answer(text, show_alert=True)
    else:
        await target.answer(text)


@router.message(Command(commands=["profileBS", "profilebs", "profile_bs"]))
async def cmd_profile_bs(message: Message):
    wait = await message.answer("⏳ Загружаю твой профиль из Brawl Stars...")
    member, profile, battles, game_stats, status = await _load_bundle(message.from_user.id)
    if status != "ok":
        try:
            await wait.delete()
        except Exception:
            pass
        await _answer_error(message, status)
        return

    if not profile:
        # Профиль API не ответил — всё равно показываем данные из базы бота
        profile = {
            "name": member.get("game_nick"),
            "tag": member.get("player_tag"),
            "trophies": member.get("trophies") or 0,
            "highest_trophies": member.get("trophies") or 0,
        }

    try:
        await wait.edit_text(_home_text(member, profile), parse_mode="HTML", reply_markup=profile_keyboard())
    except Exception as e:
        logger.error(f"/profileBS render error: {e}")
        await message.answer(_home_text(member, profile), parse_mode="HTML", reply_markup=profile_keyboard())


@router.callback_query(F.data.startswith("pbs:"))
async def cb_profile_bs(call: CallbackQuery):
    action = call.data.split(":", 1)[1]
    await call.answer("⏳ Обновляю...")

    member, profile, battles, game_stats, status = await _load_bundle(call.from_user.id)
    if status != "ok":
        await _answer_error(call, status)
        return

    if not profile:
        profile = {
            "name": member.get("game_nick"),
            "tag": member.get("player_tag"),
            "trophies": member.get("trophies") or 0,
            "highest_trophies": member.get("trophies") or 0,
        }

    if action == "home":
        text = _home_text(member, profile)
    elif action == "battles":
        text = _battles_text(member, profile, battles or [])
    elif action in ("hour", "day", "week", "all"):
        text = _period_text(action, member, profile, battles or [], game_stats or {})
    else:
        text = _home_text(member, profile)

    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=profile_keyboard())
    except Exception as e:
        # «message is not modified» и прочие безопасные случаи
        if "message is not modified" in str(e).lower():
            return
        logger.error(f"profileBS edit error: {e}")
        try:
            await call.message.answer(text, parse_mode="HTML", reply_markup=profile_keyboard())
        except Exception:
            pass
