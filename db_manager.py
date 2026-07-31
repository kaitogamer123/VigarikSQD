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
    cursor.execute("SELECT user_id, username, game_nick, role, clan, player_tag, registered FROM members")
    rows = cursor.fetchall()
    conn.close()

    print("\n" + "=" * 105)
    print(f"{'ID':<12} | {'USERNAME':<16} | {'GAME NICK':<16} | {'ROLE':<10} | {'CLAN':<10} | {'TAG':<12} | {'REG'}")
    print("=" * 105)
    for r in rows:
        uid = str(r[0]) if r[0] else "N/A"
        uname = f"@{r[1]}" if r[1] else "NONE"
        gnick = r[2] if r[2] else "N/A"
        role = r[3] if r[3] else "N/A"
        clan = r[4] if r[4] else "N/A"
        ptag = r[5] if r[5] else "N/A"
        reg = str(r[6]) if r[6] is not None else "0"
        print(f"{uid:<12} | {uname:<16} | {gnick:<16} | {role:<10} | {clan:<10} | {ptag:<12} | {reg}")
    print("=" * 105)


def search_member():
    query = input("\nВведите ID, username, игровой ник или тег для поиска: ").strip().lstrip("@")
    conn = get_connection()
    if not conn: return
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_id, username, game_nick, role, clan, player_tag, registered 
        FROM members 
        WHERE user_id LIKE ? OR username LIKE ? OR game_nick LIKE ? OR player_tag LIKE ?
    """, (f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%"))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print("❌ Ничего не найдено.")
        return

    print("\nРезультаты поиска:")
    for r in rows:
        uid = str(r[0]) if r[0] else "N/A"
        uname = f"@{r[1]}" if r[1] else "NONE"
        gnick = r[2] if r[2] else "N/A"
        role = r[3] if r[3] else "N/A"
        clan = r[4] if r[4] else "N/A"
        ptag = r[5] if r[5] else "N/A"
        reg = str(r[6]) if r[6] is not None else "0"
        print(f"ID: {uid} | Tag: {uname} | Nick: {gnick} | Role: {role} | Clan: {clan} | BS Tag: {ptag} | Reg: {reg}")

def add_member():
    print("\n➕ ДОБАВЛЕНИЕ НОВОГО УЧАСТНИКА")
    user_id = input("Введите Telegram user_id: ").strip()
    username = input("Введите Telegram username (без @, можно пропустить): ").strip().lstrip("@")
    game_nick = input("Введите игровой ник: ").strip()
    player_tag = input("Введите тег Brawl Stars (например, #9VJGR8VV8V): ").strip().upper()
    role = input("Введите роль (например, member / president / helper): ").strip() or "member"
    clan = input("Введите название клана (например, squad / academy): ").strip()
    reg_input = input("Статус регистрации (1 - активен в списках, 0 - нет) [по умолчанию 1]: ").strip()
    registered = int(reg_input) if reg_input in ("0", "1") else 1

    conn = get_connection()
    if not conn: return
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT OR REPLACE INTO members (user_id, username, game_nick, player_tag, role, clan, registered, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (user_id, username if username else None, game_nick, player_tag, role, clan, registered))
        conn.commit()
        print(f"✅ Участник {game_nick} (ID: {user_id}) успешно добавлен/обновлен в базе!")
    except Exception as e:
        print(f"❌ Ошибка при добавлении: {e}")
    conn.close()


def update_member_field():
    user_id = input("\nВведите user_id игрока, которого хотите изменить: ").strip()

    print("\nКакое поле изменить?")
    print("1. username (Телеграм юзернейм)")
    print("2. game_nick (Игровой ник)")
    print("3. player_tag (Тег Brawl Stars)")
    print("4. role (Роль)")
    print("5. clan (Клан)")
    print("6. registered (Статус верификации: 1 или 0)")

    field_choice = input("Выбор (1-6): ").strip()
    fields_map = {
        "1": "username",
        "2": "game_nick",
        "3": "player_tag",
        "4": "role",
        "5": "clan",
        "6": "registered"
    }

    if field_choice not in fields_map:
        print("❌ Неверный выбор поля.")
        return

    field_name = fields_map[field_choice]
    new_value = input(f"Введите новое значение для '{field_name}': ").strip()

    if field_name == "username":
        new_value = new_value.lstrip("@") if new_value else None
    elif field_name == "player_tag":
        new_value = new_value.upper()
    elif field_name == "registered":
        new_value = int(new_value) if new_value in ("0", "1") else 1

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
        print("\n🛠️ МИНИ-ПАНЕЛЬ БАЗЫ ДАННЫХ VIGARIK")
        print("1. 📋 Показать всех участников")
        print("2. 🔍 Найти игрока (по ID/тегу/нику)")
        print("3. ➕ Добавить нового участника вручную")
        print("4. ✏️ Изменить данные игрока")
        print("5. ❌ Удалить игрока из базы")
        print("0. 🚪 Выход из панели")

        choice = input("\nВыберите действие (0-5): ").strip()
        if choice == "1":
            show_all_members()
        elif choice == "2":
            search_member()
        elif choice == "3":
            add_member()
        elif choice == "4":
            update_member_field()
        elif choice == "5":
            delete_member()
        elif choice == "0":
            print("Выход.")
            break


if __name__ == "__main__":
    main()