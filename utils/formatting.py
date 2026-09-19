"""Безопасное форматирование ростеров и остальных сообщений бота."""
from aiogram.utils.markdown import html_decoration as hd

from config import CLAN_DISPLAY, CLAN_HEADER_EMOJI, ROLE_LABELS, ROLES


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def tg_link(user_id: int, display: str) -> str:
    """Кликабельная HTML-ссылка по постоянному Telegram ID, а не по изменяемому username."""
    uid = _safe_int(user_id, 0)
    if uid <= 0:
        return display
    return f'<a href="tg://user?id={uid}">{display}</a>'


def _member_key(member: dict) -> tuple:
    return (
        _safe_int(member.get("user_id"), 0),
        str(member.get("player_tag") or "").upper(),
    )


def format_roster(clan: str, members: list[dict]) -> str:
    """
    Формирует полный список клана. Пустые кубки и неизвестные роли не могут
    остановить обновление: неизвестная роль отображается как обычный участник.
    """
    members = [member for member in (members or []) if isinstance(member, dict)]
    emoji = CLAN_HEADER_EMOJI.get(clan, "🏰")
    title = hd.quote(str(CLAN_DISPLAY.get(clan, clan)))
    lines = [f"{emoji} <b>{title}</b> {emoji}\n"]

    role_order = sorted((ROLES or {}).keys(), key=lambda role: ROLES[role])
    if "member" not in role_order:
        role_order.append("member")
    grouped: dict[str, list[dict]] = {role: [] for role in role_order}

    for member in members:
        role = member.get("role") or "member"
        if role not in grouped:
            role = "member"
        grouped[role].append(member)

    global_top = sorted(
        members,
        key=lambda item: _safe_int(item.get("trophies"), 0),
        reverse=True,
    )
    medal_by_key = {
        _member_key(member): medal
        for member, medal in zip(global_top[:3], ("🥇 ", "🥈 ", "🥉 "))
    }

    role_headers = {
        "president": "† ★★★ Лидер клана ★★★ †",
        "grand_vice": "⊱━━━━━━━━━━━━━━━━━━━━━━⊰\n†★★★ Гранд Вице ★★★†",
        "vice": "⊱━━━━━━━━━━━━━━━━━━━━━━⊰\n†★★ Вице Президент ★★†",
        "veteran": "⊱━━━━━━━━━━━━━━━━━━━━━━⊰\n†★ Ветераны ★†",
        "helper": "⊱━━━━━━━━━━━━━━━━━━━━━━⊰\n†★ Помощники ★†",
        "member": "⊱━━━━━━━━━━━━━━━━━━━━━━⊰\nУчастники клана:",
    }

    counter = 1
    for role in role_order:
        group = grouped.get(role) or []
        if not group:
            continue
        group.sort(key=lambda item: _safe_int(item.get("trophies"), 0), reverse=True)
        lines.append(role_headers.get(role, hd.quote(str(ROLE_LABELS.get(role, role)))))

        for member in group:
            uid = _safe_int(member.get("user_id"), 0)
            nick = hd.quote(str(member.get("game_nick") or member.get("username") or "Без ника"))
            username = member.get("username")
            trophies = _safe_int(member.get("trophies"), 0)
            medal = medal_by_key.get(_member_key(member), "")

            if uid > 0:
                name = tg_link(uid, nick)
            elif username:
                clean_username = hd.quote(str(username).lstrip("@"))
                name = f'<a href="https://t.me/{clean_username}">{nick}</a>'
            else:
                name = nick

            trophies_text = f" — 🏆 {trophies:,}".replace(",", " ") if trophies > 0 else ""
            lines.append(f"{counter}. {medal}{name}{trophies_text}")
            counter += 1

    if counter == 1:
        lines.append("Список зарегистрированных участников пока пуст.")

    return "\n".join(lines)


def format_push_goal_list(members_by_clan: dict, goals_map: dict) -> str:
    """Список целей пуша, разбитый по кланам."""
    goal_emoji = {"trophies": "🏆 Трофеи", "league": "🏅 Лига"}
    lines = ["📊 Список целей на сезон"]
    for clan, members in (members_by_clan or {}).items():
        clan_title = hd.quote(str(CLAN_DISPLAY.get(clan, clan)))
        lines.append(f"\n{CLAN_HEADER_EMOJI.get(clan, '')} {clan_title}")
        for member in members or []:
            uid = member.get("user_id")
            nick = hd.quote(str(member.get("game_nick") or member.get("username") or uid))
            lines.append(f" • {nick} — {goal_emoji.get(goals_map.get(uid), '❓ Не определился')}")
    return "\n".join(lines)


def welcome_text(member: dict, clan: str) -> str:
    """Приветствие зарегистрированного участника."""
    role_label = ROLE_LABELS.get(member.get("role", "member"), "Участник")
    clan_title = CLAN_DISPLAY.get(clan, clan)
    username = member.get("username")
    address = f"@{hd.quote(str(username))}" if username else hd.quote(str(member.get("first_name") or "Игрок"))
    return (
        f"Привет, {address} 👋\n\n"
        f"Ты — {role_label} клана {clan_title}.\n"
        "Это приветственное сообщение бота ViGarik Squad 🎮"
    )


PUSH_GOAL_TEXT = """🎯 Определи свою цель на этот сезон!

🏆 Вариант 1 — Пуш трофеев:
• Цель: трофеи × 1.2 от топ-1 участника клана
• Лига: минимум Лега 1
• Если не хочешь пушить лигу — пуш трофеев × 1.1 + ранг минимум Мифик

🏅 Вариант 2 — Пуш лиги:
• Цель: минимум Лега 3 к концу сезона
• Трофеи × 1.3 от топ-1 участника клана
• Если не хочешь пушить кубки — минимум Мастер 1 + трофеи × 1.35

⏱ Изменить решение можно в течение 2 дней после выбора.
Выбери свой вариант 👇"""