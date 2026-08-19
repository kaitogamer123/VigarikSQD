from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from config import CLAN_CHATS
import aiosqlite

router = Router()
GAME_DB_PATH = "game_clans.db"

def get_clan_type_by_chat(chat_id: int) -> str | None:
    for clan_key, data in CLAN_CHATS.items():
        if data["chat_id"] == chat_id:
            return clan_key
    return None

async def get_pure_game_top(clan_type: str, period: str, limit: int = 15) -> list[dict]:
    async with aiosqlite.connect(GAME_DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row

        if period == "hour":
            diff_column = "trophies_hour_diff"
        elif period == "day":
            diff_column = "trophies_day_diff"
        elif period == "week":
            diff_column = "trophies_week_diff"
        else:
            diff_column = "trophies_month_diff"

        query = f"""
            SELECT game_nick, trophies, {diff_column} AS diff 
            FROM clan_players 
            WHERE clan_type = ? 
            ORDER BY diff DESC 
            LIMIT ?
        """
        async with db.execute(query, (clan_type, limit)) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]

@router.message(Command(commands=["TrophMonth", "TropWeek", "Trophday", "TrophHour"]))
async def pure_trophies_handler(message: Message):
    clan_type = get_clan_type_by_chat(message.chat.id)
    if not clan_type:
        return

    command = message.text.split()[0].lower()
    if "month" in command:
        period = "month"
        period_title = "за месяц"
    elif "week" in command:
        period = "week"
        period_title = "за неделю"
    elif "hour" in command:
        period = "hour"
        period_title = "за час"
    else:
        period = "day"
        period_title = "за день"

    players = await get_pure_game_top(clan_type, period)

    if not players:
        await message.answer(
            f"📊 <b>Топ по кубкам ({period_title}) — {CLAN_CHATS[clan_type]['title']}:</b>\n\n"
            f"❌ В базе пока нет данных.",
            parse_mode="HTML"
        )
        return

    lines = []
    for index, p in enumerate(players, start=1):
        diff = p["diff"] or 0
        sign = "+" if diff > 0 else ""
        lines.append(f"{index}. <b>{p['game_nick']}</b> — 📈 <code>{sign}{diff}</code> (всего: {p['trophies']})")

    joined_list = "\n".join(lines)
    text = (
        f"📊 <b>Топ по кубкам ({period_title}) — {CLAN_CHATS[clan_type]['title']}:</b>\n\n"
        f"<tg-spoiler>{joined_list}</tg-spoiler>"
    )

    await message.answer(text, parse_mode="HTML")