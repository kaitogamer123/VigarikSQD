""" Слой базы данных (SQLite через aiosqlite). Адаптирован под официальное API Brawl Stars. """
import aiosqlite
import json
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import config

DB_PATH = "vigarik.db"
logger = logging.getLogger(__name__)


async def init_db() -> None:
    """Создаёт все таблицы если их нет. Добавлен высокопроизводительный режим WAL."""
    async with aiosqlite.connect(DB_PATH) as db:
        # ИСПРАВЛЕНО: Режим WAL убирает ошибку "database is locked" при частых записях
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")

        await db.executescript(
            """ 
            -- Участники (включая модерацию) 
            CREATE TABLE IF NOT EXISTS members ( 
                user_id INTEGER, 
                username TEXT UNIQUE, 
                first_name TEXT, 
                last_name TEXT, 
                game_nick TEXT, 
                player_tag TEXT, 
                trophies INTEGER DEFAULT 0, 
                ranked_elo INTEGER DEFAULT 0,
                clan TEXT, 
                role TEXT DEFAULT 'member', 
                registered INTEGER DEFAULT 0, 
                joined_at TEXT DEFAULT (datetime('now')), 
                updated_at TEXT DEFAULT (datetime('now')) 
            ); 
            -- Хранилище сообщений-предложений 
            CREATE TABLE IF NOT EXISTS proposals ( 
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                from_id INTEGER NOT NULL, 
                text TEXT, 
                media_json TEXT, 
                sent_at TEXT DEFAULT (datetime('now')), 
                status TEXT DEFAULT 'pending' 
            ); 
            -- Цели пуша на сезон 
            CREATE TABLE IF NOT EXISTS push_goals ( 
                user_id INTEGER PRIMARY KEY, 
                goal TEXT, 
                chosen_at TEXT DEFAULT (datetime('now')), 
                season_id TEXT DEFAULT 'current' 
            ); 
            -- Очередь «нужно спросить цель пуша» 
            CREATE TABLE IF NOT EXISTS push_pending ( 
                user_id INTEGER PRIMARY KEY, 
                season_id TEXT DEFAULT 'current' 
            ); 
            -- Сообщения списков в топиках 
            CREATE TABLE IF NOT EXISTS roster_messages ( 
                clan TEXT PRIMARY KEY, 
                message_id INTEGER 
            ); 
            -- Таблица для динамических настроек системы 
            CREATE TABLE IF NOT EXISTS system_settings ( 
                key TEXT PRIMARY KEY, 
                value TEXT 
            ); 
            """
        )
        await db.commit()


# ─── Members (Слой участников) ────────────────────────────────────────────────

async def get_member(user_id: int) -> Optional[dict]:
    """Ищет игрока по его цифровому ID."""
    if not user_id:
        return None
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM members WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def upsert_member(
        user_id: Optional[int],
        username: str = None,
        first_name: str = None,
        last_name: str = None,
        game_nick: str = None,
        player_tag: str = None,
        trophies: int = None,
        ranked_elo: int = None, # <-- Добавили аргумент
        clan: str = None,
        role: str = None,
        registered: int = None,
) -> None:
    """ Надежно сохраняет или обновляет данные пользователя по его UNIQUE user_id. """
    if not user_id:
        return

    if username:
        username = username.replace("@", "").strip()
    if player_tag:
        player_tag = player_tag.strip().upper()

    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute("PRAGMA journal_mode=WAL;")

        # Атомарный апсерт по УНИКАЛЬНОМУ user_id
        await db.execute(
            """ 
            INSERT INTO members (user_id, username, first_name, last_name, game_nick, player_tag, trophies, ranked_elo, clan, role, registered) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) 
            ON CONFLICT(user_id) DO UPDATE SET 
                username = CASE WHEN ? IS NOT NULL THEN ? ELSE username END, 
                first_name = CASE WHEN ? IS NOT NULL THEN ? ELSE first_name END, 
                last_name = CASE WHEN ? IS NOT NULL THEN ? ELSE last_name END, 
                game_nick = CASE WHEN ? IS NOT NULL THEN ? ELSE game_nick END, 
                player_tag = CASE WHEN ? IS NOT NULL THEN ? ELSE player_tag END, 
                trophies = CASE WHEN ? IS NOT NULL THEN ? ELSE trophies END, 
                ranked_elo = CASE WHEN ? IS NOT NULL THEN ? ELSE ranked_elo END, 
                clan = CASE WHEN ? IS NOT NULL THEN ? ELSE clan END, 
                role = CASE WHEN ? IS NOT NULL THEN ? ELSE role END, 
                registered = CASE WHEN ? IS NOT NULL THEN ? ELSE registered END, 
                updated_at = datetime('now') 
            """,
            (
                user_id, username, first_name, last_name, game_nick, player_tag, trophies or 0, ranked_elo or 0, clan, role or "member",
                registered if registered is not None else 0,
                # Параметры для CASE WHEN:
                username, username,
                first_name, first_name,
                last_name, last_name,
                game_nick, game_nick,
                player_tag, player_tag,
                trophies, trophies,
                ranked_elo, ranked_elo,
                clan, clan,
                role, role,
                registered, registered
            ),
        )
        await db.commit()

async def get_clan_members(clan: str) -> List[dict]:
    """Возвращает упорядоченный список участников клана. Сортировка: Роль -> Кубки."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
                "SELECT * FROM members WHERE clan = ? AND registered = 1 ORDER BY "
                "CASE role "
                " WHEN 'president' THEN 1 "
                " WHEN 'grand_vice' THEN 2 "
                " WHEN 'vice' THEN 3 "
                " WHEN 'veteran' THEN 4 "
                " WHEN 'helper' THEN 5 "
                " ELSE 6 END, trophies DESC, joined_at", (clan,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]
async def get_all_members() -> List[dict]:
    """Выгружает всех людей из базы данных."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM members ORDER BY clan, role") as cur:
            return [dict(r) for r in await cur.fetchall()]

async def get_unregistered_members(clan: str = None) -> List[dict]:
    """Возвращает список участников, у которых нет игрового никнейма."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        if clan and str(clan) != "None" and str(clan).strip() != "":
            async with db.execute(
                "SELECT * FROM members WHERE (game_nick IS NULL OR game_nick = '') AND LOWER(TRIM(clan)) = LOWER(TRIM(?))", (str(clan),)
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]
        else:
            async with db.execute("SELECT * FROM members WHERE game_nick IS NULL OR game_nick = ''") as cur:
                return [dict(r) for r in await cur.fetchall()]

async def remove_member(user_id: int) -> None:
    """Удаляет участника из базы данных."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute("DELETE FROM members WHERE user_id = ?", (user_id,))
        await db.commit()


# ─── Proposals (Предложения игроков) ──────────────────────────────────────────

async def add_proposal(from_id: int, text: str, media: list) -> int:
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        cur = await db.execute(
            "INSERT INTO proposals (from_id, text, media_json) VALUES (?, ?, ?)", (from_id, text, json.dumps(media)),
        )
        await db.commit()
        return cur.lastrowid

async def get_proposals(status: str = "pending") -> List[dict]:
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM proposals WHERE status = ? ORDER BY sent_at DESC", (status,)
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
            for r in rows:
                r["media_json"] = json.loads(r["media_json"] or "[]")
            return rows

async def get_proposal(proposal_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            r = dict(row)
            r["media_json"] = json.loads(r["media_json"] or "[]")
            return r

async def update_proposal_status(proposal_id: int, status: str) -> None:
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute("UPDATE proposals SET status = ? WHERE id = ?", (status, proposal_id))
        await db.commit()


# ─── Push goals (Цели сезона и ДЕДЛАЙНЫ) ──────────────────────────────────────

async def save_push_goal(user_id: int, goal: str, season_id: str = "current") -> bool:
    """ Сохраняет цель пуша с безопасным парсингом даты. """
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
                "SELECT chosen_at FROM push_goals WHERE user_id = ? AND season_id = ?", (user_id, season_id)
        ) as cur:
            row = await cur.fetchone()
            if row:
                date_str = row["chosen_at"].replace("T", " ")
                if "." in date_str:
                    date_str = date_str.split(".")[0]
                try:
                    chosen_at = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    chosen_at = datetime.now()

                deadline_days = getattr(config, "PUSH_CHANGE_DEADLINE_DAYS", 2)
                if datetime.now() - chosen_at > timedelta(days=deadline_days):
                    return False

        await db.execute(
            """ 
            INSERT INTO push_goals (user_id, goal, chosen_at, season_id) VALUES (?, ?, datetime('now'), ?) 
            ON CONFLICT(user_id) DO UPDATE SET goal = excluded.goal, chosen_at = excluded.chosen_at, season_id = excluded.season_id 
            """, (user_id, goal, season_id),
        )
        await db.commit()
        return True


async def get_push_goal(user_id: int) -> Optional[dict]:
    """Возвращает цель пуша конкретного игрока."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM push_goals WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_push_goals() -> list[dict]:
    """ Выгружает все выбранные цели пуша участников с никами. """
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        query = """ 
            SELECT m.user_id, m.username, m.game_nick, m.clan, m.trophies, p.goal 
            FROM push_goals p 
            LEFT JOIN members m ON p.user_id = m.user_id
        """
        async with db.execute(query) as cur:
            return [dict(r) for r in await cur.fetchall()]

        # ─── Push pending (Очередь опроса) ───────────────────────────────────────────


async def add_push_pending(user_id: int, season_id: str = "current") -> None:
    """Ставит незарегистрированного юзера в очередь на опрос целей."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute(
            "INSERT OR IGNORE INTO push_pending (user_id, season_id) VALUES (?, ?)", (user_id, season_id),
        )
        await db.commit()

    # ─── Roster Messages (Управление сообщениями в топиках) ───────────────────────


async def save_roster_message_id(clan: str, message_id: int) -> None:
    """Сохраняет или обновляет ID сообщения со списком клана."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute(
            """ 
            INSERT INTO roster_messages (clan, message_id) VALUES (?, ?) 
            ON CONFLICT(clan) DO UPDATE SET message_id = excluded.message_id 
            """, (clan, message_id),
        )
        await db.commit()


async def get_roster_message_id(clan: str) -> Optional[int]:
    """Возвращает чистый числовой ID сообщения со списком клана для редактирования."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        async with db.execute("SELECT message_id FROM roster_messages WHERE clan = ?", (clan,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def clear_old_push_data() -> None:
    """Полностью очищает таблицы целей пуша и очередей перед новым сезоном."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute("DELETE FROM push_goals")
        await db.execute("DELETE FROM push_pending")
        await db.commit()

    # ─── System Settings (Динамические настройки) ─────────────────────────────────


async def set_setting(key: str, value: str) -> None:
    """Сохраняет значение динамической настройки."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute(
            "INSERT INTO system_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)),
        )
        await db.commit()


async def get_setting(key: str) -> Optional[str]:
    """Возвращает значение настройки по ключу."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        async with db.execute("SELECT value FROM system_settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None
