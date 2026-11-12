"""Тестовые данные для ручного прогона турниров и скримов.

Создаёт отдельную БД (по умолчанию D:\\Tmp\\opencode\\seed_tournaments.db) и
наполняет её:
  * 12 тестовых турниров — 1V1 / 2V2 / 3V3 / ShowDown, по 3 стадии на каждый
    (регистрация, идёт, завершён с пьедесталом и матчем за 3-е место);
  * скримы во всех статусах (invited / searching / scheduled / awaiting_scores /
    completed) на тестовых составах.

Боевая база не затрагивается: путь задаётся явно, league/league.db по умолчанию
запрещён (нужен --force).

Запуск:
    .\\venv\\Scripts\\python.exe tools\\seed_test_data.py
    # свой путь / id реального лидера для проверки регистрации:
    .\\venv\\Scripts\\python.exe tools\\seed_test_data.py --db D:\\Tmp\\opencode\\my.db --user-id 123456789

Потом бота можно поднять на этой БД:
    $env:LEAGUE_DB_PATH="D:\\Tmp\\opencode\\seed_tournaments.db"
    .\\venv\\Scripts\\python.exe main.py
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = r"D:\Tmp\opencode\seed_tournaments.db"
START_AT = "01.01.2030 10:00"

# (тип, стадия, сколько команд/игроков)
PLAN = [
    ("1v1", "registration", 5),
    ("1v1", "running", 7),
    ("1v1", "finished", 8),
    ("2v2", "registration", 4),
    ("2v2", "running", 6),
    ("2v2", "finished", 5),
    ("3v3", "registration", 3),
    ("3v3", "running", 7),
    ("3v3", "finished", 6),
    ("showdown", "registration", 6),
    ("showdown", "running", 9),
    ("showdown", "finished", 10),
]

SCRIMS = [
    ("normal", "BO3", "invited"),
    ("random", "BO3", "searching"),
    ("friendly", "BO5", "scheduled"),
    ("normal", "BO5", "awaiting_scores"),
    ("random", "BO3", "completed"),
]


def parse_args():
    p = argparse.ArgumentParser(description="Тестовые турниры и скримы")
    p.add_argument("--db", default=DEFAULT_DB, help="путь к SQLite-базе для наполнения")
    p.add_argument("--reset", action="store_true", help="удалить существующую БД и создать заново")
    p.add_argument("--force", action="store_true", help="разрешить писать в league/league.db")
    p.add_argument("--user-id", type=int, default=0,
                   help="Telegram ID тестировщика — станет лидером первого состава")
    return p.parse_args()


def _is_production(db_path: str) -> bool:
    prod = os.path.abspath(os.path.join(ROOT, "league", "league.db"))
    return os.path.abspath(db_path) == prod


def main() -> int:
    args = parse_args()
    db_path = os.path.abspath(args.db)
    if _is_production(db_path) and not args.force:
        print("Отказано: это боевая league/league.db. Для теста укажи другой --db "
              "или добавь --force, если точно понимаешь, что делаешь.")
        return 2
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    if os.path.exists(db_path):
        if not args.reset:
            print(f"Файл существует: {db_path}\nДобавь --reset, чтобы пересоздать его.")
            return 2
        os.remove(db_path)

    os.environ["LEAGUE_DB_PATH"] = db_path
    sys.path.insert(0, ROOT)

    from league import league_db as ldb
    from league.league_db import create_scrim
    from handlers.admin_features import tournament_db as tdb
    from handlers.admin_features import bracket as br

    ldb.DB_PATH = db_path
    ldb.init_league_db()
    tdb.init_tournament_tables()

    # ── Тестовые составы: 12 лидеров + по 2 игрока в каждом ─────────────────
    base_uid = 900_000
    leagues = []
    conn = ldb.get_connection()
    for i in range(12):
        leader_id = args.user_id if (i == 0 and args.user_id) else base_uid + i * 10 + 1
        name, tag = f"Test Squad {chr(65 + i)}", f"TS{chr(65 + i)}"
        cur = conn.execute(
            "INSERT INTO leagues (name, tag, leader_id, is_open, is_verified, mmr) "
            "VALUES (?, ?, ?, 1, 1, 1000)",
            (name, tag, leader_id),
        )
        league_id = int(cur.lastrowid)
        for slot in range(1, 4):
            member_id = leader_id if slot == 1 else base_uid + i * 10 + slot
            conn.execute(
                "INSERT INTO league_members (league_id, slot_index, user_id, game_nick, "
                "player_tag, username, role) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (league_id, slot, member_id, f"TestPlayer{i}{slot}", f"#TEST{i}{slot}",
                 f"test_{i}_{slot}", "лидер" if slot == 1 else "участник"),
            )
        leagues.append({"id": league_id, "leader_id": leader_id, "name": name, "tag": tag})
    conn.commit()
    conn.close()

    def play_matches(tid: int, limit: int = 400) -> int:
        """Доигрывает матчи (всегда побеждает сторона A). Возвращает их число."""
        played = 0
        for _ in range(limit):
            ready = [m for m in tdb.get_tournament_matches(tid)
                     if m["status"] == "pending" and m["team_a_id"] and m["team_b_id"]]
            if not ready:
                break
            m = ready[0]
            br._advance_winner(tid, m, int(m["team_a_id"]), int(m["team_b_id"]))
            br._promote_byes(tid)
            br._ensure_third_place(tid)
            played += 1
        return played

    # ── Турниры: каждый тип в трёх стадиях ──────────────────────────────────
    rows = []
    for idx, (t_type, stage, n) in enumerate(PLAN):
        fmt = None if t_type == "showdown" else ("BO5" if t_type == "3v3" else "BO3")
        tid = tdb.create_tournament_row(
            t_type, fmt, None, START_AT, leagues[0]["leader_id"], is_test=1)
        if stage != "registration":
            tdb.set_tournament_status(tid, "running")
        for i in range(n):
            if t_type == "showdown":
                uid = base_uid + 100 + i
                tdb.add_tournament_team(tid, None, uid, f"TestPlayer{i + 1}", f"#TP{i + 1}")
            else:
                league = leagues[i % len(leagues)]
                tdb.add_tournament_team(tid, league["id"], league["leader_id"],
                                        league["name"], league["tag"])
        teams = tdb.get_tournament_teams(tid)
        if stage != "registration":
            br.generate_first_round(tid, teams)
            if stage == "running":
                for _ in range(max(1, (n - 1) // 2)):
                    ready = [m for m in tdb.get_tournament_matches(tid, "main")
                             if m["status"] == "pending" and m["team_a_id"] and m["team_b_id"]]
                    if not ready:
                        break
                    m = ready[0]
                    br._advance_winner(tid, m, int(m["team_a_id"]), int(m["team_b_id"]))
            else:
                play_matches(tid)
                tdb.set_tournament_status(tid, "finished")
        rows.append((tid, t_type, stage, len(teams)))

    # ── Скримы во всех статусах ─────────────────────────────────────────────
    scrim_ids = []
    for i, (scrim_type, fmt, status) in enumerate(SCRIMS):
        a, b = leagues[i % len(leagues)], leagues[(i + 1) % len(leagues)]
        sid = create_scrim(
            scrim_type, fmt, a["id"], a["leader_id"], scheduled_text="01.01.2030 19:00",
            opponent_league_id=b["id"], opponent_leader_id=b["leader_id"], status=status,
        )
        scrim_ids.append(sid)

    print(f"БД: {db_path}\n")
    print(f"{'ID':>4}  {'тип':<9} {'стадия':<13} участников")
    for tid, t_type, stage, cnt in rows:
        print(f"{tid:>4}  {t_type:<9} {stage:<13} {cnt}")
    print("\nСкримы:", ", ".join(f"#{sid}" for sid in scrim_ids))
    print("\nЗапуск бота на тестовой БД:")
    print(f'  $env:LEAGUE_DB_PATH="{db_path}"')
    print(r"  .\venv\Scripts\python.exe main.py")
    print("\nУчти: лидеры турниров — тестовые ID, поэтому ЛС им не дойдут.")
    print("Чтобы проверить регистрацию своим аккаунтом, укажи --user-id <твой Telegram ID>.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
