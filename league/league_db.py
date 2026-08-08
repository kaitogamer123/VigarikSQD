import sqlite3
import os

DB_PATH = os.path.join("league", "league.db")


def get_connection():
    os.makedirs("league", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_league_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Таблица лиг
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS leagues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            tag TEXT NOT NULL,
            leader_id INTEGER NOT NULL,
            is_open INTEGER DEFAULT 1,
            record_league INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Таблица слотов участников (по 4 слота на лигу: индексы 1, 2, 3, 4)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS league_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_id INTEGER,
            slot_index INTEGER, -- от 1 до 4 (1 - лидер)
            user_id INTEGER,
            game_nick TEXT,
            player_tag TEXT,
            username TEXT,
            role TEXT DEFAULT 'участник', -- 'лидер' или 'участник'
            trophies_record INTEGER DEFAULT 0,
            FOREIGN KEY (league_id) REFERENCES leagues(id) ON DELETE CASCADE
        )
    """)

    # Таблица заявок в лиги
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS league_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_id INTEGER,
            user_id INTEGER,
            text_reason TEXT,
            sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (league_id) REFERENCES leagues(id) ON DELETE CASCADE
        )
    """)

    # Таблица приглашений
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS league_invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_id INTEGER,
            inviter_id INTEGER,
            invitee_id INTEGER,
            FOREIGN KEY (league_id) REFERENCES leagues(id) ON DELETE CASCADE
        )
    """)

    conn.commit()
    conn.close()


init_league_db()