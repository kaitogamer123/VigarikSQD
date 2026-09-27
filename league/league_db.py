"""SQLite-слой системы составов (историческое имя модуля league сохранено)."""
import os
import sqlite3
from typing import Optional

DB_PATH = os.path.join("league", "league.db")
DEPUTY_PERMISSIONS = ("can_review_apps", "can_invite", "can_kick", "can_toggle_open")


def get_connection():
    os.makedirs("league", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _columns(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def init_league_db():
    """Создаёт схему и безопасно мигрирует старую league.db без потери данных."""
    conn = get_connection()
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS leagues (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, tag TEXT NOT NULL,
            leader_id INTEGER NOT NULL, is_open INTEGER DEFAULT 1,
            record_league INTEGER DEFAULT 0, created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS league_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT, league_id INTEGER, slot_index INTEGER,
            user_id INTEGER, game_nick TEXT, player_tag TEXT, username TEXT,
            role TEXT DEFAULT 'участник', trophies_record INTEGER DEFAULT 0,
            joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (league_id) REFERENCES leagues(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS league_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT, league_id INTEGER, user_id INTEGER,
            text_reason TEXT, sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (league_id) REFERENCES leagues(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS league_invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT, league_id INTEGER, inviter_id INTEGER,
            invitee_id INTEGER, FOREIGN KEY (league_id) REFERENCES leagues(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS league_deputies (
            id INTEGER PRIMARY KEY AUTOINCREMENT, league_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            appointed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            can_review_apps INTEGER DEFAULT 1, can_invite INTEGER DEFAULT 1,
            can_kick INTEGER DEFAULT 1, can_toggle_open INTEGER DEFAULT 1,
            UNIQUE (league_id, user_id),
            FOREIGN KEY (league_id) REFERENCES leagues(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS composition_departures (
            user_id INTEGER PRIMARY KEY,
            league_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            old_clan TEXT,
            player_tag TEXT,
            left_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            check_after DATETIME NOT NULL,
            FOREIGN KEY (league_id) REFERENCES leagues(id) ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_invite ON league_invites (league_id, invitee_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_league_member
            ON league_members (league_id, user_id) WHERE user_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_deputies_order ON league_deputies (league_id, appointed_at, id);
        CREATE INDEX IF NOT EXISTS idx_composition_departures_due
            ON composition_departures (check_after);
    """)
    if "joined_at" not in _columns(conn, "league_members"):
        cur.execute("ALTER TABLE league_members ADD COLUMN joined_at DATETIME")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_league_members_joined "
        "ON league_members (league_id, joined_at, id)"
    )
    cur.execute("""
        UPDATE league_members SET joined_at = COALESCE(
            joined_at, (SELECT created_at FROM leagues WHERE leagues.id = league_members.league_id),
            CURRENT_TIMESTAMP) WHERE joined_at IS NULL AND user_id IS NOT NULL
    """)
    cur.execute("UPDATE league_members SET role = 'лидер' WHERE slot_index = 1 AND user_id IS NOT NULL")
    cur.execute("""
        UPDATE league_members SET role = 'участник'
        WHERE slot_index != 1 AND user_id IS NOT NULL AND role NOT IN ('участник', 'заместитель')
    """)
    conn.commit()
    conn.close()


def get_user_league(user_id: int):
    conn = get_connection()
    row = conn.execute("""
        SELECT l.*, m.slot_index, m.role AS member_role, m.joined_at,
               CASE WHEN d.id IS NULL THEN 0 ELSE 1 END AS is_deputy,
               COALESCE(d.can_review_apps, 0) AS can_review_apps,
               COALESCE(d.can_invite, 0) AS can_invite,
               COALESCE(d.can_kick, 0) AS can_kick,
               COALESCE(d.can_toggle_open, 0) AS can_toggle_open
        FROM league_members m JOIN leagues l ON l.id = m.league_id
        LEFT JOIN league_deputies d ON d.league_id = m.league_id AND d.user_id = m.user_id
        WHERE m.user_id = ? LIMIT 1
    """, (user_id,)).fetchone()
    conn.close()
    return row


def get_league_members(league_id: int, occupied_only: bool = False):
    conn = get_connection()
    occupied = "AND m.user_id IS NOT NULL" if occupied_only else ""
    rows = conn.execute(f"""
        SELECT m.*, d.appointed_at,
               COALESCE(d.can_review_apps, 0) AS can_review_apps,
               COALESCE(d.can_invite, 0) AS can_invite,
               COALESCE(d.can_kick, 0) AS can_kick,
               COALESCE(d.can_toggle_open, 0) AS can_toggle_open
        FROM league_members m
        LEFT JOIN league_deputies d ON d.league_id = m.league_id AND d.user_id = m.user_id
        WHERE m.league_id = ? {occupied} ORDER BY m.slot_index
    """, (league_id,)).fetchall()
    conn.close()
    return rows


def has_management_permission(user_id: int, permission: str) -> bool:
    composition = get_user_league(user_id)
    if not composition:
        return False
    if int(composition["leader_id"]) == int(user_id):
        return True
    return permission in DEPUTY_PERMISSIONS and bool(composition["is_deputy"] and composition[permission])


def set_deputy(league_id: int, user_id: int) -> bool:
    conn = get_connection()
    member = conn.execute(
        "SELECT id FROM league_members WHERE league_id = ? AND user_id = ? AND slot_index != 1",
        (league_id, user_id),
    ).fetchone()
    if not member:
        conn.close()
        return False
    conn.execute("""
        INSERT INTO league_deputies
            (league_id, user_id, can_review_apps, can_invite, can_kick, can_toggle_open)
        VALUES (?, ?, 1, 1, 1, 1) ON CONFLICT(league_id, user_id) DO NOTHING
    """, (league_id, user_id))
    conn.execute("UPDATE league_members SET role = 'заместитель' WHERE league_id = ? AND user_id = ?",
                 (league_id, user_id))
    conn.commit(); conn.close()
    return True


def remove_deputy(league_id: int, user_id: int) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM league_deputies WHERE league_id = ? AND user_id = ?", (league_id, user_id))
    conn.execute("""
        UPDATE league_members SET role = 'участник'
        WHERE league_id = ? AND user_id = ? AND slot_index != 1
    """, (league_id, user_id))
    conn.commit(); conn.close()


def toggle_deputy_permission(league_id: int, user_id: int, permission: str) -> Optional[int]:
    if permission not in DEPUTY_PERMISSIONS:
        return None
    conn = get_connection()
    row = conn.execute(f"SELECT {permission} FROM league_deputies WHERE league_id = ? AND user_id = ?",
                       (league_id, user_id)).fetchone()
    if not row:
        conn.close(); return None
    value = 0 if row[permission] else 1
    conn.execute(f"UPDATE league_deputies SET {permission} = ? WHERE league_id = ? AND user_id = ?",
                 (value, league_id, user_id))
    conn.commit(); conn.close()
    return value


def dissolve_league(league_id: int) -> None:
    conn = get_connection()
    for table in ("league_members", "league_deputies", "league_applications", "league_invites",
                  "composition_departures"):
        conn.execute(f"DELETE FROM {table} WHERE league_id = ?", (league_id,))
    conn.execute("DELETE FROM leagues WHERE id = ?", (league_id,))
    conn.commit(); conn.close()


def _clear_member_row(conn, league_id: int, user_id: int) -> None:
    conn.execute("DELETE FROM league_deputies WHERE league_id = ? AND user_id = ?", (league_id, user_id))
    conn.execute("""
        UPDATE league_members SET user_id = NULL, game_nick = '-- Свободное место --',
            player_tag = '', username = '', role = 'участник', trophies_record = 0, joined_at = NULL
        WHERE league_id = ? AND user_id = ?
    """, (league_id, user_id))


def transfer_leadership(league_id: int, old_leader_id: int, new_leader_id: int,
                        remove_old: bool = False) -> bool:
    conn = get_connection()
    old = conn.execute("SELECT * FROM league_members WHERE league_id = ? AND user_id = ?",
                       (league_id, old_leader_id)).fetchone()
    new = conn.execute("SELECT * FROM league_members WHERE league_id = ? AND user_id = ?",
                       (league_id, new_leader_id)).fetchone()
    if not old or not new or old_leader_id == new_leader_id:
        conn.close(); return False
    new_slot = int(new["slot_index"])
    conn.execute("DELETE FROM league_deputies WHERE league_id = ? AND user_id = ?", (league_id, new_leader_id))
    conn.execute("UPDATE league_members SET slot_index = -1 WHERE id = ?", (old["id"],))
    conn.execute("UPDATE league_members SET slot_index = 1, role = 'лидер' WHERE id = ?", (new["id"],))
    if remove_old:
        conn.execute("""
            UPDATE league_members SET slot_index = ?, user_id = NULL,
                game_nick = '-- Свободное место --', player_tag = '', username = '',
                role = 'участник', trophies_record = 0, joined_at = NULL WHERE id = ?
        """, (new_slot, old["id"]))
    else:
        conn.execute("UPDATE league_members SET slot_index = ?, role = 'участник' WHERE id = ?",
                     (new_slot, old["id"]))
    conn.execute("UPDATE leagues SET leader_id = ? WHERE id = ?", (new_leader_id, league_id))
    conn.execute("DELETE FROM composition_departures WHERE user_id IN (?, ?)",
                 (old_leader_id, new_leader_id))
    conn.commit(); conn.close()
    return True


def leave_league(user_id: int) -> bool:
    composition = get_user_league(user_id)
    if not composition or int(composition["leader_id"]) == int(user_id):
        return False
    conn = get_connection(); _clear_member_row(conn, composition["id"], user_id)
    conn.execute("DELETE FROM composition_departures WHERE user_id = ?", (user_id,))
    conn.commit(); conn.close()
    return True


def choose_successor(league_id: int, excluded_user_id: int):
    conn = get_connection()
    row = conn.execute("""
        SELECT m.* FROM league_deputies d
        JOIN league_members m ON m.league_id = d.league_id AND m.user_id = d.user_id
        WHERE d.league_id = ? AND d.user_id != ? AND m.user_id IS NOT NULL
        ORDER BY d.appointed_at ASC, d.id ASC LIMIT 1
    """, (league_id, excluded_user_id)).fetchone()
    if not row:
        row = conn.execute("""
            SELECT * FROM league_members WHERE league_id = ? AND user_id IS NOT NULL AND user_id != ?
            ORDER BY datetime(joined_at) ASC, id ASC LIMIT 1
        """, (league_id, excluded_user_id)).fetchone()
    conn.close()
    return row


def cancel_pending_departure(user_id: int) -> bool:
    conn = get_connection()
    cursor = conn.execute("DELETE FROM composition_departures WHERE user_id = ?", (user_id,))
    changed = cursor.rowcount > 0
    conn.commit(); conn.close()
    return changed


def get_due_departures():
    conn = get_connection()
    rows = conn.execute("""
        SELECT d.*, l.name AS league_name FROM composition_departures d
        JOIN leagues l ON l.id = d.league_id
        WHERE datetime(d.check_after) <= datetime('now')
        ORDER BY datetime(d.check_after), d.user_id
    """).fetchall()
    conn.close()
    return rows


def finalize_pending_departure(user_id: int) -> dict:
    """Передаёт лидерство только после завершения 24-часового ожидания."""
    conn = get_connection()
    pending = conn.execute(
        "SELECT * FROM composition_departures WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    if not pending:
        return {"action": "none"}

    composition = get_user_league(user_id)
    if not composition or int(composition["leader_id"]) != int(user_id):
        cancel_pending_departure(user_id)
        return {"action": "none"}

    league_id, name = int(composition["id"]), composition["name"]
    successor = choose_successor(league_id, user_id)
    if successor:
        transfer_leadership(league_id, user_id, int(successor["user_id"]), remove_old=True)
        return {"action": "leadership_transferred", "league_name": name,
                "new_leader_id": int(successor["user_id"]),
                "new_leader_name": successor["game_nick"] or str(successor["user_id"])}
    dissolve_league(league_id)
    return {"action": "dissolved", "league_name": name}


def handle_clan_departure(user_id: int, reason: str = "telegram", old_clan: str = None,
                          player_tag: str = None) -> dict:
    """
    Обычный участник удаляется сразу. Для лидера создаётся ожидание на 24 часа;
    повторные сигналы не продлевают первоначальный срок.
    """
    composition = get_user_league(user_id)
    if not composition:
        return {"action": "none"}
    league_id, name = int(composition["id"]), composition["name"]
    if int(composition["leader_id"]) == int(user_id):
        conn = get_connection()
        conn.execute("""
            INSERT INTO composition_departures
                (user_id, league_id, reason, old_clan, player_tag, left_at, check_after)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, datetime('now', '+1 day'))
            ON CONFLICT(user_id) DO UPDATE SET
                league_id = excluded.league_id,
                reason = excluded.reason,
                old_clan = COALESCE(excluded.old_clan, composition_departures.old_clan),
                player_tag = COALESCE(excluded.player_tag, composition_departures.player_tag)
        """, (user_id, league_id, reason, old_clan, player_tag))
        row = conn.execute(
            "SELECT check_after FROM composition_departures WHERE user_id = ?", (user_id,)
        ).fetchone()
        conn.commit(); conn.close()
        return {"action": "leadership_pending", "league_name": name,
                "check_after": row["check_after"] if row else None}
    leave_league(user_id)
    return {"action": "member_removed", "league_name": name}


init_league_db()