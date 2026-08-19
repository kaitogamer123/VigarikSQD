import sqlite3
import os

DB_PATH = r"D:/Дима/Vigarik/bot/league/league.db"


def clean_duplicate_invites():
    if not os.path.exists(DB_PATH):
        print(f"База данных не найдена по пути: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Удаляем дубликаты приглашений
    cursor.execute("""
        DELETE FROM league_invites
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM league_invites
            GROUP BY league_id, invitee_id
        )
    """)

    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()

    print(f"✅ Успешно удалено дубликатов приглашений: {deleted_count}")


if __name__ == "__main__":
    clean_duplicate_invites()