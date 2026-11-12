"""Участники состава: просмотр, исключение, передача лидерства, выход, роспуск."""
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.markdown import html_decoration as hd
from league.league_db import (
    dissolve_league,
    enforce_verification_requirement,
    get_connection as get_db,
    get_league_members,
    get_user_league,
    has_management_permission,
    leave_league,
    transfer_leadership,
)
from .ui import _back_row, open_settings

router = Router()


# ─── Просмотр участников / набор ─────────────────────────────────────────────
@router.message(F.text.in_({"👥 Участники состава", "👥 Состав лиги"}))
async def view_members(message: Message):
    composition = get_user_league(message.from_user.id)
    if not composition:
        await message.answer("❌ Ты не состоишь в составе."); return
    lines = [f"👥 <b>Участники состава {hd.quote(composition['name'])}</b>"]
    for member in get_league_members(composition["id"]):
        if not member["user_id"]:
            lines.append(f"{member['slot_index']}. — Свободное место —"); continue
        role = "👑 Лидер" if member["role"] == "лидер" else (
            "🛡 Заместитель" if member["role"] == "заместитель" else "👤 Участник"
        )
        lines.append(f"{member['slot_index']}. {hd.quote(member['game_nick'] or 'Игрок')} — {role}")
    is_manager = bool(composition["is_deputy"] or int(composition["leader_id"]) == message.from_user.id)
    destination = "comp:back:settings" if is_manager else "league:back_root"
    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row(destination)]),
    )


@router.message(F.text == "🔒 Закрыть набор / Открыть набор")
async def toggle_open(message: Message, state: FSMContext):
    if not has_management_permission(message.from_user.id, "can_toggle_open"):
        await message.answer("❌ Нет права управлять набором."); return
    composition = get_user_league(message.from_user.id); value = 0 if composition["is_open"] else 1
    conn = get_db(); conn.execute("UPDATE leagues SET is_open = ? WHERE id = ?", (value, composition["id"]))
    conn.commit(); conn.close()
    await message.answer(f"✅ Набор теперь {'открыт' if value else 'закрыт'}.")
    await open_settings(message)


# ─── Исключение участника ─────────────────────────────────────────────────────
async def _show_kick_choices(message: Message, user_id: int, edit: bool = False):
    composition = get_user_league(user_id)
    if not composition or not has_management_permission(user_id, "can_kick"):
        if edit:
            await message.edit_text("❌ Нет права исключать участников.")
        else:
            await message.answer("❌ Нет права исключать участников.")
        return
    buttons = [[InlineKeyboardButton(text=f"🚪 {m['game_nick']}",
                                     callback_data=f"comp:kick:{m['user_id']}")]
               for m in get_league_members(composition["id"], True)
               if int(m["user_id"]) not in {int(composition["leader_id"]), user_id}]
    buttons.append(_back_row("comp:back:settings", "◀️ В настройки"))
    text = "Кого исключить из состава?" if len(buttons) > 1 else "📭 Некого исключать."
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.message(F.text == "🚪 Выгнать участника")
async def kick_choose(message: Message):
    await _show_kick_choices(message, message.from_user.id)


@router.callback_query(F.data == "comp:back:kick")
async def kick_choose_callback(call: CallbackQuery):
    await _show_kick_choices(call.message, call.from_user.id, edit=True)
    await call.answer()


@router.callback_query(F.data.startswith("comp:kick:"))
async def kick_confirm(call: CallbackQuery):
    target = int(call.data.rsplit(":", 1)[-1])
    if not has_management_permission(call.from_user.id, "can_kick"):
        await call.answer("Нет права", show_alert=True); return
    kb = InlineKeyboardMarkup(inline_keyboard=[[ 
        InlineKeyboardButton(text="✅ Да, исключить", callback_data=f"comp:kickyes:{target}"),
    ], _back_row("comp:back:kick")])
    await call.message.edit_text("Вы уверены, что хотите исключить участника?", reply_markup=kb); await call.answer()


@router.callback_query(F.data.startswith("comp:kickyes:"))
async def kick_yes(call: CallbackQuery, bot: Bot):
    target = int(call.data.rsplit(":", 1)[-1]); composition = get_user_league(call.from_user.id)
    if not composition or not has_management_permission(call.from_user.id, "can_kick"):
        await call.answer("Нет права", show_alert=True); return
    if target == int(composition["leader_id"]):
        await call.answer("Нельзя исключить лидера", show_alert=True); return
    conn = get_db(); row = conn.execute("SELECT game_nick FROM league_members WHERE league_id = ? AND user_id = ?",
                                       (composition["id"], target)).fetchone()
    conn.close()
    if not row or not leave_league(target):
        await call.answer("Участник уже вышел", show_alert=True); return
    check = enforce_verification_requirement(int(composition["id"]))
    notice = ""
    if check["revoked"]:
        notice = (
            f"\n\n⚠️ Состав потерял верификацию: осталось {check['count']}/4 участников "
            f"(нужно минимум 3). Добейте состав и подайте заявку заново."
        )
        leader_id = int(composition["leader_id"])
        if leader_id != call.from_user.id:
            try:
                await bot.send_message(
                    leader_id,
                    f"⚠️ Состав «{composition['name']}» [{composition['tag']}] потерял верификацию: "
                    f"осталось {check['count']}/4 участников (нужно минимум 3). "
                    f"Добейте состав и подайте заявку заново.",
                )
            except Exception:
                pass
    await call.message.edit_text(
        f"✅ {hd.quote(row['game_nick'] or str(target))} исключён из состава.{notice}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row("comp:back:settings", "◀️ В настройки")]),
    ); await call.answer()
    try: await bot.send_message(target, f"🚪 Ты исключён из состава {composition['name']}.")
    except Exception: pass


# ─── Роспуск / выход / передача ───────────────────────────────────────────────
@router.message(F.text == "💥 Распустить состав")
async def dissolve_confirm(message: Message):
    composition = get_user_league(message.from_user.id)
    if not composition or int(composition["leader_id"]) != message.from_user.id:
        await message.answer("❌ Только лидер может распустить состав."); return
    kb = InlineKeyboardMarkup(inline_keyboard=[[ 
        InlineKeyboardButton(text="✅ Да, распустить", callback_data="comp:dissolve:yes"),
    ], _back_row("comp:back:settings")])
    await message.answer("⚠️ Вы уверены, что хотите распустить состав? Это действие необратимо.", reply_markup=kb)


@router.callback_query(F.data == "comp:dissolve:yes")
async def dissolve_yes(call: CallbackQuery):
    composition = get_user_league(call.from_user.id)
    if not composition or int(composition["leader_id"]) != call.from_user.id:
        await call.answer("Только лидер может распустить состав", show_alert=True); return
    name = composition["name"]; dissolve_league(composition["id"])
    await call.message.edit_text(
        f"✅ Состав {hd.quote(name)} успешно распущен.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row("league:back_root", "◀️ В составы")]),
    ); await call.answer()


def _member_choice(composition, prefix: str):
    back = "comp:back:settings"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"👤 {m['game_nick']}", callback_data=f"{prefix}:{m['user_id']}")]
        for m in get_league_members(composition["id"], True)
        if int(m["user_id"]) != int(composition["leader_id"])
    ] + [_back_row(back, "◀️ В настройки")])


async def _show_transfer_choices(message: Message, user_id: int, edit: bool = False):
    composition = get_user_league(user_id)
    if not composition or int(composition["leader_id"]) != user_id:
        if edit:
            await message.edit_text("❌ Только лидер может передать лидерство.")
        else:
            await message.answer("❌ Только лидер может передать лидерство.")
        return
    if len(get_league_members(composition["id"], True)) <= 1:
        text = "❌ В составе нет участника, которому можно передать лидерство."
        markup = InlineKeyboardMarkup(inline_keyboard=[_back_row("comp:back:settings", "◀️ В настройки")])
    else:
        text = "Кому передать лидерство?"
        markup = _member_choice(composition, "comp:transfer")
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.message(F.text.in_({"👑 Передать лидерство", "👑 Передать лидерку"}))
async def transfer_choose(message: Message):
    await _show_transfer_choices(message, message.from_user.id)


@router.callback_query(F.data == "comp:back:transfer")
async def transfer_choose_callback(call: CallbackQuery):
    await _show_transfer_choices(call.message, call.from_user.id, edit=True)
    await call.answer()


@router.callback_query(F.data.startswith("comp:transfer:"))
async def transfer_confirm(call: CallbackQuery):
    target = int(call.data.rsplit(":", 1)[-1])
    kb = InlineKeyboardMarkup(inline_keyboard=[[ 
        InlineKeyboardButton(text="✅ Да", callback_data=f"comp:transferyes:{target}"),
    ], _back_row("comp:back:transfer")])
    await call.message.edit_text("Подтвердить передачу лидерства этому участнику?", reply_markup=kb); await call.answer()


@router.callback_query(F.data.startswith("comp:transferyes:"))
async def transfer_yes(call: CallbackQuery, bot: Bot):
    target = int(call.data.rsplit(":", 1)[-1]); composition = get_user_league(call.from_user.id)
    if not composition or int(composition["leader_id"]) != call.from_user.id:
        await call.answer("Нет права", show_alert=True); return
    if not transfer_leadership(composition["id"], call.from_user.id, target):
        await call.answer("Передача не выполнена", show_alert=True); return
    await call.message.edit_text(
        "✅ Лидерство успешно передано.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row("league:back_root", "◀️ В составы")]),
    ); await call.answer()
    try: await bot.send_message(target, f"👑 Тебе передано лидерство составом {composition['name']}.")
    except Exception: pass


async def _show_leave_prompt(message: Message, user_id: int, edit: bool = False):
    composition = get_user_league(user_id)
    if not composition:
        if edit:
            await message.edit_text("❌ Ты не состоишь в составе.")
        else:
            await message.answer("❌ Ты не состоишь в составе.")
        return
    is_leader = int(composition["leader_id"]) == user_id
    occupied = get_league_members(composition["id"], True)
    if not is_leader:
        kb = InlineKeyboardMarkup(inline_keyboard=[[ 
            InlineKeyboardButton(text="✅ Да, выйти", callback_data="comp:leave:yes"),
        ], _back_row("league:back_root", "◀️ В составы")])
        text = "Вы уверены, что хотите выйти из состава?"
    elif len(occupied) == 1:
        kb = InlineKeyboardMarkup(inline_keyboard=[[ 
            InlineKeyboardButton(text="✅ Да, распустить", callback_data="comp:dissolve:yes"),
        ], _back_row("comp:back:settings", "◀️ В настройки")])
        text = "Ты единственный участник. Выйти можно только распустив состав. Продолжить?"
    else:
        kb = _member_choice(composition, "comp:leaveto")
        text = "Кому передать состав перед выходом?"
    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


@router.message(F.text.in_({"🚪 Выйти из состава", "🚪 Выйти из лиги"}))
async def leave_start(message: Message):
    await _show_leave_prompt(message, message.from_user.id)


@router.callback_query(F.data == "comp:back:leave")
async def leave_start_callback(call: CallbackQuery):
    await _show_leave_prompt(call.message, call.from_user.id, edit=True)
    await call.answer()


@router.callback_query(F.data == "comp:leave:yes")
async def leave_yes(call: CallbackQuery, bot: Bot):
    composition = get_user_league(call.from_user.id)
    if not composition:
        await call.answer("Не удалось выйти", show_alert=True); return
    league_id, leader_id = int(composition["id"]), int(composition["leader_id"])
    league_name, league_tag = composition["name"], composition["tag"]
    if not leave_league(call.from_user.id):
        await call.answer("Не удалось выйти", show_alert=True); return
    check = enforce_verification_requirement(league_id)
    if check["revoked"]:
        try:
            await bot.send_message(
                leader_id,
                f"⚠️ Состав «{league_name}» [{league_tag}] потерял верификацию: "
                f"осталось {check['count']}/4 участников (нужно минимум 3). "
                f"Добейте состав и подайте заявку заново.",
            )
        except Exception:
            pass
    await call.message.edit_text(
        "✅ Ты успешно вышел из состава.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row("league:back_root", "◀️ В составы")]),
    ); await call.answer()


@router.callback_query(F.data.startswith("comp:leaveto:"))
async def leader_leave_confirm(call: CallbackQuery):
    target = int(call.data.rsplit(":", 1)[-1])
    kb = InlineKeyboardMarkup(inline_keyboard=[[ 
        InlineKeyboardButton(text="✅ Передать и выйти", callback_data=f"comp:leaveleadyes:{target}"),
    ], _back_row("comp:back:leave")])
    await call.message.edit_text("Передать этому участнику состав и выйти?", reply_markup=kb); await call.answer()


@router.callback_query(F.data.startswith("comp:leaveleadyes:"))
async def leader_leave_yes(call: CallbackQuery, bot: Bot):
    target = int(call.data.rsplit(":", 1)[-1]); composition = get_user_league(call.from_user.id)
    if not composition or int(composition["leader_id"]) != call.from_user.id:
        await call.answer("Нет права", show_alert=True); return
    if not transfer_leadership(composition["id"], call.from_user.id, target, remove_old=True):
        await call.answer("Не удалось передать состав", show_alert=True); return
    check = enforce_verification_requirement(int(composition["id"]))
    notice = ""
    if check["revoked"]:
        notice = (
            f"\n\n⚠️ Состав потерял верификацию: осталось {check['count']}/4 участников "
            f"(нужно минимум 3). Добейте состав и подайте заявку заново."
        )
    await call.message.edit_text(
        f"✅ Состав передан, ты успешно вышел.{notice}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row("league:back_root", "◀️ В составы")]),
    ); await call.answer()
    try: await bot.send_message(target, f"👑 Тебе передан состав {composition['name']} после выхода лидера.{notice}")
    except Exception: pass


@router.callback_query(F.data == "comp:cancel")
async def cancel_action(call: CallbackQuery):
    composition = get_user_league(call.from_user.id)
    is_manager = bool(composition and (composition["is_deputy"] or int(composition["leader_id"]) == call.from_user.id))
    destination = "comp:back:settings" if is_manager else "league:back_root"
    await call.message.edit_text(
        "❌ Действие отменено.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_row(destination)]),
    ); await call.answer()
