"""SQLite-слой системы составов (историческое имя модуля league сохранено)."""
import json
import os
import sqlite3
from typing import Optional

DB_PATH = os.path.join("league", "league.db")
DEPUTY_PERMISSIONS = ("can_review_apps", "can_invite", "can_kick", "can_toggle_open")

# MMR-настройки скримов: (победа за карту, поражение за карту)
MMR_NORMAL = (10, -7)
MMR_RANDOM = (15, -10)

SCRIM_ACTIVE_STATUSES = ("searching", "invited", "scheduled", "awaiting_scores")


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
            record_league INTEGER DEFAULT 0, created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            is_verified INTEGER DEFAULT 0, mmr INTEGER DEFAULT 0
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
        CREATE TABLE IF NOT EXISTS league_verify_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_id INTEGER NOT NULL,
            leader_id INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            decided_at DATETIME,
            decided_by INTEGER,
            FOREIGN KEY (league_id) REFERENCES leagues(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS scrims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scrim_type TEXT NOT NULL,
            format TEXT NOT NULL,
            challenger_league_id INTEGER NOT NULL,
            opponent_league_id INTEGER,
            challenger_leader_id INTEGER NOT NULL,
            opponent_leader_id INTEGER,
            challenger_name TEXT DEFAULT '',
            challenger_tag TEXT DEFAULT '',
            opponent_name TEXT DEFAULT '',
            opponent_tag TEXT DEFAULT '',
            scheduled_text TEXT DEFAULT '',
            status TEXT DEFAULT 'invited',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            invite_expires_at DATETIME,
            challenger_score_a INTEGER,
            challenger_score_b INTEGER,
            opponent_score_a INTEGER,
            opponent_score_b INTEGER,
            challenger_shots TEXT DEFAULT '[]',
            opponent_shots TEXT DEFAULT '[]',
            mmr_challenger INTEGER,
            mmr_opponent INTEGER,
            completed_at DATETIME
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_invite ON league_invites (league_id, invitee_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_league_member
            ON league_members (league_id, user_id) WHERE user_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_deputies_order ON league_deputies (league_id, appointed_at, id);
        CREATE INDEX IF NOT EXISTS idx_composition_departures_due
            ON composition_departures (check_after);
        CREATE INDEX IF NOT EXISTS idx_scrims_status ON scrims (status);
        CREATE INDEX IF NOT EXISTS idx_scrims_leagues ON scrims (challenger_league_id, opponent_league_id);
    """)
    if "joined_at" not in _columns(conn, "league_members"):
        cur.execute("ALTER TABLE league_members ADD COLUMN joined_at DATETIME")
    if "is_verified" not in _columns(conn, "leagues"):
        cur.execute("ALTER TABLE leagues ADD COLUMN is_verified INTEGER DEFAULT 0")
    if "mmr" not in _columns(conn, "leagues"):
        cur.execute("ALTER TABLE leagues ADD COLUMN mmr INTEGER DEFAULT 0")
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


def get_league(league_id: int):
    conn = get_connection()
    row = conn.execute("SELECT * FROM leagues WHERE id = ?", (league_id,)).fetchone()
    conn.close()
    return row


def get_verified_leagues(exclude_id: int = None):
    conn = get_connection()
    if exclude_id:
        rows = conn.execute(
            "SELECT * FROM leagues WHERE is_verified = 1 AND id != ? ORDER BY mmr DESC, name",
            (exclude_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM leagues WHERE is_verified = 1 ORDER BY mmr DESC, name"
        ).fetchall()
    conn.close()
    return rows


def set_league_verified(league_id: int, value: bool = True) -> None:
    conn = get_connection()
    conn.execute("UPDATE leagues SET is_verified = ? WHERE id = ?", (1 if value else 0, league_id))
    conn.commit()
    conn.close()


def add_mmr(league_id: int, delta: int) -> int:
    conn = get_connection()
    conn.execute("UPDATE leagues SET mmr = COALESCE(mmr, 0) + ? WHERE id = ?", (int(delta), league_id))
    row = conn.execute("SELECT mmr FROM leagues WHERE id = ?", (league_id,)).fetchone()
    conn.commit()
    conn.close()
    return int(row["mmr"]) if row else 0


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


def count_clan_members(user_ids) -> int:
    """Сколько из user_ids состоят в клане (зарегистрированы в vigarik.db)."""
    ids = [int(x) for x in (user_ids or []) if x]
    if not ids:
        return 0
    try:
        import sqlite3 as _sq
        conn = _sq.connect("vigarik.db", timeout=10.0)
        placeholders = ",".join("?" for _ in ids)
        row = conn.execute(
            f"SELECT COUNT(*) FROM members WHERE user_id IN ({placeholders}) AND registered = 1",
            ids,
        ).fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


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
                  "composition_departures", "league_verify_requests"):
        try:
            conn.execute(f"DELETE FROM {table} WHERE league_id = ?", (league_id,))
        except Exception:
            pass
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
    # Активные скримы уходят за составом; лидерство в скримах обновляем.
    conn.execute("UPDATE scrims SET challenger_leader_id = ? WHERE challenger_league_id = ? AND status IN ('searching','invited','scheduled','awaiting_scores') AND challenger_leader_id = ?",
                 (new_leader_id, league_id, old_leader_id))
    conn.execute("UPDATE scrims SET opponent_leader_id = ? WHERE opponent_league_id = ? AND status IN ('searching','invited','scheduled','awaiting_scores') AND opponent_leader_id = ?",
                 (new_leader_id, league_id, old_leader_id))
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


# ─── Верификация составов ────────────────────────────────────────────────

def get_pending_verify_request(league_id: int):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM league_verify_requests WHERE league_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
        (league_id,),
    ).fetchone()
    conn.close()
    return row


def create_verify_request(league_id: int, leader_id: int):
    if get_pending_verify_request(league_id):
        return None
    league = get_league(league_id)
    if not league or int(league["is_verified"] or 0) == 1:
        return None
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO league_verify_requests (league_id, leader_id, status) VALUES (?, ?, 'pending')",
        (league_id, leader_id),
    )
    req_id = cur.lastrowid
    conn.commit()
    conn.close()
    return req_id


def get_verify_request(req_id: int):
    conn = get_connection()
    row = conn.execute("SELECT * FROM league_verify_requests WHERE id = ?", (req_id,)).fetchone()
    conn.close()
    return row


def decide_verify_request(req_id: int, status: str, decided_by: int) -> bool:
    conn = get_connection()
    cur = conn.execute(
        "UPDATE league_verify_requests SET status = ?, decided_at = CURRENT_TIMESTAMP, decided_by = ? "
        "WHERE id = ? AND status = 'pending'",
        (status, decided_by, req_id),
    )
    ok = cur.rowcount > 0
    conn.commit()
    conn.close()
    return ok


# ─── Скримы ──────────────────────────────────────────────────────────────

def create_scrim(scrim_type: str, fmt: str, challenger_league_id: int, challenger_leader_id: int,
                 scheduled_text: str = "", opponent_league_id=None, opponent_leader_id=None,
                 status: str = "invited", expire_hours: int = 24) -> int:
    challenger = get_league(challenger_league_id)
    opponent = get_league(opponent_league_id) if opponent_league_id else None
    conn = get_connection()
    cur = conn.execute("""
        INSERT INTO scrims (scrim_type, format, challenger_league_id, opponent_league_id,
            challenger_leader_id, opponent_leader_id, challenger_name, challenger_tag,
            opponent_name, opponent_tag, scheduled_text, status, invite_expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', ?))
    """, (
        scrim_type, fmt, challenger_league_id, opponent_league_id,
        challenger_leader_id, opponent_leader_id,
        challenger["name"] if challenger else "", challenger["tag"] if challenger else "",
        opponent["name"] if opponent else "", opponent["tag"] if opponent else "",
        scheduled_text or "", status, f"+{int(expire_hours)} hours",
    ))
    scrim_id = cur.lastrowid
    conn.commit()
    conn.close()
    return scrim_id


def get_scrim(scrim_id: int):
    conn = get_connection()
    row = conn.execute("SELECT * FROM scrims WHERE id = ?", (scrim_id,)).fetchone()
    conn.close()
    return row


def set_scrim_status(scrim_id: int, status: str) -> None:
    conn = get_connection()
    conn.execute("UPDATE scrims SET status = ? WHERE id = ?", (status, scrim_id))
    conn.commit()
    conn.close()


def claim_random_scrim(scrim_id: int, league_id: int, leader_id: int) -> bool:
    league = get_league(league_id)
    if not league:
        return False
    conn = get_connection()
    cur = conn.execute(
        "UPDATE scrims SET opponent_league_id = ?, opponent_leader_id = ?, opponent_name = ?, "
        "opponent_tag = ?, status = 'scheduled' WHERE id = ? AND status = 'searching'",
        (league_id, leader_id, league["name"], league["tag"], scrim_id),
    )
    ok = cur.rowcount > 0
    conn.commit()
    conn.close()
    return ok


def save_score_report(scrim_id: int, side: str, score_a: int, score_b: int) -> None:
    # NB: обе стороны пишут счёт в едином формате "нападающие/принявшие"
    conn = get_connection()
    if side == "challenger":
        conn.execute("UPDATE scrims SET challenger_score_a = ?, challenger_score_b = ? WHERE id = ?",
                     (int(score_a), int(score_b), scrim_id))
    else:
        conn.execute("UPDATE scrims SET opponent_score_a = ?, opponent_score_b = ? WHERE id = ?",
                     (int(score_a), int(score_b), scrim_id))
    conn.execute("UPDATE scrims SET status = 'awaiting_scores' WHERE id = ? AND status = 'scheduled'", (scrim_id,))
    conn.commit()
    conn.close()


def clear_score_reports(scrim_id: int) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE scrims SET challenger_score_a = NULL, challenger_score_b = NULL, "
        "opponent_score_a = NULL, opponent_score_b = NULL, status = 'scheduled' WHERE id = ?",
        (scrim_id,),
    )
    conn.commit()
    conn.close()


def _shots_list(raw) -> list:
    try:
        data = json.loads(raw or "[]")
        return data if isinstance(data, list) else []
    except Exception:
        return []


def add_scrim_screenshot(scrim_id: int, side: str, file_id: str) -> int:
    conn = get_connection()
    row = conn.execute("SELECT challenger_shots, opponent_shots FROM scrims WHERE id = ?", (scrim_id,)).fetchone()
    if not row:
        conn.close()
        return 0
    col = "challenger_shots" if side == "challenger" else "opponent_shots"
    shots = _shots_list(row[col])
    shots.append(file_id)
    conn.execute(f"UPDATE scrims SET {col} = ? WHERE id = ?", (json.dumps(shots), scrim_id))
    conn.commit()
    conn.close()
    return len(shots)


def get_scrim_shots_count(scrim_id: int, side: str) -> int:
    conn = get_connection()
    row = conn.execute("SELECT challenger_shots, opponent_shots FROM scrims WHERE id = ?", (scrim_id,)).fetchone()
    conn.close()
    if not row:
        return 0
    col = "challenger_shots" if side == "challenger" else "opponent_shots"
    return len(_shots_list(row[col]))


def calc_mmr(scrim_type: str, score_a: int, score_b: int) -> tuple[int, int]:
    """Возвращает (дельта нападающих, дельта принявших)."""
    if scrim_type == "friendly":
        return (0, 0)
    win, lose = MMR_RANDOM if scrim_type == "random" else MMR_NORMAL
    delta_a = int(score_a) * win + int(score_b) * lose
    delta_b = int(score_b) * win + int(score_a) * lose
    return (delta_a, delta_b)


def complete_scrim(scrim_id: int) -> dict | None:
    scrim = get_scrim(scrim_id)
    if not scrim:
        return None
    if scrim["challenger_score_a"] is None or scrim["opponent_score_a"] is None:
        return None
    if (scrim["challenger_score_a"], scrim["challenger_score_b"]) != \
       (scrim["opponent_score_a"], scrim["opponent_score_b"]):
        return None
    score_a, score_b = int(scrim["challenger_score_a"]), int(scrim["challenger_score_b"])
    delta_a, delta_b = calc_mmr(scrim["scrim_type"], score_a, score_b)
    add_mmr(int(scrim["challenger_league_id"]), delta_a)
    if scrim["opponent_league_id"]:
        add_mmr(int(scrim["opponent_league_id"]), delta_b)
    conn = get_connection()
    conn.execute(
        "UPDATE scrims SET mmr_challenger = ?, mmr_opponent = ?, status = 'completed', "
        "completed_at = CURRENT_TIMESTAMP WHERE id = ?",
        (delta_a, delta_b, scrim_id),
    )
    conn.commit()
    conn.close()
    return {"score_a": score_a, "score_b": score_b, "delta_a": delta_a, "delta_b": delta_b}


def last_random_opponent(league_id: int):
    """ID соперника по последнему завершённому рандомному скриму (для запрета повтора)."""
    conn = get_connection()
    row = conn.execute("""
        SELECT challenger_league_id, opponent_league_id FROM scrims
        WHERE scrim_type = 'random' AND status = 'completed'
          AND (challenger_league_id = ? OR opponent_league_id = ?)
        ORDER BY completed_at DESC, id DESC LIMIT 1
    """, (league_id, league_id)).fetchone()
    conn.close()
    if not row:
        return None
    if int(row["challenger_league_id"]) == int(league_id):
        return int(row["opponent_league_id"]) if row["opponent_league_id"] else None
    return int(row["challenger_league_id"])


def get_active_scrims_for_leader(user_id: int):
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM scrims
        WHERE (challenger_leader_id = ? OR opponent_leader_id = ?)
          AND status IN ('searching','invited','scheduled','awaiting_scores')
        ORDER BY id DESC
    """, (user_id, user_id)).fetchall()
    conn.close()
    return rows


def get_league_history(league_id: int, limit: int = 10):
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM scrims
        WHERE status = 'completed' AND (challenger_league_id = ? OR opponent_league_id = ?)
        ORDER BY completed_at DESC, id DESC LIMIT ?
    """, (league_id, league_id, int(limit))).fetchall()
    conn.close()
    return rows


def get_recent_completed(limit: int = 10):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM scrims WHERE status = 'completed' ORDER BY completed_at DESC, id DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    conn.close()
    return rows


def get_expired_scrims():
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM scrims
        WHERE status IN ('invited','searching') AND invite_expires_at IS NOT NULL
          AND datetime(invite_expires_at) <= datetime('now')
    """).fetchall()
    conn.close()
    return rows


init_league_db()
