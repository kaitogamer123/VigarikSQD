"""
Топ пуша клана: команда /ClanTopPush.
Шаги: выбор клана (3 кнопки) -> выбор периода (1 час / 3 часа / 24 часа)
-> список участников, которые АПНУЛИ трофеи за выбранный период.
Те, кто апнул 0, в список не выводятся.
Данные — из накопительной базы player_stats.db.
"""
import logging
import re

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)
from aiogram.utils.markdown import html_decoration as hd

from config import CLAN_CHATS, CLAN_DISPLAY, CLAN_HEADER_EMOJI
from database import get_clan_members
from player_stats_db import get_players_window_stats, norm_tag

logger = logging.getLogger(__name__)
router = Router()




NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

PERIODS = {
    "1": ("⏱ За последний час", 1),
    "3": ("🕒 За последние 3 часа", 3),
    "24": ("📅 За последние 24 часа", 24),
}


def _clan_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for key, info in (CLAN_CHATS or {}).items():
        title = (info or {}).get("title") or CLAN_DISPLAY.get(key, key)
        emoji = (CLAN_HEADER_EMOJI or {}).get(key, "🏰")
        rows.append([InlineKeyboardButton(text=f"{emoji} {title}", callback_data=f"ctp:clan:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _period_keyboard(clan_key: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="⏱ 1 час", callback_data=f"ctp:period:{clan_key}:1"),
            InlineKeyboardButton(text="🕒 3 часа", callback_data=f"ctp:period:{clan_key}:3"),
        ],
        [InlineKeyboardButton(text="📅 24 часа", callback_data=f"ctp:period:{clan_key}:24")],
        [InlineKeyboardButton(text="◀️ Другой клан", callback_data="ctp:home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _result_keyboard(clan_key: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="⏱ 1 час", callback_data=f"ctp:period:{clan_key}:1"),
            InlineKeyboardButton(text="🕒 3 часа", callback_data=f"ctp:period:{clan_key}:3"),
            InlineKeyboardButton(text="📅 24 часа", callback_data=f"ctp:period:{clan_key}:24"),
        ],
        [InlineKeyboardButton(text="◀️ Другой клан", callback_data="ctp:home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _clan_title(clan_key: str) -> str:
    return (CLAN_CHATS or {}).get(clan_key, {}).get("title") or CLAN_DISPLAY.get(clan_key, clan_key)


def _fmt_signed(value: int) -> str:
    value = int(value or 0)
    return f"+{value}" if value > 0 else str(value)


# Ловит /clantoppush и /clantoppush@ИмяБота в любом чате, независимо от
# регистра и упоминания. Надёжнее фильтра Command в групповых чатах.
_TOP_PUSH_RE = re.compile(r"^/clantoppush(?:@[A-Za-z0-9_]+)?(?:\s|$)", re.IGNORECASE)


def _is_top_push_command(message: Message) -> bool:
    return bool(message.text and _TOP_PUSH_RE.match(message.text.strip()))


async def build_top_push(clan_key: str, hours: int) -> str:
    members = await get_clan_members(clan_key)
    tags = [m.get("player_tag") for m in members if m.get("player_tag")]
    stats = await get_players_window_stats(tags, hours=hours)

    rows = []
    for m in members:
        tag = norm_tag(m.get("player_tag"))
        s = stats.get(tag) or {}
        trophies = int(s.get("trophies", 0))
        games = int(s.get("count", 0))
        # В топ идут только те, кто реально АПНУЛ трофеи (строго больше нуля).
        if trophies > 0:
            rows.append({
                "nick": m.get("game_nick") or m.get("username") or m.get("first_name") or "Без ника",
                "trophies": trophies,
                "games": games,
            })

    rows.sort(key=lambda p: (p["trophies"], p["games"]), reverse=True)

    emoji = (CLAN_HEADER_EMOJI or {}).get(clan_key, "🏰")
    title = hd.quote(str(_clan_title(clan_key)))
    period_title = {1: "⏱ Топ пуша за последний час",
                    3: "🕒 Топ пуша за последние 3 часа",
                    24: "📅 Топ пуша за последние 24 часа"}.get(hours, f"Топ пуша за {hours} ч")

    lines = [
        f"{emoji} <b>{title}</b> {emoji}",
        f"<b>{period_title}</b>",
        "",
    ]

    if not rows:
        lines.append("📭 За этот период никто не апнул трофеи.")
    else:
        total = sum(p["trophies"] for p in rows)
        lines.append(f"🏆 Общий прирост: {_fmt_signed(total)}   ·   👥 пушило: {len(rows)}")
        lines.append("")
        for index, p in enumerate(rows, start=1):
            if index == 1:
                medal = "🥇"
            elif index == 2:
                medal = "🥈"
            elif index == 3:
                medal = "🥉"
            else:
                medal = "▫️"
            nick = hd.quote(str(p["nick"]))
            games_suffix = f"  <i>(🎮 {p['games']})</i>" if p["games"] else ""
            lines.append(f"{medal} {index}. {nick} — {_fmt_signed(p['trophies'])} 🏆{games_suffix}")

    lines.append("")
    lines.append("ℹ️ Учитываются только положительные приросты по сохранённым боям.")
    return "\n".join(lines)


@router.message(_is_top_push_command)
async def cmd_clan_top_push(message: Message):
    await message.answer(
        "🏆 <b>Топ пуша клана</b>\n\nВыбери клан, по которому хочешь посмотреть прирост трофеев:",
        parse_mode="HTML",
        reply_markup=_clan_keyboard(),
    )


@router.callback_query(F.data == "ctp:home")
async def cb_ctp_home(call: CallbackQuery):
    await call.answer()
    try:
        await call.message.edit_text(
            "🏆 <b>Топ пуша клана</b>\n\nВыбери клан:",
            parse_mode="HTML",
            reply_markup=_clan_keyboard(),
        )
    except Exception as e:
        logger.debug(f"ctp home edit error: {e}")


@router.callback_query(F.data.startswith("ctp:clan:"))
async def cb_ctp_choose_clan(call: CallbackQuery):
    clan_key = call.data.rsplit(":", 1)[-1]
    if clan_key not in (CLAN_CHATS or {}):
        await call.answer("Клан не найден", show_alert=True)
        return
    await call.answer()
    emoji = (CLAN_HEADER_EMOJI or {}).get(clan_key, "🏰")
    title = hd.quote(str(_clan_title(clan_key)))
    try:
        await call.message.edit_text(
            f"{emoji} <b>{title}</b>\n\nВыбери период, за который посмотреть прирост трофеев:",
            parse_mode="HTML",
            reply_markup=_period_keyboard(clan_key),
        )
    except Exception as e:
        logger.debug(f"ctp clan edit error: {e}")


@router.callback_query(F.data.startswith("ctp:period:"))
async def cb_ctp_period(call: CallbackQuery):
    parts = call.data.split(":")
    # формат: ctp:period:<clan>:<hours>
    if len(parts) != 4:
        await call.answer("Некорректная кнопка", show_alert=True)
        return
    clan_key = parts[2]
    hours_raw = parts[3]
    if clan_key not in (CLAN_CHATS or {}) or hours_raw not in PERIODS:
        await call.answer("Некорректный выбор", show_alert=True)
        return

    hours = PERIODS[hours_raw][1]
    await call.answer("⏳ Считаю топ пуша...")
    try:
        text = await build_top_push(clan_key, hours)
        try:
            await call.message.edit_text(
                text,
                parse_mode="HTML",
                reply_markup=_result_keyboard(clan_key),
                link_preview_options=NO_PREVIEW,
            )
        except Exception:
            await call.message.answer(
                text,
                parse_mode="HTML",
                reply_markup=_result_keyboard(clan_key),
                link_preview_options=NO_PREVIEW,
            )
    except Exception as e:
        logger.error(f"ctp period render error: {e}")
        await call.answer("❌ Не удалось построить топ, попробуй позже", show_alert=True)
