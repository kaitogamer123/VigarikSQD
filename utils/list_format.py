"""
Генератор HTML-форматированных списков участников кланов без пингов.
С поддержкой кубков Brawl Stars API и защитой от спецсимволов.
"""

from aiogram.utils.markdown import html_decoration as hd  # ИСПРАВЛЕНО: Безопасное экранирование спецсимволов
from config import CLAN_DISPLAY, CLAN_HEADER_EMOJI, ROLE_LABELS


def format_clan_roster(clan_key: str, members: list[dict]) -> str:
    """
    Форматирует список участников клана в красивый HTML вид.
    Использует структуру из ТЗ с разделителями. Выводит кубки из API.
    """
    clan_name = CLAN_DISPLAY.get(clan_key, clan_key.capitalize())
    emoji = CLAN_HEADER_EMOJI.get(clan_key, "⭐")

    # Шапка списка
    lines = [
        f"{emoji} <b>{clan_name}</b> {emoji}\n"
    ]

    # Группируем пользователей по ролям
    roles_groups = {
        "president": [],
        "grand_vice": [],
        "vice": [],
        "veteran": [],
        "helper": [],
        "member": []
    }

    for m in members:
        role = m.get("role", "member")
        if role in roles_groups:
            roles_groups[role].append(m)

    # Порядковый номер для сквозного списка
    counter = 1

    # Отрезки иерархии
    role_order = ["president", "grand_vice", "vice", "veteran", "helper", "member"]

    for role in role_order:
        group_members = roles_groups[role]
        if not group_members:
            continue

        # Добавляем разделитель и название роли
        if role == "president":
            lines.append("† ★★★ Лидер клана ★★★ †")
        elif role == "grand_vice":
            lines.append("╭━──━─≪✠≫─━──━╮\nГранд Вице Президент")
        elif role == "vice":
            lines.append("╭━──━─≪✠≫─━──━╮\nВице Президент")
        elif role == "member":
            lines.append("╭━──━─≪✠≫─━──━╮\nУчастники клана:")
        else:
            lines.append(f"╭━──━─≪✠≫─━──━╮\n{ROLE_LABELS.get(role, role.capitalize())}")

        # Заполняем людей в текущей роли
        for m in group_members:
            raw_display_name = m.get("game_nick") or m.get("username") or m.get("first_name") or "Игрок"

            # ИСПРАВЛЕНО: Защита HTML-парсера от падений из-за никнеймов со спецсимволами
            display_name = hd.quote(str(raw_display_name))
            uid = m.get("user_id")

            # ДОБАВЛЕНО: Вытягиваем кубки из Brawl Stars API
            trophies = m.get("trophies", 0)
            trophies_str = f" — 🏆 <code>{trophies:,}</code>" if trophies > 0 else ""

            # ДОБАВЛЕНО: Автоматически выставляем медали топ-3 игрокам клана по кубкам
            if counter == 1:
                medal = "🥇 "
            elif counter == 2:
                medal = "🥈 "
            elif counter == 3:
                medal = "🥉 "
            else:
                medal = ""

            # Проверка вечных ссылок
            if uid and int(uid) > 0:
                profile_url = f"tg://user?id={uid}"
            elif m.get("username"):
                profile_url = f"https://t.me/{m['username']}"
            else:
                lines.append(f"{counter}) {medal}{display_name}{trophies_str}")
                counter += 1
                continue

            lines.append(f"{counter}) {medal}<a href='{profile_url}'>{display_name}</a>{trophies_str}")
            counter += 1

    return "\n".join(lines)
