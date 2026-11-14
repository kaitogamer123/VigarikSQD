"""Общие состояния и помощники системы составов."""
import sqlite3
from aiogram.fsm.state import State, StatesGroup


PERMISSION_LABELS = {
    "can_review_apps": "📋 Работа с заявками",
    "can_invite": "➕ Приглашение игроков",
    "can_kick": "🚪 Исключение участников",
    "can_toggle_open": "🔒 Управление набором",
}


class LeagueStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_tag = State()
    waiting_invite_target = State()
    waiting_apply_text = State()
    waiting_lft_comment = State()


def _main_member(user_id: int):
    conn = sqlite3.connect("vigarik.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM members WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row


def _fill_slot(conn, league_id: int, user_id: int) -> bool:
    if conn.execute("SELECT 1 FROM league_members WHERE user_id = ?", (user_id,)).fetchone():
        return False
    slot = conn.execute(
        "SELECT * FROM league_members WHERE league_id = ? AND user_id IS NULL ORDER BY slot_index LIMIT 1",
        (league_id,),
    ).fetchone()
    if not slot:
        return False
    user = _main_member(user_id)
    conn.execute("""
        UPDATE league_members SET user_id = ?, game_nick = ?, player_tag = ?, username = ?,
            role = 'участник', trophies_record = ?, joined_at = CURRENT_TIMESTAMP WHERE id = ?
    """, (
        user_id,
        user["game_nick"] if user and user["game_nick"] else "Игрок",
        user["player_tag"] if user and user["player_tag"] else "#N/A",
        user["username"] if user and user["username"] else "",
        user["trophies"] if user and "trophies" in user.keys() else 0,
        slot["id"],
    ))
    try:
        conn.execute(
            "UPDATE lft_searches SET is_active = 0, updated_at = CURRENT_TIMESTAMP "
            "WHERE user_id = ?", (user_id,))
    except Exception:
        pass

    # Игрок вступил — его прежние заявки/приглашения больше не нужны.
    conn.execute("DELETE FROM league_applications WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM league_invites WHERE invitee_id = ?", (user_id,))

    # Состав стал полным: закрываем набор и убираем входящие заявки
    # и исходящие приглашения этого состава.
    free = conn.execute(
        "SELECT COUNT(*) c FROM league_members WHERE league_id = ? AND user_id IS NULL",
        (league_id,),
    ).fetchone()["c"]
    if int(free) == 0:
        conn.execute("UPDATE leagues SET is_open = 0 WHERE id = ?", (league_id,))
        conn.execute("DELETE FROM league_applications WHERE league_id = ?", (league_id,))
        conn.execute("DELETE FROM league_invites WHERE league_id = ?", (league_id,))
    return True
