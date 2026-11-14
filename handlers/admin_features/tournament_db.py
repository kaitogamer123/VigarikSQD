"""Работа с БД турниров: таблицы, команды, матчи."""
import logging
import random
from datetime import datetime
from config import is_hidden_tester
from league import league_db as db

logger = logging.getLogger(__name__)

# Инициализация нужна один раз на файл БД: функции ниже вызывают её на каждом
# чтении, поэтому после первого успешного прогона они ничего не делают.
_ready_for_db = None

# Определения колонок для мягких миграций (ALTER TABLE ADD COLUMN), чтобы не
# терять турнирные данные при изменении схемы.
_MIGRATE_COLUMNS = {
    "tournaments": {
        "t_type": "TEXT NOT NULL DEFAULT ''",
        "t_format": "TEXT",
        "team_limit": "INTEGER",
        "starts_at": "TEXT NOT NULL DEFAULT ''",
        "status": "TEXT DEFAULT 'registration'",
        "created_by": "INTEGER",
        "created_at": "DATETIME",
        "is_test": "INTEGER DEFAULT 0",
    },
    "tournament_teams": {
        "tournament_id": "INTEGER",
        "league_id": "INTEGER",
        "leader_id": "INTEGER",
        "name": "TEXT DEFAULT ''",
        "tag": "TEXT DEFAULT ''",
        "registered_at": "DATETIME",
        "seed": "INTEGER",
    },
    "tournament_matches": {
        "tournament_id": "INTEGER",
        "round_no": "INTEGER DEFAULT 0",
        "slot_no": "INTEGER DEFAULT 0",
        "bracket": "TEXT DEFAULT 'main'",
        "team_a_id": "INTEGER",
        "team_b_id": "INTEGER",
        "winner_team_id": "INTEGER",
        "loser_team_id": "INTEGER",
        "status": "TEXT DEFAULT 'pending'",
        "score_text": "TEXT DEFAULT ''",
        "created_at": "DATETIME",
        "finished_at": "DATETIME",
        "notified": "INTEGER DEFAULT 0",
    },
    "tournament_results": {
        "match_id": "INTEGER",
        "tournament_id": "INTEGER",
        "reporter_id": "INTEGER",
        "reporter_team_id": "INTEGER",
        "text_note": "TEXT DEFAULT ''",
        "photos": "TEXT DEFAULT '[]'",
        "status": "TEXT DEFAULT 'pending'",
        "decided_by": "INTEGER",
        "decided_at": "DATETIME",
        "created_at": "DATETIME",
    },
    "tournament_posts": {
        "tournament_id": "INTEGER",
        "clan_key": "TEXT",
        "chat_id": "INTEGER",
        "thread_id": "INTEGER",
        "message_id": "INTEGER",
    },
}


def init_tournament_tables() -> None:
    """Создаёт/дополняет таблицы турниров. Безопасно вызывать сколько угодно раз.

    Старые схемы мигрируются через ALTER TABLE ADD COLUMN: турнирные данные НЕ
    удаляются. Первый вызов делает всю работу, дальше функция сразу выходит.
    """
    global _ready_for_db
    db_path = getattr(db, "DB_PATH", None)
    if _ready_for_db == db_path:
        return
    conn = db.get_connection()
    existing = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    required = {
        "tournaments": {"t_type", "t_format", "team_limit", "starts_at", "status"},
        "tournament_teams": {"tournament_id", "league_id", "leader_id", "name", "tag", "seed"},
        "tournament_matches": {"tournament_id", "round_no", "slot_no", "bracket",
                               "team_a_id", "team_b_id", "winner_team_id"},
        "tournament_results": {"match_id", "tournament_id", "reporter_id", "status"},
        "tournament_posts": {"tournament_id", "chat_id", "message_id"},
    }
    for table, need in required.items():
        if table not in existing:
            continue
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for col in sorted(need - cols):
            ddl = _MIGRATE_COLUMNS.get(table, {}).get(col)
            if not ddl:
                logger.error(f"Не знаю, как добавить колонку {table}.{col}")
                continue
            logger.warning(f"Миграция схемы турниров: ADD COLUMN {table}.{col}")
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        conn.commit()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tournaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            t_type TEXT NOT NULL,
            t_format TEXT,
            team_limit INTEGER,
            starts_at TEXT NOT NULL,
            status TEXT DEFAULT 'registration',
            created_by INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS tournament_teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            league_id INTEGER,
            leader_id INTEGER NOT NULL,
            name TEXT DEFAULT '',
            tag TEXT DEFAULT '',
            seed INTEGER,
            registered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS tournament_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            clan_key TEXT,
            chat_id INTEGER NOT NULL,
            thread_id INTEGER,
            message_id INTEGER NOT NULL,
            FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS tournament_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            round_no INTEGER NOT NULL,
            slot_no INTEGER NOT NULL,
            bracket TEXT DEFAULT 'main',
            team_a_id INTEGER,
            team_b_id INTEGER,
            winner_team_id INTEGER,
            loser_team_id INTEGER,
            status TEXT DEFAULT 'pending',
            score_text TEXT DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished_at DATETIME,
            FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS tournament_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER NOT NULL,
            tournament_id INTEGER NOT NULL,
            reporter_id INTEGER NOT NULL,
            reporter_team_id INTEGER,
            text_note TEXT DEFAULT '',
            photos TEXT DEFAULT '[]',
            status TEXT DEFAULT 'pending',
            decided_by INTEGER,
            decided_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (match_id) REFERENCES tournament_matches(id) ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tournament_team_unique
            ON tournament_teams (tournament_id, leader_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tournament_match_slot
            ON tournament_matches (tournament_id, bracket, round_no, slot_no);
        CREATE INDEX IF NOT EXISTS idx_tournament_status ON tournaments (status);
        CREATE INDEX IF NOT EXISTS idx_tournament_posts_t ON tournament_posts (tournament_id);
        CREATE INDEX IF NOT EXISTS idx_tournament_matches_t ON tournament_matches (tournament_id, status);
        CREATE INDEX IF NOT EXISTS idx_tournament_results_m ON tournament_results (match_id, status);
    """)
    # Миграция: флаг тестового турнира (без рассылки в ЛС всем игрокам)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(tournaments)").fetchall()}
    if "is_test" not in cols:
        conn.execute("ALTER TABLE tournaments ADD COLUMN is_test INTEGER DEFAULT 0")
    # Миграция: флаг «лидеры новой пары уже уведомлены» (защита от повторных рассылок)
    cols_m = {r["name"] for r in conn.execute("PRAGMA table_info(tournament_matches)").fetchall()}
    if "notified" not in cols_m:
        conn.execute("ALTER TABLE tournament_matches ADD COLUMN notified INTEGER DEFAULT 0")
    conn.commit()
    conn.close()
    _ready_for_db = db_path


def create_tournament_row(t_type, t_format, team_limit, starts_at, created_by,
                          is_test: int = 0) -> int:
    init_tournament_tables()
    conn = db.get_connection()
    cur = conn.execute(
        "INSERT INTO tournaments (t_type, t_format, team_limit, starts_at, created_by, is_test) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (t_type, t_format, team_limit, starts_at, created_by, int(is_test)),
    )
    conn.commit()
    tid = int(cur.lastrowid)
    conn.close()
    return tid


def get_tournament(tournament_id: int):
    init_tournament_tables()
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
    conn.close()
    return row


def get_tournament_teams(tournament_id: int):
    init_tournament_tables()
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM tournament_teams WHERE tournament_id = ? "
        "ORDER BY (seed IS NULL) ASC, seed ASC, registered_at, id",
        (tournament_id,),
    ).fetchall()
    conn.close()
    return rows


def _shuffle_layout(n: int):
    """Пары первого круга и число автопроходов по правилам _bracket_layout.

    Возвращает (positions, autos): positions — пары индексов в списке участников.
    """
    if n < 2:
        return [], 0
    size = 1
    while size < n:
        size *= 2
    autos = size - n
    if autos == 0:
        return [(2 * j, 2 * j + 1) for j in range(size // 2)], 0
    playin = n - size // 2
    return [(autos + 2 * j, autos + 2 * j + 1) for j in range(playin)], autos


def shuffle_tournament_teams(tournament_id: int) -> dict:
    """Случайно перемешивает участников и сохраняет жеребьёвку в seed."""
    teams = list(get_tournament_teams(tournament_id))
    n = len(teams)
    if n < 2:
        return {}

    ids = [int(t["id"]) for t in teams]
    names = [str(t["name"] or "Команда") for t in teams]
    positions, autos = _shuffle_layout(n)
    current_pairs = {frozenset((ids[a], ids[b])) for a, b in positions}
    current_autos = set(ids[:autos])
    best = None
    best_key = None

    for _ in range(25):
        candidate = ids[:]
        random.shuffle(candidate)
        if candidate == ids:
            continue
        cand_pairs = {frozenset((candidate[a], candidate[b])) for a, b in positions}
        matched_pairs = len(current_pairs & cand_pairs)
        same_autos = len(current_autos & set(candidate[:autos]))
        same_positions = sum(1 for i, team_id in enumerate(candidate) if team_id == ids[i])
        key = (matched_pairs, same_autos, same_positions)
        if best_key is None or key < best_key:
            best_key = key
            best = candidate

    if best is None:
        # Крайний случай (например, 2 участника): любой отличный от текущего.
        best = ids[:]
        random.shuffle(best)
        if best == ids:
            best = ids[1:] + ids[:1]

    conn = db.get_connection()
    try:
        for index, team_id in enumerate(best, 1):
            conn.execute("UPDATE tournament_teams SET seed = ? WHERE id = ?",
                         (index, int(team_id)))
        conn.commit()
    finally:
        conn.close()

    name_by_id = dict(zip(ids, names))
    pairs = [(name_by_id[best[a]], name_by_id[best[b]]) for a, b in positions]
    autos_names = [name_by_id[team_id] for team_id in best[:autos]]
    return {"count": n, "pairs": pairs, "autos": autos_names}


def add_tournament_team(tournament_id, league_id, leader_id, name, tag) -> bool:
    """Регистрирует команду (или соло-игрока в ShowDown). False — если уже зарегистрирован."""
    # Скрытый тестер не должен светиться в турнирных списках своим ником.
    if is_hidden_tester(leader_id):
        name, tag = "🧪 Тест", "TEST"
    init_tournament_tables()
    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT INTO tournament_teams (tournament_id, league_id, leader_id, name, tag) "
            "VALUES (?, ?, ?, ?, ?)",
            (tournament_id, league_id, leader_id, name, tag),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def save_tournament_post(tournament_id, clan_key, chat_id, thread_id, message_id) -> None:
    init_tournament_tables()
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO tournament_posts (tournament_id, clan_key, chat_id, thread_id, message_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (tournament_id, clan_key, chat_id, thread_id, message_id),
    )
    conn.commit()
    conn.close()


def get_tournament_posts(tournament_id: int):
    init_tournament_tables()
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM tournament_posts WHERE tournament_id = ?", (tournament_id,)
    ).fetchall()
    conn.close()
    return rows


def get_active_tournaments():
    """Активные турниры: идёт регистрация или уже запущен.

    Терпим к легаси-статусам из старой сборки (например, 'announced'):
    прячем только финализированные/отменённые, остальное показываем.
    """
    init_tournament_tables()
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM tournaments WHERE status NOT IN ('finished', 'cancelled') "
        "ORDER BY starts_at, id"
    ).fetchall()
    conn.close()
    return rows


def set_tournament_limit(tournament_id: int, team_limit) -> None:
    init_tournament_tables()
    conn = db.get_connection()
    conn.execute("UPDATE tournaments SET team_limit = ? WHERE id = ?", (team_limit, tournament_id))
    conn.commit()
    conn.close()


def set_tournament_status(tournament_id: int, status: str) -> bool:
    """Переводит турнир в новый статус. False — если статус уже был изменён (гонка)."""
    init_tournament_tables()
    conn = db.get_connection()
    cur = conn.execute(
        "UPDATE tournaments SET status = ? WHERE id = ? AND status != ?",
        (status, tournament_id, status),
    )
    conn.commit()
    changed = cur.rowcount > 0
    conn.close()
    return changed


def get_due_tournaments():
    """Турниры со статусом registration, у которых наступило время старта."""
    init_tournament_tables()
    now = datetime.now()
    due = []
    conn = db.get_connection()
    rows = conn.execute("SELECT * FROM tournaments WHERE status = 'registration'").fetchall()
    conn.close()
    for row in rows:
        try:
            starts = datetime.strptime(str(row["starts_at"]).strip(), "%d.%m.%Y %H:%M")
        except Exception:
            continue
        if starts <= now:
            due.append(row)
    return due


def _m_get(match_id: int):
    init_tournament_tables()
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM tournament_matches WHERE id = ?", (match_id,)).fetchone()
    conn.close()
    return row


def get_tournament_matches(tournament_id: int, bracket: str = None):
    init_tournament_tables()
    conn = db.get_connection()
    if bracket:
        rows = conn.execute(
            "SELECT * FROM tournament_matches WHERE tournament_id = ? AND bracket = ? "
            "ORDER BY round_no, slot_no", (tournament_id, bracket)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tournament_matches WHERE tournament_id = ? "
            "ORDER BY bracket DESC, round_no, slot_no", (tournament_id,)).fetchall()
    conn.close()
    return rows


def _team_row(team_id):
    if not team_id:
        return None
    init_tournament_tables()
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM tournament_teams WHERE id = ?", (int(team_id),)).fetchone()
    conn.close()
    return row


def _team_name(team_id, default: str = "—") -> str:
    row = _team_row(team_id)
    return str(row["name"] or "Команда") if row else default


def create_match(tournament_id, round_no, slot_no, team_a_id, team_b_id,
                 bracket="main", partial=False) -> int:
    """Создаёт матч. BYE (нет соперника) закрывается сразу автопроходом.

    partial=True — матч может быть не укомплектован (сторона ждёт победителя
    плей-ина), его НЕ помечаем как bye.
    """
    init_tournament_tables()
    conn = db.get_connection()
    status = "pending"
    winner = None
    if not partial:
        if team_a_id and not team_b_id:
            status, winner = "bye", team_a_id
        elif team_b_id and not team_a_id:
            status, winner = "bye", team_b_id
    cur = conn.execute(
        "INSERT OR IGNORE INTO tournament_matches "
        "(tournament_id, round_no, slot_no, bracket, team_a_id, team_b_id, winner_team_id, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (tournament_id, round_no, slot_no, bracket, team_a_id, team_b_id, winner, status),
    )
    conn.commit()
    mid = int(cur.lastrowid or 0)
    conn.close()
    return mid


def _find_user_pending_match(user_id: int):
    """Ищет незавершённый матч, где пользователь — лидер одной из команд."""
    init_tournament_tables()
    conn = db.get_connection()
    row = conn.execute("""
        SELECT m.*, t.t_format, t.id AS tid FROM tournament_matches m
        JOIN tournaments t ON t.id = m.tournament_id
        LEFT JOIN tournament_teams ta ON ta.id = m.team_a_id
        LEFT JOIN tournament_teams tb ON tb.id = m.team_b_id
        WHERE t.status = 'running' AND m.status = 'pending'
          AND m.team_a_id IS NOT NULL AND m.team_b_id IS NOT NULL
          AND (ta.leader_id = ? OR tb.leader_id = ?)
        ORDER BY m.round_no, m.slot_no LIMIT 1
    """, (user_id, user_id)).fetchone()
    conn.close()
    return row


def _collect_tournament_for_delete(tournament_id: int) -> dict:
    """Собирает данные ДО удаления: посты в топиках, лидеры команд, имя турнира."""
    init_tournament_tables()
    conn = db.get_connection()
    tour = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
    posts = conn.execute("SELECT * FROM tournament_posts WHERE tournament_id = ?",
                         (tournament_id,)).fetchall()
    leaders = conn.execute("SELECT DISTINCT leader_id FROM tournament_teams WHERE tournament_id = ?",
                           (tournament_id,)).fetchall()
    teams_cnt = conn.execute("SELECT COUNT(*) c FROM tournament_teams WHERE tournament_id = ?",
                             (tournament_id,)).fetchone()["c"]
    conn.close()
    return {
        "tour": tour, "posts": posts,
        "leaders": [int(r["leader_id"]) for r in leaders if r["leader_id"]],
        "teams_cnt": int(teams_cnt),
    }


def _drop_tournament_rows(tournament_id: int) -> None:
    """Удаляет турнир; дети (команды/матчи/результаты/посты) — каскадом.

    FK включены (PRAGMA foreign_keys=ON на каждом соединении league_db),
    поэтому DELETE FROM tournaments сносит tournament_teams/matches/posts,
    а результаты — через матчи.
    """
    conn = db.get_connection()
    conn.execute("DELETE FROM tournament_results WHERE tournament_id = ?", (tournament_id,))
    conn.execute("DELETE FROM tournaments WHERE id = ?", (tournament_id,))
    conn.commit()
    conn.close()


def _pending_full_matches(tournament_id: int):
    """Матчи текущего/ожидаемого круга: обе команды известны, исход не записан."""
    return [m for m in get_tournament_matches(tournament_id)
            if m["status"] == "pending" and m["team_a_id"] and m["team_b_id"]]
