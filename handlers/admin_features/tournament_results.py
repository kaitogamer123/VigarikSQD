"""Приём и подтверждение результатов матчей."""
import asyncio
import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.markdown import html_decoration as hd
from aiogram.fsm.context import FSMContext
from utils.admin_logger import log_admin_action
from league import league_db as db
from .bracket import (
    _advance_winner,
    _ensure_third_place,
    _podium,
    _promote_byes,
    _tournament_is_over,
)
from .common import _cb_is_admin, _tg_caption, _tour_name
from .states import TournamentResultStates
from .tournament_db import (
    _find_user_pending_match,
    _m_get,
    _team_name,
    _team_row,
    get_tournament,
    get_tournament_teams,
    set_tournament_status,
)
from .tournament_notify import _leader_contact, _notify_next_pairs, _repost_bracket

logger = logging.getLogger(__name__)
router = Router()


@router.message(F.text.in_({"/result", "/результат"}))
async def tournament_result_start(message: Message, state: FSMContext):
    """Лидер отправляет результат матча: фото + текст."""
    match = _find_user_pending_match(message.from_user.id)
    if not match:
        await message.answer("📭 Сейчас у тебя нет активных турнирных матчей, "
                             "по которым нужно сдать результат.")
        return
    a, b = _team_name(match["team_a_id"]), _team_name(match["team_b_id"])
    await state.set_state(TournamentResultStates.waiting_result)
    await state.update_data(result_match_id=int(match["id"]))
    await message.answer(
        f"📸 <b>Результат матча</b>\n\n"
        f"Матч: <b>{hd.quote(a)}</b> 🆚 <b>{hd.quote(b)}</b>\n\n"
        f"Отправь <b>фотографию</b> с результатом игры и подписью к ней "
        f"(например: «2:1, победили мы»).\n"
        f"Можно прислать несколько фото — затем нажми «Готово».\n"
        f"Для отмены отправь /cancel.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Готово, отправить админам",
                                 callback_data="trnres:send")]]),
    )


@router.message(TournamentResultStates.waiting_result, F.text.startswith("/"))
async def tournament_result_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отправка результата отменена.")


@router.message(TournamentResultStates.waiting_result, F.photo)
async def tournament_result_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = list(data.get("result_photos") or [])
    photos.append(message.photo[-1].file_id)
    note = data.get("result_note") or ""
    if message.caption:
        note = (note + "\n" + message.caption).strip()
    await state.update_data(result_photos=photos, result_note=note)
    await message.answer(
        f"📸 Скриншот №{len(photos)} принят. Пришли ещё или нажми «Готово».",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Готово, отправить админам",
                                 callback_data="trnres:send")]]),
    )


@router.message(TournamentResultStates.waiting_result, F.text)
async def tournament_result_text(message: Message, state: FSMContext):
    data = await state.get_data()
    note = ((data.get("result_note") or "") + "\n" + message.text).strip()
    await state.update_data(result_note=note)
    await message.answer(
        "📝 Комментарий сохранён. Пришли фото результата или нажми «Готово».",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Готово, отправить админам",
                                 callback_data="trnres:send")]]),
    )


@router.callback_query(F.data == "trnres:send")
async def tournament_result_send(call: CallbackQuery, state: FSMContext, bot: Bot):
    """Отправляет заявку с результатом в чат администрации."""
    import json
    data = await state.get_data()
    match_id = int(data.get("result_match_id") or 0)
    match = _m_get(match_id)
    if not match or match["status"] != "pending":
        await state.clear()
        await call.answer("Матч уже закрыт", show_alert=True)
        return
    photos = list(data.get("result_photos") or [])
    note = (data.get("result_note") or "").strip()
    if not photos and not note:
        await call.answer("Сначала пришли фото или текст результата", show_alert=True)
        return
    await state.clear()

    team_a, team_b = _team_row(match["team_a_id"]), _team_row(match["team_b_id"])
    reporter_team = team_a if team_a and int(team_a["leader_id"]) == call.from_user.id else team_b

    conn = db.get_connection()
    cur = conn.execute(
        "INSERT INTO tournament_results (match_id, tournament_id, reporter_id, reporter_team_id, "
        "text_note, photos) VALUES (?, ?, ?, ?, ?, ?)",
        (match_id, int(match["tournament_id"]), call.from_user.id,
         int(reporter_team["id"]) if reporter_team else None, note, json.dumps(photos)),
    )
    conn.commit()
    result_id = int(cur.lastrowid)
    conn.close()

    name_a = str(team_a["name"] if team_a else "A")
    name_b = str(team_b["name"] if team_b else "B")
    stage = "🥉 матч за 3-е место" if match["bracket"] == "third" else f"раунд {match['round_no']}"
    caption = (
        f"📨 <b>Результат турнирного матча</b>\n\n"
        f"Турнир #{match['tournament_id']} · {stage}\n"
        f"Матч: <b>{hd.quote(name_a)}</b> 🆚 <b>{hd.quote(name_b)}</b>\n"
        f"Отправил: {await _leader_contact(call.from_user.id)} "
        f"(команда {hd.quote(str(reporter_team['name'] if reporter_team else '—'))})\n\n"
        f"📝 Комментарий: {hd.quote(note) if note else '—'}\n\n"
        f"Кто победил?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Да, победил {name_a[:20]}",
                              callback_data=f"trnres:win:{result_id}:{match['team_a_id']}")],
        [InlineKeyboardButton(text=f"❌ Нет, победил {name_b[:20]}",
                              callback_data=f"trnres:win:{result_id}:{match['team_b_id']}")],
        [InlineKeyboardButton(text="🚫 Отклонить заявку", callback_data=f"trnres:rej:{result_id}")],
    ])
    import config as _cfg
    admin_chat = _cfg.ADMIN_CHAT_ID
    try:
        if photos:
            from aiogram.types import InputMediaPhoto
            if len(photos) > 1:
                await bot.send_media_group(admin_chat,
                                           [InputMediaPhoto(media=p) for p in photos[:10]])
                await bot.send_message(admin_chat, caption, parse_mode="HTML", reply_markup=kb)
            else:
                await bot.send_photo(admin_chat, photos[0], caption=_tg_caption(caption),
                                     parse_mode="HTML", reply_markup=kb)
        else:
            await bot.send_message(admin_chat, caption, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.error(f"Не удалось отправить результат {result_id} админам: {e}")
        await call.message.answer("❌ Не удалось связаться с администрацией. Сообщи админу вручную.")
        await call.answer()
        return
    await call.message.edit_text(
        "✅ Результат отправлен администрации на подтверждение. "
        "Как только админ подтвердит — сетка обновится.", reply_markup=None)
    await call.answer("Отправлено")


@router.callback_query(F.data.startswith("trnres:win:"))
async def tournament_result_confirm(call: CallbackQuery, bot: Bot):
    """Админ подтверждает победителя → продвижение по сетке и обновление постов."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    _, _, result_id, winner_id = call.data.split(":")
    result_id, winner_id = int(result_id), int(winner_id)

    conn = db.get_connection()
    res = conn.execute("SELECT * FROM tournament_results WHERE id = ?", (result_id,)).fetchone()
    conn.close()
    if not res or res["status"] != "pending":
        await call.answer("Заявка уже обработана", show_alert=True)
        return
    match = _m_get(int(res["match_id"]))
    if not match or match["status"] != "pending":
        await call.answer("Матч уже закрыт", show_alert=True)
        return

    loser_id = int(match["team_b_id"]) if winner_id == int(match["team_a_id"]) \
        else int(match["team_a_id"])
    tournament_id = int(match["tournament_id"])

    conn = db.get_connection()
    conn.execute("UPDATE tournament_results SET status='accepted', decided_by=?, "
                 "decided_at=CURRENT_TIMESTAMP WHERE id=?", (call.from_user.id, result_id))
    conn.execute("UPDATE tournament_matches SET score_text=? WHERE id=?",
                 (str(res["text_note"] or "")[:60], int(match["id"])))
    conn.commit()
    conn.close()

    admin_name = call.from_user.username or call.from_user.first_name or "admin"
    await _apply_confirmed_result(bot, tournament_id, match, winner_id, loser_id,
                                  str(res["text_note"] or "")[:60], admin_name,
                                  admin_id=call.from_user.id)
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(
        f"✅ Результат подтверждён: победила <b>{hd.quote(_team_name(winner_id))}</b> "
        f"(админ {hd.quote(admin_name)}).",
        parse_mode="HTML")
    await call.answer("Подтверждено")


async def _apply_confirmed_result(bot: Bot, tournament_id: int, match,
                                  winner_id: int, loser_id: int,
                                  score_text: str, admin_name: str,
                                  admin_id: int = 0) -> None:
    """Общий пайплайн «результат матча подтверждён»: продвижение по сетке,
    автостарт укомплектованных пар следующего круга, матч за 3-е место,
    обновление постов, финал турнира. Работает и для заявок от лидеров,
    и для ручного внесения победителя администратором."""
    conn = db.get_connection()
    conn.execute("UPDATE tournament_matches SET score_text=? WHERE id=?",
                 (score_text, int(match["id"])))
    conn.commit()
    conn.close()

    _advance_winner(tournament_id, match, winner_id, loser_id)
    _promote_byes(tournament_id)
    third_id = _ensure_third_place(tournament_id)

    win_name = _team_name(winner_id)
    lose_name = _team_name(loser_id)

    # Уведомляем обе команды (публичное имя турнира — его дата)
    tour_pub = hd.quote(_tour_name(get_tournament(tournament_id) or
                                   {"id": tournament_id, "starts_at": ""}))
    for team_id, text in (
        (winner_id, f"🎉 Победа в турнире {tour_pub} засчитана! "
                    + ("Вы заняли 3-е место." if str(match["bracket"] or "main") == "third"
                       else "Вы проходите дальше.")),
        (loser_id, f"😔 В турнире {tour_pub} засчитано поражение против "
                   f"«{hd.quote(win_name)}»."),
    ):
        row = _team_row(team_id)
        if row and int(row["leader_id"] or 0) > 0:
            try:
                await bot.send_message(int(row["leader_id"]), text, parse_mode="HTML")
            except Exception:
                pass

    if third_id:
        logger.info(f"Турнир {tournament_id}: создан матч за 3-е место #{third_id}")
    await _repost_bracket(bot, tournament_id)
    await _notify_next_pairs(bot, tournament_id)

    if _tournament_is_over(tournament_id):
        set_tournament_status(tournament_id, "finished")
        podium = _podium(tournament_id)
        for t in get_tournament_teams(tournament_id):
            try:
                await bot.send_message(int(t["leader_id"]),
                                       f"🏁 <b>Турнир {tour_pub} завершён!</b>\n\n{podium}",
                                       parse_mode="HTML")
            except Exception:
                pass
            await asyncio.sleep(0.05)
        await _repost_bracket(bot, tournament_id)

    await log_admin_action(
        bot=bot, admin_id=admin_id,
        admin_name=admin_name,
        action_text=(f"🏆 Зафиксирован результат матча турнира #{tournament_id}: "
                     f"победа «{win_name}» над «{lose_name}»."),
    )


@router.callback_query(F.data.startswith("trnres:rej:"))
async def tournament_result_reject(call: CallbackQuery, bot: Bot):
    """Админ отклоняет заявку с результатом."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    result_id = int(call.data.rsplit(":", 1)[-1])
    conn = db.get_connection()
    res = conn.execute("SELECT * FROM tournament_results WHERE id = ?", (result_id,)).fetchone()
    if not res or res["status"] != "pending":
        conn.close()
        await call.answer("Заявка уже обработана", show_alert=True)
        return
    conn.execute("UPDATE tournament_results SET status='rejected', decided_by=?, "
                 "decided_at=CURRENT_TIMESTAMP WHERE id=?", (call.from_user.id, result_id))
    conn.commit()
    conn.close()
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("🚫 Заявка с результатом отклонена.")
    await call.answer("Отклонено")
    try:
        await bot.send_message(
            int(res["reporter_id"]),
            "🚫 Ваша заявка с результатом турнирного матча отклонена администрацией. "
            "Проверьте скриншоты и отправьте результат заново через /result.")
    except Exception:
        pass
