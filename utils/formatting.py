"""
Форматирование сообщений и списков участников с поддержкой кубков Brawl Stars API.
Полная синхронизация сортировки должностей и трофеев.
"""

from aiogram.utils.markdown import html_decoration as hd
from config import CLAN_DISPLAY, CLAN_HEADER_EMOJI, ROLE_LABELS, ROLES


def tg_link(user_id: int, display: str) -> str:
    """HTML-ссылка на профиль через tg://user?id= (не пингует)."""
    if not user_id:
        return display
    return f'<a href="tg://user?id={user_id}">{display}</a>'


def format_roster(clan: str, members: list[dict]) -> str:
    """
    Форматирует список участников клана в красивый HTML с выводом кубков.
    Сортирует участников по кубкам строго внутри каждой роли.
    """
    emoji = CLAN_HEADER_EMOJI.get(clan, "🏰")
    title = CLAN_DISPLAY.get(clan, clan)

    lines = [f"{emoji} <b>{title}</b> {emoji}\n"]

    # 1. Порядок вывода ролей сверху вниз (по весам из ROLES в конфиге)
    role_order = sorted(ROLES.keys(), key=lambda r: ROLES[r])
    grouped: dict[str, list] = {r: [] for r in role_order}

    # Распределяем участников по ролям
    for m in members:
        role = m.get("role", "member")
        grouped.setdefault(role, []).append(m)

    # 2. ОПРЕДЕЛЯЕМ ТОП-3 ВСЕГО КЛАНА ПО КУБКАМ ДЛЯ ВЫДАЧИ МЕДАЛЕЙ
    # Сортируем абсолютно всех игроков клана по трофеям (от большего к меньшему)
    global_top_by_trophies = sorted(members, key=lambda m: m.get("trophies", 0), reverse=True)
    # Берем теги или ID топ-3 игроков, чтобы узнать их в лицо при генерации списка
    top_1_id = global_top_by_trophies[0].get("player_tag") if len(global_top_by_trophies) > 0 else None
    top_2_id = global_top_by_trophies[1].get("player_tag") if len(global_top_by_trophies) > 1 else None
    top_3_id = global_top_by_trophies[2].get("player_tag") if len(global_top_by_trophies) > 2 else None

    counter = 1
    role_headers = {
        "president":  "† ★★★ Лидер клана ★★★ †",
        "grand_vice": "⊱━━━━━━━━━━━━━━━━━━━━━━⊰\n†★★★ Гранд Вице ★★★†",
        "vice":       "⊱━━━━━━━━━━━━━━━━━━━━━━⊰\n†★★ Вице Президент ★★†",
        "veteran":    "⊱━━━━━━━━━━━━━━━━━━━━━━⊰\n†★Ветераны★†",
        "helper":     "⊱━━━━━━━━━━━━━━━━━━━━━━⊰\n†★Помощники★†",
        "member":     "⊱━━━━━━━━━━━━━━━━━━━━━━⊰\nУчастники клана:",
    }

    for role in role_order:
        group = grouped.get(role, [])
        if not group:
            continue

        # ИСПРАВЛЕНО: Сортируем участников внутри этой роли по кубкам (от большего к меньшему)
        group = sorted(group, key=lambda m: m.get("trophies", 0), reverse=True)

        lines.append(role_headers.get(role, ""))
        for m in group:
            uid = m.get("user_id")
            raw_nick = m.get("game_nick") or "—"
            ptag = m.get("player_tag")

            # Безопасное экранирование ников
            nick = hd.quote(str(raw_nick))
            uname = m.get("username")

            # Вытягиваем кубки из API
            trophies = m.get("trophies", 0)
            trophies_str = f" — 🏆 <code>{trophies:,}</code>" if trophies > 0 else ""

            # ИСПРАВЛЕНО: Медали выдаются честно за глобальный топ по кубкам во всем клане!
            if ptag and ptag == top_1_id:
                medal = "🥇 "
            elif ptag and ptag == top_2_id:
                medal = "🥈 "
            elif ptag and ptag == top_3_id:
                medal = "🥉 "
            else:
                medal = ""

            # Безопасная генерация ссылок на Telegram профили
            if uid and int(uid) > 0:
                name_link = tg_link(uid, nick)
            elif uname:
                name_link = f'<a href="tg://resolve?domain={uname}">{nick}</a>'
            else:
                name_link = nick

            lines.append(f"{counter}. {medal}{name_link}{trophies_str}")
            counter += 1

    return "\n".join(lines)


def format_push_goal_list(members_by_clan: dict, goals_map: dict) -> str:
    """Список «кто что пушит» разбитый по кланам с экранированием имен."""
    goal_emoji = {"trophies": "🏆 Трофеи", "league": "🏅 Лига"}
    lines = ["<b>📊 Список целей на сезон</b>\n"]

    for clan, members in members_by_clan.items():
        clan_title = CLAN_DISPLAY.get(clan, clan)
        lines.append(f"\n{CLAN_HEADER_EMOJI.get(clan,'')} <b>{clan_title}</b>")
        for m in members:
            uid = m.get("user_id")
            raw_nick = m.get("game_nick") or m.get("username") or str(uid)
            nick = hd.quote(str(raw_nick))

            goal = goals_map.get(uid) if uid else None
            goal_str = goal_emoji.get(goal, "❓ Не определился")
            lines.append(f"  • {nick} — {goal_str}")

    return "\n".join(lines)


def welcome_text(member: dict, clan: str) -> str:
    """Генерация приветственного текста для новых пользователей."""
    role_label = ROLE_LABELS.get(member.get("role", "member"), "Участник")
    clan_title = CLAN_DISPLAY.get(clan, clan)
    uname = member.get("username")

    if uname:
        address = f"@{hd.quote(str(uname))}"
    else:
        address = hd.quote(str(member.get("first_name", "")))

    return (
        f"Привет, {address} 👋\n\n"
        f"Ты — <b>{role_label}</b> клана <b>{clan_title}</b>.\n"
        f"Это приветственное сообщение бота ViGarik Squad 🎮"
    )
PUSH_GOAL_TEXT = """🎯 <b>Определи свою цель на этот сезон!</b>

<b>🏆 Вариант 1 — Пуш трофеев:</b>
• Цель: трофеи × 1.2 от топ-1 участника клана
• Лига: минимум <b>Лега 1</b>
• Если не хочешь пушить лигу — пуш трофеев × 1.1 + ранг <b>минимум Мифик</b>
  (штрафа не будет)

<b>🏅 Вариант 2 — Пуш лиги:</b>
• Цель: минимум <b>Лега 3</b> к концу сезона
• Трафеи × 1.3 от топ-1 участника клана
• Если не хочешь пушить кубки — <b>минимум Мастер 1</b> + трофеи × 1.35
  (штрафа не будет)

⏱ Изменить решение можно в течение <b>2 дней</b> после выбора.

Выбери свой вариант 👇"""
