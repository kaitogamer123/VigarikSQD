"""Запуск турнира и финальная карточка."""
import asyncio
import logging
from aiogram import Router, F, Bot
from aiogram.utils.markdown import html_decoration as hd
from league import league_db as db
from .bracket import generate_first_round
from .bracket_image import render_bracket_image
from .common import (
    TOURNAMENT_TYPES,
    _is_solo_type,
    _is_test,
    _test_mark,
    _tg_caption,
    _tour_name,
)
from .tournament_cards import _tournament_topics
from .tournament_db import (
    _team_name,
    _team_row,
    get_tournament,
    get_tournament_matches,
    get_tournament_posts,
    get_tournament_teams,
    set_tournament_status,
)
from .tournament_notify import _leader_contact, _notify_next_pairs

logger = logging.getLogger(__name__)


def _schedule_block(tournament_id: int) -> list[str]:
    """Расписание плей-ина/первого круга по данным матчей: только реальные пары,
    плюс явный список команд, проходящих сразу в следующий круг."""
    matches = get_tournament_matches(tournament_id, "main")
    r1 = [m for m in matches if int(m["round_no"]) == 1]
    lines = []
    for i, m in enumerate(r1, 1):
        a = _team_name(m["team_a_id"], "ожидается…")
        b = _team_name(m["team_b_id"], "ожидается…")
        lines.append(f"{i}. <b>{hd.quote(str(a))}</b> 🆚 <b>{hd.quote(str(b))}</b>")
    # Автопроходы: команды второго круга, которых нет в первом
    r1_ids = {int(m[k]) for m in r1 for k in ("team_a_id", "team_b_id") if m[k]}
    autos = []
    for m in matches:
        if int(m["round_no"]) == 2:
            for k in ("team_a_id", "team_b_id"):
                if m[k] and int(m[k]) not in r1_ids:
                    nm = _team_name(m[k])
                    if nm not in autos:
                        autos.append(nm)
    if autos:
        lines.append(f"⏭ Сразу в следующем круге: <b>{hd.quote(', '.join(autos))}</b>")
    if not r1:
        lines.append("Все команды начинают с первого полного круга сетки.")
    return lines


def _final_card(tournament, teams) -> str:
    """Пост с финальной сеткой и расписанием пар."""
    t_key = tournament["t_type"]
    matches = get_tournament_matches(int(tournament["id"]), "main")
    r1_cnt = len([m for m in matches if int(m["round_no"]) == 1])
    autos = max(0, len(teams) - r1_cnt * 2)
    lines = [
        f"{_test_mark(tournament)}🔥 <b>ТУРНИР {hd.quote(_tour_name(tournament))} НАЧАЛСЯ!</b>",
        "",
        f"• Тип: <b>{TOURNAMENT_TYPES.get(t_key, t_key)}</b>",
    ]
    if t_key != "showdown":
        lines.append(f"• Формат матча: <b>{tournament['t_format'] or '—'}</b>")
    lines.append(f"• Участников: <b>{len(teams)}</b>")
    if autos:
        lines.append(f"• Автопроход в следующий круг: <b>{autos}</b>")
    lines.append(f"• Старт: <b>{hd.quote(str(tournament['starts_at']))}</b>")
    lines.append("")
    lines.append("🗓 <b>Расписание первого игрового этапа:</b>")
    lines.extend(_schedule_block(int(tournament["id"])))
    lines.append("")
    lines.append("Участникам отправлены контакты соперников в ЛС. Удачи!"
                 if _is_solo_type(t_key) else
                 "Лидерам отправлены контакты соперников в ЛС. Удачи!")
    return "\n".join(lines)


async def start_tournament(bot: Bot, tournament_id: int, manual_by: int = 0):
    """Запуск турнира: финальная сетка в топиках + ЛС участникам и лидерам."""
    tournament = get_tournament(tournament_id)
    if not tournament:
        return None
    if str(tournament["status"]) != "registration":
        return None
    teams = get_tournament_teams(tournament_id)
    if len(teams) < 2:
        if set_tournament_status(tournament_id, "cancelled"):
            for t in teams:
                try:
                    await bot.send_message(
                        int(t["leader_id"]),
                        f"😔 Турнир {hd.quote(_tour_name(tournament))} отменён: "
                        f"не набралось участников (зарегистрировано {len(teams)}).",
                    )
                except Exception:
                    pass
        return {"cancelled": True, "teams": len(teams), "notified": 0}
    if not set_tournament_status(tournament_id, "running"):
        return None  # кто-то уже запустил турнир

    names = [str(t["name"] or "Команда") for t in teams]
    by_name = {str(t["name"] or "Команда"): t for t in teams}

    # Создаём матчи: плей-ин только из реальных пар + автопроходы во втором круге
    generate_first_round(tournament_id, teams)
    round1_matches = [m for m in get_tournament_matches(tournament_id, "main")
                      if int(m["round_no"]) == 1]
    autos_count = len(teams) - len(round1_matches) * 2

    # 1) Финальная сетка и расписание в топики с анонсом
    text = _final_card(tournament, teams)
    try:
        image = render_bracket_image(names, f"{'🧪 [ТЕСТ] ' if _is_test(tournament) else ''}"
                                            f"{_tour_name(tournament)} · финальная сетка",
                                     matches=get_tournament_matches(tournament_id))
    except Exception as e:
        logger.warning(f"Финальная сетка турнира {tournament_id} не отрисована: {e}")
        image = None
    posts = get_tournament_posts(tournament_id)
    for post in posts:
        try:
            if image is not None:
                from aiogram.types import BufferedInputFile, InputMediaPhoto
                image.seek(0)
                media = InputMediaPhoto(
                    media=BufferedInputFile(image.read(), filename=f"bracket_{tournament_id}.png"),
                    caption=_tg_caption(text), parse_mode="HTML",
                )
                await bot.edit_message_media(chat_id=post["chat_id"],
                                             message_id=post["message_id"],
                                             media=media, reply_markup=None)
            else:
                await bot.edit_message_text(chat_id=post["chat_id"],
                                            message_id=post["message_id"],
                                            text=text, parse_mode="HTML", reply_markup=None)
        except Exception as e:
            logger.debug(f"Не удалось обновить пост турнира {tournament_id}: {e}")
            try:
                await bot.send_message(post["chat_id"], text, parse_mode="HTML",
                                       message_thread_id=post["thread_id"])
            except Exception:
                pass
    if not posts:
        for clan_key, target in _tournament_topics(is_test=_is_test(tournament)).items():
            try:
                await bot.send_message(target.get("chat_id"), text, parse_mode="HTML",
                                       message_thread_id=target.get("thread_id"))
            except Exception:
                pass

    # 2) Персональные уведомления лидерам: соперник + контакт для связи
    fmt = tournament["t_format"] or "BO3"
    fmt_label = "Бо3" if str(fmt).upper() == "BO3" else ("Бо5" if str(fmt).upper() == "BO5" else fmt)
    # Автопроходы новой схемы: команды второго круга, которых нет в первом
    r1_ids = {int(m[k]) for m in round1_matches
              for k in ("team_a_id", "team_b_id") if m[k]}
    auto_names = []
    for m in get_tournament_matches(tournament_id, "main"):
        if int(m["round_no"]) == 2 and not (m["team_a_id"] and m["team_b_id"]):
            for k in ("team_a_id", "team_b_id"):
                if m[k] and int(m[k]) not in r1_ids:
                    row = _team_row(int(m[k]))
                    if row and str(row["name"]) not in auto_names:
                        auto_names.append(str(row["name"]))
    pairs = []
    for m in round1_matches:
        na = _team_name(m["team_a_id"]) if m["team_a_id"] else None
        nb = _team_name(m["team_b_id"]) if m["team_b_id"] else None
        if na and nb:
            pairs.append((na, nb))

    notified = 0
    for a, b in pairs:
        for mine, rival in ((a, b), (b, a)):
            if not mine:
                continue
            team = by_name.get(mine)
            if not team:
                continue
            leader_id = int(team["leader_id"])
            if leader_id <= 0:  # бот тестового турнира — в ЛС не пишем
                continue
            rival_team = by_name.get(rival)
            contact = await _leader_contact(int(rival_team["leader_id"]),
                                            str(rival_team["name"] or "")) if rival_team else "—"
            rival_tag = f" [{hd.quote(str(rival_team['tag']))}]" if rival_team and rival_team["tag"] else ""
            if _is_solo_type(tournament["t_type"]):
                msg = (
                    f"🏆 <b>Турнир {hd.quote(_tour_name(tournament))} начался!</b>\n\n"
                    f"У вас матч <b>{fmt_label}</b> против "
                    f"<b>{hd.quote(str(rival))}</b>{rival_tag}.\n"
                    f"Свяжитесь с соперником: {contact}\n\n"
                    f"После игры отправьте результат командой /result "
                    f"(фото + счёт)."
                )
            else:
                msg = (
                    f"🏆 <b>Турнир {hd.quote(_tour_name(tournament))} начался!</b>\n\n"
                    f"У вас скрим <b>{fmt_label}</b> против команды "
                    f"<b>{hd.quote(str(rival))}</b>{rival_tag}, "
                    f"спишитесь с их лидером: {contact}\n\n"
                    f"После игры отправьте результат командой /result "
                    f"(фото + счёт)."
                )
            try:
                await bot.send_message(leader_id, msg, parse_mode="HTML")
                notified += 1
            except Exception:
                pass
            await asyncio.sleep(0.05)

    # Лидерам команд с автопроходом: вы стартуете со следующего круга
    for auto_name in auto_names:
        team = by_name.get(auto_name)
        if not team or int(team["leader_id"]) <= 0:
            continue
        try:
            await bot.send_message(
                int(team["leader_id"]),
                f"🏆 <b>Турнир {hd.quote(_tour_name(tournament))} начался!</b>\n\n"
                + (
                    f"⏭ Ты <b>{hd.quote(auto_name)}</b> проходишь "
                    f"<b>сразу в следующий круг</b> (соперника на первом этапе нет). "
                    if _is_solo_type(tournament["t_type"]) else
                    f"⏭ Ваша команда <b>{hd.quote(auto_name)}</b> проходит "
                    f"<b>сразу в следующий круг</b> (соперника на первом этапе нет). "
                )
                + "Пара определится после завершения плей-ина — бот пришлёт уведомление.",
                parse_mode="HTML",
            )
            notified += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)

    # Полностью известные пары второго круга (автопроход vs автопроход)
    try:
        conn = db.get_connection()
        conn.execute("UPDATE tournament_matches SET notified = 1 "
                     "WHERE tournament_id = ? AND round_no = 1 AND bracket = 'main'",
                     (tournament_id,))
        conn.commit()
        conn.close()
        notified += await _notify_next_pairs(bot, tournament_id)
    except Exception as e:
        logger.warning(f"Не удалось уведомить пары второго круга турнира {tournament_id}: {e}")

    # 3) Уведомление остальным участникам составов
    for team in teams:
        if not team["league_id"]:
            continue
        try:
            members = db.get_league_members(int(team["league_id"]), occupied_only=True)
        except Exception:
            continue
        for m in members:
            uid = m["user_id"]
            if not uid or int(uid) == int(team["leader_id"]):
                continue
            try:
                await bot.send_message(
                    int(uid),
                    f"🏆 <b>Турнир {hd.quote(_tour_name(tournament))} начался!</b>\n\n"
                    f"Ваша команда <b>{hd.quote(str(team['name'] or 'Команда'))}</b> участвует. "
                    f"Детали пары уточните у лидера состава.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            await asyncio.sleep(0.05)

    logger.info(f"Турнир {tournament_id} запущен: {len(teams)} участников, {notified} уведомлений")
    return {"cancelled": False, "teams": len(teams), "notified": notified,
            "byes": autos_count}
