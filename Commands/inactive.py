from aiogram import Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram import F
from config import CLAN_CHATS
import aiosqlite
import re
from datetime import datetime
from database import DB_PATH
import logging

router = Router()
logger = logging.getLogger(__name__)


def get_clan_type_by_chat(chat_id: int) -> str | None:
    for clan_key, data in CLAN_CHATS.items():
        if data["chat_id"] == chat_id:
            return clan_key
    return None


async def get_inactive_list(clan_type: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        query = """
            SELECT game_nick, updated_at
            FROM members
            WHERE clan = ? AND registered = 1
            ORDER BY datetime(updated_at) ASC
        """
        async with db.execute(query, (clan_type,)) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


def inactive_keyboard(expanded: bool) -> InlineKeyboardMarkup:
    if expanded:
        button = InlineKeyboardButton(text="🔼 Свернуть список", callback_data="inactive:collapse")
    else:
        button = InlineKeyboardButton(text="📋 Показать список", callback_data="inactive:expand")
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


def format_inactive_header(clan_type: str, player_count: int) -> str:
    return (
        f"📊 <b>Активность клана ({CLAN_CHATS[clan_type]['title']})</b>\n"
        f"<i>По времени последнего обновления данных бота</i>\n"
        f"Игроков: <b>{player_count}</b>"
    )


def format_inactive_lines(players: list[dict]) -> str:
    now = datetime.now()
    lines = []

    for player in players:
        nick = player["game_nick"] or "Игрок"
        last_str = player["updated_at"]

        try:
            last_time = datetime.strptime(last_str, "%Y-%m-%d %H:%M:%S")
            diff = now - last_time
            days = diff.days
            hours = diff.seconds // 3600
        except (TypeError, ValueError):
            days = 0
            hours = 0

        if days >= 3:
            emoji = "🔴"
        elif days >= 2:
            emoji = "🟡"
        else:
            emoji = "🟢"

        time_text = f"{days} дн. {hours} ч. назад" if days > 0 else f"{hours} ч. назад"
        lines.append(f"{emoji} <b>{nick}</b> — Последняя проверка: {time_text}")

    return "\n".join(lines)


@router.message(F.text.regexp(r"^/inactive(?:@\w+)?(?:\s|$)", flags=re.IGNORECASE))
async def inactive_handler(message: Message):
    clan_type = get_clan_type_by_chat(message.chat.id)
    if not clan_type:
        logger.warning(
            "Команда /inactive получена вне кланового чата: chat_id=%s thread_id=%s",
            message.chat.id,
            message.message_thread_id,
        )
        return

    try:
        players = await get_inactive_list(clan_type)
    except Exception:
        logger.exception("Ошибка при загрузке списка активности: clan=%s", clan_type)
        await message.answer("❌ Не удалось загрузить список активности. Ошибка записана в лог.")
        return

    if not players:
        await message.answer(
            f"📊 <b>Список активности клана — {CLAN_CHATS[clan_type]['title']}:</b>\n\n"
            f"❌ Данные об активности пока отсутствуют.",
            parse_mode="HTML"
        )
        return

    text = format_inactive_header(clan_type, len(players))
    await message.answer(text, parse_mode="HTML", reply_markup=inactive_keyboard(expanded=False))


@router.callback_query(F.data.in_({"inactive:expand", "inactive:collapse"}))
async def inactive_toggle_handler(callback: CallbackQuery):
    if not callback.message:
        await callback.answer()
        return

    clan_type = get_clan_type_by_chat(callback.message.chat.id)
    if not clan_type:
        await callback.answer("Команда доступна только в клановых чатах.", show_alert=True)
        return

    try:
        players = await get_inactive_list(clan_type)
    except Exception:
        logger.exception("Ошибка при обновлении списка активности: clan=%s", clan_type)
        await callback.answer("Не удалось загрузить список.", show_alert=True)
        return

    if not players:
        text = (
            f"📊 <b>Активность клана ({CLAN_CHATS[clan_type]['title']})</b>\n\n"
            "❌ Данные об активности пока отсутствуют."
        )
        await callback.message.edit_text(text, parse_mode="HTML")
        await callback.answer()
        return

    expanded = callback.data == "inactive:expand"
    text = format_inactive_header(clan_type, len(players))
    if expanded:
        text += f"\n\n{format_inactive_lines(players)}"

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=inactive_keyboard(expanded=expanded),
    )
    await callback.answer()