""" Сервис для массовых рассылок, управления сезонами пуша и уведомлений игроков
кланов с пуш-системой (Основа + Академия). """
import logging
import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest
from aiogram.utils.markdown import html_decoration as hd

import config
from database import get_all_members, get_push_goals, clear_old_push_data
from utils.formatting import PUSH_GOAL_TEXTS, PUSH_GOAL_TEXT
from utils.keyboards import push_goal_keyboard
from .control import record_season_start_trophies, PUSH_CLANS

logger = logging.getLogger(__name__)


def _clan_text(clan: str) -> str:
    """Текст анкеты цели по клану (fallback — текст основы)."""
    return PUSH_GOAL_TEXTS.get(clan, PUSH_GOAL_TEXT)


async def launch_push_vote(bot: Bot) -> dict:
    """Запускает опрос целей сезона для игроков кланов пуш-системы (squad + academy).
    Каждый клан получает СВОЙ текст анкеты со своими коэффициентами.
    После рассылки фиксирует стартовые трофеи сезона КАЖДОМУ игроку
    (включая тех, кто ещё не выбрал цель) — от них считается прогресс.

    Возвращает словарь вида {"squad": N, "academy": M} с числом доставленных анкет.
    """
    await clear_old_push_data()
    logger.info("Старые данные пуш-целей очищены перед новым сезоном.")
    all_members = await get_all_members()

    sent_by_clan = {}
    for clan in PUSH_CLANS:
        clan_members = [m for m in all_members
                        if m.get("clan") == clan and m.get("registered") == 1]
        sent_count = 0
        for m in clan_members:
            user_id = m.get("user_id")
            if not user_id or int(user_id) <= 0:
                continue
            try:
                await bot.send_message(
                    chat_id=int(user_id),
                    text=_clan_text(clan),
                    parse_mode="HTML",
                    reply_markup=push_goal_keyboard(),
                )
                sent_count += 1
                await asyncio.sleep(0.07)  # бережём лимиты Telegram
            except TelegramRetryAfter as e:
                # Превысили лимит Telegram — выдержим паузу и повторим.
                await asyncio.sleep(e.retry_after + 1)
                try:
                    await bot.send_message(
                        chat_id=int(user_id),
                        text=_clan_text(clan),
                        parse_mode="HTML",
                        reply_markup=push_goal_keyboard(),
                    )
                    sent_count += 1
                except Exception:
                    pass
            except (TelegramForbiddenError, TelegramBadRequest):
                pass
            except Exception as e:
                logger.warning(f"Пуш-анкета не ушла пользователю {user_id} ({clan}): {e}")
        sent_by_clan[clan] = sent_count

        # Стартовые трофеи сезона по этому клану (свой снимок на клан)
        try:
            await record_season_start_trophies(clan)
        except Exception as e:
            logger.error(f"Не удалось зафиксировать старт сезона для {clan}: {e}")

        if clan_members:
            logger.info(f"Пуш-анкета ({clan}): доставлена {sent_count} из {len(clan_members)}")

    return sent_by_clan


async def get_undecided_members() -> list[dict]:
    """Возвращает список игроков кланов пуш-системы, которые еще не выбрали цель."""
    all_members = await get_all_members()
    voted_goals = await get_push_goals()
    voted_ids = {int(g["user_id"]) for g in voted_goals if g.get("user_id")}

    undecided = []
    for m in all_members:
        uid = m.get("user_id")
        if not uid or int(uid) in voted_ids or m.get("registered") != 1:
            continue
        if m.get("clan") in PUSH_CLANS:
            undecided.append(m)
    return undecided


async def notify_undecided_users(bot: Bot) -> int:
    """Рассылает в ЛС напоминание должникам (по основе и академии)."""
    undecided = await get_undecided_members()
    remind_text = ("⚠️ ВНИМАНИЕ! \n\n"
                   "Ты до сих пор не определился со своей целью пуша на сезон.\n"
                   "Пожалуйста, сделай выбор немедленно через кнопку ниже! 👇")

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
    """Публикует список должников в новостной топик каждого клана (свой пост на клан)."""
    undecided = await get_undecided_members()
    if not undecided:
        return

    by_clan: dict[str, list[dict]] = {}
    for u in undecided:
        by_clan.setdefault(u.get("clan") or "squad", []).append(u)

    for clan, members in by_clan.items():
        targets = config.ADMIN_NEWS_TARGETS.get(clan)
        if not targets:
            continue
        chat_id = targets.get("chat_id")
        thread_id = targets.get("thread_id")
        if not chat_id or not thread_id:
            continue
        clan_title = config.CLAN_DISPLAY.get(clan, clan)
        news_text = f"🚨 СПИСОК ИГРОКОВ {hd.quote(str(clan_title)).upper()}, НЕ ВЫБРАВШИХ ЦЕЛЬ ПУША: \n\n"
        for idx, u in enumerate(members, 1):
            uname = u.get("username")
            mention = f"@{uname}" if uname else f" {hd.quote(str(u.get('game_nick') or u['user_id']))} "
            news_text += f" {idx}. {mention}\n"
        news_text += "\n⏱ Срочно выберите цель в боте, иначе администрация примет меры! "

        try:
            await bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                text=news_text,
                parse_mode="HTML"
            )
        except TelegramBadRequest as e:
            logger.error(f"Не удалось отправить теги должников в новости ({clan}): {e.message}")
        except Exception as e:
            logger.error(f"Ошибка публикации пуш-новостей ({clan}): {e}")
