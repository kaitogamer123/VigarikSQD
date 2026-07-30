"""
Обработчик событий в клановых чатах (вход, выход, сбор участников).
Полная синхронизация состава (Игра <-> Telegram).
"""

import logging
from aiogram import Router, F, Bot
from aiogram.types import ChatMemberUpdated, Message, User
from aiogram.utils.markdown import html_decoration as hd

from config import CLAN_CHATS, CLAN_DISPLAY
from database import get_member, upsert_member, remove_member, add_push_pending
from utils.roster_sync import sync_roster_msg

logger = logging.getLogger(__name__)
router = Router()


def detect_clan_by_chat(chat_id: int):
    for clan, data in CLAN_CHATS.items():
        if data["chat_id"] == chat_id:
            return clan
    return None


def build_user_link(user: User) -> str:
    """Генерирует кликабельную ссылку на игрока. Защищено от спецсимволов и смены юзернейма."""
    uid = user.id
    uname = user.username

    raw_name = user.first_name or uname or "Игрок"
    nick = hd.quote(str(raw_name))

    if uid and int(uid) > 0:
        return f'<a href="tg://user?id={uid}">{nick}</a>'

    if uname:
        return f"@{hd.quote(str(uname))}"

    return nick


@router.chat_member()
async def on_chat_member_update(event: ChatMemberUpdated, bot: Bot):
    chat_id = event.chat.id
    clan = detect_clan_by_chat(chat_id)

    if not clan:
        return

    user = event.new_chat_member.user
    user_id = user.id

    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status

    # ─── ВХОД В КЛАН (TELEGRAM) ─────────────────────────────
    if new_status in ("member", "administrator") and old_status in ("left", "kicked", "left_chat_member"):

        member = await get_member(user_id)

        if not member:
            await upsert_member(
                user_id=user_id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                clan=clan,
                registered=0,
            )
        else:
            await upsert_member(
                user_id=user_id,
                clan=clan,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
            )

        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"👋 Добро пожаловать в <b>{CLAN_DISPLAY.get(clan, clan).upper()}</b>, {build_user_link(user)}!\n"
                     f"Пройди регистрацию в боте через ЛС, чтобы попасть в автоматический список участников.",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"welcome msg failed: {e}")

        try:
            await bot.send_message(
                chat_id=user_id,
                text=f"👋 Ты вступил в клан <b>{CLAN_DISPLAY.get(clan, clan).upper()}</b>.\n\n"
                     f"Обязательно пройди регистрацию в боте через команду /start, указав свой тег аккаунта Brawl Stars.",
                parse_mode="HTML"
            )
        except Exception:
            pass

        if not member or member.get("registered") != 1:
            await add_push_pending(user_id)

        try:
            await sync_roster_msg(bot, clan)
        except Exception as e:
            logger.error(f"Roster sync error on member join: {e}")

    # ─── ВЫХОД ИЗ КЛАНА (TELEGRAM) ─────────────────────────
    elif new_status in ("left", "kicked"):

        member = await get_member(user_id)
        if member:
            # Мягко убираем основной аккаунт из списков клана в боте
            await upsert_member(
                user_id=user_id,
                clan=None,
                registered=0
            )

            # Проверяем, есть ли у этого игрока твинки в нашей базе данных
            import aiosqlite
            from database import DB_PATH

            twinks_in_clan = []
            async with aiosqlite.connect(DB_PATH) as db_conn:
                db_conn.row_factory = aiosqlite.Row
                # Ищем записи с таким же user_id, которые числятся в этом клане
                async with db_conn.execute(
                        "SELECT game_nick, player_tag FROM members WHERE user_id = ? AND clan = ?",
                        (user_id, clan)
                ) as cursor:
                    rows = await cur.fetchall()
                    for r in rows:
                        twinks_in_clan.append(f"• <b>{hd.quote(r['game_nick'])}</b> (<code>{r['player_tag']}</code>)")

            # Если у ливнувшего человека обнаружены твинки в этом клане — оповещаем админов!
            if twinks_in_clan:
                from utils.admin_logger import log_admin_action

                twinks_str = "\n".join(twinks_in_clan)
                tg_user = f"@{event.from_user.username}" if event.from_user.username else f"ID: {user_id}"

                # Формируем текст алерта для топика логов администрации
                alert_text = (
                    f"⚠️ <b>ВНИМАНИЕ РУКОВОДСТВУ! УЧАСТНИК ВЫШЕЛ ИЗ ЧАТА</b>\n\n"
                    f"👤 Игрок в TG: {tg_user}\n"
                    f"🏰 Клан: {CLAN_DISPLAY.get(clan, clan).upper()}\n\n"
                    f"❗ У этого пользователя в данном клане остались привязанные аккаунты/твинки:\n{twinks_str}\n\n"
                    f"👉 <u>Пожалуйста, исключите эти аккаунты из клуба внутри игры Brawl Stars!</u>"
                )

                # Отправляем сообщение в соответствующий топик логов этого клана
                try:
                    await log_admin_action(
                        bot=bot,
                        admin_id=user_id,
                        admin_name="Система Контроля",
                        action_text=alert_text,
                        clan_key=clan  # Бот сам отправит это в логи нужного клана (squad, academy или events)
                    )
                except Exception as e:
                    logger.error(f"Не удалось отправить уведомление о твинках в логи админов: {e}")

        try:
            await sync_roster_msg(bot, clan)
        except Exception as e:
            logger.error(f"Roster sync error on member leave: {e}")


# ─── ПЕРЕХВАТ СООБЩЕНИЙ ДЛЯ СБОРА УЧАСТНИКОВ ───────────────────────────────────

@router.message(F.chat.type.in_({"group", "supergroup"}))
async def on_group_message_collect_user(message: Message):
    chat_id = message.chat.id
    clan = detect_clan_by_chat(chat_id)

    if not clan:
        return

    user_id = message.from_user.id
    if message.from_user.is_bot:
        return

    member = await get_member(user_id)

    if member and member.get("registered") == 1:
        return

    if member and member.get("clan") == clan:
        return

    if not member or not member.get("game_nick"):
        await upsert_member(
            user_id=user_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            clan=clan,
            registered=0
        )
        await add_push_pending(user_id)
        logger.info(f"Игрок {user_id} актуализирован по сообщению в чате {clan} и удержан в списке без ников.")
