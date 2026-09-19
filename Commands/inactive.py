"""
Активность клана: команда /inactive.
Показывает, кто сколько не играл (по данным трекера game_clans.db).
Работает в клановых чатах и в ЛС (в личке — по клану игрока или с выбором клана).
"""
import logging
from datetime import datetime

import aiosqlite
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.markdown import html_decoration as hd

from config import CLAN_CHATS
from database import get_member

logger = logging.getLogger(__name__)
router = Router()

GAME_DB_PATH = "game_clans.db"
MAX_MSG_LEN = 4000


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


def _parse_dt(raw) -> datetime | None:
    """Парсит last_played_at из SQLite. Возвращает None, если даты нет."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.lower() in ("none", "null", "n/a", "0"):
        return None
    # Убираем миллисекунды и часовой пояс для простоты
    s = s.replace("T", " ").replace("Z", "").strip()
    if "." in s:
        s = s.split(".")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


async def get_inactive_list(clan_type: str) -> list[dict]:
    """
    Самые неактивные — первыми. Игроки без даты (NULL) идут в самом начале.
    """
    try:
        async with aiosqlite.connect(GAME_DB_PATH, timeout=20.0) as db:
            db.row_factory = aiosqlite.Row
            query = """
                SELECT game_nick, last_played_at
                FROM clan_players
                WHERE clan_type = ?
                ORDER BY
                    CASE WHEN last_played_at IS NULL OR TRIM(last_played_at) = '' THEN 0 ELSE 1 END,
                    last_played_at ASC
            """
            async with db.execute(query, (clan_type,)) as cur:
                rows = await cur.fetchall()
                return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"get_inactive_list error ({clan_type}): {e}")
        return []


def _classify(days: int | None) -> str:
    if days is None:
        return "⚪"
    if days >= 3:
        return "🔴"
    if days >= 2:
        return "🟡"
    return "🟢"


def _ago_text(dt: datetime | None, now: datetime) -> tuple[str, int | None, int]:
    """Возвращает (текст 'X назад', days, hours)."""
    if dt is None:
        return "нет данных", None, 0
    diff = now - dt
    total_seconds = int(diff.total_seconds())
    # Дата из будущего (расхождение часов) — считаем как «только что»
    if total_seconds < 0:
        return "только что", 0, 0
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    if days > 0:
        return f"{days} дн. {hours} ч. назад", days, hours
    if hours > 0:
        return f"{hours} ч. {minutes} мин. назад", 0, hours
    return f"{minutes} мин. назад", 0, 0


def _build_chunks(clan_type: str, players: list[dict]) -> list[str]:
    # DB хранит datetime('now') — это UTC без таймзоны, сравниваем с utcnow()
    now = datetime.utcnow()
    title = hd.quote(str(_clan_title(clan_type)))

    parsed = []
    for p in players:
        dt = _parse_dt(p.get("last_played_at"))
        parsed.append({"nick": p.get("game_nick") or "Игрок", "dt": dt})

    red = yellow = green = unknown = 0
    lines: list[str] = []
    for index, p in enumerate(parsed, start=1):
        ago, days, _h = _ago_text(p["dt"], now)
        emoji = _classify(days)
        if days is None:
            unknown += 1
        elif days >= 3:
            red += 1
        elif days >= 2:
            yellow += 1
        else:
            green += 1
        nick = hd.quote(str(p["nick"]))
        date_suffix = ""
        if p["dt"] is not None:
            date_suffix = f" <i>({p['dt'].strftime('%d.%m %H:%M')})</i>"
        lines.append(f"{index}. {emoji} {nick} — {ago}{date_suffix}")

    header = (
        f"📊 Активность клана — {title}\n"
        f"🔴 {red} · 🟡 {yellow} · 🟢 {green}"
        + (f" · ⚪ {unknown}" if unknown else "")
        + f"  |  Всего: {len(parsed)}\n"
        f"🔴 — не играл 3+ дня · 🟡 — 2+ дня · 🟢 — активен"
        + (" · ⚪ — нет данных" if unknown else "")
    )

    # Режем на чанки, чтобы не упереться в лимит Telegram 4096 символов
    chunks: list[str] = []
    current = header + "\n\n"
    for line in lines:
        if len(current) + len(line) + 1 > MAX_MSG_LEN:
            chunks.append(current.rstrip())
            current = f"📊 Активность — {title} <i>(продолжение)</i>\n\n"
        current += line + "\n"
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


async def _send_report(target: Message, clan_type: str) -> None:
    players = await get_inactive_list(clan_type)
    if not players:
        await target.answer(
            f"📊 Активность клана — {hd.quote(str(_clan_title(clan_type)))}:\n\n"
            f"❌ Данные об активности пока отсутствуют.",
            parse_mode="HTML",
        )
        return

    try:
        for chunk in _build_chunks(clan_type, players):
            await target.answer(chunk, parse_mode="HTML")
    except Exception as e:
        logger.error(f"inactive send error ({clan_type}): {e}")
        await target.answer("❌ Не удалось вывести список активности. Попробуй позже.")


@router.message(Command(commands=["inactive", "in_active"], ignore_case=True, ignore_mention=True))
async def inactive_handler(message: Message):
    clan_type = get_clan_type_by_chat(message.chat.id)

    # В личке: берём клан игрока, иначе предлагаем выбрать кнопками
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

    # Чужой групповой чат — молча игнорируем
    if not clan_type:
        return

    await _send_report(message, clan_type)


@router.callback_query(F.data.startswith("inact:clan:"))
async def inactive_clan_choice(call: CallbackQuery):
    clan_type = call.data.split(":", 2)[-1]
    if clan_type not in (CLAN_CHATS or {}):
        await call.answer("Клан не найден", show_alert=True)
        return
    await call.answer("⏳ Загружаю...")
    players = await get_inactive_list(clan_type)
    if not players:
        await call.message.edit_text(
            f"📊 Активность клана — {hd.quote(str(_clan_title(clan_type)))}:\n\n"
            f"❌ Данные об активности пока отсутствуют.",
            parse_mode="HTML",
            reply_markup=_clan_choice_keyboard(),
        )
        return

    chunks = _build_chunks(clan_type, players)
    # Первый чанк — редактируем, остальные — новыми сообщениями
    try:
        await call.message.edit_text(chunks[0], parse_mode="HTML", reply_markup=_clan_choice_keyboard())
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            logger.error(f"inactive edit error: {e}")
    for chunk in chunks[1:]:
        try:
            await call.message.answer(chunk, parse_mode="HTML")
        except Exception as e:
            logger.error(f"inactive extra chunk error: {e}")
            break
