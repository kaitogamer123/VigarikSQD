"""Управление заместителями состава."""
from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.markdown import html_decoration as hd
from league.league_db import (
    DEPUTY_PERMISSIONS,
    get_league_members,
    get_user_league,
    remove_deputy,
    set_deputy,
    toggle_deputy_permission,
)
from .shared import PERMISSION_LABELS
from .ui import _back_row

router = Router()


# ─── Заместители ──────────────────────────────────────────────────────────────
def _deputy_panel(league_id: int, user_id: int):
    member = next((m for m in get_league_members(league_id, True) if int(m["user_id"]) == user_id), None)
    if not member:
        return None
    rows = []
    for permission in DEPUTY_PERMISSIONS:
        marker = "✅" if member[permission] else "❌"
        rows.append([InlineKeyboardButton(text=f"{marker} {PERMISSION_LABELS[permission]}",
                                          callback_data=f"comp:depperm:{user_id}:{permission}")])
    rows.append([InlineKeyboardButton(text="🗑 Снять заместителя", callback_data=f"comp:depremove:{user_id}")])
    rows.append([InlineKeyboardButton(text="◀️ К списку", callback_data="comp:deputies:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _deputy_list_keyboard(composition, leader_id: int):
    buttons = []
    for member in get_league_members(composition["id"], True):
        if int(member["user_id"]) == leader_id:
            continue
        marker = "🛡" if member["role"] == "заместитель" else "👤"
        buttons.append([InlineKeyboardButton(text=f"{marker} {member['game_nick']}",
                                             callback_data=f"comp:deputy:{member['user_id']}")])
    buttons.append(_back_row("comp:back:settings", "◀️ В настройки"))
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(F.text == "🛡 Управление заместителями")
async def deputies(message: Message):
    composition = get_user_league(message.from_user.id)
    if not composition or int(composition["leader_id"]) != message.from_user.id:
        await message.answer("❌ Назначать заместителей может только лидер."); return
    members = get_league_members(composition["id"], True)
    text = ("🛡 Выбери участника для назначения или настройки заместителя:"
            if len(members) > 1 else "📭 В составе нет участников для назначения.")
    await message.answer(text, reply_markup=_deputy_list_keyboard(composition, message.from_user.id))


@router.callback_query(F.data == "comp:deputies:list")
async def deputies_callback(call: CallbackQuery):
    composition = get_user_league(call.from_user.id)
    if not composition or int(composition["leader_id"]) != call.from_user.id:
        await call.answer("Нет права", show_alert=True); return
    members = get_league_members(composition["id"], True)
    text = "Выбери участника:" if len(members) > 1 else "📭 В составе нет участников для назначения."
    await call.message.edit_text(
        text,
        reply_markup=_deputy_list_keyboard(composition, call.from_user.id),
    ); await call.answer()


@router.callback_query(F.data.startswith("comp:deputy:"))
async def deputy_select(call: CallbackQuery, bot: Bot):
    target = int(call.data.rsplit(":", 1)[-1]); composition = get_user_league(call.from_user.id)
    if not composition or int(composition["leader_id"]) != call.from_user.id:
        await call.answer("Нет права", show_alert=True); return
    target_row = next((m for m in get_league_members(composition["id"], True)
                       if int(m["user_id"]) == target), None)
    if not target_row:
        await call.answer("Участник не найден", show_alert=True); return
    newly = target_row["role"] != "заместитель"
    if newly and not set_deputy(composition["id"], target):
        await call.answer("Не удалось назначить", show_alert=True); return
    text = (
        f"✅ {hd.quote(target_row['game_nick'])} назначен заместителем.\n\n"
        if newly else f"🛡 Настройка заместителя {hd.quote(target_row['game_nick'])}.\n\n"
    ) + "Какие права хотите выдать заместителю?\n✅ — выдано, ❌ — не выдано. Нажатие переключает право."
    await call.message.edit_text(text, parse_mode="HTML",
                                 reply_markup=_deputy_panel(composition["id"], target)); await call.answer()
    if newly:
        try: await bot.send_message(target, f"🛡 Ты назначен заместителем состава {composition['name']}.")
        except Exception: pass


@router.callback_query(F.data.startswith("comp:depperm:"))
async def deputy_permission(call: CallbackQuery):
    parts = call.data.split(":", 3); target, permission = int(parts[2]), parts[3]
    composition = get_user_league(call.from_user.id)
    if not composition or int(composition["leader_id"]) != call.from_user.id:
        await call.answer("Нет права", show_alert=True); return
    value = toggle_deputy_permission(composition["id"], target, permission)
    if value is None:
        await call.answer("Заместитель не найден", show_alert=True); return
    await call.message.edit_reply_markup(reply_markup=_deputy_panel(composition["id"], target))
    await call.answer("Право выдано ✅" if value else "Право отозвано ❌")


@router.callback_query(F.data.startswith("comp:depremove:"))
async def deputy_remove_confirm(call: CallbackQuery):
    target = int(call.data.rsplit(":", 1)[-1])
    composition = get_user_league(call.from_user.id)
    if not composition or int(composition["leader_id"]) != call.from_user.id:
        await call.answer("Нет права", show_alert=True); return
    kb = InlineKeyboardMarkup(inline_keyboard=[[ 
        InlineKeyboardButton(text="✅ Да, снять", callback_data=f"comp:depremoveyes:{target}"),
        InlineKeyboardButton(text="❌ Нет", callback_data=f"comp:deputy:{target}"),
    ]])
    await call.message.edit_text("Снять этого заместителя? История назначения будет удалена.", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("comp:depremoveyes:"))
async def deputy_remove_yes(call: CallbackQuery, bot: Bot):
    target = int(call.data.rsplit(":", 1)[-1]); composition = get_user_league(call.from_user.id)
    if not composition or int(composition["leader_id"]) != call.from_user.id:
        await call.answer("Нет права", show_alert=True); return
    remove_deputy(composition["id"], target)
    await call.message.edit_text(
        "✅ Заместитель снят. Память о назначении удалена.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row("comp:deputies:list", "◀️ К заместителям")]),
    ); await call.answer()
    try: await bot.send_message(target, f"ℹ️ Ты больше не заместитель состава {composition['name']}.")
    except Exception: pass
