import sqlite3
import os

DB_PATH = "vigarik.db"


def get_connection():
    if not os.path.exists(DB_PATH):
        print(f"❌ Файл базы данных '{DB_PATH}' не найден!")
        return None
    return sqlite3.connect(DB_PATH)


def show_all_members():
    conn = get_connection()
    if not conn: return
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, game_nick, role, clan FROM members")
    rows = cursor.fetchall()
    conn.close()

    print("\n" + "=" * 70)
    print(f"{'ID':<12} | {'USERNAME':<18} | {'GAME NICK':<18} | {'ROLE':<10} | {'CLAN'}")
    print("=" * 70)
    for r in rows:
        uid = str(r[0]) if r[0] else "N/A"
        uname = f"@{r[1]}" if r[1] else "NONE"
        gnick = r[2] if r[2] else "N/A"
        role = r[3] if r[3] else "N/A"
        clan = r[4] if r[4] else "N/A"
        print(f"{uid:<12} | {uname:<18} | {gnick:<18} | {role:<10} | {clan}")
    print("=" * 70)


def search_member():
    query = input("\nВведите ID, username или игровой ник для поиска: ").strip().lstrip("@")
    conn = get_connection()
    if not conn: return
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_id, username, game_nick, role, clan 
        FROM members 
        WHERE strftime('%s', user_id) = ? OR username LIKE ? OR game_nick LIKE ?
    """, (query, f"%{query}%", f"%{query}%"))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print("❌ Ничего не найдено.")
        return

    print("\nРезультаты поиска:")
    for r in rows:
        print(f"ID: {r[0]} | Tag: @{r[1]} | Nick: {r[2]} | Role: {r[3]} | Clan: {r[4]}")


def update_member_field():
    user_id = input("\nВведите user_id игрока, которого хотите изменить: ").strip()

    print("\nКакое поле изменить?")
    print("1. username (Тег)")
    print("2. game_nick (Игровой ник)")
    print("3. role (Роль: member / president / mod)")
    print("4. clan (Клан)")

    field_choice = input("Выбор (1-4): ").strip()
    fields_map = {"1": "username", "2": "game_nick", "3": "role", "4": "clan"}

    if field_choice not in fields_map:
        print("❌ Неверный выбор поля.")
        return

    field_name = fields_map[field_choice]
    new_value = input(f"Введите новое значение для '{field_name}': ").strip()

    if field_name == "username":
        new_value = new_value.lstrip("@") if new_value else None

    conn = get_connection()
    if not conn: return
    cursor = conn.cursor()
    cursor.execute(f"UPDATE members SET {field_name} = ?, updated_at = datetime('now') WHERE user_id = ?",
                   (new_value, user_id))
    conn.commit()

    if cursor.rowcount > 0:
        print(f"✅ Поле '{field_name}' для ID {user_id} успешно обновлено на '{new_value}'!")
    else:
        print(f"❌ Игрок с ID {user_id} не найден.")
    conn.close()


def delete_member():
    user_id = input("\n⚠️ Введите user_id игрока для УДАЛЕНИЯ: ").strip()
    confirm = input(f"Вы уверены, что хотите удалить игрока с ID {user_id}? (y/n): ").strip().lower()

    if confirm != 'y':
        print("Отмена удаления.")
        return

    conn = get_connection()
    if not conn: return
    cursor = conn.cursor()
    cursor.execute("DELETE FROM members WHERE user_id = ?", (user_id,))
    conn.commit()

    if cursor.rowcount > 0:
        print(f"✅ Игрок {user_id} удален из базы.")
    else:
        print(f"❌ Игрок с ID {user_id} не найден.")
    conn.close()


def main():
    while True:
        print("\n🛠️  МИНИ-ПО ДЛЯ РЕДАКТИРОВАНИЯ БАЗЫ VIGARIK")
        print("1. 📋 Показать всех участников")
        print("2. 🔍 Найти игрока (по ID/тегу/нику)")
        print("3. ✏️ Изменить данные игрока (username, nick, role и т.д.)")
        print("4. ❌ Удалить игрока из базы")
        print("0. 🚪 Выход")

        choice = input("\nВыберите действие (0-4): ").strip()
        if choice == "1":
            show_all_members()
        elif choice == "2":
            search_member()
        elif choice == "3":
            update_member_field()
        elif choice == "4":
            delete_member()
        elif choice == "0":
            print("Выход из программы.")
            break


if __name__ == "__main__":
    main()