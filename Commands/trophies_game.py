"""
Личный профиль Brawl Stars: команды /profileBS и /ProfileBSOther.
Все бои из API навсегда сохраняются в player_stats.db — статистика считается
из накопленной истории, а не только из последних ~25 боёв, которые отдаёт API.
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
from player_stats_db import (
    ensure_player_tracked,
    get_recent_battles,
    get_total_battles_count,
    get_window_summary,
    save_battlelog,
)

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
    "": "Бой",
}

PERIODS = {
    "hour": ("⏱ Статистика за последний час", 1, None),
    "day": ("📅 Статистика за последний день", 24, None),
    "week": ("📆 Статистика за неделю", None, 7),
    "clan": ("🏰 Статистика с момента входа в клан", None, None),
    # Совместимость с кнопками «Всё время» в уже отправленных старых сообщениях.
    "all": ("🏰 Статистика с момента входа в клан", None, None),
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
        [InlineKeyboardButton(text="📆 Неделя", callback_data=f"{prefix}:week")],
        [InlineKeyboardButton(
            text="🏰 С момента входа в клан",
            callback_data=f"{prefix}:clan",
        )],
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
    now = datetime.now(timezone.utc)
    minutes = int((now - dt).total_seconds() // 60)
    if minutes < 1:
        return "только что"
    if minutes < 60:
        return f"{minutes} мин назад"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} ч назад"
    days = hours // 24
    return f"{days} дн. назад"


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
    """Грузит профиль из API, сохраняет новые бои в историю, возвращает всё для отрисовки."""
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

    # Сохраняем новые бои в постоянную player_stats.db.
    try:
        api_battles = await get_player_battlelog(tag)
        new_count = await save_battlelog(tag, api_battles)
        if new_count:
            logger.info(f"[player_stats] {tag}: сохранено новых боёв: {new_count}")
        await ensure_player_tracked(
            tag,
            profile.get("name") or member.get("game_nick") or "Игрок",
            profile.get("trophies") or member.get("trophies") or 0,
        )
    except Exception as e:
        logger.error(f"[player_stats] ошибка сохранения для {tag}: {e}")

    game_stats = await _game_db_stats(tag)
    return member, profile, game_stats, "ok"


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

    return (
        f"🎮 Профиль Brawl Stars\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Ник: {hd.quote(str(nick))}\n"
        f"🏷️ Тег: {hd.quote(str(tag))}\n"
        f"🏰 Клуб: {hd.quote(str(clan_line))}\n"
        f"🏆 Кубки: {_fmt_num(trophies)}\n"
        f"🥇 Рекорд: {_fmt_num(highest)}\n"
        f"⭐ Уровень: {level}   🧸 Бравлеров: {brawlers or '—'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


def _summary_block(summary: dict) -> str:
    count = summary.get("count", 0)
    if count == 0:
        return "📭 Боёв за этот период в истории нет."

    wins = summary.get("wins", 0)
    winrate = round(wins / count * 100) if count else 0
    trop = summary.get("trophies", 0)
    trop_emoji = "📈" if trop >= 0 else "📉"
    stars = summary.get("stars", 0)
    top_b = summary.get("top_brawler") or ""
    top_g = summary.get("top_brawler_games") or 0

    lines = [
        f"🎮 Боёв: {count}",
        f"✅ Побед: {wins}   ❌ Поражений: {summary.get('losses', 0)}   🤝 Ничьих: {summary.get('draws', 0)}",
        f"📊 Винрейт: {winrate}%",
        f"{trop_emoji} Кубки по боям: {_fmt_signed(trop)}",
    ]
    if stars:
        lines.append(f"⭐ Звёздный игрок: {stars} раз")
    if top_b:
        lines.append(f"🧸 Чаще всего: {hd.quote(top_b)} ({top_g} боёв)")
    return "\n".join(lines)


async def _period_text(period: str, member: dict, profile: dict, game_stats: dict) -> str:
    tag = member.get("player_tag")
    title, hours, days = PERIODS[period]

    since_joined = member.get("joined_at") if period in ("clan", "all") else None
    summary = await get_window_summary(
        tag,
        hours=hours,
        days=days,
        since=since_joined,
    )

    tracker_map = {
        "hour": game_stats.get("trophies_hour_diff"),
        "day": game_stats.get("trophies_day_diff"),
        "week": game_stats.get("trophies_week_diff"),
    }
    tracker_val = tracker_map.get(period)
    tracker_line = ""
    if tracker_val is not None:
        tracker_line = f"\n🗂 Трекер клана: {_fmt_signed(tracker_val)} кубков"

    parts = [_header(member, profile), "", title, _summary_block(summary)]

    if period in ("clan", "all"):
        joined = _parse_db_time(member.get("joined_at"))
        if joined:
            joined_text = joined.strftime("%d.%m.%Y в %H:%M")
            parts.append(f"🗓 В клане с: {joined_text}")
        else:
            parts.append("🗓 Дата входа в клан не зафиксирована.")

    if tracker_line:
        parts.append(tracker_line.strip("\n"))

    parts.append("")
    parts.append("ℹ️ Бои сохраняются в историю при каждом открытии профиля.")
    parts.append("👇 Выбери другой период:")
    return "\n".join(parts)


async def _battles_text(member: dict, profile: dict) -> str:
    tag = member.get("player_tag")
    rows = await get_recent_battles(tag, limit=15)
    total = await get_total_battles_count(tag)

    lines = [_header(member, profile), "", f"🎮 История боёв (всего сохранено: {total})"]
    if not rows:
        lines.append("📭 История пуста. Сыграй матч и нажми «Обновить профиль».")
        lines.append("")
        lines.append("👇 Выбери период статистики:")
        return "\n".join(lines)

    for r in rows:
        emoji = _result_emoji(r)
        mode = MODE_RU.get(r.get("mode") or "", r.get("mode") or "Бой")
        mp = r.get("map") or "Случайная карта"
        trop = _fmt_signed(r.get("trophy_change"))
        ago = _human_ago(_parse_db_time(r.get("battle_time")))
        brawler = r.get("brawler") or ""
        star = " ⭐" if r.get("star_player") else ""
        ranked = " 🏅" if "rank" in str(r.get("battle_type") or "").lower() else ""
        brawler_str = f" · {hd.quote(brawler)}" if brawler else ""
        lines.append(
            f"{emoji} {hd.quote(mode)} — {trop} 🏆{brawler_str}{star}{ranked}\n"
            f"    └ {hd.quote(str(mp))} · {ago}"
        )

    lines.append("")
    lines.append("👇 Выбери период статистики:")
    return "\n".join(lines)


def _home_text(member: dict, profile: dict) -> str:
    return (
        f"{_header(member, profile)}\n\n"
        f"📊 Нажми кнопку ниже, чтобы посмотреть прирост кубков\n"
        f"за час, день, неделю, с момента входа в клан или историю боёв."
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


async def _find_clan_member(query: str):
    """Ищет игрока по Telegram ID, @username, игровому тегу, имени или игровому нику."""
    raw = (query or "").strip()
    if not raw:
        return None

    # Telegram может прислать ввод как /@username или /#TAG, если пользователь
    # по привычке поставил slash. Для поиска этот slash не нужен.
    if raw.startswith("/"):
        raw = raw[1:].strip()
    if not raw:
        return None

    members = await get_all_members() or []
    # Не отбрасываем запись только из-за registered/clan: в старых строках БД
    # registered иногда хранится строкой, а clan может временно быть пустым.
    # Приоритет активным участникам задаётся сортировкой ниже.
    candidates = [m for m in members if m and m.get("user_id") and m.get("player_tag")]

    def priority(member: dict) -> tuple:
        registered = str(member.get("registered") or "0").strip().lower() in ("1", "true", "yes")
        has_clan = bool(member.get("clan"))
        return (not registered, not has_clan)

    candidates.sort(key=priority)

    # Telegram ID имеет наивысший приоритет.
    if raw.isdigit():
        wanted_id = int(raw)
        return next((m for m in candidates if int(m.get("user_id") or 0) == wanted_id), None)

    text = " ".join(raw.casefold().split())
    username = raw.lstrip("@").casefold()
    player_tag = _norm_tag(raw)

    # Сначала ищем строгое совпадение, чтобы похожие ники не путались.
    for m in candidates:
        if raw.startswith("@") and (m.get("username") or "").casefold() == username:
            return m
        if raw.startswith("#") and _norm_tag(m.get("player_tag")) == player_tag:
            return m

    for m in candidates:
        if (m.get("username") or "").casefold() == username:
            return m
        if _norm_tag(m.get("player_tag")) == player_tag:
            return m
        game_nick = " ".join(str(m.get("game_nick") or "").casefold().split())
        first_name = " ".join(str(m.get("first_name") or "").casefold().split())
        full_name = " ".join(
            f"{m.get('first_name') or ''} {m.get('last_name') or ''}".casefold().split()
        )
        if game_nick == text or first_name == text or full_name == text:
            return m

    # Если точного совпадения нет, разрешаем однозначный частичный поиск по нику.
    partial = []
    if len(text) >= 3:
        for m in candidates:
            values = (
                str(m.get("username") or "").casefold(),
                str(m.get("game_nick") or "").casefold(),
                str(m.get("first_name") or "").casefold(),
            )
            if any(text in value for value in values if value):
                partial.append(m)
    if len(partial) == 1:
        return partial[0]

    return None


async def _show_other_profile(message: Message, query: str) -> bool:
    """Ищет участника и показывает профиль. Возвращает False, если игрок не найден."""
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
        await wait.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=profile_keyboard(target_id),
        )
    except Exception as e:
        logger.error(f"/ProfileBSOther render error: {e}")
        await message.answer(text, parse_mode="HTML", reply_markup=profile_keyboard(target_id))
    return True


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


@router.message(Command(commands=["ProfileBSOther", "profilebsother", "profile_bs_other"]))
async def cmd_profile_bs_other(message: Message, command: CommandObject, state: FSMContext):
    """Запрашивает игрока, профиль которого нужно открыть."""
    viewer = await get_member(message.from_user.id)
    if not viewer or not viewer.get("registered"):
        await message.answer("❌ Сначала пройди регистрацию через /start.")
        return

    # В группе privacy mode не отдаёт боту последующее обычное сообщение,
    # поэтому поддерживаем поиск прямо в команде.
    if command.args:
        await state.clear()
        if not await _show_other_profile(message, command.args):
            await message.answer(
                "❌ Игрок не найден среди зарегистрированных участников.\n"
                "Проверь ID, @username, игровой тег или точный ник."
            )
        return

    if message.chat.type != "private":
        await message.answer(
            "🔎 Укажи игрока прямо после команды:\n\n"
            "<code>/ProfileBSOther @username</code>\n"
            "<code>/ProfileBSOther #9PJYV82CC</code>\n"
            "<code>/ProfileBSOther 123456789</code>\n\n"
            "В личных сообщениях можно вызвать команду без аргумента.",
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
    raw_text = (message.text or "").strip()
    # В группе Telegram дописывает имя бота: /cancel@VGSStatsBot.
    command = raw_text.split(maxsplit=1)[0].split("@", 1)[0].lower() if raw_text else ""
    if command in ("/cancel", "cancel", "отмена"):
        await state.clear()
        await message.answer("❌ Поиск профиля отменён.")
        return

    # Можно ответить на сообщение нужного участника вместо ручного ввода.
    target = None
    replied_user = getattr(getattr(message, "reply_to_message", None), "from_user", None)
    if replied_user:
        target = await _find_clan_member(str(replied_user.id))

    # Можно переслать сообщение пользователя, если Telegram не скрыл автора.
    if target is None:
        forward_origin = getattr(message, "forward_origin", None)
        forwarded_user = getattr(forward_origin, "sender_user", None)
        if forwarded_user:
            target = await _find_clan_member(str(forwarded_user.id))

    if target is None and raw_text:
        target = await _find_clan_member(raw_text)

    if not raw_text and target is None:
        await message.answer(
            "❌ Отправь ID, @username, игровой тег или ник текстом.\n"
            "Также можно переслать сообщение игрока или ответить на его сообщение."
        )
        return

    if not target:
        await message.answer(
            "❌ Игрок не найден в базе бота.\n"
            "Попробуй Telegram ID, игровой тег или точный игровой ник. "
            "Также можно ответить на сообщение игрока.\n\n"
            "Для отмены отправь /cancel."
        )
        return

    await state.clear()
    await _show_other_profile(message, str(target.get("user_id")))


@router.callback_query(F.data.startswith("pbso:"))
async def cb_profile_bs_other(call: CallbackQuery):
    """Переключает периоды в профиле выбранного участника, а не нажавшего пользователя."""
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
        await call.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=profile_keyboard(target_id),
        )
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            logger.error(f"ProfileBSOther edit error: {e}")


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
        if "message is not modified" in str(e).lower():
            return
        logger.error(f"profileBS edit error: {e}")
        try:
            await call.message.answer(text, parse_mode="HTML", reply_markup=profile_keyboard())
        except Exception:
            pass
