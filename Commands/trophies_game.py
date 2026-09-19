"""
Профиль Brawl Stars.
/profileBS        — свой профиль
/ProfileBSOther   — профиль любого участника клана (по ID / @username / тегу / нику)

Статистика считается по накопленной базе player_stats.db,
а не только по последним ~25 боям, которые отдаёт официальный API.
"""
import logging
from datetime import datetime, timezone

import aiosqlite
from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.markdown import html_decoration as hd

from config import CLAN_DISPLAY
from database import get_all_members, get_member
from services.api_service import get_player_battlelog, get_player_profile
import player_stats_db as stats

logger = logging.getLogger(__name__)
router = Router()

GAME_DB_PATH = "game_clans.db"

MODE_RU = {
    "gemGrab": "Захват кристаллов",
    "brawlBall": "Броулбол",
    "soloShowdown": "Столкновение",
    "duoShowdown": "Дуо-столкновение",
    "trioShowdown": "Трио-столкновение",
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
    "unknown": "Бой",
    "": "Бой",
}

PERIODS = {
    "hour": ("⏱ Статистика за последний час", 1, None),
    "day": ("📅 Статистика за последний день", 24, None),
    "week": ("📆 Статистика за неделю", None, 7),
    "all": ("🏆 Статистика за всё время", None, None),
}


class OtherProfileState(StatesGroup):
    waiting_for_player = State()


def profile_keyboard(target_user_id: int = None) -> InlineKeyboardMarkup:
    prefix = f"pbso:{target_user_id}" if target_user_id else "pbs"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏱ Час", callback_data=f"{prefix}:hour"),
            InlineKeyboardButton(text="📅 День", callback_data=f"{prefix}:day"),
        ],
        [
            InlineKeyboardButton(text="📆 Неделя", callback_data=f"{prefix}:week"),
            InlineKeyboardButton(text="🏆 Всё время", callback_data=f"{prefix}:all"),
        ],
        [InlineKeyboardButton(text="🎮 История боёв", callback_data=f"{prefix}:battles")],
        [InlineKeyboardButton(text="🔄 Обновить профиль", callback_data=f"{prefix}:home")],
    ])


def _norm_tag(tag: str) -> str:
    return (tag or "").strip().upper().replace("#", "")


def _fmt_num(value) -> str:
    try:
        return f"{int(value or 0):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "0"


def _fmt_signed(value) -> str:
    value = int(value or 0)
    return f"+{value}" if value > 0 else str(value)


def _parse_db_time(raw: str):
    try:
        return datetime.strptime(str(raw), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _human_ago(dt) -> str:
    if not dt:
        return "недавно"
    minutes = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
    if minutes < 1:
        return "только что"
    if minutes < 60:
        return f"{minutes} мин назад"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} ч назад"
    return f"{hours // 24} дн. назад"


def _result_emoji(row: dict) -> str:
    result = (row.get("result") or "").lower()
    if result == "victory":
        return "✅"
    if result == "defeat":
        return "❌"
    if result == "draw":
        return "🤝"
    rank = row.get("rank")
    if rank:
        return f"#{rank}"
    return "⚔️"


async def _game_db_stats(player_tag: str) -> dict:
    """Прирост из клан-трекера game_clans.db, если он есть на сервере."""
    tag = _norm_tag(player_tag)
    if not tag:
        return {}
    try:
        async with aiosqlite.connect(GAME_DB_PATH, timeout=10.0) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT trophies_hour_diff, trophies_day_diff, trophies_week_diff, trophies_month_diff
                FROM clan_players
                WHERE REPLACE(UPPER(player_tag), '#', '') = ?
                LIMIT 1
                """,
                (tag,),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else {}
    except Exception:
        return {}


async def _load_bundle(user_id: int):
    """Грузит профиль из API и докачивает новые бои в player_stats.db."""
    member = await get_member(user_id)
    if not member or not member.get("registered"):
        return None, None, None, "unregistered"
    tag = member.get("player_tag")
    if not tag:
        return member, None, None, "no_tag"

    profile = await get_player_profile(tag)
    if not profile:
        profile = {
            "name": member.get("game_nick"),
            "tag": tag,
            "trophies": member.get("trophies") or 0,
            "highest_trophies": member.get("trophies") or 0,
        }

    try:
        await stats.ensure_player_tracked(
            tag,
            profile.get("name") or member.get("game_nick"),
            profile.get("trophies") or 0,
        )
        battles = await get_player_battlelog(tag)
        new_count = await stats.save_battlelog(tag, battles)
        if new_count:
            logger.info(f"[player_stats] {tag}: сохранено новых боёв {new_count}")
    except Exception as e:
        logger.error(f"[player_stats] ошибка сохранения для {tag}: {e}")

    return member, profile, await _game_db_stats(tag), "ok"


def _header(member: dict, profile: dict) -> str:
    nick = (profile or {}).get("name") or member.get("game_nick") or member.get("first_name") or "Игрок"
    tag = (profile or {}).get("tag") or member.get("player_tag") or "—"
    if tag and not str(tag).startswith("#"):
        tag = f"#{tag}"
    clan_key = member.get("clan")
    clan_title = CLAN_DISPLAY.get(clan_key, clan_key) if clan_key else None
    clan_line = (profile or {}).get("clan_name") or (clan_title.upper() if clan_title else "нет клуба")
    trophies = (profile or {}).get("trophies")
    if trophies is None:
        trophies = member.get("trophies") or 0
    highest = (profile or {}).get("highest_trophies") or trophies

    return (
        f"🎮 Профиль Brawl Stars\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Ник: {hd.quote(str(nick))}\n"
        f"🏷️ Тег: {hd.quote(str(tag))}\n"
        f"🏰 Клуб: {hd.quote(str(clan_line))}\n"
        f"🏆 Кубки: {_fmt_num(trophies)}\n"
        f"🥇 Рекорд: {_fmt_num(highest)}\n"
        f"⭐ Уровень: {(profile or {}).get('exp_level') or '—'}   "
        f"🧸 Бравлеров: {(profile or {}).get('brawlers_count') or '—'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


def _summary_block(summary: dict) -> str:
    count = int(summary.get("count") or 0)
    if count == 0:
        return "📭 Боёв за этот период в истории нет."

    wins = int(summary.get("wins") or 0)
    winrate = round(wins / count * 100)
    trop = int(summary.get("trophies") or 0)
    trop_emoji = "📈" if trop >= 0 else "📉"

    return (
        f"🎮 Боёв: {count}\n"
        f"✅ Побед: {wins}   ❌ Поражений: {int(summary.get('losses') or 0)}   "
        f"🤝 Ничьих: {int(summary.get('draws') or 0)}\n"
        f"📊 Винрейт: {winrate}%\n"
        f"{trop_emoji} Кубки по боям: {_fmt_signed(trop)}"
    )


async def _period_text(period: str, member: dict, profile: dict, game_stats: dict) -> str:
    tag = member.get("player_tag")
    title, hours, days = PERIODS[period]

    summary = await stats.get_window_summary(tag, hours=hours, days=days)
    parts = [_header(member, profile), "", title, _summary_block(summary)]

    if period == "all":
        trophies_now = (profile or {}).get("trophies") or member.get("trophies") or 0
        highest = (profile or {}).get("highest_trophies") or trophies_now
        total = await stats.get_total_battles_count(tag)
        tracked = await stats.get_tracked_player(tag)

        growth_line = ""
        if tracked:
            delta = int(trophies_now) - int(tracked.get("start_trophies") or 0)
            since = _parse_db_time(tracked.get("first_seen"))
            since_str = since.strftime("%d.%m.%Y") if since else "начала отслеживания"
            growth_line = f"\n📈 Прирост с {since_str}: {_fmt_signed(delta)} кубков"

        parts.append(
            f"\n🏅 Общая карьера:\n"
            f"🏆 Текущие кубки: {_fmt_num(trophies_now)}\n"
            f"🥇 Максимум кубков: {_fmt_num(highest)}\n"
            f"🥇 Побед 3на3: {_fmt_num((profile or {}).get('trio_wins'))}\n"
            f"🧍 Побед соло: {_fmt_num((profile or {}).get('solo_wins'))}\n"
            f"👥 Побед дуо: {_fmt_num((profile or {}).get('duo_wins'))}\n"
            f"🗂 Боёв сохранено в базе: {total}"
            f"{growth_line}"
        )

    tracker_val = {
        "hour": game_stats.get("trophies_hour_diff"),
        "day": game_stats.get("trophies_day_diff"),
        "week": game_stats.get("trophies_week_diff"),
        "all": game_stats.get("trophies_month_diff"),
    }.get(period)
    if tracker_val is not None:
        label = "за месяц" if period == "all" else ""
        parts.append(f"🗂 Трекер клана {label}: {_fmt_signed(tracker_val)} кубков".replace("  ", " "))

    parts.append("")
    parts.append("ℹ️ Бои сохраняются в базу при каждом открытии профиля.")
    parts.append("👇 Выбери другой период:")
    return "\n".join(parts)


async def _battles_text(member: dict, profile: dict) -> str:
    tag = member.get("player_tag")
    rows = await stats.get_recent_battles(tag, limit=15)
    total = await stats.get_total_battles_count(tag)

    lines = [_header(member, profile), "", f"🎮 История боёв (всего сохранено: {total})"]
    if not rows:
        lines += ["📭 История пуста. Сыграй матч и нажми «Обновить профиль».", "", "👇 Выбери период статистики:"]
        return "\n".join(lines)

    for r in rows:
        mode_key = r.get("mode") or ""
        mode = MODE_RU.get(mode_key, mode_key or "Бой")
        mp = r.get("map") or "Случайная карта"
        ranked = " 🏅" if "rank" in str(r.get("battle_type") or "").lower() else ""
        lines.append(
            f"{_result_emoji(r)} {hd.quote(mode)} — {_fmt_signed(r.get('trophy_change'))} 🏆{ranked}\n"
            f"    └ {hd.quote(str(mp))} · {_human_ago(_parse_db_time(r.get('battle_time')))}"
        )

    lines += ["", "👇 Выбери период статистики:"]
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


def _clean_query(raw: str) -> str:
    """Убирает упоминание бота, лишний слэш и пробелы: '/@sotoruu@VGStatsBot' -> '@sotoruu'."""
    text = (raw or "").strip()
    # Убираем приписку @ИмяБота, которую Telegram добавляет в группах
    parts = text.split()
    cleaned = []
    for p in parts:
        if p.lower().endswith("bot") and "@" in p and p.startswith("/"):
            p = p.split("@")[0]
        cleaned.append(p)
    text = " ".join(cleaned).strip()
    # Пользователь мог случайно отправить '/@sotoruu' или '/#TAG'
    if text.startswith("/") and len(text) > 1 and text[1] in "@#":
        text = text[1:]
    return text.strip()


def _is_cancel(raw: str) -> bool:
    text = _clean_query(raw).lower()
    if text.startswith("/"):
        text = text.split("@")[0]
    return text in ("/cancel", "cancel", "отмена")


async def _find_clan_member(query: str):
    """Ищет зарегистрированного игрока по Telegram ID, @username, игровому тегу или нику."""
    raw = _clean_query(query)
    if not raw:
        return None

    members = await get_all_members() or []
    candidates = [m for m in members if m and m.get("registered") == 1]

    if raw.isdigit():
        wanted = int(raw)
        return next((m for m in candidates if int(m.get("user_id") or 0) == wanted), None)

    text = raw.casefold()
    username = raw.lstrip("@").casefold()
    player_tag = _norm_tag(raw)

    # 1. Точное совпадение с явным префиксом
    for m in candidates:
        if raw.startswith("@") and (m.get("username") or "").casefold() == username:
            return m
        if raw.startswith("#") and _norm_tag(m.get("player_tag")) == player_tag:
            return m

    # 2. Точное совпадение без префикса
    for m in candidates:
        if (m.get("username") or "").casefold() == username:
            return m
        if player_tag and _norm_tag(m.get("player_tag")) == player_tag:
            return m
        if (m.get("game_nick") or "").casefold() == text:
            return m

    # 3. Частичное совпадение по нику (если ник написан не идеально)
    matches = [m for m in candidates if text and text in (m.get("game_nick") or "").casefold()]
    if len(matches) == 1:
        return matches[0]

    return None


@router.message(Command(commands=["profileBS", "profilebs", "profile_bs"]))
async def cmd_profile_bs(message: Message):
    wait = await message.answer("⏳ Загружаю твой профиль из Brawl Stars...")
    member, profile, game_stats, status = await _load_bundle(message.from_user.id)
    if status != "ok":
        try:
            await wait.delete()
        except Exception:
            pass
        await _answer_error(message, status)
        return

    text = _home_text(member, profile)
    try:
        await wait.edit_text(text, parse_mode="HTML", reply_markup=profile_keyboard())
    except Exception as e:
        logger.error(f"/profileBS render error: {e}")
        await message.answer(text, parse_mode="HTML", reply_markup=profile_keyboard())


@router.callback_query(F.data.startswith("pbs:"))
async def cb_profile_bs(call: CallbackQuery):
    action = call.data.split(":", 1)[1]
    await call.answer("⏳ Обновляю...")

    member, profile, game_stats, status = await _load_bundle(call.from_user.id)
    if status != "ok":
        await _answer_error(call, status)
        return

    try:
        if action == "battles":
            text = await _battles_text(member, profile)
        elif action in PERIODS:
            text = await _period_text(action, member, profile, game_stats or {})
        else:
            text = _home_text(member, profile)
    except Exception as e:
        logger.error(f"profileBS build error ({action}): {e}")
        text = _home_text(member, profile) + "\n\n❌ Не удалось построить статистику, попробуй ещё раз."

    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=profile_keyboard())
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            logger.error(f"profileBS edit error: {e}")


async def _show_other_profile(message: Message, query: str) -> bool:
    """Ищет игрока и выводит его профиль. True — если получилось."""
    target = await _find_clan_member(query)
    if not target:
        return False

    target_id = int(target.get("user_id"))
    wait = await message.answer("⏳ Загружаю профиль игрока из Brawl Stars...")
    member, profile, game_stats, status = await _load_bundle(target_id)
    if status != "ok":
        try:
            await wait.delete()
        except Exception:
            pass
        await message.answer("❌ У выбранного игрока нет привязанного тега Brawl Stars.")
        return True

    text = "👥 Профиль участника клана\n\n" + _home_text(member, profile)
    try:
        await wait.edit_text(text, parse_mode="HTML", reply_markup=profile_keyboard(target_id))
    except Exception as e:
        logger.error(f"/ProfileBSOther render error: {e}")
        await message.answer(text, parse_mode="HTML", reply_markup=profile_keyboard(target_id))
    return True


@router.message(Command(commands=["ProfileBSOther", "profilebsother", "profile_bs_other"]))
async def cmd_profile_bs_other(message: Message, command: CommandObject, state: FSMContext):
    viewer = await get_member(message.from_user.id)
    if not viewer or not viewer.get("registered"):
        await message.answer("❌ Сначала пройди регистрацию через /start.")
        return

    # Вариант в одну строку: /ProfileBSOther @sotoruu
    if command.args:
        await state.clear()
        ok = await _show_other_profile(message, command.args)
        if not ok:
            await message.answer(
                "❌ Игрок не найден среди зарегистрированных участников.\n"
                "Проверь написание и попробуй ещё раз."
            )
        return

    is_private = message.chat.type == "private"

    # В группах Telegram не отдаёт боту обычные сообщения (privacy mode),
    # поэтому там работает только формат с аргументом в одной строке.
    if not is_private:
        await message.answer(
            "🔎 Чей профиль Brawl Stars открыть?\n\n"
            "В группе укажи игрока сразу в команде:\n"
            "<code>/ProfileBSOther @username</code>\n"
            "<code>/ProfileBSOther #9PJYV82CC</code>\n"
            "<code>/ProfileBSOther 123456789</code>\n\n"
            "Либо напиши мне в личные сообщения — там можно просто ответить ником.",
            parse_mode="HTML",
        )
        return

    await state.set_state(OtherProfileState.waiting_for_player)
    await message.answer(
        "🔎 Чей профиль Brawl Stars открыть?\n\n"
        "Отправь один из вариантов:\n"
        "• Telegram ID\n"
        "• @username\n"
        "• игровой тег, например #9PJYV82CC\n"
        "• точный игровой ник\n\n"
        "Для отмены отправь /cancel."
    )


@router.message(OtherProfileState.waiting_for_player)
async def receive_other_profile_player(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("❌ Отправь ID, @username, игровой тег или точный ник текстом.")
        return

    if _is_cancel(message.text):
        await state.clear()
        await message.answer("❌ Поиск профиля отменён.")
        return

    ok = await _show_other_profile(message, message.text)
    if not ok:
        await message.answer(
            "❌ Игрок не найден среди зарегистрированных участников.\n"
            "Проверь написание и попробуй ещё раз или отправь /cancel."
        )
        return

    await state.clear()


@router.callback_query(F.data.startswith("pbso:"))
async def cb_profile_bs_other(call: CallbackQuery):
    parts = call.data.split(":", 2)
    if len(parts) != 3 or not parts[1].isdigit():
        await call.answer("Некорректная кнопка", show_alert=True)
        return

    viewer = await get_member(call.from_user.id)
    if not viewer or not viewer.get("registered"):
        await call.answer("Сначала пройди регистрацию через /start", show_alert=True)
        return

    target_id = int(parts[1])
    action = parts[2]
    await call.answer("⏳ Обновляю...")

    member, profile, game_stats, status = await _load_bundle(target_id)
    if status != "ok":
        await call.answer("Профиль игрока больше недоступен", show_alert=True)
        return

    try:
        if action == "battles":
            body = await _battles_text(member, profile)
        elif action in PERIODS:
            body = await _period_text(action, member, profile, game_stats or {})
        else:
            body = _home_text(member, profile)
        text = "👥 Профиль участника клана\n\n" + body
    except Exception as e:
        logger.error(f"ProfileBSOther build error ({target_id}, {action}): {e}")
        text = "👥 Профиль участника клана\n\n" + _home_text(member, profile)

    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=profile_keyboard(target_id))
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            logger.error(f"ProfileBSOther edit error: {e}")
