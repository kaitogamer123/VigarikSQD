"""Верификация составов, скримы (обычные/рандомные/дружеские), MMR и истории матчей."""
import asyncio
import logging
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.utils.markdown import html_decoration as hd

from config import ADMIN_CHAT_ID
from database import get_member
from league import league_db as db
from league.league_db import (
    add_mmr,
    calc_mmr,
    claim_random_scrim,
    complete_scrim,
    count_clan_members,
    create_scrim,
    create_verify_request,
    decide_verify_request,
    get_expired_scrims,
    get_league,
    get_league_history,
    get_league_members,
    get_pending_verify_request,
    get_recent_completed,
    get_scrim,
    get_user_league,
    get_verified_leagues,
    get_verify_request,
    last_random_opponent,
    set_league_verified,
)
from utils.permissions import get_admin_rights_from_file, is_any_admin

logger = logging.getLogger(__name__)
router = Router()

SCRIM_TYPE_LABEL = {"normal": "⚔️ Обычный", "random": "🎲 Рандомный", "friendly": "🤝 Дружеский"}
LEAGUES_MENU_BUTTONS = {"⚔️ Лиги", "🏆 Лиги", "Лиги"}


class ScrimStates(StatesGroup):
    waiting_datetime = State()
    waiting_score = State()
    waiting_screenshots = State()


# ─── helpers ─────────────────────────────────────────────────────────────

def _reply(rows) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in row] for row in rows],
        resize_keyboard=True,
    )


def _leagues_menu() -> ReplyKeyboardMarkup:
    return _reply([
        ["⚔️ Обычный скрим", "🎲 Рандомный скрим"],
        ["🤝 Дружеский скрим"],
        ["🏁 Мои скримы", "📜 История матчей"],
        ["◀️ Назад в составы"],
    ])


def _back_inline(label: str = "◀️ В составы") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, callback_data="league:back_root")
    ]])


async def _is_admin(user_id: int) -> bool:
    try:
        member = await get_member(user_id)
    except Exception:
        member = None
    if member and is_any_admin(member):
        return True
    try:
        return get_admin_rights_from_file(user_id) is not None
    except Exception:
        return False


def _is_leader(composition, user_id: int) -> bool:
    return bool(composition) and int(composition["leader_id"]) == int(user_id)


def _is_verified(composition) -> bool:
    try:
        return int(composition["is_verified"] or 0) == 1
    except Exception:
        return False


def _fmt_dt(raw: str) -> str:
    if not raw:
        return "—"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(str(raw)[:19], fmt).strftime("%d.%m %H:%M")
        except Exception:
            continue
    return str(raw)[:16]


def _parse_score(text: str):
    if not text:
        return None
    t = text.strip().replace(":", "/").replace("-", "/").replace(" ", "/")
    parts = [p for p in t.split("/") if p != ""]
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    a, b = int(parts[0]), int(parts[1])
    if a > 9 or b > 9:
        return None
    return (a, b)


def _valid_score(fmt: str, a: int, b: int) -> bool:
    if a == b:
        return False
    if fmt == "BO3":
        return (a == 2 and b <= 1) or (b == 2 and a <= 1)
    if fmt == "BO5":
        return (a == 3 and b <= 2) or (b == 3 and a <= 2)
    return False


def _fmt_signed(value) -> str:
    v = int(value or 0)
    return f"+{v}" if v > 0 else str(v)


async def _show_root(message: Message, state: FSMContext, user_id: int = None):
    from league.handlers import show_root
    await show_root(message, state, user_id=user_id or message.from_user.id)


def _history_line(row, perspective_league_id: int | None = None) -> str:
    """Строка истории одного завершённого скрима."""
    date = _fmt_dt(row["completed_at"] or row["created_at"])
    typ = SCRIM_TYPE_LABEL.get(row["scrim_type"], row["scrim_type"])
    fmt = row["format"] or ""
    a, b = int(row["challenger_score_a"] or 0), int(row["challenger_score_b"] or 0)
    name_a = f"{row['challenger_name']} [{row['challenger_tag']}]" if row["challenger_name"] else f"#{row['challenger_league_id']}"
    name_b = f"{row['opponent_name']} [{row['opponent_tag']}]" if row["opponent_name"] else "?"
    if perspective_league_id and int(row["opponent_league_id"] or 0) == int(perspective_league_id):
        mine, theirs = b, a
        delta = int(row["mmr_opponent"] or 0)
        rival = name_a
    else:
        mine, theirs = a, b
        delta = int(row["mmr_challenger"] or 0)
        rival = name_b
    result = "✅ Победа" if mine > theirs else ("❌ Поражение" if mine < theirs else "➖ Ничья")
    mmr = f"{_fmt_signed(delta)} MMR🌟" if row["scrim_type"] != "friendly" else "без MMR"
    if perspective_league_id:
        return f"📅 {date} | {typ} {fmt} | vs {hd.quote(rival)} | {mine}:{theirs} {result} | {mmr}"
    return (f"📅 {date} | {typ} {fmt} | {hd.quote(name_a)} {a}:{b} {hd.quote(name_b)} | "
            f"{_fmt_signed(row['mmr_challenger'] or 0)}/{_fmt_signed(row['mmr_opponent'] or 0)} MMR")


# ─── Верификация: заявка лидера ────────────────────────────────────────────

@router.message(F.text == "✅ Верифицировать состав")
async def verify_start(message: Message, bot: Bot):
    composition = get_user_league(message.from_user.id)
    if not composition or not _is_leader(composition, message.from_user.id):
        await message.answer("❌ Верификацию может запросить только лидер состава.")
        return
    if _is_verified(composition):
        await message.answer("✅ Ваш состав уже верифицирован. Открой вкладку «⚔️ Лиги».")
        return
    league_id = int(composition["id"])
    occupied = [m for m in get_league_members(league_id, True)]
    if len(occupied) < 3:
        await message.answer(
            f"Чтобы верифицировать команду нужен полный состав, хотя бы 3/4 участника "
            f"(сейчас {len(occupied)}/4)."
        )
        return
    in_clan = count_clan_members([m["user_id"] for m in occupied])
    if in_clan < 3:
        await message.answer(
            f"Для верификации нужно, чтобы хотя бы 3 участника состава состояли в клане "
            f"(сейчас {in_clan}). Попроси участников пройти регистрацию через /start."
        )
        return
    if get_pending_verify_request(league_id):
        await message.answer("⏳ Заявка на верификацию уже отправлена и ждёт решения администрации.")
        return
    req_id = create_verify_request(league_id, message.from_user.id)
    if not req_id:
        await message.answer("❌ Не удалось создать заявку. Попробуй позже.")
        return
    # Заявка в админ-чат
    lines = [
        "📋 <b>Заявка на верификацию состава</b>",
        "",
        f"Команда «{hd.quote(composition['name'])}» [{hd.quote(composition['tag'])}]",
        "Подала заявку на верификацию",
        "",
        "<b>Состав:</b>",
    ]
    for m in occupied:
        role = "👑" if m["role"] == "лидер" else ("🛡" if m["role"] == "заместитель" else "👤")
        lines.append(f"{role} {hd.quote(m['game_nick'] or 'Игрок')} (ID <code>{m['user_id']}</code>)")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"vrf:accept:{req_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"vrf:reject:{req_id}"),
    ]])
    try:
        await bot.send_message(ADMIN_CHAT_ID, "\n".join(lines), parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.error(f"Не удалось отправить заявку на верификацию {req_id} в админ-чат: {e}")
        await message.answer("❌ Не удалось связаться с администрацией. Попробуй позже.")
        return
    await message.answer("✅ Ваша заявка отправлена в администрацию.")


def _verify_confirm_kb(req_id: int, action: str) -> InlineKeyboardMarkup:
    if action == "accept":
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить принятие",
                                  callback_data=f"vrf:accept_yes:{req_id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"vrf:back:{req_id}")],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Подтвердить отклонение",
                              callback_data=f"vrf:reject_yes:{req_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"vrf:back:{req_id}")],
    ])


def _verify_initial_kb(req_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"vrf:accept:{req_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"vrf:reject:{req_id}"),
    ]])


@router.callback_query(F.data.startswith("vrf:accept:"), ~F.data.startswith("vrf:accept_yes:"))
async def vrf_accept_ask(call: CallbackQuery):
    if not await _is_admin(call.from_user.id):
        await call.answer("Нет прав", show_alert=True)
        return
    req_id = int(call.data.rsplit(":", 1)[-1])
    req = get_verify_request(req_id)
    if not req or req["status"] != "pending":
        await call.answer("Заявка уже обработана", show_alert=True)
        return
    await call.message.edit_reply_markup(reply_markup=_verify_confirm_kb(req_id, "accept"))
    await call.answer("Вы уверены?")


@router.callback_query(F.data.startswith("vrf:reject:"), ~F.data.startswith("vrf:reject_yes:"))
async def vrf_reject_ask(call: CallbackQuery):
    if not await _is_admin(call.from_user.id):
        await call.answer("Нет прав", show_alert=True)
        return
    req_id = int(call.data.rsplit(":", 1)[-1])
    req = get_verify_request(req_id)
    if not req or req["status"] != "pending":
        await call.answer("Заявка уже обработана", show_alert=True)
        return
    await call.message.edit_reply_markup(reply_markup=_verify_confirm_kb(req_id, "reject"))
    await call.answer("Вы уверены?")


@router.callback_query(F.data.startswith("vrf:back:"))
async def vrf_back(call: CallbackQuery):
    if not await _is_admin(call.from_user.id):
        await call.answer("Нет прав", show_alert=True)
        return
    req_id = int(call.data.rsplit(":", 1)[-1])
    await call.message.edit_reply_markup(reply_markup=_verify_initial_kb(req_id))
    await call.answer()


@router.callback_query(F.data.startswith("vrf:accept_yes:"))
async def vrf_accept_yes(call: CallbackQuery, bot: Bot):
    if not await _is_admin(call.from_user.id):
        await call.answer("Нет прав", show_alert=True)
        return
    req_id = int(call.data.rsplit(":", 1)[-1])
    req = get_verify_request(req_id)
    if not req or req["status"] != "pending":
        await call.answer("Заявка уже обработана", show_alert=True)
        return
    if not decide_verify_request(req_id, "accepted", call.from_user.id):
        await call.answer("Заявка уже обработана", show_alert=True)
        return
    set_league_verified(int(req["league_id"]), True)
    league = get_league(int(req["league_id"]))
    await call.message.edit_text(
        f"{call.message.html_text}\n\n✅ <b>Успешно подтверждено.</b> Решение: "
        f"{hd.quote(call.from_user.full_name)}",
        parse_mode="HTML", reply_markup=None,
    )
    await call.answer("Состав верифицирован")
    try:
        await bot.send_message(
            int(req["leader_id"]),
            f"✅ Ваш состав «{league['name']}» [{league['tag']}] <b>верифицирован</b>! "
            f"Теперь доступна вкладка «⚔️ Лиги»: скримы, MMR и история матчей.",
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("vrf:reject_yes:"))
async def vrf_reject_yes(call: CallbackQuery, bot: Bot):
    if not await _is_admin(call.from_user.id):
        await call.answer("Нет прав", show_alert=True)
        return
    req_id = int(call.data.rsplit(":", 1)[-1])
    req = get_verify_request(req_id)
    if not req or req["status"] != "pending":
        await call.answer("Заявка уже обработана", show_alert=True)
        return
    if not decide_verify_request(req_id, "rejected", call.from_user.id):
        await call.answer("Заявка уже обработана", show_alert=True)
        return
    await call.message.edit_text(
        f"{call.message.html_text}\n\n✅ <b>Успешно отклонено.</b> Решение: "
        f"{hd.quote(call.from_user.full_name)}",
        parse_mode="HTML", reply_markup=None,
    )
    await call.answer("Заявка отклонена")
    try:
        await bot.send_message(
            int(req["leader_id"]),
            "❌ В верификации состава отказано администрацией. "
            "Добейте состав до 3+ участников из клана и подайте заявку снова.",
        )
    except Exception:
        pass


# ─── Меню «Лиги» ───────────────────────────────────────────────────────────

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
    await state.update_data(scrim_type="random")
    await state.set_state(ScrimStates.waiting_datetime)
    await message.answer(
        "🎲 <b>Рандомный скрим.</b> Соперник подберётся сам из верифицированных составов "
        "(кто первый согласится). Твоё название никому не покажем до начала игры.\n\n"
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


@router.message(ScrimStates.waiting_datetime)
async def scrim_datetime(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not (3 <= len(text) <= 60):
        await message.answer("❌ Напиши дату и время текстом от 3 до 60 символов (например: 25.12 19:00).")
        return
    await state.update_data(scheduled_text=text)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="BO3 (до 2 побед)", callback_data="scrim:format:BO3"),
        InlineKeyboardButton(text="BO5 (до 3 побед)", callback_data="scrim:format:BO5"),
    ]])
    await message.answer("Выбери формат игры:", reply_markup=kb)


@router.callback_query(F.data.startswith("scrim:format:"))
async def scrim_format(call: CallbackQuery, state: FSMContext, bot: Bot):
    composition = get_user_league(call.from_user.id)
    err = _need_verified_leader(composition, call.from_user.id)
    if err:
        await call.answer(err, show_alert=True)
        return
    data = await state.get_data()
    scrim_type = data.get("scrim_type") or "normal"
    scheduled = data.get("scheduled_text") or ""
    fmt = call.data.rsplit(":", 1)[-1]
    if fmt not in ("BO3", "BO5"):
        await call.answer("Неизвестный формат", show_alert=True)
        return

    if scrim_type == "random":
        rivals = get_verified_leagues(exclude_id=int(composition["id"]))
        if not rivals:
            await call.answer("Нет других верифицированных составов", show_alert=True)
            return
        scrim_id = create_scrim(
            "random", fmt, int(composition["id"]), call.from_user.id,
            scheduled_text=scheduled, status="searching", expire_hours=24,
        )
        await state.clear()
        # Рассылка всем верифицированным лидерам, кроме создателя (анонимно).
        sent = 0
        for rival in rivals:
            try:
                await bot.send_message(
                    int(rival["leader_id"]),
                    f"🎲 <b>Кто-то хочет сыграть рандомный скрим!</b>\n\n"
                    f"Формат: <b>{fmt}</b>\nДата/время: {hd.quote(scheduled)}\n\n"
                    f"Кто первый согласится — тот и играет. Организатор не раскрывается.",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="✅ Хочу поучаствовать",
                                             callback_data=f"scrim:random_accept:{scrim_id}"),
                    ]]),
                )
                sent += 1
            except Exception:
                continue
        await call.message.answer(
            f"🎲 Поиск соперника запущен (скрим #{scrim_id}, {fmt}). "
            f"Приглашение отправлено {sent} лидерам. Кто первый согласится — тот и соперник."
        )
        await call.answer()
        return

    opp_id = data.get("opponent_league_id")
    opp = get_league(int(opp_id)) if opp_id else None
    if not opp or int(opp["is_verified"] or 0) != 1:
        await call.answer("Соперник недоступен, начни заново", show_alert=True)
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
            f"⚔️ <b>Вам бросили вызов: {label} скрим!</b>\n\n"
            f"Соперник: <b>{hd.quote(composition['name'])} [{hd.quote(composition['tag'])}]</b> "
            f"({int(composition['mmr'] or 0)} MMR🌟)\n"
            f"Формат: <b>{fmt}</b>\nДата/время: {hd.quote(scheduled)}\n\n"
            f"Ответь в течение суток — иначе приглашение сгорит.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Принять", callback_data=f"scrim:inv_accept:{scrim_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"scrim:inv_decline:{scrim_id}"),
            ]]),
        )
    except Exception:
        await call.message.answer("❌ Не смог написать лидеру соперника (он заблокировал бота?).")
        return
    await call.message.answer(
        f"✅ Заявка отправлена лидеру «{opp['name']}» в ЛС. "
        f"Если он примет в течение суток — договоритесь о точном времени и играйте."
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


# ─── Ответы на приглашения ───────────────────────────────────────────────

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
            await call.answer("Приглашение просрочено", show_alert=True)
            return
    except Exception:
        pass
    db.set_scrim_status(scrim_id, "scheduled")
    label = SCRIM_TYPE_LABEL.get(scrim["scrim_type"], scrim["scrim_type"])
    await call.message.edit_text(
        f"✅ Ты принял {label} скрим #{scrim_id} против "
        f"«{scrim['challenger_name']}» ({scrim['format']}, {scrim['scheduled_text']}). "
        f"Договоритесь в ЛС и играйте. После игры каждый лидер жмёт «🏁 Скрим завершён».",
        reply_markup=None,
    )
    await call.answer()
    try:
        challenger = get_league(int(scrim["challenger_league_id"]))
        await bot.send_message(
            int(scrim["challenger_leader_id"]),
            f"✅ «{scrim['opponent_name']}» <b>принял</b> твой {label} скрим #{scrim_id} "
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
    # Запрет двух подряд рандомных игр одной пары.
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


# ─── Активные скримы и завершение ────────────────────────────────────────

@router.message(F.text == "🏁 Мои скримы")
async def my_scrims(message: Message):
    composition = get_user_league(message.from_user.id)
    if not composition:
        await message.answer("❌ Ты не состоишь в составе.")
        return
    rows = db.get_active_scrims_for_leader(message.from_user.id)
    # Обычным участникам показываем активные скримы их состава (без кнопок завершения).
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


@router.message(ScrimStates.waiting_score)
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
    # Определяем сторону по лидеру, если FSM слетел.
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
        try:
            await bot.send_message(
                other_leader,
                f"⏳ Соперник уже отправил результат скрима #{scrim_id}. "
                f"Нажми «🏁 Скрим завершён» в разделе «⚔️ Лиги» → «🏁 Мои скримы» и введи счёт.",
            )
        except Exception:
            pass
        return
    # Оба отчитались — сверяем.
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
        if leader_id and leader_id != call.from_user.id:
            try:
                await bot.send_message(leader_id, text, parse_mode="HTML")
            except Exception:
                pass


# ─── Истории матчей ──────────────────────────────────────────────────────

@router.message(F.text == "📜 История матчей")
async def composition_history(message: Message):
    composition = get_user_league(message.from_user.id)
    if not composition:
        await message.answer("❌ Ты не состоишь в составе.")
        return
    rows = get_league_history(int(composition["id"]), limit=10)
    if not rows:
        await message.answer("📭 У твоего состава пока нет завершённых игр.")
        return
    lines = [f"📜 <b>История матчей «{hd.quote(composition['name'])}»</b> "
             f"(MMR: {int(composition['mmr'] or 0)}🌟):", ""]
    for r in rows:
        lines.append("• " + _history_line(r, perspective_league_id=int(composition["id"])))
    await message.answer("\n".join(lines), parse_mode="HTML",
                         reply_markup=_back_inline("◀️ В составы"))


@router.message(F.text == "📜 История игр")
async def admin_history(message: Message):
    if not await _is_admin(message.from_user.id):
        await message.answer("⛔ Нет прав.")
        return
    rows = get_recent_completed(limit=10)
    if not rows:
        await message.answer("📭 Завершённых игр пока нет.")
        return
    lines = ["📜 <b>Последние игры клуба:</b>", ""]
    for r in rows:
        lines.append("• " + _history_line(r))
    # Выбор состава для детальной истории.
    leagues = get_verified_leagues()
    kb_rows = []
    for lg in leagues[:20]:
        kb_rows.append([InlineKeyboardButton(
            text=f"{lg['name']} [{lg['tag']}]",
            callback_data=f"hist:team:{lg['id']}",
        )])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows) if kb_rows else None
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("hist:team:"))
async def admin_team_history(call: CallbackQuery):
    if not await _is_admin(call.from_user.id):
        await call.answer("Нет прав", show_alert=True)
        return
    league_id = int(call.data.rsplit(":", 1)[-1])
    league = get_league(league_id)
    if not league:
        await call.answer("Состав не найден", show_alert=True)
        return
    rows = get_league_history(league_id, limit=15)
    if not rows:
        await call.message.answer(f"📭 У «{league['name']}» пока нет завершённых игр.")
        await call.answer()
        return
    lines = [f"📜 <b>История «{hd.quote(league['name'])}»</b> (MMR: {int(league['mmr'] or 0)}🌟):", ""]
    for r in rows:
        lines.append("• " + _history_line(r, perspective_league_id=league_id))
    await call.message.answer("\n".join(lines), parse_mode="HTML")
    await call.answer()


# ─── Фоновая задача: сгорание приглашений ─────────────────────────────────

async def scrim_watchdog_task(bot: Bot) -> None:
    await asyncio.sleep(30)
    while True:
        try:
            for scrim in get_expired_scrims():
                db.set_scrim_status(int(scrim["id"]), "expired")
                try:
                    if scrim["status"] == "searching":
                        await bot.send_message(
                            int(scrim["challenger_leader_id"]),
                            f"⏰ Поиск соперника для рандомного скрима #{scrim['id']} завершён: "
                            f"за сутки никто не откликнулся. Создай новый поиск.",
                        )
                    else:
                        await bot.send_message(
                            int(scrim["challenger_leader_id"]),
                            f"⏰ Приглашение на скрим #{scrim['id']} сгорело: соперник не ответил за сутки.",
                        )
                        if scrim["opponent_leader_id"]:
                            await bot.send_message(
                                int(scrim["opponent_leader_id"]),
                                f"⏰ Приглашение на скрим #{scrim['id']} сгорело.",
                            )
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"scrim watchdog error: {e}")
        await asyncio.sleep(300)
