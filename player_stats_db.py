"""Накопительная база личной статистики Brawl Stars: player_stats.db."""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiosqlite

logger = logging.getLogger(__name__)
STATS_DB_PATH = "player_stats.db"


def norm_tag(tag: str) -> str:
    return (tag or "").strip().upper().replace("#", "")


def _battle_time_iso(raw: str) -> Optional[str]:
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
    for player in battle.get("players") or []:
        if norm_tag(player.get("tag")) == tag:
            try:
                return int(player.get("trophyChange") or 0)
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
    await init_player_stats_db()
    tag = norm_tag(player_tag)
    if not tag or not items:
        return 0

    rows = []
    for item in items:
        battle_time = _battle_time_iso(item.get("battleTime"))
        if not battle_time:
            continue
        event = item.get("event") or {}
        battle = item.get("battle") or {}
        rank = battle.get("rank")
        rows.append((
            tag,
            battle_time,
            str(event.get("mode") or battle.get("mode") or "unknown"),
            str(event.get("map") or "Unknown map"),
            str(battle.get("type") or ""),
            str(battle.get("result") or "").lower(),
            int(rank) if isinstance(rank, (int, float)) else None,
            int(battle.get("duration") or 0),
            _trophy_change(item, tag),
        ))

    if not rows:
        return 0

    try:
        async with aiosqlite.connect(STATS_DB_PATH, timeout=20.0) as db:
            cur = await db.execute("SELECT COUNT(*) FROM battles WHERE player_tag = ?", (tag,))
            before = (await cur.fetchone())[0]
            await db.executemany(
                """
                INSERT OR IGNORE INTO battles
                (player_tag, battle_time, mode, map, battle_type, result, rank, duration, trophy_change)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            await db.commit()
            cur = await db.execute("SELECT COUNT(*) FROM battles WHERE player_tag = ?", (tag,))
            after = (await cur.fetchone())[0]
            return max(0, after - before)
    except Exception as e:
        logger.error(f"save_battlelog({tag}) error: {e}")
        return 0


async def ensure_player_tracked(player_tag: str, game_nick: str, trophies: int) -> None:
    await init_player_stats_db()
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
            await db.execute(
                """
                UPDATE players_tracked SET game_nick = ?
                WHERE player_tag = ? AND (game_nick IS NULL OR game_nick = '')
                """,
                (game_nick or "Игрок", tag),
            )
            await db.commit()
    except Exception as e:
        logger.error(f"ensure_player_tracked({tag}) error: {e}")


async def get_tracked_player(player_tag: str) -> Optional[dict]:
    await init_player_stats_db()
    tag = norm_tag(player_tag)
    if not tag:
        return None
    async with aiosqlite.connect(STATS_DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM players_tracked WHERE player_tag = ?", (tag,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_window_summary(
    player_tag: str,
    hours: int = None,
    days: int = None,
    since: str = None,
) -> dict:
    """
    Возвращает статистику за окно времени.

    Приоритет фильтров: since -> hours -> days. Параметр since нужен для
    статистики «с момента входа в клан» и принимает SQLite-дату
    вида YYYY-MM-DD HH:MM:SS.
    """
    await init_player_stats_db()
    tag = norm_tag(player_tag)
    empty = {"count": 0, "wins": 0, "losses": 0, "draws": 0, "other": 0, "trophies": 0}
    if not tag:
        return empty

    where = "player_tag = ?"
    params: list = [tag]
    if since:
        where += " AND battle_time >= ?"
        params.append(str(since).replace("T", " ")[:19])
    elif hours is not None:
        where += " AND battle_time >= datetime('now', ?)"
        params.append(f"-{int(hours)} hours")
    elif days is not None:
        where += " AND battle_time >= datetime('now', ?)"
        params.append(f"-{int(days)} days")

    query = f"""
        SELECT COUNT(*) AS count,
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
            cur = await db.execute(query, params)
            row = await cur.fetchone()
            return {key: (row[key] or 0) for key in empty} if row else empty
    except Exception as e:
        logger.error(f"get_window_summary({tag}) error: {e}")
        return empty


async def get_recent_battles(player_tag: str, limit: int = 15) -> List[dict]:
    await init_player_stats_db()
    tag = norm_tag(player_tag)
    if not tag:
        return []
    try:
        async with aiosqlite.connect(STATS_DB_PATH, timeout=20.0) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT battle_time, mode, map, battle_type, result, rank, trophy_change
                FROM battles WHERE player_tag = ?
                ORDER BY battle_time DESC LIMIT ?
                """,
                (tag, int(limit)),
            )
            return [dict(row) for row in await cur.fetchall()]
    except Exception as e:
        logger.error(f"get_recent_battles({tag}) error: {e}")
        return []


async def get_total_battles_count(player_tag: str) -> int:
    await init_player_stats_db()
    tag = norm_tag(player_tag)
    if not tag:
        return 0
    try:
        async with aiosqlite.connect(STATS_DB_PATH, timeout=20.0) as db:
            cur = await db.execute("SELECT COUNT(*) FROM battles WHERE player_tag = ?", (tag,))
            row = await cur.fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0


async def get_players_window_stats(
    player_tags: List[str],
    hours: int = None,
    days: int = None,
) -> Dict[str, dict]:
    """
    Сводка по НЕСКОЛЬКИМ игрокам (целому клану) за период.
    Возвращает {нормализованный тег: {count, wins, losses, draws, trophies, last_time}}.
    """
    await init_player_stats_db()
    tags = [norm_tag(t) for t in (player_tags or []) if norm_tag(t)]
    result: Dict[str, dict] = {}
    if not tags:
        return result

    where = "player_tag IN ({})".format(",".join("?" for _ in tags))
    params: list = list(tags)
    if hours is not None:
        where += " AND battle_time >= datetime('now', ?)"
        params.append(f"-{int(hours)} hours")
    elif days is not None:
        where += " AND battle_time >= datetime('now', ?)"
        params.append(f"-{int(days)} days")

    query = f"""
        SELECT
            player_tag AS tag,
            COUNT(*) AS count,
            SUM(CASE WHEN result = 'victory' THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN result = 'defeat' THEN 1 ELSE 0 END) AS losses,
            SUM(CASE WHEN result = 'draw' THEN 1 ELSE 0 END) AS draws,
            COALESCE(SUM(trophy_change), 0) AS trophies,
            MAX(battle_time) AS last_time
        FROM battles WHERE {where}
        GROUP BY player_tag
    """
    try:
        async with aiosqlite.connect(STATS_DB_PATH, timeout=20.0) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(query, params)
            for row in await cur.fetchall():
                d = dict(row)
                result[norm_tag(d.get("tag"))] = {
                    "count": int(d.get("count") or 0),
                    "wins": int(d.get("wins") or 0),
                    "losses": int(d.get("losses") or 0),
                    "draws": int(d.get("draws") or 0),
                    "trophies": int(d.get("trophies") or 0),
                    "last_time": d.get("last_time"),
                }
    except Exception as e:
        logger.error(f"get_players_window_stats error: {e}")
    return result