"""Просмотр скримов, ввод счёта и скриншотов."""
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.markdown import html_decoration as hd
from league import league_db as db
from league.league_db import complete_scrim, get_scrim, get_user_league
from utils.notify_settings import is_muted
from .competitive_common import (
    SCRIM_MENU_TEXTS,
    SCRIM_TYPE_LABEL,
    ScrimStates,
    _fmt_signed,
    _is_leader,
    _leagues_menu,
    _parse_score,
    _scrim_input_allowed,
    _show_root,
    _valid_score,
)

router = Router()


@router.message(F.text == "🏁 Мои скримы")
async def my_scrims(message: Message):
    composition = get_user_league(message.from_user.id)
    if not composition:
        await message.answer("❌ Ты не состоишь в составе.")
        return
    rows = db.get_active_scrims_for_leader(message.from_user.id)
    if not rows and not _is_leader(composition, message.from_user.id):
        league_id = int(composition["id"])
        conn_rows = []
        for s in db.get_active_scrims_for_leader(int(composition["leader_id"])):
            if int(s["challenger_league_id"]) == league_id or int(s["opponent_league_id"] or 0) == league_id:
                conn_rows.append(s)
        rows = conn_rows
    if not rows:
        await message.answer("📭 Активных скримов нет.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"#{r['id']} {SCRIM_TYPE_LABEL.get(r['scrim_type'], '')} "
                 f"{r['challenger_name']} vs {r['opponent_name'] or '?'} ({r['status']})",
            callback_data=f"scrim:view:{r['id']}",
        )]
        for r in rows
    ])
    await message.answer("🏁 Твои активные скримы:", reply_markup=kb)


@router.callback_query(F.data.startswith("scrim:view:"))
async def scrim_view(call: CallbackQuery):
    scrim_id = int(call.data.rsplit(":", 1)[-1])
    scrim = get_scrim(scrim_id)
    if not scrim:
        await call.answer("Скрим не найден", show_alert=True)
        return
    label = SCRIM_TYPE_LABEL.get(scrim["scrim_type"], scrim["scrim_type"])
    text = (
        f"⚔️ <b>Скрим #{scrim_id}</b> ({label}, {scrim['format']})\n"
        f"{hd.quote(scrim['challenger_name'])} 🆚 {hd.quote(scrim['opponent_name'] or 'поиск...')}\n"
        f"Дата/время: {hd.quote(scrim['scheduled_text'] or '—')}\n"
        f"Статус: <code>{scrim['status']}</code>"
    )
    rows = []
    if scrim["status"] in ("scheduled", "awaiting_scores") and call.from_user.id in (
        int(scrim["challenger_leader_id"] or 0), int(scrim["opponent_leader_id"] or 0)
    ):
        rows.append([InlineKeyboardButton(text="🏁 Скрим завершён",
                                          callback_data=f"scrim:finish:{scrim_id}")])
    rows.append([InlineKeyboardButton(text="◀️ В составы", callback_data="league:back_root")])
    await call.message.edit_text(text, parse_mode="HTML",
                                 reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(F.data.startswith("scrim:finish:"))
async def scrim_finish_start(call: CallbackQuery, state: FSMContext):
    scrim_id = int(call.data.rsplit(":", 1)[-1])
    scrim = get_scrim(scrim_id)
    if not scrim or scrim["status"] not in ("scheduled", "awaiting_scores"):
        await call.answer("Скрим уже завершён или отменён", show_alert=True)
        return
    if call.from_user.id not in (int(scrim["challenger_leader_id"] or 0),
                                 int(scrim["opponent_leader_id"] or 0)):
        await call.answer("Завершить могут только лидеры играющих составов", show_alert=True)
        return
    side = "challenger" if call.from_user.id == int(scrim["challenger_leader_id"]) else "opponent"
    await state.update_data(finish_scrim_id=scrim_id, finish_side=side)
    await state.set_state(ScrimStates.waiting_score)
    await call.message.answer(
        f"🏁 <b>Скрим #{scrim_id} завершён.</b> Введи счёт в формате:\n"
        f"<b>{hd.quote(scrim['challenger_name'])} / {hd.quote(scrim['opponent_name'])}</b>\n"
        f"Например: <code>3/2</code> — первое число всегда «{hd.quote(scrim['challenger_name'])}», "
        f"второе — «{hd.quote(scrim['opponent_name'])}».\n"
        f"Формат {scrim['format']}: {'победа — 2 выигранные карты' if scrim['format'] == 'BO3' else 'победа — 3 выигранные карты'}.",
        parse_mode="HTML",
    )
    await call.answer()


@router.message(
    ScrimStates.waiting_score,
    F.text.in_(SCRIM_MENU_TEXTS) | F.text.startswith("/"),
)
async def scrim_score_exit(message: Message, state: FSMContext):
    await state.clear()
    if (message.text or "").strip() == "◀️ Назад в составы":
        await _show_root(message, state)
        return
    await message.answer("❌ Ввод счёта отменён.", reply_markup=_leagues_menu())


@router.message(ScrimStates.waiting_score, _scrim_input_allowed)
async def scrim_score(message: Message, state: FSMContext):
    data = await state.get_data()
    scrim_id, side = data.get("finish_scrim_id"), data.get("finish_side")
    scrim = get_scrim(int(scrim_id)) if scrim_id else None
    if not scrim:
        await state.clear()
        await message.answer("❌ Скрим не найден.")
        return
    parsed = _parse_score(message.text or "")
    if not parsed or not _valid_score(scrim["format"], *parsed):
        await message.answer(
            f"❌ Неверный счёт для {scrim['format']}. Примеры: "
            f"{'2/0, 2/1, 1/2, 0/2' if scrim['format'] == 'BO3' else '3/0, 3/1, 3/2, 2/3, 1/3, 0/3'}. "
            f"Введи в формате «{scrim['challenger_name']} / {scrim['opponent_name']}»."
        )
        return
    db.save_score_report(scrim_id, side, *parsed)
    await state.set_state(ScrimStates.waiting_screenshots)
    await message.answer(
        f"✅ Счёт {parsed[0]}/{parsed[1]} записан. Теперь пришли <b>скриншоты игр</b> "
        f"(фото, можно несколько), затем нажми «Готово».",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Всё отправил", callback_data=f"scrim:shots_done:{scrim_id}")
        ]]),
    )


@router.message(ScrimStates.waiting_screenshots, F.photo)
async def scrim_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    scrim_id, side = data.get("finish_scrim_id"), data.get("finish_side")
    file_id = message.photo[-1].file_id
    n = db.add_scrim_screenshot(int(scrim_id), side, file_id)
    await message.answer(
        f"📸 Скриншот №{n} сохранён. Пришли ещё или нажми «Готово».",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Всё отправил", callback_data=f"scrim:shots_done:{scrim_id}")
        ]]),
    )


@router.callback_query(F.data.startswith("scrim:shots_done:"))
async def scrim_shots_done(call: CallbackQuery, state: FSMContext, bot: Bot):
    scrim_id = int(call.data.rsplit(":", 1)[-1])
    data = await state.get_data()
    side = data.get("finish_side")
    scrim = get_scrim(scrim_id)
    if not scrim:
        await state.clear()
        await call.answer("Скрим не найден", show_alert=True)
        return
    if call.from_user.id == int(scrim["challenger_leader_id"] or 0):
        side = "challenger"
    elif call.from_user.id == int(scrim["opponent_leader_id"] or 0):
        side = "opponent"
    else:
        await call.answer("Ты не лидер этого скрима", show_alert=True)
        return
    if db.get_scrim_shots_count(scrim_id, side) < 1:
        await call.answer("Пришли хотя бы 1 скриншот", show_alert=True)
        return
    await state.clear()
    scrim = get_scrim(scrim_id)
    other_side = "opponent" if side == "challenger" else "challenger"
    other_reported = (scrim["opponent_score_a"] is not None) if side == "challenger" else \
                     (scrim["challenger_score_a"] is not None)
    if not other_reported:
        db.set_scrim_status(scrim_id, "awaiting_scores")
        await call.message.answer("✅ Твой результат сохранён. Ждём результат второго лидера.")
        await call.answer()
        other_leader = int(scrim["opponent_leader_id"] or 0) if side == "challenger" else \
            int(scrim["challenger_leader_id"] or 0)
        if await is_muted(other_leader, "scrims"):
            return
        try:
            await bot.send_message(
                other_leader,
                f"⏳ Соперник уже отправил результат скрима #{scrim_id}. "
                f"Нажми «🏁 Скрим завершён» в разделе «⚔️ Лиги» → «🏁 Мои скримы» и введи счёт.",
            )
        except Exception:
            pass
        return
    a1 = (int(scrim["challenger_score_a"]), int(scrim["challenger_score_b"]))
    a2 = (int(scrim["opponent_score_a"]), int(scrim["opponent_score_b"]))
    if a1 != a2:
        db.clear_score_reports(scrim_id)
        mine = a1 if side == "challenger" else a2
        theirs = a2 if side == "challenger" else a1
        await call.message.answer(
            f"⚠️ <b>Счета не совпали!</b> Ты ввёл {mine[0]}/{mine[1]}, "
            f"а соперник — {theirs[0]}/{theirs[1]}. "
            f"Сверьтесь в ЛС и введите счёт заново через «🏁 Скрим завершён».",
            parse_mode="HTML",
        )
        await call.answer("Счета не совпали")
        try:
            other_leader = int(scrim["opponent_leader_id"] or 0) if side == "challenger" else \
                int(scrim["challenger_leader_id"] or 0)
            if await is_muted(other_leader, "scrims"):
                return
            await bot.send_message(
                other_leader,
                f"⚠️ В скриме #{scrim_id} счета не совпали ({a1[0]}/{a1[1]} vs {a2[0]}/{a2[1]}). "
                f"Договоритесь и введите заново.",
            )
        except Exception:
            pass
        return
    result = complete_scrim(scrim_id)
    if not result:
        await call.answer("Не удалось завершить", show_alert=True)
        return
    scrim = get_scrim(scrim_id)
    label = SCRIM_TYPE_LABEL.get(scrim["scrim_type"], scrim["scrim_type"])
    text = (
        f"🏁 <b>Скрим #{scrim_id} завершён!</b> ({label}, {scrim['format']})\n"
        f"{hd.quote(scrim['challenger_name'])} <b>{result['score_a']}:{result['score_b']}</b> "
        f"{hd.quote(scrim['opponent_name'])}\n"
    )
    if scrim["scrim_type"] == "friendly":
        text += "🤝 Дружеский скрим — MMR не меняется."
    else:
        text += (f"MMR: {hd.quote(scrim['challenger_name'])} {_fmt_signed(result['delta_a'])}🌟, "
                 f"{hd.quote(scrim['opponent_name'])} {_fmt_signed(result['delta_b'])}🌟")
    await call.message.answer(text, parse_mode="HTML")
    await call.answer("Скрим завершён!")
    for leader_id in (int(scrim["challenger_leader_id"]), int(scrim["opponent_leader_id"] or 0)):
        if leader_id and leader_id != call.from_user.id and not await is_muted(leader_id, "scrims"):
            try:
                await bot.send_message(leader_id, text, parse_mode="HTML")
            except Exception:
                pass
