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


def is_ranked_battle(battle_type: str) -> bool:
    """Ранкед-режим — это только soloRanked/teamRanked.

    Тип "ranked" в battlelog API — обычный бой на кубки, а не Ranked.
    """
    return str(battle_type or "").strip().lower() in ("soloranked", "teamranked")


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


_INITIALIZED = False


async def init_player_stats_db(force: bool = False) -> None:
    global _INITIALIZED
    if _INITIALIZED and not force:
        return
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
                ranked_elo_change INTEGER,
                UNIQUE(player_tag, battle_time, mode, map)
            );
            CREATE INDEX IF NOT EXISTS idx_battles_tag_time
                ON battles(player_tag, battle_time);
            CREATE TABLE IF NOT EXISTS players_tracked (
                player_tag TEXT PRIMARY KEY,
                game_nick TEXT,
                first_seen TEXT DEFAULT (datetime('now')),
                start_trophies INTEGER DEFAULT 0,
                last_ranked_elo INTEGER DEFAULT 0,
                last_trophies INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS stats_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        for table, column, ddl in (
            ("battles", "ranked_elo_change", "INTEGER"),
            ("players_tracked", "last_ranked_elo", "INTEGER DEFAULT 0"),
            ("players_tracked", "last_trophies", "INTEGER DEFAULT 0"),
        ):
            async with db.execute(f"PRAGMA table_info({table})") as cur:
                cols = {row[1] for row in await cur.fetchall()}
            if column not in cols:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        # Исправление: тип "ranked" — это бой на кубки, а не ранкед-режим.
        # Его изменение кубков ранее ошибочно уехало в ranked_elo_change.
        # Выполняем один раз, чтобы не трогать новые корректные записи.
        async with db.execute(
            "SELECT 1 FROM stats_meta WHERE key = 'ranked_trophy_fix' LIMIT 1"
        ) as cur:
            fix_done = await cur.fetchone()
        if not fix_done:
            await db.execute(
                "UPDATE battles SET trophy_change = ranked_elo_change, "
                "ranked_elo_change = NULL "
                "WHERE LOWER(COALESCE(battle_type, '')) = 'ranked' "
                "AND ranked_elo_change IS NOT NULL"
            )
            await db.execute(
                "INSERT OR REPLACE INTO stats_meta (key, value) "
                "VALUES ('ranked_trophy_fix', datetime('now'))"
            )
        await db.commit()
    _INITIALIZED = True


async def save_battlelog(
    player_tag: str,
    items: List[Dict[str, Any]],
    current_ranked_elo: int = None,
    current_trophies: int = None,
) -> int:
    await init_player_stats_db()
    tag = norm_tag(player_tag)
    if not tag or not items:
        return 0

    rows = []
    new_times = []
    ranked_candidate_times = []
    trophy_candidate_times = []
    explicit_trophy_by_time = {}
    explicit_elo_by_time = {}
    for item in items:
        battle_time = _battle_time_iso(item.get("battleTime"))
        if not battle_time:
            continue
        event = item.get("event") or {}
        battle = item.get("battle") or {}
        battle_type = str(battle.get("type") or "")
        ranked_mode = is_ranked_battle(battle_type)
        rank = battle.get("rank")
        value = battle.get("trophyChange")
        api_change = None
        if value is not None:
            try:
                api_change = int(value)
            except (TypeError, ValueError):
                api_change = None
        # В ранкед-режиме trophyChange — это изменение ранкед-очков, а не кубков.
        ranked_elo_change = api_change if ranked_mode else None
        if ranked_mode:
            if ranked_elo_change is None:
                ranked_candidate_times.append(battle_time)
            else:
                explicit_elo_by_time[battle_time] = ranked_elo_change
            trophy_change = 0
        else:
            if api_change is not None:
                trophy_change = api_change
                explicit_trophy_by_time[battle_time] = api_change
            else:
                fallback = _trophy_change(item, tag)
                if fallback:
                    trophy_change = fallback
                else:
                    # None = «API не сказал», дельту вычислим по профилю.
                    trophy_change = None
                    trophy_candidate_times.append(battle_time)
        new_times.append(battle_time)
        rows.append((
            tag,
            battle_time,
            str(event.get("mode") or battle.get("mode") or "unknown"),
            str(event.get("map") or "Unknown map"),
            battle_type,
            str(battle.get("result") or "").lower(),
            int(rank) if isinstance(rank, (int, float)) else None,
            int(battle.get("duration") or 0),
            trophy_change,
            ranked_elo_change,
        ))

    if not rows:
        return 0

    try:
        async with aiosqlite.connect(STATS_DB_PATH, timeout=20.0) as db:
            cur = await db.execute("SELECT COUNT(*) FROM battles WHERE player_tag = ?", (tag,))
            before = (await cur.fetchone())[0]
            cur = await db.execute(
                "SELECT last_ranked_elo, last_trophies FROM players_tracked WHERE player_tag = ?",
                (tag,),
            )
            prev_row = await cur.fetchone()
            prev_elo = int(prev_row[0] or 0) if prev_row else 0
            prev_trophies = int(prev_row[1] or 0) if prev_row else 0

            # Дельту профиля можно вешать только на реально новые бои.
            row_placeholders = ",".join("?" for _ in rows)
            cur = await db.execute(
                f"SELECT battle_time FROM battles WHERE player_tag = ? "
                f"AND battle_time IN ({row_placeholders})",
                (tag, *(r[1] for r in rows)),
            )
            existing_times = {r[0] for r in await cur.fetchall()}
            new_times = [t for t in dict.fromkeys(new_times) if t not in existing_times]
            ranked_candidate_times = [
                t for t in dict.fromkeys(ranked_candidate_times) if t not in existing_times
            ]
            trophy_candidate_times = [
                t for t in dict.fromkeys(trophy_candidate_times) if t not in existing_times
            ]
            explicit_trophy_sum = sum(explicit_trophy_by_time.get(t, 0) for t in new_times)
            explicit_elo_sum = sum(explicit_elo_by_time.get(t, 0) for t in new_times)

            await db.executemany(
                """
                INSERT INTO battles
                (player_tag, battle_time, mode, map, battle_type, result, rank,
                 duration, trophy_change, ranked_elo_change)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(player_tag, battle_time, mode, map) DO UPDATE SET
                    battle_type = excluded.battle_type,
                    result = excluded.result,
                    rank = excluded.rank,
                    duration = excluded.duration,
                    trophy_change = COALESCE(excluded.trophy_change,
                                             battles.trophy_change),
                    ranked_elo_change = COALESCE(excluded.ranked_elo_change,
                                                 battles.ranked_elo_change)
                WHERE (excluded.trophy_change IS NOT NULL
                       AND COALESCE(battles.trophy_change, -999999)
                           != excluded.trophy_change)
                   OR (excluded.ranked_elo_change IS NOT NULL
                       AND COALESCE(battles.ranked_elo_change, -999999)
                           != excluded.ranked_elo_change)
                """,
                rows,
            )
            await db.commit()

            def as_int(value):
                if value is None:
                    return None
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None

            current_elo = as_int(current_ranked_elo)
            current_tr = as_int(current_trophies)
            trophy_target = None

            # Кубки: API не всегда отдаёт trophyChange, поэтому берём реальную
            # разницу кубков профиля и вешаем её на самый свежий новый бой.
            if current_tr is not None and prev_trophies > 0 and trophy_candidate_times:
                # Вычитаем изменения, которые API уже отдал сам, чтобы не задвоить.
                delta = current_tr - prev_trophies - explicit_trophy_sum
                if delta != 0 and abs(delta) <= 300:
                    placeholders = ",".join("?" for _ in trophy_candidate_times)
                    cur = await db.execute(
                        f"""
                        SELECT battle_time FROM battles
                        WHERE player_tag = ? AND battle_time IN ({placeholders})
                          AND COALESCE(trophy_change, 0) = 0
                          AND COALESCE(ranked_elo_change, 0) = 0
                        ORDER BY battle_time DESC LIMIT 1
                        """,
                        (tag, *trophy_candidate_times),
                    )
                    trophy_target = await cur.fetchone()
                    if trophy_target:
                        await db.execute(
                            "UPDATE battles SET trophy_change = ? "
                            "WHERE player_tag = ? AND battle_time = ?",
                            (delta, tag, trophy_target[0]),
                        )

            # ELO: API не отдаёт изменение ELO за бой, поэтому считаем дельту
            # от последнего известного ELO и вешаем на новый ранкед-бой.
            if current_elo is not None and prev_elo > 0:
                delta = current_elo - prev_elo - explicit_elo_sum
                if delta != 0 and abs(delta) <= 400:
                    target = None
                    if ranked_candidate_times:
                        placeholders = ",".join("?" for _ in ranked_candidate_times)
                        cur = await db.execute(
                            f"""
                            SELECT battle_time FROM battles
                            WHERE player_tag = ? AND battle_time IN ({placeholders})
                              AND ranked_elo_change IS NULL
                              AND COALESCE(trophy_change, 0) = 0
                            ORDER BY battle_time DESC LIMIT 1
                            """,
                            (tag, *ranked_candidate_times),
                        )
                        target = await cur.fetchone()
                    if not target:
                        # Тип "ranked" в API бывает и ранкедом, и кубками.
                        # Поэтому фоллбек — любой новый бой без изменений.
                        placeholders = ",".join("?" for _ in new_times)
                        exclude_sql = ""
                        params = [tag, *new_times]
                        if trophy_target:
                            exclude_sql = " AND battle_time != ?"
                            params.append(trophy_target[0])
                        cur = await db.execute(
                            f"""
                            SELECT battle_time FROM battles
                            WHERE player_tag = ? AND battle_time IN ({placeholders})
                              AND ranked_elo_change IS NULL
                              AND COALESCE(trophy_change, 0) = 0
                              {exclude_sql}
                            ORDER BY battle_time DESC LIMIT 1
                            """,
                            tuple(params),
                        )
                        target = await cur.fetchone()
                    if target:
                        await db.execute(
                            "UPDATE battles SET ranked_elo_change = ? "
                            "WHERE player_tag = ? AND battle_time = ?",
                            (delta, tag, target[0]),
                        )

            # Базу отсчёта двигаем только когда реально сохранили новые бои.
            # Иначе профиль может обновиться раньше battlelog, и дельта потеряется.
            if new_times and (current_elo is not None or current_tr is not None):
                await db.execute(
                    """
                    INSERT INTO players_tracked (player_tag, last_ranked_elo, last_trophies)
                    VALUES (?, ?, ?)
                    ON CONFLICT(player_tag) DO UPDATE SET
                        last_ranked_elo = COALESCE(excluded.last_ranked_elo,
                                                   players_tracked.last_ranked_elo),
                        last_trophies = COALESCE(excluded.last_trophies,
                                                 players_tracked.last_trophies)
                    """,
                    (tag, current_elo, current_tr),
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
    empty = {"count": 0, "wins": 0, "losses": 0, "draws": 0, "other": 0,
             "trophies": 0, "elo": 0, "ranked": 0}
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
               COALESCE(SUM(trophy_change), 0) AS trophies,
               COALESCE(SUM(ranked_elo_change), 0) AS elo,
               SUM(CASE WHEN ranked_elo_change IS NOT NULL
                          OR LOWER(COALESCE(battle_type, '')) IN ('soloranked', 'teamranked')
                        THEN 1 ELSE 0 END) AS ranked
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
                SELECT battle_time, mode, map, battle_type, result, rank,
                       trophy_change, ranked_elo_change
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
            COALESCE(SUM(ranked_elo_change), 0) AS elo,
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
                    "elo": int(d.get("elo") or 0),
                    "last_time": d.get("last_time"),
                }
    except Exception as e:
        logger.error(f"get_players_window_stats error: {e}")
    return result