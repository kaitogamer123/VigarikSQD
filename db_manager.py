import sqlite3
import os
import asyncio
import aiohttp

DB_PATH = "vigarik.db"


def get_connection():
    if not os.path.exists(DB_PATH):
        print(f"❌ Файл базы данных '{DB_PATH}' не найден!")
        return None
    return sqlite3.connect(DB_PATH)


async def fetch_player_from_api(player_tag: str):
    """Асинхронно забирает ник и кубки из официального API Brawl Stars."""
    clean_tag = player_tag.strip().upper().replace("#", "")
    # Берем токен из config.py, если он там есть
    try:
        from config import BS_API_TOKEN
        headers = {"Authorization": f"Bearer {BS_API_TOKEN}"}
    except ImportError:
        headers = {}

    url = f"https://api.brawlstars.com/v1/players/%23{clean_tag}"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        "name": data.get("name"),
                        "trophies": data.get("trophies", 0),
                        "tag": f"#{clean_tag}"
                    }
                else:
                    print(f"⚠️ Ошибка API Brawl Stars: статус {response.status}")
        except Exception as e:
            print(f"⚠️ Не удалось подключиться к API: {e}")
    return None


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
    print("\n➕ ДОБАВЛЕНИЕ НОВОГО УЧАСТНИКА ПО ТЕГУ ИЗ API")
    user_id = input("Введите Telegram user_id: ").strip()
    username = input("Введите Telegram username (без @, можно пропустить): ").strip().lstrip("@")
    player_tag = input("Введите тег Brawl Stars (например, #9VJGR8VV8V): ").strip()
    role = input("Введите роль (например, member / president / helper): ").strip() or "member"
    clan = input("Введите название клана (например, squad / academy): ").strip()
    reg_input = input("Статус регистрации (1 - активен в списках, 0 - нет) [по умолчанию 1]: ").strip()
    registered = int(reg_input) if reg_input in ("0", "1") else 1

    print("⏳ Запрашиваем данные игрока из Brawl Stars API...")
    api_data = asyncio.run(fetch_player_from_api(player_tag))

    if api_data:
        game_nick = api_data["name"]
        trophies = api_data["trophies"]
        clean_tag = api_data["tag"]
        print(f"✅ Найдено в API! Ник: {game_nick} | Кубки: {trophies}")
    else:
        print("⚠️ Не удалось получить данные из API (тег введен верно?). Записываем без авто-ника.")
        game_nick = "Игрок"
        trophies = 0
        clean_tag = player_tag.upper()

    conn = get_connection()
    if not conn: return
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT OR REPLACE INTO members 
            (user_id, username, game_nick, player_tag, trophies, role, clan, registered, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (user_id, username if username else None, game_nick, clean_tag, trophies, role, clan, registered))
        conn.commit()
        print(f"✅ Участник {game_nick} (ID: {user_id}) успешно сохранен в базе с актуальным ником и кубками!")
    except Exception as e:
        print(f"❌ Ошибка при добавлении: {e}")
    conn.close()


def update_member_field():
    user_id = input("\nВведите user_id игрока, которого хотите изменить: ").strip()

    print("\nКакое поле изменить?")
    print("1. username (Телеграм юзернейм)")
    print("2. game_nick (Игровой ник)")
    print("3. player_tag (Тег Brawl Stars - подтянет ник и кубки из API)")
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
    elif field_name == "registered":
        new_value = int(new_value) if new_value in ("0", "1") else 1

    conn = get_connection()
    if not conn: return
    cursor = conn.cursor()

    # Особенность: если меняем тег через пункт 3, автоматически обновляем и ник с кубками из API!
    if field_name == "player_tag":
        clean_tag = new_value.upper()
        print("⏳ Запрашиваем новые данные из Brawl Stars API...")
        api_data = asyncio.run(fetch_player_from_api(clean_tag))

        if api_data:
            cursor.execute("""
                UPDATE members 
                SET player_tag = ?, game_nick = ?, trophies = ?, updated_at = datetime('now') 
                WHERE user_id = ?
            """, (api_data["tag"], api_data["name"], api_data["trophies"], user_id))
            print(
                f"✅ Тег обновлен, а ник автоматически сменен на '{api_data['name']}' ({api_data['trophies']} кубков)!")
        else:
            cursor.execute("UPDATE members SET player_tag = ?, updated_at = datetime('now') WHERE user_id = ?",
                           (clean_tag, user_id))
            print("⚠️ Тег обновлен, но из API данные не подтянулись.")
    else:
        cursor.execute(f"UPDATE members SET {field_name} = ?, updated_at = datetime('now') WHERE user_id = ?",
                       (new_value, user_id))

    conn.commit()
    if cursor.rowcount > 0:
        print(f"✅ Данные для ID {user_id} успешно обновлены!")
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
        print("3. ➕ Добавить нового участника по тегу (авто-ник из API)")
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