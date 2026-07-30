import sqlite3

conn = sqlite3.connect("vigarik.db")
cursor = conn.cursor()

# 1. Посмотри структуру таблицы
cursor.execute("PRAGMA table_info(members);")
print("Структура таблицы:", cursor.fetchall())

# 2. Посмотри, как записан игрок с ID 6519735751 (из вашего скриншота)
cursor.execute("SELECT * FROM members WHERE user_id = 6519735751;")
print("Запись игрока:", cursor.fetchall())

conn.close()