"""Фоновая чистка дублей участников."""
import asyncio
import logging
from aiogram import Router, F, Bot
from aiogram.utils.markdown import html_decoration as hd
from utils.admin_logger import log_admin_action
from league import league_db as db

logger = logging.getLogger(__name__)
router = Router()


def _find_duplicate_memberships():
    """Возвращает список членств для снятия (игрок числится в 2+ составах)."""
    conn = db.get_connection()
    rows = conn.execute("""
        SELECT m.id AS row_id, m.user_id, m.league_id, m.slot_index, m.joined_at,
               l.name AS league_name
        FROM league_members m JOIN leagues l ON l.id = m.league_id
        WHERE m.user_id IS NOT NULL
        ORDER BY m.user_id, m.joined_at, m.id
    """).fetchall()
    conn.close()
    by_user = {}
    for r in rows:
        by_user.setdefault(int(r["user_id"]), []).append(r)
    purge, conflicts = [], []
    for uid, lst in by_user.items():
        if len(lst) < 2:
            continue
        keep = max(lst, key=lambda r: (str(r["joined_at"] or ""), int(r["row_id"])))
        for r in lst:
            if int(r["row_id"]) == int(keep["row_id"]):
                continue
            if int(r["slot_index"]) == 1:
                # лидера его же состава не трогаем — предупреждаем админов
                conflicts.append((uid, r["league_name"], keep["league_name"]))
                continue
            purge.append((int(r["row_id"]), uid, int(r["league_id"]),
                          str(r["league_name"]), str(keep["league_name"])))
    return purge, conflicts


def _apply_membership_purge(purge):
    if not purge:
        return set()
    affected = set()
    conn = db.get_connection()
    for row_id, uid, league_id, old_name, new_name in purge:
        conn.execute("DELETE FROM league_members WHERE id = ?", (row_id,))
        conn.execute("DELETE FROM league_deputies WHERE league_id = ? AND user_id = ?",
                     (league_id, uid))
        affected.add(league_id)
    conn.commit()
    conn.close()
    return affected


async def membership_janitor_task(bot: Bot) -> None:
    """Каждые 15 минут вычищает сдвоенные членства в составах."""
    await asyncio.sleep(90)
    while True:
        try:
            purge, conflicts = _find_duplicate_memberships()
            if purge:
                affected = _apply_membership_purge(purge)
                # после снятия проверяем минимум 3 участников у пострадавших составов
                for league_id in affected:
                    try:
                        from league.league_db import enforce_verification_requirement
                        enforce_verification_requirement(league_id)
                    except Exception:
                        pass
                for row_id, uid, old_name, new_name in purge:
                    logger.info(
                        f"Санитар составов: {uid} убран из «{old_name}», оставлен в «{new_name}»")
                    try:
                        await bot.send_message(
                            uid,
                            f"ℹ️ Ты числился сразу в двух составах. Оставлен в "
                            f"«{hd.quote(new_name)}» — из «{hd.quote(old_name)}» "
                            f"тебя убрали автоматически (правило: один игрок — один состав).",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
                    await asyncio.sleep(0.05)
                await log_admin_action(
                    bot=bot, admin_id=0, admin_name="system",
                    action_text=(f"🧹 Санитар составов: вычищено {len(purge)} двойных "
                                 f"членств (игрок оставлен в более позднем составе)."),
                )
            for uid, old_name, new_name in conflicts:
                await log_admin_action(
                    bot=bot, admin_id=0, admin_name="system",
                    action_text=(f"⚠️ Игрок {uid} лидер «{old_name}» и при этом числится в "
                                 f"«{new_name}». Автоматикой лидера не снял — решите вручную."),
                )
        except Exception as e:
            logger.error(f"membership janitor error: {e}")
        await asyncio.sleep(900)


@router.startup()
async def _launch_membership_janitor(bot: Bot = None, **kwargs):
    bot = bot or kwargs.get("bot")
    if bot is None:
        return
    asyncio.create_task(membership_janitor_task(bot))
    logger.info("Санитар составов (один игрок — один состав) запущен")
