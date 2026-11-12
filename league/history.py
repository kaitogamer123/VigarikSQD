"""История матчей состава и сети."""
from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.markdown import html_decoration as hd
from league.league_db import (
    get_league,
    get_league_history,
    get_recent_completed,
    get_user_league,
    get_verified_leagues,
)
from .competitive_common import _back_inline, _history_line, _is_admin

router = Router()


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
