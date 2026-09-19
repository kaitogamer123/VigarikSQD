"""Ежедневная проверка Telegram-тегов и имён участников по их user_id."""
import logging

import aiosqlite
from aiogram import Bot
from aiogram.utils.markdown import html_decoration as hd

from config import CLAN_CHATS, INITIAL_ADMINS
from database import DB_PATH, get_all_members
from utils.roster_sync import sync_roster_msg

logger = logging.getLogger(__name__)


def _clean_username(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = str(value).strip().lstrip("@")
    return cleaned or None


async def update_telegram_identity(
    user_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
) -> None:
    """Обновляет Telegram-данные строго по user_id и устраняет конфликт старого username."""
    clean_username = _clean_username(username)
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute("PRAGMA journal_mode=WAL;")

        # username в Telegram может перейти к другому человеку. Убираем старую
        # привязку, иначе UNIQUE(username) не даст записать актуального владельца.
        if clean_username:
            await db.execute(
                """
                UPDATE members
                SET username = NULL, updated_at = datetime('now')
                WHERE user_id != ? AND LOWER(COALESCE(username, '')) = LOWER(?)
                """,
                (user_id, clean_username),
            )

        await db.execute(
            """
            UPDATE members
            SET username = ?, first_name = ?, last_name = ?, updated_at = datetime('now')
            WHERE user_id = ?
            """,
            (clean_username, first_name, last_name, user_id),
        )
        await db.commit()


async def update_just_username(user_id: int, new_username: str | None):
    """Совместимость со старыми вызовами: обновляет только username."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT first_name, last_name FROM members WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
    await update_telegram_identity(
        user_id,
        new_username,
        row["first_name"] if row else None,
        row["last_name"] if row else None,
    )


async def _get_current_user(bot: Bot, member: dict):
    """Ищет пользователя в его клановом чате, затем в остальных чатах сети."""
    user_id = int(member.get("user_id") or 0)
    if not user_id:
        return None

    preferred_clan = member.get("clan")
    clan_order = []
    if preferred_clan in (CLAN_CHATS or {}):
        clan_order.append(preferred_clan)
    clan_order.extend(key for key in (CLAN_CHATS or {}) if key not in clan_order)

    for clan_key in clan_order:
        info = (CLAN_CHATS or {}).get(clan_key) or {}
        chat_id = info.get("chat_id") if isinstance(info, dict) else None
        if not chat_id:
            continue
        try:
            chat_member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if chat_member and chat_member.user:
                return chat_member.user
        except Exception as e:
            logger.debug(f"Не удалось получить ID {user_id} из чата {clan_key}: {e}")
    return None


def _president_ids(members: list[dict]) -> set[int]:
    result = {
        int(uid)
        for uid, info in (INITIAL_ADMINS or {}).items()
        if (info or {}).get("role") == "president"
    }
    for member in members:
        if member.get("role") == "president" and member.get("user_id"):
            result.add(int(member["user_id"]))
    return result


async def _notify_presidents(
    bot: Bot,
    president_ids: set[int],
    member: dict,
    old_username: str | None,
    new_username: str | None,
) -> None:
    old_display = f"@{hd.quote(old_username)}" if old_username else "отсутствовал"
    new_display = f"@{hd.quote(new_username)}" if new_username else "удалён"
    game_nick = hd.quote(str(member.get("game_nick") or "Без игрового ника"))
    user_id = int(member.get("user_id"))
    text = (
        "🔔 Уведомление о смене Telegram-тега!\n\n"
        f"🎮 Игрок: {game_nick}\n"
        f"🆔 Telegram ID: <code>{user_id}</code>\n"
        f"Старый тег: {old_display}\n"
        f"Новый тег: {new_display}\n\n"
        "✅ Новый тег автоматически записан в базу по Telegram ID, "
        "а список клана поставлен на обновление."
    )
    for president_id in president_ids:
        try:
            await bot.send_message(president_id, text, parse_mode="HTML")
        except Exception as e:
            logger.debug(f"Не удалось уведомить президента {president_id}: {e}")


async def check_and_update_usernames(bot: Bot) -> dict:
    """
    Проверяет Telegram-теги всех участников и обновляет изменившиеся записи.
    После изменений сразу перерисовывает ростеры затронутых кланов.
    """
    logger.info("Запуск ежедневной проверки Telegram-тегов...")
    members = await get_all_members() or []
    presidents = _president_ids(members)
    affected_clans: set[str] = set()
    checked = changed = unavailable = 0

    for member in members:
        user_id = int(member.get("user_id") or 0)
        if not user_id:
            continue

        current_user = await _get_current_user(bot, member)
        if current_user is None:
            unavailable += 1
            continue

        checked += 1
        old_username = _clean_username(member.get("username"))
        new_username = _clean_username(current_user.username)
        old_first = member.get("first_name") or None
        old_last = member.get("last_name") or None
        identity_changed = (
            old_username != new_username
            or old_first != (current_user.first_name or None)
            or old_last != (current_user.last_name or None)
        )
        if not identity_changed:
            continue

        try:
            await update_telegram_identity(
                user_id=user_id,
                username=new_username,
                first_name=current_user.first_name,
                last_name=current_user.last_name,
            )
            changed += 1
            clan_key = member.get("clan")
            if clan_key in (CLAN_CHATS or {}):
                affected_clans.add(clan_key)

            if old_username != new_username:
                await _notify_presidents(
                    bot,
                    presidents,
                    member,
                    old_username,
                    new_username,
                )
                logger.info(
                    f"Telegram-тег ID {user_id} обновлён: "
                    f"{old_username or '-'} -> {new_username or '-'}"
                )
        except Exception as e:
            logger.error(f"Ошибка обновления Telegram-данных ID {user_id}: {e}")

    for clan_key in affected_clans:
        try:
            await sync_roster_msg(bot, clan_key, force=True)
        except Exception as e:
            logger.error(f"Не удалось обновить ростер {clan_key} после смены тега: {e}")

    result = {
        "checked": checked,
        "changed": changed,
        "unavailable": unavailable,
        "rosters": len(affected_clans),
    }
    logger.info(
        "Проверка Telegram-тегов завершена: "
        f"проверено={checked}, изменено={changed}, "
        f"недоступно={unavailable}, ростеров обновлено={len(affected_clans)}"
    )
    return result