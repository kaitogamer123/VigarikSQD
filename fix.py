"""
seed_test_teams.py — удаление 2 тестовых составов из league.db

Удаляет ПОЛНОСТЬЮ (лига + участники + замы + заявки + инвайты + скримы + верификации):
  1. ТестБоты [TBOT]  (лидер 6430486779)
  2. АдминТест [ADMIN] (лидер 7899153362)

Запуск:
    Положи этот файл РЯДОМ с league.db и запусти:
        python seed_test_teams.py

Скрипт сам найдёт league.db рядом (или в ./league/league.db),
сделает бэкап и всё удалит. Бота перезапускать не нужно.
"""
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

# ─── КОГО УДАЛЯЕМ (поменяй тут, если надо другие) ─────────────
DELETE_TARGETS = [
    {"label": "ТестБоты", "tag": "TBOT", "leader_id": 6430486779},
    {"label": "АдминТест", "tag": "ADMIN", "leader_id": 7899153362},
]
# ───────────────────────────────────────────────────────────────


def find_db() -> Path:
    here = Path(__file__).resolve().parent
    candidates = [
        here / "league.db",
        here / "league" / "league.db",
        Path.cwd() / "league.db",
        Path.cwd() / "league" / "league.db",
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError(
        "league.db не найден. Положи seed_test_teams.py рядом с league.db\n"
        f"Проверены пути: {', '.join(str(p) for p in candidates)}"
    )


def table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def get_columns(conn, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def delete_league_fully(conn, league_id: int):
    """Удаляет лигу и все связанные строки. Возвращает счётчик удалений."""
    stats = {}

    # 1. Сначала находим скримы этой лиги, чтобы снести их детей
    scrim_ids = []
    if table_exists(conn, "scrims"):
        cols = get_columns(conn, "scrims")
        if "challenger_league_id" in cols and "opponent_league_id" in cols:
            rows = conn.execute(
                "SELECT id FROM scrims WHERE challenger_league_id = ? OR opponent_league_id = ?",
                (league_id, league_id),
            ).fetchall()
            scrim_ids = [r[0] for r in rows]

    # 2. Дети скримов
    for table in ("random_scrim_invites", "scrim_reports"):
        if table_exists(conn, table) and scrim_ids:
            cols = get_columns(conn, table)
            if "scrim_id" in cols:
                placeholders = ",".join("?" for _ in scrim_ids)
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE scrim_id IN ({placeholders})",
                    scrim_ids,
                )
                stats[table] = cur.rowcount

    # 3. Сами скримы
    if table_exists(conn, "scrims") and scrim_ids:
        placeholders = ",".join("?" for _ in scrim_ids)
        cur = conn.execute(
            f"DELETE FROM scrims WHERE id IN ({placeholders})", scrim_ids
        )
        stats["scrims"] = cur.rowcount

    # 4. Всё остальное по league_id
    for table in (
        "league_members",
        "league_deputies",
        "league_applications",
        "league_invites",
        "composition_departures",
        "league_verify_requests",
        "composition_verifications",
    ):
        if not table_exists(conn, table):
            continue
        if "league_id" not in get_columns(conn, table):
            continue
        cur = conn.execute(f"DELETE FROM {table} WHERE league_id = ?", (league_id,))
        stats[table] = cur.rowcount

    # 5. Сама лига
    cur = conn.execute("DELETE FROM leagues WHERE id = ?", (league_id,))
    stats["leagues"] = cur.rowcount
    return stats


def main():
    db_path = find_db()
    print(f"База: {db_path}")

    backup = db_path.with_name(
        f"{db_path.stem}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}{db_path.suffix}"
    )
    shutil.copy(db_path, backup)
    print(f"Бэкап: {backup.name}\n")

    conn = sqlite3.connect(str(db_path), timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF;")

    for target in DELETE_TARGETS:
        tag = target["tag"].upper()
        leader_id = target["leader_id"]
        label = target["label"]

        # Ищем по тегу ИЛИ по лидеру (что найдётся)
        rows = conn.execute(
            "SELECT id, name, tag, leader_id FROM leagues "
            "WHERE UPPER(tag) = ? OR leader_id = ?",
            (tag, leader_id),
        ).fetchall()

        if not rows:
            print(f"[!] {label} (тег {tag}, лидер {leader_id}) — не найдена, пропускаю.")
            continue

        for r in rows:
            lid = int(r["id"])
            print(f"[-] Удаляю id={lid} | {r['name']} [{r['tag']}] (лидер {r['leader_id']})...")
            stats = delete_league_fully(conn, lid)
            conn.commit()
            removed = sum(stats.values())
            print(f"    Готово, удалено строк: {removed} {stats}")

    print("\n── Осталось в базе ──")
    for r in conn.execute(
        "SELECT l.id, l.name, l.tag, l.leader_id, "
        "(SELECT COUNT(*) FROM league_members m "
        " WHERE m.league_id = l.id AND m.user_id IS NOT NULL) AS cnt "
        "FROM leagues l ORDER BY l.id"
    ):
        print(f"  id={r['id']} | {r['name']} [{r['tag']}] | лидер {r['leader_id']} | {r['cnt']}/4")

    conn.close()
    print("\nГотово. Бэкап лежит рядом на случай отката.")


if __name__ == "__main__":
    main()
