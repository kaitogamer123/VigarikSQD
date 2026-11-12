"""Создание скримов: обычный, дружеский, рандомный."""
from datetime import datetime
from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.markdown import html_decoration as hd
from league import league_db as db
from league.league_db import (
    claim_random_scrim,
    create_scrim,
    get_league,
    get_scrim,
    get_user_league,
    get_verified_leagues,
    last_random_opponent,
)
from utils.notify_settings import SCRIM_MUTE_HINT, is_muted
from .competitive_common import (
    LEAGUES_MENU_BUTTONS,
    SCRIM_MENU_TEXTS,
    SCRIM_TYPE_LABEL,
    ScrimFormatState,
    ScrimStates,
    _is_leader,
    _is_verified,
    _leagues_menu,
    _normalise_scheduled,
    _scrim_input_allowed,
    _show_root,
)

router = Router()


@router.message(F.text.in_(LEAGUES_MENU_BUTTONS))
async def open_leagues_menu(message: Message):
    composition = get_user_league(message.from_user.id)
    if not composition or not _is_leader(composition, message.from_user.id):
        await message.answer("❌ Раздел «Лиги» доступен только лидеру состава.")
        return
    if not _is_verified(composition):
        await message.answer("❌ Сначала верифицируй состав (нужно 3+ участника из клана).")
        return
    await message.answer(
        "⚔️ <b>Лиги / Скримы</b>\n\n"
        "• <b>Обычный скрим</b> — выбираешь соперника сам. Победа +10 MMR за карту, поражение −7.\n"
        "• <b>Рандомный скрим</b> — соперник находится сам (кто первый согласится). "
        "Победа +15, поражение −10. Два раза подряд с одним соперником играть нельзя.\n"
        "• <b>Дружеский скрим</b> — как обычный, но без подсчёта MMR.\n\n"
        "Выбери действие:",
        parse_mode="HTML", reply_markup=_leagues_menu(),
    )


def _need_verified_leader(composition, user_id: int) -> str | None:
    if not composition or not _is_leader(composition, user_id):
        return "❌ Создавать скримы может только лидер состава."
    if not _is_verified(composition):
        return "❌ Сначала верифицируй состав."
    return None


def _eligible_random_rivals(composition):
    """Верифицированные составы, с которыми нельзя играть 2 раза подряд."""
    my_id = int(composition["id"])
    prev = last_random_opponent(my_id)
    rivals = []
    for league in get_verified_leagues(exclude_id=my_id):
        if prev is not None and int(league["id"]) == int(prev):
            continue
        if last_random_opponent(int(league["id"])) == my_id:
            continue
        rivals.append(league)
    return rivals


@router.message(F.text == "⚔️ Обычный скрим")
async def scrim_normal_start(message: Message, state: FSMContext):
    composition = get_user_league(message.from_user.id)
    err = _need_verified_leader(composition, message.from_user.id)
    if err:
        await message.answer(err)
        return
    rivals = get_verified_leagues(exclude_id=int(composition["id"]))
    if not rivals:
        await message.answer("📭 Пока нет других верифицированных составов для игры.")
        return
    await state.update_data(scrim_type="normal")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{r['name']} [{r['tag']}] | {int(r['mmr'] or 0)} MMR🌟",
                              callback_data=f"scrim:opp:{r['id']}")]
        for r in rivals
    ])
    await message.answer("Выбери, против кого хочешь сыграть:", reply_markup=kb)


@router.message(F.text == "🤝 Дружеский скрим")
async def scrim_friendly_start(message: Message, state: FSMContext):
    composition = get_user_league(message.from_user.id)
    err = _need_verified_leader(composition, message.from_user.id)
    if err:
        await message.answer(err)
        return
    rivals = get_verified_leagues(exclude_id=int(composition["id"]))
    if not rivals:
        await message.answer("📭 Пока нет других верифицированных составов для игры.")
        return
    await state.update_data(scrim_type="friendly")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{r['name']} [{r['tag']}] | {int(r['mmr'] or 0)} MMR🌟",
                              callback_data=f"scrim:opp:{r['id']}")]
        for r in rivals
    ])
    await message.answer("Выбери соперника для дружеского скрима (без MMR):", reply_markup=kb)


@router.message(F.text == "🎲 Рандомный скрим")
async def scrim_random_start(message: Message, state: FSMContext):
    composition = get_user_league(message.from_user.id)
    err = _need_verified_leader(composition, message.from_user.id)
    if err:
        await message.answer(err)
        return
    rivals = _eligible_random_rivals(composition)
    if not rivals:
        await message.answer(
            "📭 Сейчас нет подходящих соперников для рандомного скрима: "
            "либо нет других верифицированных составов, либо все уже играли с тобой "
            "в прошлом рандомном скриме."
        )
        return
    await state.update_data(scrim_type="random", random_pool=[r["id"] for r in rivals])
    await state.set_state(ScrimStates.waiting_datetime)
    await message.answer(
        "🎲 Рандомный скрим. Соперник подберётся сам из верифицированных составов "
        "(кто первый согласится). Твоё название никому не покажем до начала игры.\n\n"
        f"Доступно соперников: <b>{len(rivals)}</b>.\n\n"
        "Напиши дату и время, когда хочешь сыграть (например: <code>25.12 19:00</code>):",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("scrim:opp:"))
async def scrim_choose_opp(call: CallbackQuery, state: FSMContext):
    composition = get_user_league(call.from_user.id)
    err = _need_verified_leader(composition, call.from_user.id)
    if err:
        await call.answer(err, show_alert=True)
        return
    data = await state.get_data()
    scrim_type = data.get("scrim_type") or "normal"
    opp_id = int(call.data.rsplit(":", 1)[-1])
    opp = get_league(opp_id)
    if not opp or int(opp["is_verified"] or 0) != 1 or opp_id == int(composition["id"]):
        await call.answer("Этот соперник недоступен", show_alert=True)
        return
    await state.update_data(opponent_league_id=opp_id)
    await state.set_state(ScrimStates.waiting_datetime)
    await call.message.answer(
        f"Соперник: <b>{hd.quote(opp['name'])} [{hd.quote(opp['tag'])}]</b>.\n"
        f"Напиши дату и время игры (например: <code>25.12 19:00</code>):",
        parse_mode="HTML",
    )
    await call.answer()


@router.message(
    ScrimStates.waiting_datetime,
    F.text.in_(SCRIM_MENU_TEXTS) | F.text.startswith("/"),
)
async def scrim_datetime_exit(message: Message, state: FSMContext):
    """Кнопки меню и команды никогда не считаются датой."""
    await state.clear()
    if (message.text or "").strip() == "◀️ Назад в составы":
        await _show_root(message, state)
        return
    await message.answer("❌ Создание скрима отменено.", reply_markup=_leagues_menu())


@router.message(ScrimStates.waiting_datetime, _scrim_input_allowed)
async def scrim_datetime_entered(message: Message, state: FSMContext):
    scheduled = _normalise_scheduled(message.text or "")
    if not scheduled:
        await message.answer(
            "❌ Неверный формат даты. Введи так: <code>25.12 19:00</code> или <code>25.12.2026 19:00</code>.\n"
            "Для выхода нажми «◀️ Назад в составы» или отправь /cancel.",
            parse_mode="HTML",
        )
        return
    await state.update_data(scheduled_text=scheduled)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="BO3 (до 2 побед)", callback_data="scrim:fmt:BO3"),
        InlineKeyboardButton(text="BO5 (до 3 побед)", callback_data="scrim:fmt:BO5"),
    ]])
    await state.set_state(ScrimFormatState)
    await message.answer("Выбери формат игры:", reply_markup=kb)


@router.message(
    ScrimFormatState,
    F.text.in_(SCRIM_MENU_TEXTS) | F.text.startswith("/"),
)
async def scrim_format_exit(message: Message, state: FSMContext):
    await state.clear()
    if (message.text or "").strip() == "◀️ Назад в составы":
        await _show_root(message, state)
        return
    await message.answer("❌ Создание скрима отменено.", reply_markup=_leagues_menu())


@router.callback_query(ScrimFormatState, F.data.startswith("scrim:fmt:"))
async def scrim_format_chosen(call: CallbackQuery, state: FSMContext, bot: Bot):
    fmt = call.data.rsplit(":", 1)[-1]
    if fmt not in ("BO3", "BO5"):
        await call.answer("Неизвестный формат", show_alert=True)
        return
    data = await state.get_data()
    composition = get_user_league(call.from_user.id)
    err = _need_verified_leader(composition, call.from_user.id)
    if err:
        await state.clear()
        await call.answer(err, show_alert=True)
        return
    scrim_type = data.get("scrim_type") or "normal"
    scheduled = data.get("scheduled_text") or ""

    if scrim_type == "random":
        pool_ids = data.get("random_pool") or []
        pool = [get_league(int(league_id)) for league_id in pool_ids]
        pool = [league for league in pool if league and int(league["is_verified"] or 0) == 1]
        if not pool:
            await state.clear()
            await call.message.edit_text(
                "❌ Сейчас нет подходящих соперников для рандомного скрима: "
                "нет других верифицированных составов либо все уже играли с тобой "
                "в прошлом рандомном скриме."
            )
            await call.answer()
            return
        scrim_id = create_scrim(
            "random", fmt, int(composition["id"]), call.from_user.id,
            scheduled_text=scheduled, status="searching", expire_hours=24,
        )
        await state.clear()
        sent = 0
        for league in pool:
            if await is_muted(int(league["leader_id"]), "scrims"):
                continue
            try:
                await bot.send_message(
                    int(league["leader_id"]),
                    f"🎲 Кто-то хочет сыграть рандомный скрим!\n\n"
                    f"Формат: {fmt}\nДата/время: {hd.quote(scheduled)}\n\n"
                    f"Кто первый согласится — тот и играет. Организатор не раскрывается."
                    + SCRIM_MUTE_HINT,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="✅ Хочу поучаствовать",
                                             callback_data=f"scrim:random_accept:{scrim_id}"),
                    ]]),
                )
                sent += 1
            except Exception:
                continue
        await call.message.edit_text(
            f"🎲 Поиск соперника запущен (скрим #{scrim_id}, {fmt}).\n"
            f"Приглашение отправлено {sent} лидерам. Кто первый согласится — тот и соперник.",
            reply_markup=None,
        )
        await call.answer()
        return

    opp_id = data.get("opponent_league_id")
    opp = get_league(int(opp_id)) if opp_id else None
    if not opp or int(opp["is_verified"] or 0) != 1:
        await state.clear()
        await call.answer("Соперник недоступен, начни заново", show_alert=True)
        return
    if await is_muted(int(opp["leader_id"]), "scrims"):
        await state.clear()
        await call.message.edit_text(
            "❌ У лидера этого состава отключены уведомления о скримах — "
            "он не увидит вызов. Выбери другого соперника.",
            reply_markup=None,
        )
        await call.answer()
        return
    scrim_id = create_scrim(
        scrim_type, fmt, int(composition["id"]), call.from_user.id,
        scheduled_text=scheduled, opponent_league_id=int(opp["id"]),
        opponent_leader_id=int(opp["leader_id"]), status="invited", expire_hours=24,
    )
    await state.clear()
    label = SCRIM_TYPE_LABEL.get(scrim_type, scrim_type)
    try:
        await bot.send_message(
            int(opp["leader_id"]),
            f"⚔️ Вам бросили вызов: {label} скрим!\n\n"
            f"Соперник: {hd.quote(composition['name'])} [{hd.quote(composition['tag'])}] "
            f"({int(composition['mmr'] or 0)} MMR🌟)\n"
            f"Формат: {fmt}\nДата/время: {hd.quote(scheduled)}\n\n"
            f"Ответь в течение суток — иначе приглашение сгорит."
            + SCRIM_MUTE_HINT,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Принять", callback_data=f"scrim:inv_accept:{scrim_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"scrim:inv_decline:{scrim_id}"),
            ]]),
        )
    except Exception:
        await call.message.edit_text("❌ Не смог написать лидеру соперника (он заблокировал бота?).")
        await call.answer()
        return
    await call.message.edit_text(
        f"✅ Заявка отправлена лидеру «{opp['name']}» в ЛС. "
        f"Если он примет в течение суток — договоритесь о точном времени и играйте.",
        reply_markup=None,
    )
    await call.answer()


@router.message(
    StateFilter(ScrimStates.waiting_datetime, ScrimStates.waiting_score, ScrimStates.waiting_screenshots),
    F.text.in_({"◀️ Назад", "❌ Отмена", "◀️ Назад в составы"}),
)
async def scrim_input_back(message: Message, state: FSMContext):
    await state.clear()
    if message.text == "◀️ Назад в составы":
        await _show_root(message, state)
        return
    await message.answer("❌ Создание/завершение скрима отменено.", reply_markup=_leagues_menu())


@router.callback_query(F.data.startswith("scrim:inv_accept:"))
async def scrim_invite_accept(call: CallbackQuery, bot: Bot):
    scrim_id = int(call.data.rsplit(":", 1)[-1])
    scrim = get_scrim(scrim_id)
    if not scrim or scrim["status"] != "invited":
        await call.answer("Приглашение уже неактивно", show_alert=True)
        return
    if int(scrim["opponent_leader_id"] or 0) != call.from_user.id:
        await call.answer("Это приглашение не тебе", show_alert=True)
        return
    try:
        exp = datetime.strptime(str(scrim["invite_expires_at"])[:19], "%Y-%m-%d %H:%M:%S")
        if exp < datetime.now():
            db.set_scrim_status(scrim_id, "expired")
            await call.answer("Приглашение сгорело", show_alert=True)
            return
    except Exception:
        pass
    if not db.set_scrim_status(scrim_id, "scheduled"):
        await call.answer("Приглашение уже неактивно", show_alert=True)
        return
    await call.message.edit_text(
        f"✅ Ты принял скрим #{scrim_id} ({scrim['format']}, {scrim['scheduled_text']}). "
        f"Договоритесь с соперником в ЛС и играйте. После игры жми «🏁 Скрим завершён».",
        reply_markup=None,
    )
    await call.answer("Скрим принят")
    label = SCRIM_TYPE_LABEL.get(scrim["scrim_type"], scrim["scrim_type"])
    if await is_muted(int(scrim["challenger_leader_id"]), "scrims"):
        return
    try:
        await bot.send_message(
            int(scrim["challenger_leader_id"]),
            f"✅ «{scrim['opponent_name']}» принял твой {label} скрим #{scrim_id} "
            f"({scrim['format']}, {scrim['scheduled_text']}). Договоритесь в ЛС и играйте!",
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("scrim:inv_decline:"))
async def scrim_invite_decline(call: CallbackQuery, bot: Bot):
    scrim_id = int(call.data.rsplit(":", 1)[-1])
    scrim = get_scrim(scrim_id)
    if not scrim or scrim["status"] != "invited":
        await call.answer("Приглашение уже неактивно", show_alert=True)
        return
    if int(scrim["opponent_leader_id"] or 0) != call.from_user.id:
        await call.answer("Это приглашение не тебе", show_alert=True)
        return
    db.set_scrim_status(scrim_id, "declined")
    await call.message.edit_text(f"❌ Ты отклонил скрим #{scrim_id}.", reply_markup=None)
    await call.answer()
    if await is_muted(int(scrim["challenger_leader_id"]), "scrims"):
        return
    try:
        await bot.send_message(
            int(scrim["challenger_leader_id"]),
            f"❌ «{scrim['opponent_name']}» отклонил твой скрим #{scrim_id}.",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("scrim:random_accept:"))
async def scrim_random_accept(call: CallbackQuery, bot: Bot):
    scrim_id = int(call.data.rsplit(":", 1)[-1])
    scrim = get_scrim(scrim_id)
    if not scrim or scrim["status"] != "searching":
        await call.answer("Этот скрим уже забрали", show_alert=True)
        return
    composition = get_user_league(call.from_user.id)
    if not composition or not _is_leader(composition, call.from_user.id) or not _is_verified(composition):
        await call.answer("Участвовать может только лидер верифицированного состава", show_alert=True)
        return
    my_league = int(composition["id"])
    if my_league == int(scrim["challenger_league_id"]):
        await call.answer("Это твой же скрим", show_alert=True)
        return
    if last_random_opponent(int(scrim["challenger_league_id"])) == my_league or \
       last_random_opponent(my_league) == int(scrim["challenger_league_id"]):
        await call.answer(
            "Вы уже играли друг с другом в прошлом рандомном скриме. Дождись другого соперника.",
            show_alert=True,
        )
        return
    if not claim_random_scrim(scrim_id, my_league, call.from_user.id):
        await call.answer("Этот скрим уже забрали", show_alert=True)
        return
    scrim = get_scrim(scrim_id)
    await call.message.edit_text(
        f"✅ Ты в игре! 🎲 Рандомный скрим #{scrim_id} ({scrim['format']}, {scrim['scheduled_text']}).\n"
        f"Соперник: <b>{hd.quote(scrim['challenger_name'])} [{hd.quote(scrim['challenger_tag'])}]</b>. "
        f"Договоритесь в ЛС и играйте. После игры каждый лидер жмёт «🏁 Скрим завершён».",
        parse_mode="HTML", reply_markup=None,
    )
    await call.answer("Соперник найден!")
    if await is_muted(int(scrim["challenger_leader_id"]), "scrims"):
        return
    try:
        await bot.send_message(
            int(scrim["challenger_leader_id"]),
            f"🎲 На твой рандомный скрим #{scrim_id} откликнулся состав "
            f"<b>{hd.quote(scrim['opponent_name'])} [{hd.quote(scrim['opponent_tag'])}]</b>! "
            f"Договоритесь в ЛС и играйте.",
            parse_mode="HTML",
        )
    except Exception:
        pass
