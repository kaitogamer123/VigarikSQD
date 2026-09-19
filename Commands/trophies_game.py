"""
Личный профиль Brawl Stars: команда /profileBS.
Старые клановые команды /TrophHour /Trophday /TropWeek /TrophMonth удалены.

Статистика часов/дня/недели и история боёв берутся из НАКОПИТЕЛЬНОЙ базы
player_stats.db (фоновый сборщик utils/stats_collector.py дописывает новые бои),
поэтому история не ограничивается последними 25 боями из API.
"""
import logging
from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.markdown import html_decoration as hd

from config import CLAN_DISPLAY
from database import get_member
from player_stats_db import (
    ensure_player_tracked,
    get_recent_battles,
    get_tracked_player,
    get_window_summary,
    save_battlelog,
)
from services.api_service import get_player_battlelog, get_player_profile

logger = logging.getLogger(__name__)
router = Router()

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


def _fmt_signed(value: int) -> str:
    value = int(value or 0)
    return f"+{value}" if value > 0 else str(value)


def _mode_name(mode: str) -> str:
    return MODE_RU.get(mode or "unknown", mode or "Неизвестно")


def _result_emoji(row: dict) -> str:
    result = (row.get("result") or "").lower()
    if result == "victory":
        return "✅"
    if result == "defeat":
        return "❌"
    if result == "draw":
        return "🤝"
    if row.get("rank"):
        return f"#{row['rank']}"
    return "⚔️"


def _parse_iso(raw: str):
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        try:
            return datetime.fromisoformat(str(raw)).replace(tzinfo=timezone.utc)
        except Exception:
            return None


def _human_ago(raw: str) -> str:
    dt = _parse_iso(raw)
    if not dt:
        return "недавно"
    diff = datetime.now(timezone.utc) - dt
    minutes = int(diff.total_seconds() // 60)
    if minutes < 1:
        return "только что"
    if minutes < 60:
        return f"{minutes} мин назад"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} ч назад"
    return f"{hours // 24} дн. назад"


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


def _stats_block(summary: dict) -> str:
    count = summary.get("count", 0)
    wins = summary.get("wins", 0)
    losses = summary.get("losses", 0)
    draws = summary.get("draws", 0)
    trop = summary.get("trophies", 0)
    winrate = round(wins / count * 100) if count else 0
    emoji = "📈" if trop >= 0 else "📉"
    return (
        f"🎮 Боёв: {count}\n"
        f"✅ Побед: {wins}   ❌ Поражений: {losses}   🤝 Ничьих: {draws}\n"
        f"📊 Винрейт: {winrate}%\n"
        f"{emoji} Кубков за период: {_fmt_signed(trop)}"
    )


async def _load_bundle(user_id: int):
    member = await get_member(user_id)
    if not member or not member.get("registered"):
        return None, None, None, "unregistered"
    tag = member.get("player_tag")
    if not tag:
        return member, None, None, "no_tag"

    profile = await get_player_profile(tag)
    fresh_battles = await get_player_battlelog(tag)

    # Сразу дописываем в накопительную базу только новые бои
    try:
        await save_battlelog(tag, fresh_battles)
        nick = (profile or {}).get("name") or member.get("game_nick") or "Игрок"
        trophies = (profile or {}).get("trophies") if profile else member.get("trophies")
        await ensure_player_tracked(tag, nick, trophies or 0)
    except Exception as e:
        logger.error(f"Не удалось сохранить battlelog для {tag}: {e}")

    return member, profile, tag, "ok"


def _fallback_profile(member: dict) -> dict:
    return {
        "name": member.get("game_nick"),
        "tag": member.get("player_tag"),
        "trophies": member.get("trophies") or 0,
        "highest_trophies": member.get("trophies") or 0,
    }


async def _period_text(period: str, member: dict, profile: dict, tag: str) -> str:
    if period == "hour":
        title = "⏱ Статистика за последний час"
        summary = await get_window_summary(tag, hours=1)
    elif period == "day":
        title = "📅 Статистика за последние 24 часа"
        summary = await get_window_summary(tag, hours=24)
    elif period == "week":
        title = "📆 Статистика за последние 7 дней"
        summary = await get_window_summary(tag, days=7)
    else:
        title = "🏆 Статистика за всё время слежки"
        summary = await get_window_summary(tag)

    if period == "all":
        tracked = await get_tracked_player(tag) or {}
        trophies_now = (profile or {}).get("trophies") or member.get("trophies") or 0
        highest = (profile or {}).get("highest_trophies") or trophies_now
        trio = (profile or {}).get("trio_wins") or 0
        solo = (profile or {}).get("solo_wins") or 0
        duo = (profile or {}).get("duo_wins") or 0
        start_trophies = tracked.get("start_trophies")
        first_seen = tracked.get("first_seen")

        delta_line = ""
        if start_trophies is not None and first_seen:
            delta = int(trophies_now) - int(start_trophies)
            delta_line = f"\n📈 Кубков с {str(first_seen)[:10]}: {_fmt_signed(delta)}"

        if summary.get("count", 0) == 0:
            body = (
                "📭 Пока нет сохранённых боёв.\n"
                "История накопится автоматически после следующих матчей "
                "(фоновый сбор каждые 10 минут)."
            )
        else:
            body = _stats_block(summary)

        return (
            f"{_header(member, profile)}\n\n"
            f"{title}\n"
            f"{body}\n\n"
            f"🏆 Текущие кубки: {int(trophies_now):,}\n"
            f"🥇 Максимум кубков: {int(highest):,}\n"
            f"🥇 Побед 3на3: {int(trio):,}\n"
            f"🧍 Побед соло: {int(solo):,}\n"
            f"👥 Побед дуо: {int(duo):,}"
            f"{delta_line}\n\n"
            f"👇 Выбери другой период:"
        ).replace(",", " ")

    if summary.get("count", 0) == 0:
        body = (
            "📭 За этот период сохранённых боёв нет.\n"
            "Если ты только что сыграл — нажми «🔄 Обновить профиль» через несколько секунд."
        )
    else:
        body = _stats_block(summary)

    return (
        f"{_header(member, profile)}\n\n"
        f"{title}\n"
        f"{body}\n\n"
        f"ℹ️ Данные накапливаются ботом и больше не ограничены 25 боями API.\n"
        f"👇 Выбери другой период:"
    )


async def _battles_text(member: dict, profile: dict, tag: str) -> str:
    rows = await get_recent_battles(tag, limit=20)
    total = await get_window_summary(tag)
    total_count = total.get("count", 0)

    lines = [_header(member, profile), "", "🎮 История боёв"]
    if not rows:
        lines.append("📭 История пуста. Сыграй матч и нажми «🔄 Обновить профиль».")
        lines.append("")
        lines.append("👇 Выбери период статистики:")
        return "\n".join(lines)

    if total_count > len(rows):
        lines.append(f"<i>Показаны последние {len(rows)} из {total_count} сохранённых боёв</i>")
    else:
        lines.append(f"<i>Всего сохранено боёв: {total_count}</i>")
    lines.append("")

    for row in rows:
        emoji = _result_emoji(row)
        mode = hd.quote(_mode_name(row.get("mode")))
        mp = hd.quote(str(row.get("map") or "Случайная карта"))
        trop = _fmt_signed(row.get("trophies") or 0)
        ago = _human_ago(row.get("battle_time"))
        btype = row.get("battle_type") or ""
        type_mark = " 🏅" if "rank" in str(btype).lower() else ""
        when = str(row.get("battle_time") or "")[:16].replace("-", ".")
        lines.append(
            f"{emoji} {mode} — {trop} 🏆 · {mp}{type_mark}\n"
            f"    └ {when} · {ago}"
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


async def _render(target, action: str):
    if isinstance(target, CallbackQuery):
        user_id = target.from_user.id
    else:
        user_id = target.from_user.id

    member, profile, tag, status = await _load_bundle(user_id)
    if status != "ok":
        await _answer_error(target, status)
        return None

    if not profile:
        profile = _fallback_profile(member)

    if action == "home":
        return _home_text(member, profile)
    if action == "battles":
        return await _battles_text(member, profile, tag)
    if action in ("hour", "day", "week", "all"):
        return await _period_text(action, member, profile, tag)
    return _home_text(member, profile)


@router.message(Command(commands=["profileBS", "profilebs", "profile_bs"]))
async def cmd_profile_bs(message: Message):
    wait = await message.answer("⏳ Загружаю твой профиль и сохраняю свежие бои...")
    text = await _render(message, "home")
    if text is None:
        try:
            await wait.delete()
        except Exception:
            pass
        return
    try:
        await wait.edit_text(text, parse_mode="HTML", reply_markup=profile_keyboard())
    except Exception as e:
        logger.error(f"/profileBS render error: {e}")
        await message.answer(text, parse_mode="HTML", reply_markup=profile_keyboard())


@router.callback_query(F.data.startswith("pbs:"))
async def cb_profile_bs(call: CallbackQuery):
    action = call.data.split(":", 1)[1]
    await call.answer("⏳ Обновляю...")
    text = await _render(call, action)
    if text is None:
        return
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=profile_keyboard())
    except Exception as e:
        if "message is not modified" in str(e).lower():
            return
        logger.error(f"profileBS edit error: {e}")
        try:
            await call.message.answer(text, parse_mode="HTML", reply_markup=profile_keyboard())
        except Exception:
            pass
