from aiogram import Router
from aiogram.types import Message
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

    now = datetime.now()
    lines = []

    for p in players:
        nick = p["game_nick"] or "Игрок"
        last_str = p["updated_at"]

        # Считаем разницу во времени с последней игрой
        try:
            last_time = datetime.strptime(last_str, "%Y-%m-%d %H:%M:%S")
            diff = now - last_time
            days = diff.days
            hours = diff.seconds // 3600
        except Exception:
            days = 0
            hours = 0

        # 3+ дня без обновления данных — красный, 1-3 дня — жёлтый.
        if days >= 3:
            emoji = "🔴"
        elif days >= 2:
            emoji = "🟡"
        else:
            emoji = "🟢"

        time_text = f"{days} дн. {hours} ч. назад" if days > 0 else f"{hours} ч. назад"
        lines.append(f"{emoji} <b>{nick}</b> — Последняя проверка: {time_text}")

    # Упаковываем весь список внутрь тега спойлера <tg-spoiler>
    joined_list = "\n".join(lines)
    text = (
        f"📊 <b>Активность клана ({CLAN_CHATS[clan_type]['title']}):</b>\n"
        f"<i>По времени последнего обновления данных бота</i>\n\n"
        f"<tg-spoiler>{joined_list}</tg-spoiler>"
    )

    await message.answer(text, parse_mode="HTML")