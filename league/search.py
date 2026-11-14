"""Биржа игроков LFT: поиск команды, анкеты и приглашения."""
import asyncio
import logging
import sqlite3
import time
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.markdown import html_decoration as hd

from config import (
    CLAN_DISPLAY,
    CLAN_HEADER_EMOJI,
    LFT_COMMENT_MAX_LEN,
    LFT_PAGE_SIZE,
    LFT_TOGGLE_COOLDOWN_SEC,
    LFT_TTL_DAYS,
)
from database import get_member
from league.league_db import (
    get_connection,
    get_league,
    get_league_members,
    get_user_league,
    has_management_permission,
    lft_count,
    lft_disable,
    lft_get,
    lft_is_active,
    lft_leaders_with_free_slot,
    lft_list,
    lft_set_active,
    lft_set_comment,
    lft_touch,
)
from .competitive_common import SCRIM_MENU_TEXTS
from .overview import composition_card_text
from .shared import LeagueStates
from .ui import _input_keyboard, _settings_keyboard, show_root

router = Router()
logger = logging.getLogger(__name__)

_last_toggle: dict[int, float] = {}


def _registered(member) -> bool:
    try:
        return bool(member) and int(member.get("registered") or 0) == 1
    except Exception:
        return False


def _fmt_date(raw) -> str:
    if not raw:
        return "—"
    text = str(raw)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt).strftime("%d.%m.%Y")
        except ValueError:
            continue
    return text[:10]


def _clan_label(clan) -> str:
    key = str(clan or "").strip()
    if not key:
        return "—"
    name = CLAN_DISPLAY.get(key, key)
    emoji = CLAN_HEADER_EMOJI.get(key, "🏰")
    return f"{emoji} {name}" if key in CLAN_DISPLAY else str(name)


def _cooldown_left(user_id: int) -> int:
    last = _last_toggle.get(int(user_id))
    if not last:
        return 0
    left = LFT_TOGGLE_COOLDOWN_SEC - (time.monotonic() - last)
    return max(0, int(left) + 1) if left > 0 else 0


def _card_text(user_id: int, lft_row, member) -> str:
    nick = (member or {}).get("game_nick") or (member or {}).get("first_name") or "Игрок"
    tag = (member or {}).get("player_tag") or "—"
    trophies = int((member or {}).get("trophies") or 0)
    lines = [
        "🔎 <b>Игрок в поиске</b>",
        "",
        f"👤 Ник: <b>{hd.quote(str(nick))}</b>",
        f"🏷 Тег: <b>{hd.quote(str(tag))}</b>",
        f"🏆 Кубки: <b>{trophies:,}</b>".replace(",", " "),
        f"🏰 Клан: <b>{hd.quote(_clan_label((member or {}).get('clan')))}</b>",
        f"📅 В поиске с: <b>{_fmt_date(lft_row['created_at'] if lft_row else None)}</b>",
        f"⏳ Скрытие: <b>{_fmt_date(lft_row['expires_at'] if lft_row else None)}</b>",
    ]
    comment = str(lft_row["comment"] or "").strip() if lft_row else ""
    if comment:
        lines.append(f"💬 «{hd.quote(comment)}»")
    return "\n".join(lines)


def _own_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏹ Остановить поиск", callback_data="lft:toggle")],
        [InlineKeyboardButton(text="✏️ Комментарий", callback_data="lft:comment")],
        [InlineKeyboardButton(text="🔁 Продлить на 7 дней", callback_data="lft:extend")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="league:back_root")],
    ])


def _has_free_slot(league_id: int) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM league_members WHERE league_id = ? AND user_id IS NULL LIMIT 1",
        (int(league_id),),
    ).fetchone()
    conn.close()
    return bool(row)


def _in_any_league(user_id: int) -> bool:
    conn = get_connection()
    row = conn.execute("SELECT 1 FROM league_members WHERE user_id = ? LIMIT 1",
                       (int(user_id),)).fetchone()
    conn.close()
    return bool(row)


def _invite_exists(league_id: int, user_id: int) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM league_invites WHERE league_id = ? AND invitee_id = ?",
        (int(league_id), int(user_id)),
    ).fetchone()
    conn.close()
    return bool(row)


def _invite_allowed(caller_id: int, target_id: int, composition=None):
    """(можно ли приглашать, причина отказа)."""
    if int(caller_id) == int(target_id):
        return False, "Нельзя пригласить самого себя"
    if not composition:
        return False, "Приглашать может только лидер или заместитель состава"
    if not has_management_permission(int(caller_id), "can_invite"):
        return False, "У тебя нет права приглашать игроков"
    if not _has_free_slot(int(composition["id"])):
        return False, "В составе нет свободного слота"
    if _in_any_league(int(target_id)):
        return False, "Игрок уже состоит в составе"
    if _invite_exists(int(composition["id"]), int(target_id)):
        return False, "Этому игроку уже отправлено приглашение"
    return True, ""


async def _notify_leaders(bot: Bot, user_id: int) -> None:
    """Рассылка лидерам с тумблером вкл и свободным слотом."""
    try:
        member = await get_member(int(user_id))
        lft_row = lft_get(int(user_id))
        if not lft_row or not int(lft_row["is_active"] or 0):
            return
        text = _card_text(int(user_id), lft_row, member)
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📨 Пригласить", callback_data=f"lft:invite:{user_id}"),
            InlineKeyboardButton(text="👁 Открыть анкету", callback_data=f"lft:card:{user_id}"),
        ]])
        for league in lft_leaders_with_free_slot():
            if int(league["leader_id"]) == int(user_id):
                continue
            try:
                await bot.send_message(int(league["leader_id"]), text,
                                       parse_mode="HTML", reply_markup=kb)
            except Exception as e:
                logger.info(f"LFT: лидеру {league['leader_id']} не отправлено: {e}")
            await asyncio.sleep(0.05)
    except Exception as e:
        logger.error(f"LFT: ошибка рассылки лидерам по {user_id}: {e}")


async def _send_status(message: Message, user_id: int) -> None:
    lft_row = lft_get(int(user_id))
    member = await get_member(int(user_id))
    await message.answer(_card_text(int(user_id), lft_row, member),
                         parse_mode="HTML", reply_markup=_own_kb())


def _list_kb(page: int, rows) -> InlineKeyboardMarkup:
    buttons = []
    for row in rows:
        nick = row["game_nick"] or "Игрок"
        clan = _clan_label(row["clan"])
        buttons.append([InlineKeyboardButton(
            text=f"🔎 {nick} · {clan}"[:60],
            callback_data=f"lft:card:{int(row['user_id'])}",
        )])
    total = lft_count()
    pages = max(1, (total + LFT_PAGE_SIZE - 1) // LFT_PAGE_SIZE)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"lft:list:{page - 1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"lft:list:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="◀️ В составы", callback_data="league:back_root")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _list_text(page: int, total: int) -> str:
    if total <= 0:
        return "📭 Сейчас никто не ищет команду."
    pages = max(1, (total + LFT_PAGE_SIZE - 1) // LFT_PAGE_SIZE)
    return f"🔎 <b>Игроки в поиске</b> (стр. {page + 1}/{pages})\n\nВыбери игрока:"


async def _show_list(message: Message, page: int = 0, edit: bool = False) -> None:
    total = lft_count()
    pages = max(1, (total + LFT_PAGE_SIZE - 1) // LFT_PAGE_SIZE)
    page = min(max(0, int(page or 0)), pages - 1)
    rows = lft_list(page, LFT_PAGE_SIZE)
    text = _list_text(page, total)
    kb = _list_kb(page, rows) if total else InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="◀️ В составы", callback_data="league:back_root"),
    ]])
    if edit:
        await message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)


# ─── Включение / выключение поиска ──────────────────────────────────────────

@router.message(F.text == "🔎 Я ищу команду")
async def lft_start(message: Message, state: FSMContext, bot: Bot):
    member = await get_member(message.from_user.id)
    if not _registered(member):
        await message.answer("Сначала пройди регистрацию в боте (/start).")
        return
    if get_user_league(message.from_user.id):
        await message.answer("Ты уже состоишь в составе — статус поиска недоступен.")
        return
    row = lft_get(message.from_user.id)
    if row and int(row["is_active"] or 0) == 1:
        await _send_status(message, message.from_user.id)
        return
    left = _cooldown_left(message.from_user.id)
    if left:
        await message.answer(f"⏳ Подожди {left} сек. перед повторным переключением.")
        return
    lft_set_active(message.from_user.id, True, LFT_TTL_DAYS)
    _last_toggle[message.from_user.id] = time.monotonic()
    await message.answer("✅ Ты в поиске. Лидеры составов увидят тебя в списке.")
    await _send_status(message, message.from_user.id)
    asyncio.create_task(_notify_leaders(bot, message.from_user.id))


@router.message(F.text == "⏹ Остановить поиск")
async def lft_stop(message: Message, state: FSMContext):
    left = _cooldown_left(message.from_user.id)
    if left:
        await message.answer(f"⏳ Подожди {left} сек. перед повторным переключением.")
        return
    lft_set_active(message.from_user.id, False)
    _last_toggle[message.from_user.id] = time.monotonic()
    await message.answer("⏹ Поиск команды остановлен. Анкета скрыта из списка.")
    await show_root(message, state, user_id=message.from_user.id)


@router.callback_query(F.data == "lft:toggle")
async def lft_toggle_callback(call: CallbackQuery, state: FSMContext, bot: Bot):
    member = await get_member(call.from_user.id)
    if not _registered(member):
        await call.answer("Сначала пройди регистрацию в боте (/start)", show_alert=True)
        return
    if get_user_league(call.from_user.id):
        await call.answer("Ты уже состоишь в составе", show_alert=True)
        return
    left = _cooldown_left(call.from_user.id)
    if left:
        await call.answer(f"⏳ Подожди {left} сек.", show_alert=True)
        return
    row = lft_get(call.from_user.id)
    active = bool(row and int(row["is_active"] or 0) == 1)
    if active:
        lft_set_active(call.from_user.id, False)
        await call.answer("⏹ Поиск остановлен")
        try:
            await call.message.edit_text("⏹ Поиск команды остановлен.",
                                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                                             InlineKeyboardButton(text="◀️ В составы", callback_data="league:back_root"),
                                         ]]))
        except Exception:
            pass
    else:
        lft_set_active(call.from_user.id, True, LFT_TTL_DAYS)
        await call.answer("✅ Поиск включён")
        try:
            await call.message.edit_text(
                _card_text(call.from_user.id, lft_get(call.from_user.id), member),
                parse_mode="HTML", reply_markup=_own_kb())
        except Exception:
            pass
        asyncio.create_task(_notify_leaders(bot, call.from_user.id))
    _last_toggle[call.from_user.id] = time.monotonic()


@router.callback_query(F.data == "lft:status")
async def lft_status_callback(call: CallbackQuery):
    await _send_status(call.message, call.from_user.id)
    await call.answer()


@router.callback_query(F.data == "lft:extend")
async def lft_extend(call: CallbackQuery):
    if get_user_league(call.from_user.id):
        await call.answer("Ты уже состоишь в составе — поиск недоступен", show_alert=True)
        return
    row = lft_get(call.from_user.id)
    if not row:
        await call.answer("Анкета не найдена — включи поиск заново", show_alert=True)
        return
    lft_touch(call.from_user.id, LFT_TTL_DAYS)
    await call.answer("🔁 Анкета продлена на 7 дней")
    member = await get_member(call.from_user.id)
    try:
        text = _card_text(call.from_user.id, lft_get(call.from_user.id), member)
        if call.message.text:
            await call.message.edit_text(text, parse_mode="HTML", reply_markup=_own_kb())
    except Exception:
        pass


# ─── Комментарий ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "lft:comment")
async def lft_comment_ask(call: CallbackQuery, state: FSMContext):
    row = lft_get(call.from_user.id)
    if not row or not int(row["is_active"] or 0):
        await call.answer("Сначала включи поиск команды", show_alert=True)
        return
    await state.set_state(LeagueStates.waiting_lft_comment)
    prompt = (f"✏️ Напиши комментарий к анкете (до {LFT_COMMENT_MAX_LEN} символов).\n"
              "Пустое сообщение удалит комментарий.")
    try:
        await call.message.answer(prompt, reply_markup=_input_keyboard())
    except Exception:
        try:
            await call.bot.send_message(call.from_user.id, prompt,
                                        reply_markup=_input_keyboard())
        except Exception:
            pass
    await call.answer()


@router.message(LeagueStates.waiting_lft_comment,
                F.text.in_({"◀️ Назад", "❌ Отмена", "◀️ Назад в составы"}))
async def lft_comment_cancel(message: Message, state: FSMContext):
    await state.clear()
    await show_root(message, state, user_id=message.from_user.id)


@router.message(LeagueStates.waiting_lft_comment,
                F.text.startswith("/") | F.text.in_(SCRIM_MENU_TEXTS))
async def lft_comment_guard(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Изменение комментария отменено.")
    await show_root(message, state, user_id=message.from_user.id)


@router.message(LeagueStates.waiting_lft_comment)
async def lft_comment_save(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if len(text) > LFT_COMMENT_MAX_LEN:
        await message.answer(
            f"❌ Слишком длинно: {len(text)} символов, максимум {LFT_COMMENT_MAX_LEN}.")
        return
    lft_set_comment(message.from_user.id, text)
    await state.clear()
    await message.answer("✅ Комментарий обновлён." if text else "✅ Комментарий удалён.")
    await _send_status(message, message.from_user.id)


# ─── Список и анкеты ────────────────────────────────────────────────────────

@router.message(F.text == "👀 Игроки в поиске")
async def lft_players(message: Message):
    member = await get_member(message.from_user.id)
    if not _registered(member):
        await message.answer("Сначала пройди регистрацию в боте (/start).")
        return
    await _show_list(message, 0)


@router.callback_query(F.data.startswith("lft:list:"))
async def lft_list_callback(call: CallbackQuery):
    page = int(call.data.rsplit(":", 1)[-1])
    try:
        await _show_list(call.message, page, edit=True)
    except Exception:
        total = lft_count()
        pages = max(1, (total + LFT_PAGE_SIZE - 1) // LFT_PAGE_SIZE)
        page = min(max(0, page), pages - 1)
        try:
            await call.bot.send_message(
                call.from_user.id, _list_text(page, total), parse_mode="HTML",
                reply_markup=_list_kb(page, lft_list(page, LFT_PAGE_SIZE)))
        except Exception:
            pass
    await call.answer()


@router.callback_query(F.data.startswith("lft:card:"))
async def lft_card(call: CallbackQuery):
    target_id = int(call.data.rsplit(":", 1)[-1])
    lft_row = lft_get(target_id)
    if not lft_row or not lft_is_active(target_id):
        await call.answer("Анкета больше не активна", show_alert=True)
        return
    member = await get_member(target_id)
    if not _registered(member):
        await call.answer("Игрок больше не зарегистрирован", show_alert=True)
        return
    buttons = []
    composition = get_user_league(call.from_user.id)
    allowed, _reason = _invite_allowed(call.from_user.id, target_id, composition)
    if allowed:
        buttons.append([InlineKeyboardButton(text="📨 Пригласить в состав",
                                             callback_data=f"lft:invite:{target_id}")])
    buttons.append([InlineKeyboardButton(text="◀️ К списку", callback_data="lft:list:0")])
    text = _card_text(target_id, lft_row, member)
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    try:
        await call.message.answer(text, parse_mode="HTML", reply_markup=markup)
    except Exception:
        try:
            await call.bot.send_message(call.from_user.id, text, parse_mode="HTML",
                                        reply_markup=markup)
        except Exception:
            pass
    await call.answer()


# ─── Приглашение ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("lft:invite:"))
async def lft_invite(call: CallbackQuery, bot: Bot):
    target_id = int(call.data.rsplit(":", 1)[-1])
    composition = get_user_league(call.from_user.id)
    allowed, reason = _invite_allowed(call.from_user.id, target_id, composition)
    if not allowed:
        await call.answer(reason, show_alert=True)
        return
    lft_row = lft_get(target_id)
    if not lft_row or not lft_is_active(target_id):
        await call.answer("Игрок больше не в поиске", show_alert=True)
        return
    member = await get_member(target_id)
    if not _registered(member):
        await call.answer("Игрок больше не зарегистрирован", show_alert=True)
        return

    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO league_invites (league_id, inviter_id, invitee_id) VALUES (?, ?, ?)",
            (int(composition["id"]), call.from_user.id, target_id),
        )
        conn.commit()
        invite_id = int(cur.lastrowid)
    except sqlite3.IntegrityError:
        conn.close()
        await call.answer("Этому игроку уже отправлено приглашение", show_alert=True)
        return
    except Exception as e:
        conn.close()
        logger.error(f"LFT: не удалось создать приглашение {target_id}: {e}")
        await call.answer("Не удалось создать приглашение", show_alert=True)
        return
    conn.close()

    team_text = composition_card_text(composition, get_league_members(int(composition["id"])))
    text = (
        "📨 <b>Приглашение в состав</b>\n\n"
        f"{team_text}\n\n"
        "Принять приглашение?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"league:invite_accept:{invite_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"league:invite_reject:{invite_id}"),
    ]])
    try:
        await bot.send_message(target_id, text, parse_mode="HTML", reply_markup=kb)
        await call.answer("✅ Приглашение отправлено")
    except Exception:
        await call.answer(
            "⚠️ У игрока закрыты личные сообщения. Приглашение сохранено — "
            "он увидит его в списке приглашений.",
            show_alert=True,
        )


# ─── Тумблер уведомлений лидера ─────────────────────────────────────────────

def _toggle_notify(user_id: int) -> bool:
    composition = get_user_league(user_id)
    if not composition:
        return False
    try:
        current = int(composition["notify_lft"] if composition["notify_lft"] is not None else 1)
    except (IndexError, KeyError, TypeError, ValueError):
        current = 1
    new_value = 0 if current else 1
    conn = get_connection()
    conn.execute("UPDATE leagues SET notify_lft = ? WHERE id = ?",
                 (new_value, int(composition["id"])))
    conn.commit()
    conn.close()
    return bool(new_value)


@router.message(F.text.startswith("🔔 Уведомления о поиске"))
async def lft_notify_toggle_text(message: Message):
    composition = get_user_league(message.from_user.id)
    if not composition or int(composition["leader_id"]) != message.from_user.id:
        await message.answer("❌ Настройка доступна только лидеру состава.")
        return
    enabled = _toggle_notify(message.from_user.id)
    await message.answer(
        f"🔔 Уведомления о поиске: <b>{'Вкл' if enabled else 'Выкл'}</b>",
        parse_mode="HTML",
    )
    composition = get_user_league(message.from_user.id)
    if composition:
        await message.answer(
            "⚙️ <b>Настройка состава</b>\n\nВыбери действие:",
            parse_mode="HTML",
            reply_markup=_settings_keyboard(composition, message.from_user.id),
        )


@router.callback_query(F.data == "lft:notify_toggle")
async def lft_notify_toggle_callback(call: CallbackQuery):
    composition = get_user_league(call.from_user.id)
    if not composition or int(composition["leader_id"]) != call.from_user.id:
        await call.answer("Только лидер состава", show_alert=True)
        return
    enabled = _toggle_notify(call.from_user.id)
    await call.answer(f"Уведомления о поиске: {'Вкл' if enabled else 'Выкл'}")
