"""Верификация составов."""
import logging
from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.markdown import html_decoration as hd
from config import ADMIN_CHAT_ID
from league.league_db import (
    count_clan_members,
    create_verify_request,
    decide_verify_request,
    get_league,
    get_league_members,
    get_pending_verify_request,
    get_user_league,
    get_verify_request,
    set_league_verified,
)
from .competitive_common import VERIFY_BUTTON_TEXTS, _is_admin, _is_leader, _is_verified

logger = logging.getLogger(__name__)
router = Router()


@router.message(F.text.in_(VERIFY_BUTTON_TEXTS))
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
