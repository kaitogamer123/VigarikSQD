import sqlite3

DB_PATH = "vigarik.db"

def clean_database():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print("🧹 Начинаем очистку базы от дубликатов...")

    # 1. Создаем временную чистую таблицу, оставляя для каждого user_id
    # только 1 самую актуальную запись (где заполнен username/game_nick)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS members_clean AS
        SELECT * FROM members
        WHERE rowid IN (
            SELECT rowid FROM (
                SELECT rowid, ROW_NUMBER() OVER (
                    PARTITION BY user_id 
                    ORDER BY 
                        CASE WHEN username IS NOT NULL AND username != '' THEN 1 ELSE 2 END,
                        CASE WHEN game_nick IS NOT NULL AND game_nick != 'N/A' THEN 1 ELSE 2 END,
                        updated_at DESC
                ) as rn
                FROM members
            ) WHERE rn = 1
        );
    """)

    # Посчитаем, сколько было и сколько стало
    cursor.execute("SELECT COUNT(*) FROM members;")
    old_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM members_clean;")
    new_count = cursor.fetchone()[0]

    # 2. Очищаем основную таблицу и перезаписываем чистыми данными
    cursor.execute("DELETE FROM members;")
    cursor.execute("""
        INSERT INTO members 
        SELECT * FROM members_clean;
    """)

    # 3. Удаляем временную таблицу
    cursor.execute("DROP TABLE members_clean;")

    conn.commit()
    conn.close()

    print(f"✅ Готово! Удалено мусорных строк: {old_count - new_count}")
    print(f"📊 Было записей: {old_count} | Стало записей: {new_count}")

if __name__ == "__main__":
    clean_database()