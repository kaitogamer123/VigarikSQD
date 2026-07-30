"""
Модуль автоматического контроля выполнения сезонных норм пуша.
Проверяет цели (Трофеи / Лига) строго по формуле: кубки ТОП-1 / коэффициент.
"""

import logging
from aiogram import Bot
from database import get_all_members, get_push_goals
from services.api_service import get_player_profile

logger = logging.getLogger(__name__)

RANKED_LABELS = {
    1: "Бронза I", 2: "Бронза II", 3: "Бронза III",
    4: "Серебро I", 5: "Серебро II", 6: "Серебро III",
    7: "Золото I", 8: "Золото II", 9: "Золото III",
    10: "Алмаз I", 11: "Алмаз II", 12: "Алмаз III",
    13: "Мифик I", 14: "Мифик II", 15: "Мифик III",
    16: "Легенда I", 17: "Легенда II", 18: "Легенда III",
    19: "Мастер"
}


async def start_new_push_season() -> None:
    """Оставляем функцию для совместимости, фиксация стартовых кубков больше не требуется."""
    logger.info("Сезон пуша запущен (расчет идет от текущего ТОП-1 в конце сезона).")


async def check_season_results(bot: Bot) -> str:
    """
    Вызывается В КОНЦЕ сезона.
    Сверяет результаты основы с API по формуле: кубки ТОП-1 / коэффициент.
    """
    all_members = await get_all_members()
    goals = await get_push_goals()

    goals_map = {int(g["user_id"]): g["goal"] for g in goals if g.get("user_id")}
    squad_members = [m for m in all_members if m.get("clan") == "squad" and m.get("registered") == 1]

    if not squad_members:
        return "📭 В основном составе сейчас нет зарегистрированных игроков."

    # Находим ТОП-1 игрока основы по текущим кубкам (Это наш X)
    top_1_member = max(squad_members, key=lambda m: m.get("trophies", 0))
    X = top_1_member.get("trophies", 0)

    success_list = []
    failed_list = []
    unvoted_list = []

    # Заранее рассчитываем планки по вашей формуле (округляем вниз до целого)
    trophies_high_req = int(X / 1.1)  # Планка для Мифик 1
    trophies_low_req = int(X / 1.2)   # Планка для Леги 1

    league_high_req = int(X / 1.3)    # Планка для Леги 3
    league_low_req = int(X / 1.35)   # Планка для Мастера

    for m in squad_members:
        user_id = int(m["user_id"])
        tag = m.get("player_tag")
        nick = m.get("game_nick", f"ID: {user_id}")

        if not tag:
            continue

        # Не проверяем самого ТОП-1, он автоматически выполнил
        if user_id == int(top_1_member["user_id"]):
            success_list.append(f"• 👑 <b>{nick}</b> — ТОП-1 клана ({X:,} 🏆)")
            continue

        profile = await get_player_profile(tag)
        if not profile:
            failed_list.append(f"• <b>{nick}</b> — Ошибка API (профиль не доступен)")
            continue

        current_trophies = profile["trophies"]
        current_rank = profile["ranked_rank"]

        goal = goals_map.get(user_id)
        rank_text = RANKED_LABELS.get(current_rank, f"Ранг {current_rank}")

        if not goal:
            unvoted_list.append(f"• <b>{nick}</b> — Не выбрал цель на сезон")
            continue

        # ─── ПУШ ТРОФЕЕВ ───
        if goal == "trophies":
            # Условие 1: кубки >= X/1.2 И лига >= Лега 1 (16)
            # Условие 2: кубки >= X/1.1 И лига >= Мифик 1 (13)
            if current_trophies >= trophies_low_req and current_rank >= 16:
                success_list.append(f"• <b>{nick}</b> — Выполнил (Трофеи: {current_trophies:,} >= {X}/1.2, Лига: {rank_text})")
            elif current_trophies >= trophies_high_req and current_rank >= 13:
                success_list.append(f"• <b>{nick}</b> — Выполнил (Трофеи: {current_trophies:,} >= {X}/1.1, Лига: {rank_text})")
            else:
                failed_list.append(
                    f"• <b>{nick}</b> — Провал (Кубки: {current_trophies:,}, Лига: {rank_text}). "
                    f"Надо было: {trophies_low_req:,} + Лега 1 ИЛИ {trophies_high_req:,} + Мифик 1"
                )

        # ─── ПУШ ЛИГИ ───
        elif goal == "league":
            # Условие 1: лига >= Лега 3 (18) И кубки >= X/1.3
            # Условие 2: лига >= Мастер (19) И кубки >= X/1.35
            if current_rank >= 19 and current_trophies >= league_low_req:
                success_list.append(f"• <b>{nick}</b> — Выполнил (Мастер, Трофеи: {current_trophies:,} >= {X}/1.35)")
            elif current_rank >= 18 and current_trophies >= league_high_req:
                success_list.append(f"• <b>{nick}</b> — Выполнил (Легенда 3, Трофеи: {current_trophies:,} >= {X}/1.3)")
            else:
                failed_list.append(
                    f"• <b>{nick}</b> — Провал (Лига: {rank_text}, Кубки: {current_trophies:,}). "
                    f"Надо было: Лега 3 + {league_high_req:,} ИЛИ Мастер + {league_low_req:,}"
                )

    report = f"📊 <b>ИТОГИ СЕЗОНА ПУША (ОСНОВА):</b>\n"
    report += f"👑 ТОП-1 клана (X): <code>{X:,}</code> 🏆\n━━━━━━━\n"
    report += f"✅ <b>ВЫПОЛНИЛИ НОРМУ:</b>\n" + ("\n".join(success_list) if success_list else "  — нет игроков\n")
    report += f"\n\n❌ <b>НЕ ВЫПОЛНИЛИ (ШТРАФНИКИ):</b>\n" + ("\n".join(failed_list) if failed_list else "  — нет игроков\n")

    if unvoted_list:
        report += f"\n\n❓ <b>НЕ ВЫБИРАЛИ ЦЕЛЬ:</b>\n" + "\n".join(unvoted_list)

    return report
