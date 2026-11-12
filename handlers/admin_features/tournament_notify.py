"""Уведомления участникам и переопубликование сетки."""
import asyncio
import logging
from aiogram import Router, F, Bot
from aiogram.utils.markdown import html_decoration as hd
from database import get_member, get_setting
from league import league_db as db
from .bracket import _bracket_board, _podium, _tournament_is_over
from .bracket_image import render_bracket_image
from .common import _is_test, _test_mark, _tg_caption, _tour_name
from .tournament_db import (
    _team_row,
    get_tournament,
    get_tournament_matches,
    get_tournament_posts,
    get_tournament_teams,
)

logger = logging.getLogger(__name__)


async def _leader_contact(leader_id: int) -> str:
    """Контакт лидера для связи: @username, иначе ник и ID."""
    try:
        member = await get_member(int(leader_id))
    except Exception:
        member = None
    if member:
        uname = member.get("username")
        if uname and str(uname).lower() != "unknown":
            return f"@{uname}"
        nick = member.get("game_nick") or member.get("first_name")
        if nick:
            return f"{hd.quote(str(nick))} (ID {leader_id})"
    return f"ID {leader_id}"


async def _repost_bracket(bot: Bot, tournament_id: int):
    """Обновляет пост сетки. Если редактирование невозможно (лимит 2 суток) —
    удаляет старый пост и публикует новый."""
    tournament = get_tournament(tournament_id)
    teams = get_tournament_teams(tournament_id)
    names = [str(t["name"] or "Команда") for t in teams]
    finished = _tournament_is_over(tournament_id)
    mark = _test_mark(tournament)
    pub = hd.quote(_tour_name(tournament))
    head = (f"{mark}🏁 <b>ТУРНИР {pub} ЗАВЕРШЁН</b>\n\n{_podium(tournament_id)}"
            if finished else f"{mark}🔥 <b>ТУРНИР {pub}</b> — текущая сетка")
    text = f"{head}\n{_bracket_board(tournament_id)}"
    try:
        image = render_bracket_image(names, f"{'🧪 [ТЕСТ] ' if _is_test(tournament) else ''}"
                                            f"{_tour_name(tournament)} · сетка",
                                     matches=get_tournament_matches(tournament_id))
    except Exception as e:
        logger.warning(f"Сетка турнира {tournament_id} не отрисована: {e}")
        image = None

    for post in get_tournament_posts(tournament_id):
        edited = False
        try:
            if image is not None:
                from aiogram.types import BufferedInputFile, InputMediaPhoto
                image.seek(0)
                media = InputMediaPhoto(
                    media=BufferedInputFile(image.read(), filename=f"bracket_{tournament_id}.png"),
                    caption=_tg_caption(text), parse_mode="HTML")
                await bot.edit_message_media(chat_id=post["chat_id"],
                                             message_id=post["message_id"], media=media)
            else:
                await bot.edit_message_text(chat_id=post["chat_id"], message_id=post["message_id"],
                                            text=text, parse_mode="HTML")
            edited = True
        except Exception as e:
            logger.info(f"Пост турнира {tournament_id} не отредактирован ({e}), пересоздаю")

        if edited:
            continue
        # Лимит редактирования истёк (2 суток) — удаляем старый пост и шлём новый
        try:
            await bot.delete_message(chat_id=post["chat_id"], message_id=post["message_id"])
        except Exception:
            pass
        try:
            if image is not None:
                from aiogram.types import BufferedInputFile
                image.seek(0)
                photo = BufferedInputFile(image.read(), filename=f"bracket_{tournament_id}.png")
                msg = await bot.send_photo(chat_id=post["chat_id"],
                                           message_thread_id=post["thread_id"],
                                           photo=photo, caption=_tg_caption(text), parse_mode="HTML")
            else:
                msg = await bot.send_message(chat_id=post["chat_id"],
                                             message_thread_id=post["thread_id"],
                                             text=text, parse_mode="HTML")
            conn = db.get_connection()
            conn.execute("UPDATE tournament_posts SET message_id = ? WHERE id = ?",
                         (msg.message_id, int(post["id"])))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Не удалось пересоздать пост сетки турнира {tournament_id}: {e}")


async def _notify_next_pairs(bot: Bot, tournament_id: int):
    """Старт новых пар: рассылает лидерам пары, у которых появились ОБЕ команды.

    Это и есть «автоматический старт следующего круга»: как только у пары
    определились обе команды, капитаны сразу получают уведомление с контактом
    соперника. Пара не ждёт чужих матчей — уведомляем по факту готовности.
    Каждая пара уведомляется один раз (флаг notified в tournament_matches).
    """
    tournament = get_tournament(tournament_id)
    fmt = str(tournament["t_format"] or "BO3").upper()
    fmt_label = "Бо3" if fmt == "BO3" else ("Бо5" if fmt == "BO5" else fmt)
    sent = 0
    for m in get_tournament_matches(tournament_id):
        if m["status"] != "pending" or not m["team_a_id"] or not m["team_b_id"]:
            continue
        try:
            if int(m["notified"] or 0):
                continue
        except Exception:
            pass
        stage = "🥉 матч за 3-е место" if m["bracket"] == "third" \
            else f"круг {m['round_no']}"
        for mine, rival in ((m["team_a_id"], m["team_b_id"]), (m["team_b_id"], m["team_a_id"])):
            my_team, rival_team = _team_row(mine), _team_row(rival)
            if not my_team or not rival_team:
                continue
            contact = await _leader_contact(int(rival_team["leader_id"]))
            try:
                await bot.send_message(
                    int(my_team["leader_id"]),
                    f"⚔️ <b>Турнир {hd.quote(_tour_name(tournament))}</b> — {stage}!\n\n"
                    f"У вас скрим <b>{fmt_label}</b> против команды "
                    f"<b>{hd.quote(str(rival_team['name']))}</b>, "
                    f"спишитесь с их лидером: {contact}\n\n"
                    f"После игры отправьте результат командой /result",
                    parse_mode="HTML")
                sent += 1
            except Exception:
                pass
            await asyncio.sleep(0.05)
        conn = db.get_connection()
        conn.execute("UPDATE tournament_matches SET notified = 1 WHERE id = ?",
                     (int(m["id"]),))
        conn.commit()
        conn.close()
    return sent
