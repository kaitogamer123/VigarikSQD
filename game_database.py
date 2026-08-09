import aiosqlite
import logging

GAME_DB_PATH = "game_clans.db"
logger = logging.getLogger(__name__)

async def init_game_db() -> None:
    async with aiosqlite.connect(GAME_DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")

        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS clan_players (
                player_tag TEXT PRIMARY KEY,
                game_nick TEXT NOT NULL,
                clan_type TEXT NOT NULL,
                trophies INTEGER DEFAULT 0,
                trophies_hour_start INTEGER DEFAULT 0,     -- Для часового прироста
                trophies_hour_diff INTEGER DEFAULT 0,      -- Апнуто за час
                trophies_day_start INTEGER DEFAULT 0,
                trophies_current_check INTEGER DEFAULT 0,
                trophies_day_diff INTEGER DEFAULT 0,
                trophies_yesterday_diff INTEGER DEFAULT 0,
                trophies_week_start INTEGER DEFAULT 0,
                trophies_week_end INTEGER DEFAULT 0,
                trophies_week_diff INTEGER DEFAULT 0,
                trophies_month_start INTEGER DEFAULT 0,
                trophies_month_diff INTEGER DEFAULT 0,
                last_played_at TEXT DEFAULT (datetime('now')) -- Время последней игры
            );
            """
        )
        await db.commit()
