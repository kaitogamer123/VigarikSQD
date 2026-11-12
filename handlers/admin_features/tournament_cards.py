"""Карточки турниров и публикация постов в топики/ЛС."""
import asyncio
import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.markdown import html_decoration as hd
from config import CLAN_CHATS
from league import league_db as db
from utils.notify_settings import TOURNAMENT_MUTE_HINT, is_muted
from .bracket import _bracket_text
from .bracket_image import render_bracket_image
from .common import (
    TOURNAMENT_TYPES,
    _is_duo,
    _is_solo_type,
    _is_test,
    _limit_label,
    _limit_text,
    _limit_unit,
    _players_per_team,
    _split_text,
    _test_mark,
    _tg_caption,
    _tour_name,
)
from .tournament_db import (
    get_tournament,
    get_tournament_posts,
    get_tournament_teams,
    save_tournament_post,
)

logger = logging.getLogger(__name__)


def _tournament_card(tournament, teams) -> str:
    """Пост-анонс турнира: параметры, лимит команд, список участников."""
    t_key = tournament["t_type"]
    limit = tournament["team_limit"]
    lines = [
        f"{_test_mark(tournament)}🏆 <b>АНОНС ТУРНИРА</b>",
        "",
    ]
    if _is_test(tournament):
        lines.append("⚠️ Это <b>тестовый турнир</b> для проверки системы.")
        lines.append("")
    lines.append(f"• Тип: <b>{TOURNAMENT_TYPES.get(t_key, t_key)}</b>")
    if t_key != "showdown":
        lines.append(f"• Формат матча: <b>{tournament['t_format'] or '—'}</b>")
        if t_key == "1v1":
            lines.append("• Играет: <b>1 игрок (соло)</b>")
        else:
            lines.append(f"• От команды играет: <b>{_players_per_team(t_key)}</b>")
    lines.append(f"• {_limit_label(t_key)}: <b>{_limit_text(limit, t_key)}</b>")
    lines.append(f"• Начало: <b>{hd.quote(str(tournament['starts_at']))}</b>")
    lines.append("")
    unit = _limit_unit(t_key)
    if limit is None:
        lines.append(f"📋 <b>Зарегистрировано {unit}: {len(teams)}</b> (без лимита)")
    else:
        lines.append(f"📋 <b>Зарегистрировано {unit}: {len(teams)} из {int(limit)}</b>")
    if teams:
        shown = list(teams)[:12]
        for i, t in enumerate(shown, 1):
            tag = f" [{hd.quote(str(t['tag']))}]" if t["tag"] else ""
            duo = " · <i>дуо</i>" if _is_duo(t_key, t) else ""
            lines.append(f"{i}. {hd.quote(str(t['name'] or 'Без названия'))}{tag}{duo}")
        if len(teams) > len(shown):
            lines.append(f"… и ещё {len(teams) - len(shown)}")
    else:
        lines.append("Пока никто не зарегистрировался.")
    lines.append("")
    if t_key == "showdown":
        lines.append("Участвовать может любой игрок сети — жми кнопку ниже.")
    elif t_key == "1v1":
        lines.append(
            "1V1 — соло: состав не нужен. Зарегистрироваться может любой игрок "
            "наших кланов — главное быть зарегистрированным в боте и состоять "
            "в клановом чате. Жми кнопку ниже."
        )
    elif t_key == "2v2":
        lines.append(
            "Регистрирует команду <b>лидер состава</b>. В состав может входить "
            "любой участник из трёх кланов сети.\n"
            "👥 Если в составе ровно <b>2 игрока</b> — команда помечается "
            "<b>дуо</b> (играет только вдвоём). С тремя и более игроками "
            "регистрация проходит без дополнительных проверок."
        )
    else:
        lines.append(
            "Регистрирует команду <b>лидер состава</b>. В состав может входить "
            "любой участник из трёх кланов сети."
        )
    return "\n".join(lines)


def _tournament_reg_kb(tournament_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Зарегистрироваться", callback_data=f"trn:reg:{tournament_id}")
    ]])


# Тестовые посты турниров летят ТОЛЬКО в этот топик клана Squad
# (из ссылки https://t.me/c/2187842577/405196: chat_id = -1002187842577, thread_id = 405196)
TOURNAMENT_TEST_TOPIC = {"chat_id": -1002187842577, "thread_id": 405196}


def _tournament_topics(is_test: bool = False) -> dict:
    """Топики «Турниры» по кланам.

    - Тестовый турнир: только тестовый топик Squad (TOURNAMENT_TEST_TOPIC
      или config.TOURNAMENT_TEST_TOPIC).
    - Боевой турнир: config.TOURNAMENT_TOPICS, если заданы, иначе новостные
      топики из ADMIN_NEWS_TARGETS.
    """
    import config
    if is_test:
        return {"squad": getattr(config, "TOURNAMENT_TEST_TOPIC", None) or TOURNAMENT_TEST_TOPIC}
    topics = getattr(config, "TOURNAMENT_TOPICS", None)
    if topics:
        return topics
    return getattr(config, "ADMIN_NEWS_TARGETS", {}) or {}


async def _publish_tournament_posts(bot: Bot, tournament_id: int):
    """Публикует анонс с картинкой сетки в клановые топики «Турниры»."""
    tournament = get_tournament(tournament_id)
    teams = get_tournament_teams(tournament_id)
    text = _tournament_card(tournament, teams)
    names = [str(t["name"] or "Команда") for t in teams]
    try:
        image = render_bracket_image(names, f"{_tour_name(tournament)} · предварительная сетка")
    except Exception as e:
        logger.warning(f"Сетка турнира {tournament_id} не отрисована: {e}")
        image = None
    if image is None and names:
        text += "\n\n<b>Предварительная сетка:</b>\n" + hd.quote(_bracket_text(names))

    ok, failed = [], []
    for clan_key, target in _tournament_topics(is_test=_is_test(tournament)).items():
        chat_id = target.get("chat_id")
        thread_id = target.get("thread_id")
        if not chat_id:
            continue
        try:
            if image is not None:
                from aiogram.types import BufferedInputFile
                image.seek(0)
                photo = BufferedInputFile(image.read(), filename=f"bracket_{tournament_id}.png")
                msg = await bot.send_photo(
                    chat_id=chat_id, message_thread_id=thread_id, photo=photo,
                    caption=_tg_caption(text), parse_mode="HTML",
                    reply_markup=_tournament_reg_kb(tournament_id),
                )
            else:
                parts = _split_text(text, 4000)  # текст без фото: не режем по 1024
                msg = await bot.send_message(
                    chat_id=chat_id, message_thread_id=thread_id, text=parts[0],
                    parse_mode="HTML", reply_markup=_tournament_reg_kb(tournament_id),
                )
                for extra in parts[1:]:
                    await bot.send_message(chat_id=chat_id, message_thread_id=thread_id,
                                           text=extra, parse_mode="HTML")
            save_tournament_post(tournament_id, clan_key, chat_id, thread_id, msg.message_id)
            ok.append(clan_key)
        except Exception as e:
            logger.warning(f"Не удалось опубликовать анонс турнира в {clan_key}: {e}")
            failed.append(clan_key)
    return ok, failed


async def _broadcast_tournament_dm(bot: Bot, tournament_id: int):
    """Рассылает уведомление в ЛС всем зарегистрированным игрокам сети."""
    from database import get_all_members
    tournament = get_tournament(tournament_id)
    t_key = tournament["t_type"]
    base_text = (
        "🏆 <b>Скоро турнир!</b>\n\n"
        f"• Тип: <b>{TOURNAMENT_TYPES.get(t_key, t_key)}</b>\n"
        + (f"• Формат матча: <b>{tournament['t_format'] or '—'}</b>\n" if t_key != "showdown" else "")
        + f"• {_limit_label(t_key)}: <b>{_limit_text(tournament['team_limit'], t_key)}</b>\n"
        f"• Начало: <b>{hd.quote(str(tournament['starts_at']))}</b>\n\n"
    )
    no_team_text = (
        "❗ Для участия нужно иметь <b>состав</b>. Сейчас у тебя его нет.\n"
        "Создай состав в разделе «Составы🏆» — в него может входить "
        "<b>любой участник из трёх кланов</b> сети (Squad, Academy, Events), "
        "после этого лидер сможет зарегистрировать команду на турнир."
    )
    sent = blocked = no_team = 0

    # 🧪 ТЕСТОВЫЙ ТУРНИР: массовую рассылку не делаем, пишем только создателю,
    # чтобы можно было полностью прогнать сценарий без спама игрокам.
    if _is_test(tournament):
        creator = tournament["created_by"]
        if creator:
            try:
                composition = db.get_user_league(int(creator))
            except Exception:
                composition = None
            if _is_solo_type(t_key):
                kb = _tournament_reg_kb(tournament_id)
                inline = ("1V1 — соло: можешь зарегистрироваться сам:"
                          if t_key == "1v1" else "Это соло-режим: можешь зарегистрироваться сам:")
            elif composition and int(composition["leader_id"]) == int(creator):
                kb = _tournament_reg_kb(tournament_id)
                inline = "Ты лидер состава — можешь зарегистрировать команду:"
            elif not composition:
                kb = None
                inline = no_team_text
            else:
                kb = None
                inline = "Зарегистрировать команду может только лидер состава."
            try:
                await bot.send_message(
                    int(creator),
                    "🧪 <b>ТЕСТОВЫЙ ТУРНИР</b>\n"
                    "(массовая рассылка в ЛС отключена, это сообщение видишь только ты)\n\n"
                    + base_text + inline,
                    parse_mode="HTML", reply_markup=kb,
                )
                sent = 1
            except Exception:
                blocked = 1
        return sent, blocked, no_team

    try:
        members = await get_all_members()
    except Exception as e:
        logger.error(f"Рассылка турнира: не удалось получить участников: {e}")
        return 0, 0, 0
    muted = 0
    for m in members:
        user_id = m.get("user_id")
        if not user_id or int(m.get("registered") or 0) != 1:
            continue
        # Участник мог отключить уведомления о турнирах: /notify_off tournaments
        if await is_muted(int(user_id), "tournaments"):
            muted += 1
            continue
        if _is_solo_type(t_key):
            if t_key == "1v1" and str(m.get("clan") or "") not in CLAN_CHATS:
                text = base_text + (
                    "1V1 — только для игроков наших кланов: нужно состоять "
                    "в клановом чате сети (Squad / Academy / Events)."
                )
                kb = None
                no_team += 1
            else:
                text = base_text + (
                    "1V1 — соло: состав не нужен, зарегистрируйся сам:"
                    if t_key == "1v1" else
                    "Это соло-режим: участвовать может любой игрок сети."
                )
                kb = _tournament_reg_kb(tournament_id)
        else:
            try:
                composition = db.get_user_league(int(user_id))
            except Exception:
                composition = None
            if not composition:
                text = base_text + no_team_text
                kb = None
                no_team += 1
            elif int(composition["leader_id"]) == int(user_id):
                text = base_text + "Ты лидер состава — можешь зарегистрировать команду прямо сейчас:"
                kb = _tournament_reg_kb(tournament_id)
            else:
                text = base_text + (
                    f"Твой состав: <b>{hd.quote(str(composition['name']))}</b>. "
                    "Зарегистрировать команду на турнир может только лидер состава."
                )
                kb = None
        try:
            await bot.send_message(user_id, text + TOURNAMENT_MUTE_HINT,
                                   parse_mode="HTML", reply_markup=kb)
            sent += 1
        except Exception:
            blocked += 1
        await asyncio.sleep(0.05)  # бережём лимиты Telegram
    if muted:
        logger.info(f"Рассылка турнира {tournament_id}: пропущено {muted} с /notify_off")
    return sent, blocked, no_team


async def _refresh_tournament_posts(bot: Bot, tournament_id: int):
    """Обновляет анонсы (список участников + сетка) после новой регистрации."""
    tournament = get_tournament(tournament_id)
    if tournament and str(tournament["status"]) != "registration":
        from .tournament_notify import _repost_bracket
        await _repost_bracket(bot, tournament_id)
        return
    teams = get_tournament_teams(tournament_id)
    text = _tournament_card(tournament, teams)
    names = [str(t["name"] or "Команда") for t in teams]
    try:
        image = render_bracket_image(names, f"{_tour_name(tournament)} · предварительная сетка")
    except Exception as e:
        logger.warning(f"Сетка турнира {tournament_id} не отрисована: {e}")
        image = None
    for post in get_tournament_posts(tournament_id):
        try:
            if image is not None:
                from aiogram.types import BufferedInputFile, InputMediaPhoto
                image.seek(0)
                media = InputMediaPhoto(
                    media=BufferedInputFile(image.read(), filename=f"bracket_{tournament_id}.png"),
                    caption=_tg_caption(text), parse_mode="HTML",
                )
                await bot.edit_message_media(
                    chat_id=post["chat_id"], message_id=post["message_id"], media=media,
                    reply_markup=_tournament_reg_kb(tournament_id),
                )
            else:
                await bot.edit_message_text(
                    chat_id=post["chat_id"], message_id=post["message_id"], text=text,
                    parse_mode="HTML", reply_markup=_tournament_reg_kb(tournament_id),
                )
        except Exception as e:
            logger.debug(f"Анонс турнира {tournament_id} не обновлён: {e}")
