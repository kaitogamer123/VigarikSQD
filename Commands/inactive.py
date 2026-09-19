"""
Активность клана: команда /inactive.
Берёт время ПОСЛЕДНЕГО БОЯ из накопительной базы player_stats.db
(её наполняет utils/stats_collector.py каждые 10 минут + /profileBS).

Работает в клановых чатах и в ЛС (по клану игрока или с выбором клана).
"""
import logging
from datetime import datetime, timezone

import aiosqlite
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.markdown import html_decoration as hd

from config import CLAN_CHATS
from database import get_clan_members, get_member
from player_stats_db import STATS_DB_PATH, norm_tag

logger = logging.getLogger(__name__)
router = Router()


def get_clan_type_by_chat(chat_id: int) -> str | None:
    for clan_key, data in (CLAN_CHATS or {}).items():
        if isinstance(data, dict) and data.get("chat_id") == chat_id:
            return clan_key
    return None


def _clan_title(clan_type: str) -> str:
    info = (CLAN_CHATS or {}).get(clan_type) or {}
    return info.get("title") or clan_type


def _clan_choice_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for key, info in (CLAN_CHATS or {}).items():
        title = (info or {}).get("title") or key
        rows.append([InlineKeyboardButton(text=f"🏰 {title}", callback_data=f"inact:clan:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _last_battle_map(tags: list[str]) -> dict[str, str | None]:
    """Возвращает {нормализованный тег: время последнего боя UTC}."""
    result: dict[str, str | None] = {}
    if not tags:
        return result
    try:
        async with aiosqlite.connect(STATS_DB_PATH, timeout=20.0) as db:
            db.row_factory = aiosqlite.Row
            for tag in tags:
                clean = norm_tag(tag)
                if not clean:
                    continue
                async with db.execute(
                    "SELECT MAX(battle_time) AS last_time FROM battles WHERE player_tag = ?",
                    (clean,),
                ) as cur:
                    row = await cur.fetchone()
                    result[clean] = row["last_time"] if row else None
    except Exception as e:
        logger.error(f"Не удалось прочитать последнюю активность из {STATS_DB_PATH}: {e}")
    return result


def _parse_utc(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _classify(days: int | None) -> str:
    if days is None:
        return "⚪"
    if days >= 3:
        return "🔴"
    if days >= 2:
        return "🟡"
    return "🟢"


def _ago(dt: datetime | None, now: datetime) -> tuple[str, int | None]:
    if dt is None:
        return "нет данных", None
    seconds = int((now - dt).total_seconds())
    if seconds < 0:
        return "только что", 0
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    if days > 0:
        return f"{days} дн. {hours} ч. назад", days
    if hours > 0:
        return f"{hours} ч. {minutes} мин. назад", 0
    return f"{minutes} мин. назад", 0


async def _build_report(clan_type: str) -> str:
    title = hd.quote(str(_clan_title(clan_type)))
    members = await get_clan_members(clan_type)

    if not members:
        return (
            f"📊 Активность клана — {title}:\n\n"
            f"❌ В боте пока нет зарегистрированных участников этого клана."
        )

    tags = [m.get("player_tag") for m in members if m.get("player_tag")]
    last_map = await _last_battle_map(tags)

    now = datetime.now(timezone.utc)
    rows = []
    for m in members:
        tag = norm_tag(m.get("player_tag"))
        last_raw = last_map.get(tag)
        dt = _parse_utc(last_raw)
        ago, days = _ago(dt, now)
        nick = m.get("game_nick") or m.get("username") or m.get("first_name") or "Игрок"
        rows.append({
            "nick": nick,
            "dt": dt,
            "ago": ago,
            "days": days,
        })

    # Неактивные сверху: без данных -> oldest -> newest
    rows.sort(key=lambda r: (r["dt"] is not None, r["dt"] or datetime.max.replace(tzinfo=timezone.utc)))

    red = yellow = green = unknown = 0
    lines = []
    for index, r in enumerate(rows, start=1):
        emoji = _classify(r["days"])
        if r["days"] is None:
            unknown += 1
        elif r["days"] >= 3:
            red += 1
        elif r["days"] >= 2:
            yellow += 1
        else:
            green += 1

        suffix = ""
        if r["dt"] is not None:
            suffix = f" <i>({r['dt'].strftime('%d.%m %H:%M')})</i>"
        lines.append(f"{index}. {emoji} {hd.quote(str(r['nick']))} — {r['ago']}{suffix}")

    header = (
        f"📊 Активность клана — {title}\n"
        f"🔴 {red} · 🟡 {yellow} · 🟢 {green}"
        + (f" · ⚪ {unknown}" if unknown else "")
        + f"  |  Всего: {len(rows)}\n"
        f"🔴 — не играл 3+ дня · 🟡 — 2+ дня · 🟢 — активен"
        + (" · ⚪ — боёв ещё не видел бот" if unknown else "")
    )

    MAX_LEN = 4000
    body = "\n".join(lines)
    if len(header) + len(body) > MAX_LEN:
        body = "\n".join(lines[:60])
        body += f"\n\n<i>Показаны первые 60 из {len(lines)} участников.</i>"

    return f"{header}\n\n{body}"


async def _send_report(target: Message, clan_type: str) -> None:
    try:
        text = await _build_report(clan_type)
        await target.answer(text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"inactive send error ({clan_type}): {e}")
        await target.answer("❌ Не удалось вывести список активности. Попробуй позже.")


@router.message(Command(commands=["inactive", "InActive", "in_active"]))
async def inactive_handler(message: Message):
    clan_type = get_clan_type_by_chat(message.chat.id)

    if not clan_type and message.chat.type == "private":
        member = await get_member(message.from_user.id)
        own_clan = (member or {}).get("clan")
        if own_clan in (CLAN_CHATS or {}):
            clan_type = own_clan
        else:
            await message.answer(
                "📊 Выбери клан, чтобы посмотреть активность:",
                reply_markup=_clan_choice_keyboard(),
            )
            return

    if not clan_type:
        return

    wait = await message.answer("⏳ Считаю активность по сохранённым боям...")
    try:
        text = await _build_report(clan_type)
        await wait.edit_text(text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"inactive render error: {e}")
        try:
            await wait.delete()
        except Exception:
            pass
        await _send_report(message, clan_type)


@router.callback_query(F.data.startswith("inact:clan:"))
async def inactive_clan_choice(call: CallbackQuery):
    clan_type = call.data.split(":", 2)[-1]
    if clan_type not in (CLAN_CHATS or {}):
        await call.answer("Клан не найден", show_alert=True)
        return
    await call.answer("⏳ Загружаю...")
    try:
        text = await _build_report(clan_type)
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=_clan_choice_keyboard())
    except Exception as e:
        logger.error(f"inactive choice edit error: {e}")
