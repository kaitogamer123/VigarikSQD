"""Логика турнирной сетки: пары, раунды, продвижение победителей."""
import logging
from aiogram.utils.markdown import html_decoration as hd
from league import league_db as db
from .tournament_db import _team_name, create_match, get_tournament_matches, init_tournament_tables

logger = logging.getLogger(__name__)





def _bracket_text(names: list[str]) -> str:
    """Текстовая сетка — запасной вариант, если Pillow недоступен."""
    layout, auto_n = _bracket_layout(names)
    if not layout:
        return "Участников пока недостаточно для сетки."
    playin_pairs = [p for p in layout[0] if p[0] and p[1]]
    lines = [f"Плей-ин / первый круг ({len(playin_pairs)} пар):"]
    for i, (a, b) in enumerate(playin_pairs, 1):
        lines.append(f"{i}. {a} 🆚 {b}")
    if auto_n:
        autos = names[:auto_n]
        lines.append(f"⏭ Сразу в следующий круг ({auto_n}): {', '.join(autos)}")
    return "\n".join(lines)


def _match_by_slot(matches, bracket: str, round_no: int, slot_no: int):
    """Находит матч из списка по ветке/раунду/слоту (для отрисовки победителей)."""
    if not matches:
        return None
    for m in matches:
        if (str(m["bracket"] or "main") == bracket and
                int(m["round_no"]) == int(round_no) and int(m["slot_no"]) == int(slot_no)):
            return m
    return None


def _round_title(pairs_in_round: int, round_idx: int, total_rounds: int,
                 playin: bool) -> str:
    """Заголовок раунда с учётом плей-ина и позиции в дереве.

    Без эмодзи: шрифт DejaVu Sans их не содержит, Pillow рисовал бы «квадраты».
    """
    if playin and round_idx == 0:
        return "ПЛЕЙ-ИН"
    if round_idx == total_rounds - 1:
        return "ФИНАЛ"
    if round_idx == total_rounds - 2:
        return "ПОЛУФИНАЛ"
    names = {4: "1/4", 8: "1/8", 16: "1/16", 32: "1/32", 64: "1/64"}
    return f"{names.get(pairs_in_round, f'1/{pairs_in_round}')} ФИНАЛА"


def _next_pow2(n: int) -> int:
    size = 1
    while size < n:
        size *= 2
    return size


def _total_rounds(n: int) -> int:
    """Сколько раундов в сетке на n участников (минимум 1)."""
    return _next_pow2(max(int(n), 2)).bit_length() - 1


def _bracket_layout(names: list[str]):
    """Раскладка сетки без фейковых матчей с BYE.

    Возвращает (rounds, autos):
      rounds[0] — первый круг: реальные пары (плей-ин) или полный круг;
      rounds[1] — второй круг: пары из победителей плей-ина и автопроходов;
      дальше — пустые пары (None, None) до финала.
    """
    n = len(names)
    if n < 2:
        return [], n

    size = _next_pow2(n)
    total_rounds = size.bit_length() - 1
    playin = n - size // 2      # матчей в первом круге
    autos = size - n            # команд проходят сразу во второй круг

    if autos == 0:
        # Ровно степень двойки: обычный полный первый круг.
        rounds = [[(names[2 * j], names[2 * j + 1]) for j in range(size // 2)]]
    else:
        # Плей-ин: пары из нижней части списка, верхние (autos) — сразу дальше.
        rounds = [[(names[autos + 2 * j], names[autos + 2 * j + 1])
                   for j in range(playin)]]
        # Второй круг: ровно size/2 слотов = playin победителей + autos.
        slots = [None] * playin + list(names[:autos])
        rounds.append([(slots[2 * i], slots[2 * i + 1])
                       for i in range(size // 4)])

    while len(rounds) < total_rounds:
        rounds.append([(None, None)] * (size >> (len(rounds) + 1)))
    return rounds[:total_rounds], autos


def generate_first_round(tournament_id: int, teams) -> None:
    """Создаёт матчи: плей-ин (слоты 1..m1) + предсоздаёт второй круг.

    ВАЖНО: слоты плей-ина нумеруются ПОДРЯД (1, 2, 3...), а не нечётными
    числами. Правило продвижения (slot -> (slot+1)//2, чётность -> сторона A/B)
    тогда само раскладывает победителей по слотам второго круга.
    """
    names = [str(t["name"] or "Команда") for t in teams]
    by_name = {str(t["name"] or "Команда"): t for t in teams}
    layout, auto_n = _bracket_layout(names)
    if not layout:
        return

    for slot, (a, b) in enumerate(layout[0], 1):
        create_match(tournament_id, 1, slot,
                     by_name[a]["id"], by_name[b]["id"])

    # Второй круг предсоздаём только когда есть плей-ин: у степеней двойки
    # он создастся сам в _advance_winner.
    if auto_n and len(layout) > 1:
        for slot, (a, b) in enumerate(layout[1], 1):
            create_match(tournament_id, 2, slot,
                         by_name[a]["id"] if a else None,
                         by_name[b]["id"] if b else None,
                         partial=True)


def _promote_byes(tournament_id: int) -> None:
    """Продвигает автопроходы, пока появляются полностью укомплектованные пары."""
    for _ in range(12):
        byes = [m for m in get_tournament_matches(tournament_id, "main")
                if m["status"] == "bye" and m["winner_team_id"]]
        if not byes:
            break
        for m in byes:
            _advance_winner(tournament_id, m, int(m["winner_team_id"]), None, silent=True)
            # Автопроход всегда закрываем, чтобы не обрабатывать его повторно
            conn = db.get_connection()
            conn.execute("UPDATE tournament_matches SET status='done', "
                         "finished_at=CURRENT_TIMESTAMP WHERE id=?", (int(m["id"]),))
            conn.commit()
            conn.close()


def _next_slot(round_no: int, slot_no: int) -> tuple[int, int]:
    return round_no + 1, (slot_no + 1) // 2


def _advance_winner(tournament_id, match, winner_id, loser_id, silent=False) -> bool:
    """Записывает победителя в матч следующего раунда. True — если что-то изменилось."""
    init_tournament_tables()
    conn = db.get_connection()
    if match["status"] != "bye":
        conn.execute(
            "UPDATE tournament_matches SET status='done', winner_team_id=?, loser_team_id=?, "
            "finished_at=CURRENT_TIMESTAMP WHERE id=?",
            (winner_id, loser_id, int(match["id"])),
        )

    changed = False
    is_main = str(match["bracket"] or "main") == "main"
    round_no = int(match["round_no"])

    teams_cnt = conn.execute(
        "SELECT COUNT(*) c FROM tournament_teams WHERE tournament_id=?",
        (tournament_id,)).fetchone()["c"]
    total_rounds = _total_rounds(int(teams_cnt))

    # Есть ли следующий раунд — определяем по числу команд, а НЕ по числу
    # матчей в текущем раунде.
    if is_main and round_no < total_rounds:
        nr, ns = _next_slot(round_no, int(match["slot_no"]))
        nxt = conn.execute(
            "SELECT * FROM tournament_matches WHERE tournament_id=? AND bracket='main' "
            "AND round_no=? AND slot_no=?", (tournament_id, nr, ns)).fetchone()
        side = "team_a_id" if int(match["slot_no"]) % 2 == 1 else "team_b_id"
        if nxt is None:
            conn.execute(
                f"INSERT INTO tournament_matches (tournament_id, round_no, slot_no, bracket, {side}) "
                f"VALUES (?, ?, ?, 'main', ?)", (tournament_id, nr, ns, winner_id))
            changed = True
        elif not nxt[side]:
            conn.execute(f"UPDATE tournament_matches SET {side}=? WHERE id=?",
                         (winner_id, int(nxt["id"])))
            changed = True

    conn.commit()
    conn.close()
    return changed


def _ensure_third_place(tournament_id: int) -> int:
    """Создаёт матч за 3-е место, когда оба полуфинала сыграны."""
    mains = get_tournament_matches(tournament_id, "main")
    if not mains:
        return 0
    last_round = max(int(m["round_no"]) for m in mains)
    semis = [m for m in mains if int(m["round_no"]) == last_round - 1] if last_round >= 2 else []
    if len(semis) != 2 or any(m["status"] not in ("done", "bye") or not m["loser_team_id"]
                              for m in semis):
        return 0
    existing = [m for m in get_tournament_matches(tournament_id, "third")]
    if existing:
        return int(existing[0]["id"])
    return create_match(tournament_id, last_round, 1,
                        int(semis[0]["loser_team_id"]), int(semis[1]["loser_team_id"]),
                        bracket="third")


def _tournament_is_over(tournament_id: int) -> bool:
    mains = get_tournament_matches(tournament_id, "main")
    thirds = get_tournament_matches(tournament_id, "third")
    if not mains:
        return False
    last_round = max(int(m["round_no"]) for m in mains)
    final = [m for m in mains if int(m["round_no"]) == last_round]
    if len(final) != 1 or final[0]["status"] not in ("done", "bye"):
        return False
    if thirds and thirds[0]["status"] not in ("done", "bye"):
        return False
    return True


def _podium(tournament_id: int) -> str:
    mains = get_tournament_matches(tournament_id, "main")
    thirds = get_tournament_matches(tournament_id, "third")
    last_round = max(int(m["round_no"]) for m in mains)
    final = [m for m in mains if int(m["round_no"]) == last_round][0]
    lines = [
        f"🥇 1 место: <b>{hd.quote(_team_name(final['winner_team_id']))}</b>",
        f"🥈 2 место: <b>{hd.quote(_team_name(final['loser_team_id']))}</b>",
    ]
    if thirds and thirds[0]["winner_team_id"]:
        lines.append(f"🥉 3 место: <b>{hd.quote(_team_name(thirds[0]['winner_team_id']))}</b>")
    return "\n".join(lines)


def _bracket_board(tournament_id: int) -> str:
    """Текущее состояние сетки: все раунды, включая матч за 3-е место."""
    lines = []
    mains = get_tournament_matches(tournament_id, "main")
    if mains:
        last_round = max(int(m["round_no"]) for m in mains)
        for rnd in sorted({int(m["round_no"]) for m in mains}):
            in_round = [m for m in mains if int(m["round_no"]) == rnd]
            if rnd == last_round and len(in_round) == 1:
                title = "🏁 Финал"
            elif len(in_round) == 2:
                title = "🥊 Полуфиналы"
            else:
                title = f"Раунд {rnd}"
            lines.append(f"\n<b>{title}:</b>")
            for m in in_round:
                a = _team_name(m["team_a_id"], "ожидается")
                b = _team_name(m["team_b_id"], "ожидается")
                if m["status"] in ("done", "bye") and m["winner_team_id"]:
                    win = _team_name(m["winner_team_id"])
                    score = f" ({hd.quote(str(m['score_text']))})" if m["score_text"] else ""
                    lines.append(f"• {hd.quote(a)} 🆚 {hd.quote(b)} → ✅ <b>{hd.quote(win)}</b>{score}")
                else:
                    lines.append(f"• {hd.quote(a)} 🆚 {hd.quote(b)}")
    thirds = get_tournament_matches(tournament_id, "third")
    if thirds:
        m = thirds[0]
        a, b = _team_name(m["team_a_id"], "ожидается"), _team_name(m["team_b_id"], "ожидается")
        lines.append("\n<b>🥉 Матч за 3-е место:</b>")
        if m["winner_team_id"]:
            lines.append(f"• {hd.quote(a)} 🆚 {hd.quote(b)} → ✅ <b>{hd.quote(_team_name(m['winner_team_id']))}</b>")
        else:
            lines.append(f"• {hd.quote(a)} 🆚 {hd.quote(b)}")
    return "\n".join(lines)
