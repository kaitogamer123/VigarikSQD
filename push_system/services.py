"""
Сервис для массовых рассылок, управления сезонами пуша и уведомлений игроков основы.
"""

import logging
import asyncio
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest
from aiogram.utils.markdown import html_decoration as hd

import config
from database import get_all_members, get_push_goals, clear_old_push_data
from utils.formatting import PUSH_GOAL_TEXT
from utils.keyboards import push_goal_keyboard

logger = logging.getLogger(__name__)


async def launch_push_vote(bot: Bot) -> int:
    """Запускает опрос целей сезона СТРОГО для игроков основы (squad)."""
    await clear_old_push_data()
    logger.info("Старые данные пуш-целей очищены перед новым сезоном.")

    all_members = await get_all_members()
    squad_members = [m for m in all_members if m.get("clan") == "squad" and m.get("registered") == 1]
    sent_count = 0

    for m in squad_members:
        user_id = m.get("user_id")
        if not user_id or int(user_id) <= 0:
            continue

        try:
            await bot.send_message(
                chat_id=user_id,
                text=PUSH_GOAL_TEXT,
                parse_mode="HTML",
                reply_markup=push_goal_keyboard()
            )
            sent_count += 1
            await asyncio.sleep(0.05)

        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.send_message(chat_id=user_id, text=PUSH_GOAL_TEXT, parse_mode="HTML", reply_markup=push_goal_keyboard())
                sent_count += 1
            except Exception:
                pass
        except TelegramForbiddenError:
            logger.warning(f"Игрок ID {user_id} заблокировал бота.")
        except Exception as e:
            logger.error(f"Не удалось отправить пуш-голосование {user_id}: {e}")

    return sent_count


async def get_undecided_members() -> list[dict]:
    """Возвращает список игроков ОСНОВЫ, которые еще не выбрали цель."""
    all_members = await get_all_members()
    voted_goals = await get_push_goals()

    voted_ids = {int(g["user_id"]) for g in voted_goals if g.get("user_id")}

    undecided = []
    for m in all_members:
        uid = m.get("user_id")
        if not uid or int(uid) in voted_ids or m.get("registered") != 1:
            continue

        if m.get("clan") == "squad":
            undecided.append(m)

    return undecided


async def notify_undecided_users(bot: Bot) -> int:
    """Рассылает в ЛС напоминание должникам из основы."""
    undecided = await get_undecided_members()
    remind_text = "⚠️ <b>ВНИМАНИЕ, УЧАСТНИК ОСНОВНОГО СОСТАВА!</b>\n\n" \
                  "Ты до сих пор не определился со своей целью пуша на сезон.\n" \
                  "Пожалуйста, сделай выбор немедленно через кнопку ниже! 👇"

    sent_count = 0
    for u in undecided:
        uid = int(u["user_id"])
        try:
            await bot.send_message(
                chat_id=uid,
                text=remind_text,
                parse_mode="HTML",
                reply_markup=push_goal_keyboard()
            )
            sent_count += 1
            await asyncio.sleep(0.05)
        except Exception:
            continue

    return sent_count


async def notify_clan_news(bot: Bot):
    """Публикует список должников основы в новостной топик."""
    undecided = await get_undecided_members()
    if not undecided:
        return

    targets = config.ADMIN_NEWS_TARGETS.get("squad")
    if not targets:
        return

    chat_id = targets.get("chat_id")
    thread_id = targets.get("thread_id")

    if not chat_id or not thread_id:
        return

    news_text = "🚨 <b>СПИСОК ИГРОКОВ ОСНОВЫ, НЕ ВЫБРАВШИХ ЦЕЛЬ ПУША:</b>\n\n"
    for idx, u in enumerate(undecided, 1):
        uname = u.get("username")
        mention = f"@{uname}" if uname else f"<b>{hd.quote(u['game_nick'])}</b>"
        news_text += f"  {idx}. {mention}\n"

    news_text += "\n⏱ <i>Срочно выберите цель в боте, иначе администрация примет меры!</i>"

    try:
        await bot.send_message(
            chat_id=chat_id,
            message_thread_id=thread_id,
            text=news_text,
            parse_mode="HTML"
        )
    except TelegramBadRequest as e:
        logger.error(f"Не удалось отправить теги должников в новости: {e.message}")
    except Exception as e:
        logger.error(f"Ошибка публикации пуш-новостей: {e}")
