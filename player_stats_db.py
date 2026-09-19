"""
Отдельная база данных личной статистики игроков Brawl Stars.
Файл: player_stats.db

Зачем: официальный battlelog API отдаёт только последние ~25 боёв.
Этот модуль накапливает ВСЕ бои, которые бот успел увидеть,
и хранит время боя, режим, карту, результат и изменение кубков.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiosqlite

logger = logging.getLogger(__name__)

STATS_DB_PATH = "player_stats.db"


def norm_tag(tag: str) -> str:
    return (tag or "").strip().upper().replace("#", "")


def _battle_time_iso(raw: str) -> Optional[str]:
    """'20260101T120000.000Z' -> '2026-01-01 12:00:00' (UTC)."""
    if not raw:
        return None
    try:
        dt = datetime.strptime(str(raw)[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return None


def _trophy_change(item: Dict[str, Any], player_tag: str) -> int:
    battle = item.get("battle") or {}
    val = battle.get("trophyChange")
    if val is not None:
        try:
            return int(val)
        except (TypeError, ValueError):
            pass
    tag = norm_tag(player_tag)
    for p in battle.get("players") or []:
        if norm_tag(p.get("tag")) == tag:
            try:
                return int(p.get("trophyChange") or 0)
            except (TypeError, ValueError):
                return 0
    return 0


async def init_player_stats_db() -> None:
    async with aiosqlite.connect(STATS_DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS battles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_tag TEXT NOT NULL,
                battle_time TEXT NOT NULL,
                mode TEXT,
                map TEXT,
                battle_type TEXT,
                result TEXT,
                rank INTEGER,
                duration INTEGER,
                trophy_change INTEGER DEFAULT 0,
                UNIQUE(player_tag, battle_time, mode, map)
            );

            CREATE INDEX IF NOT EXISTS idx_battles_tag_time
                ON battles(player_tag, battle_time);

            CREATE TABLE IF NOT EXISTS players_tracked (
                player_tag TEXT PRIMARY KEY,
                game_nick TEXT,
                first_seen TEXT DEFAULT (datetime('now')),
                start_trophies INTEGER DEFAULT 0
            );
            """
        )
        await db.commit()


async def save_battlelog(player_tag: str, items: List[Dict[str, Any]]) -> int:
    """
    Сохраняет только новые бои. Возвращает количество добавленных записей.
    """
    tag = norm_tag(player_tag)
    if not tag or not items:
        return 0

    rows = []
    for item in items:
        iso_time = _battle_time_iso(item.get("battleTime"))
        if not iso_time:
            continue
        event = item.get("event") or {}
        battle = item.get("battle") or {}
        mode = event.get("mode") or battle.get("mode") or "unknown"
        mp = event.get("map") or "Unknown map"
        rank = battle.get("rank")
        rows.append(
            (
                tag,
                iso_time,
                str(mode),
                str(mp),
                str(battle.get("type") or ""),
                str(battle.get("result") or "").lower(),
                int(rank) if isinstance(rank, (int, float)) else None,
                int(battle.get("duration") or 0),
                _trophy_change(item, tag),
            )
        )

    if not rows:
        return 0

    inserted = 0
    try:
        async with aiosqlite.connect(STATS_DB_PATH, timeout=20.0) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            async with db.execute(
                "SELECT COUNT(*) FROM battles WHERE player_tag = ?",
                (tag,),
            ) as cur:
                before_count = (await cur.fetchone())[0]
            await db.executemany(
                """
                INSERT OR IGNORE INTO battles
                    (player_tag, battle_time, mode, map, battle_type, result, rank, duration, trophy_change)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            await db.commit()
            async with db.execute(
                "SELECT COUNT(*) FROM battles WHERE player_tag = ?",
                (tag,),
            ) as cur:
                after_count = (await cur.fetchone())[0]
            inserted = max(0, after_count - before_count)
    except Exception as e:
        logger.error(f"save_battlelog({tag}) error: {e}")
        return 0
    return inserted


async def ensure_player_tracked(player_tag: str, game_nick: str, trophies: int) -> None:
    """Запоминает первый факт слежки и стартовые кубки (один раз)."""
    tag = norm_tag(player_tag)
    if not tag:
        return
    try:
        async with aiosqlite.connect(STATS_DB_PATH, timeout=20.0) as db:
            await db.execute(
                """
                INSERT OR IGNORE INTO players_tracked (player_tag, game_nick, start_trophies)
                VALUES (?, ?, ?)
                """,
                (tag, game_nick or "Игрок", int(trophies or 0)),
            )
            # Если ник появился позже — обновим его
            await db.execute(
                "UPDATE players_tracked SET game_nick = ? WHERE player_tag = ? AND (game_nick IS NULL OR game_nick = '')",
                (game_nick or "Игрок", tag),
            )
            await db.commit()
    except Exception as e:
        logger.error(f"ensure_player_tracked({tag}) error: {e}")


async def get_tracked_player(player_tag: str) -> Optional[dict]:
    tag = norm_tag(player_tag)
    if not tag:
        return None
    async with aiosqlite.connect(STATS_DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM players_tracked WHERE player_tag = ?",
            (tag,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_window_summary(player_tag: str, hours: int = None, days: int = None) -> dict:
    """
    Сводка по боям за последние N часов / дней.
    Считается по НАКОПЛЕННОЙ базе, а не по последним 25 боям API.
    """
    tag = norm_tag(player_tag)
    empty = {"count": 0, "wins": 0, "losses": 0, "draws": 0, "other": 0, "trophies": 0}
    if not tag:
        return empty

    where = "player_tag = ?"
    params: list = [tag]
    if hours is not None:
        where += " AND battle_time >= datetime('now', ?)"
        params.append(f"-{int(hours)} hours")
    elif days is not None:
        where += " AND battle_time >= datetime('now', ?)"
        params.append(f"-{int(days)} days")

    query = f"""
        SELECT
            COUNT(*) AS count,
            SUM(CASE WHEN result = 'victory' THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN result = 'defeat' THEN 1 ELSE 0 END) AS losses,
            SUM(CASE WHEN result = 'draw' THEN 1 ELSE 0 END) AS draws,
            SUM(CASE WHEN result NOT IN ('victory','defeat','draw') THEN 1 ELSE 0 END) AS other,
            COALESCE(SUM(trophy_change), 0) AS trophies
        FROM battles WHERE {where}
    """
    try:
        async with aiosqlite.connect(STATS_DB_PATH, timeout=20.0) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cur:
                row = await cur.fetchone()
                if not row:
                    return empty
                return {k: (row[k] or 0) for k in empty.keys()}
    except Exception as e:
        logger.error(f"get_window_summary({tag}) error: {e}")
        return empty


async def get_recent_battles(player_tag: str, limit: int = 15) -> List[dict]:
    """Последние бои из НАКОПЛЕННОЙ базы (может быть сильно больше 25)."""
    tag = norm_tag(player_tag)
    if not tag:
        return []
    try:
        async with aiosqlite.connect(STATS_DB_PATH, timeout=20.0) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT battle_time, mode, map, battle_type, result, rank, trophy_change
                FROM battles
                WHERE player_tag = ?
                ORDER BY battle_time DESC
                LIMIT ?
                """,
                (tag, int(limit)),
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]
    except Exception as e:
        logger.error(f"get_recent_battles({tag}) error: {e}")
        return []


async def get_total_battles_count(player_tag: str) -> int:
    tag = norm_tag(player_tag)
    if not tag:
        return 0
    try:
        async with aiosqlite.connect(STATS_DB_PATH, timeout=20.0) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM battles WHERE player_tag = ?",
                (tag,),
            ) as cur:
                row = await cur.fetchone()
                return int(row[0]) if row else 0
    except Exception:
        return 0
