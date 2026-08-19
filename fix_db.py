import sqlite3

db_path = "vigarik.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    cursor.execute("ALTER TABLE members ADD COLUMN ranked_elo INTEGER DEFAULT 0;")
    conn.commit()
    print("Колонка ranked_elo успешно добавлена!")
except sqlite3.OperationalError as e:
    print(f"Ошибка (возможно, колонка уже есть): {e}")
finally:
    conn.close()