from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from config import CLAN_CHATS
import aiosqlite
from datetime import datetime

router = Router()
GAME_DB_PATH = "game_clans.db"


def get_clan_type_by_chat(chat_id: int) -> str | None:
    for clan_key, data in CLAN_CHATS.items():
        if data["chat_id"] == chat_id:
            return clan_key
    return None


async def get_inactive_list(clan_type: str) -> list[dict]:
    async with aiosqlite.connect(GAME_DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        query = """
            SELECT game_nick, last_played_at 
            FROM clan_players 
            WHERE clan_type = ? 
            ORDER BY last_played_at DESC
        """
        async with db.execute(query, (clan_type,)) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


@router.message(Command(commands=["InActive", "inactive"]))
async def inactive_handler(message: Message):
    clan_type = get_clan_type_by_chat(message.chat.id)
    if not clan_type:
        return

    players = await get_inactive_list(clan_type)

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
        last_str = p["last_played_at"]

        # Считаем разницу во времени с последней игрой
        try:
            last_time = datetime.strptime(last_str, "%Y-%m-%d %H:%M:%S")
            diff = now - last_time
            days = diff.days
            hours = diff.seconds // 3600
        except Exception:
            days = 0
            hours = 0

        # Распределяем круги по условиям:
        # 3+ дня — красный, до 2 дней (но больше 1) — жёлтый, до 2 дней (менее 24-48ч) — зелёный
        if days >= 3:
            emoji = "🔴"
        elif days >= 2:
            emoji = "🟡"
        else:
            emoji = "🟢"

        time_text = f"{days} дн. {hours} ч. назад" if days > 0 else f"{hours} ч. назад"
        lines.append(f"{emoji} <b>{nick}</b> — Последняя игра: {time_text}")

    # Упаковываем весь список внутрь тега спойлера <tg-spoiler>
    joined_list = "\n".join(lines)
    text = (
        f"📊 <b>Список активности клана ({CLAN_CHATS[clan_type]['title']}):</b>\n\n"
        f"<tg-spoiler>{joined_list}</tg-spoiler>"
    )

    await message.answer(text, parse_mode="HTML")