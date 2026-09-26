"""Твинки и конфликты входа пользователя в чат другого клана."""
import aiosqlite

from database import DB_PATH


async def init_clan_account_tables() -> None:
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS member_twinks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_user_id INTEGER NOT NULL,
                player_tag TEXT NOT NULL UNIQUE,
                game_nick TEXT NOT NULL,
                trophies INTEGER DEFAULT 0,
                clan TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS clan_entry_conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                old_clan TEXT,
                new_clan TEXT NOT NULL,
                username TEXT,
                status TEXT DEFAULT 'pending',
                resolution TEXT,
                resolved_by INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                resolved_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_member_twinks_clan
                ON member_twinks (clan, trophies DESC);
            CREATE INDEX IF NOT EXISTS idx_clan_entry_conflicts_pending
                ON clan_entry_conflicts (user_id, new_clan, status);
        """)
        await db.commit()


async def create_or_get_conflict(
    user_id: int,
    old_clan: str,
    new_clan: str,
    username: str = None,
) -> int:
    await init_clan_account_tables()
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        async with db.execute("""
            SELECT id FROM clan_entry_conflicts
            WHERE user_id = ? AND new_clan = ? AND status = 'pending'
            ORDER BY id DESC LIMIT 1
        """, (user_id, new_clan)) as cursor:
            row = await cursor.fetchone()
        if row:
            return int(row[0])
        cursor = await db.execute("""
            INSERT INTO clan_entry_conflicts (user_id, old_clan, new_clan, username)
            VALUES (?, ?, ?, ?)
        """, (user_id, old_clan, new_clan, username))
        await db.commit()
        return int(cursor.lastrowid)


async def get_conflict(conflict_id: int):
    await init_clan_account_tables()
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM clan_entry_conflicts WHERE id = ?", (conflict_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def resolve_conflict(conflict_id: int, resolution: str, admin_id: int) -> None:
    await init_clan_account_tables()
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute("""
            UPDATE clan_entry_conflicts
            SET status = 'resolved', resolution = ?, resolved_by = ?, resolved_at = datetime('now')
            WHERE id = ?
        """, (resolution, admin_id, conflict_id))
        await db.commit()


async def add_twink(owner_user_id: int, player_tag: str, game_nick: str,
                    trophies: int, clan: str) -> None:
    await init_clan_account_tables()
    clean_tag = str(player_tag).strip().upper()
    if not clean_tag.startswith("#"):
        clean_tag = f"#{clean_tag}"
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                "SELECT 1 FROM members WHERE REPLACE(UPPER(player_tag), '#', '') = ? LIMIT 1",
                (clean_tag[1:],),
            ) as cursor:
                if await cursor.fetchone():
                    raise ValueError("Этот игровой тег уже зарегистрирован как основной аккаунт.")

            cursor = await db.execute("""
                INSERT INTO member_twinks
                    (owner_user_id, player_tag, game_nick, trophies, clan)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(player_tag) DO UPDATE SET
                    game_nick = excluded.game_nick,
                    trophies = excluded.trophies,
                    clan = excluded.clan,
                    updated_at = datetime('now')
                WHERE member_twinks.owner_user_id = excluded.owner_user_id
            """, (owner_user_id, clean_tag, game_nick, int(trophies or 0), clan))
            if cursor.rowcount == 0:
                raise ValueError("Этот твинк уже привязан к другому владельцу.")
            await db.commit()
        except Exception:
            await db.rollback()
            raise


async def get_clan_twinks(clan: str) -> list[dict]:
    await init_clan_account_tables()
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT t.*, m.username, m.first_name, m.last_name
            FROM member_twinks t
            LEFT JOIN members m ON m.user_id = t.owner_user_id
            WHERE t.clan = ? ORDER BY t.trophies DESC
        """, (clan,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_all_twinks() -> list[dict]:
    await init_clan_account_tables()
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM member_twinks") as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def update_twink(player_tag: str, game_nick: str, trophies: int, clan: str = None) -> None:
    await init_clan_account_tables()
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        if clan:
            await db.execute("""
                UPDATE member_twinks SET game_nick = ?, trophies = ?, clan = ?,
                    updated_at = datetime('now') WHERE player_tag = ?
            """, (game_nick, int(trophies or 0), clan, player_tag))
        else:
            await db.execute("""
                UPDATE member_twinks SET game_nick = ?, trophies = ?,
                    updated_at = datetime('now') WHERE player_tag = ?
            """, (game_nick, int(trophies or 0), player_tag))
        await db.commit()


async def move_main_account(user_id: int, new_clan: str) -> None:
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute("""
            UPDATE members SET clan = ?, registered = 1, joined_at = datetime('now'),
                updated_at = datetime('now') WHERE user_id = ?
        """, (new_clan, user_id))
        await db.commit()


async def assign_admin_role(user_id: int, role: str) -> None:
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute(
            "UPDATE members SET role = ?, updated_at = datetime('now') WHERE user_id = ?",
            (role, user_id),
        )
        await db.commit()