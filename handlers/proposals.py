"""
Предложения участников → президентам.
Полная монолитная версия.
"""

import logging
import json
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove, InputMediaPhoto
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command  # ИСПРАВЛЕНО: Собрали все импорты наверх
from aiogram.utils.markdown import html_decoration as hd

# Импортируем состояния из нашего нового базового модуля админки
from handlers.admin_features.base import AdminStates

from database import (
    get_member, add_proposal, get_proposals, get_proposal,
    update_proposal_status, get_all_members,
)
from utils.permissions import can_read_proposals
from utils.keyboards import (
    proposals_list_keyboard, proposal_actions_keyboard, back_keyboard, main_menu, admin_panel_keyboard
)
from config import INITIAL_ADMINS

logger = logging.getLogger(__name__)
router = Router()


class ProposalStates(StatesGroup):
    collecting = State()


# ─── 1. ОТПРАВИТЬ ПРЕДЛОЖЕНИЕ ─────────────────────────────────────────────────

@router.message(F.text == "💡 Отправить предложение")
async def start_proposal(message: Message, state: FSMContext):
    member = await get_member(message.from_user.id)
    if not member or not member.get("registered"):
        await message.answer("Сначала пройди регистрацию через /start")
        return

    await state.set_state(ProposalStates.collecting)
    await state.update_data(texts=[], photos=[])
    await message.answer(
        "✍️ Напиши своё предложение (можно прикрепить фото).\n\n"
        "Когда закончишь — отправь команду /done\n"
        "Для отмены — отправь /cancel",
        reply_markup=ReplyKeyboardRemove()
    )


@router.message(ProposalStates.collecting, Command(commands=["cancel"]))
@router.message(ProposalStates.collecting, F.text.lower() == "/cancel")
async def cancel_proposal(message: Message, state: FSMContext):
    await state.clear()
    member = await get_member(message.from_user.id)
    await message.answer("❌ Сбор предложения отменен.", reply_markup=main_menu(member))


@router.message(ProposalStates.collecting, Command(commands=["done"]))
@router.message(ProposalStates.collecting, F.text.lower() == "/done")
async def done_proposal(message: Message, state: FSMContext, bot: Bot):
    await finalize_proposal(message, state, bot)


@router.message(ProposalStates.collecting, F.photo)
async def collect_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])
    texts  = data.get("texts", [])

    photos.append(message.photo[-1].file_id)
    caption = message.caption or ""
    if caption:
        texts.append(hd.quote(caption.strip()))

    await state.update_data(photos=photos, texts=texts)
    await message.answer("📎 Фото добавлено. Продолжай писать текст или отправь /done для завершения.")


@router.message(ProposalStates.collecting, F.text)
async def collect_text(message: Message, state: FSMContext):
    text = message.text.strip()

    data = await state.get_data()
    texts = data.get("texts", [])
    texts.append(hd.quote(text))

    await state.update_data(texts=texts)
    await message.answer("📝 Текст добавлен. Можно написать еще или отправить /done")


async def finalize_proposal(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    texts  = data.get("texts", [])
    photos = data.get("photos", [])
    full_text = "\n".join(texts)

    if not full_text and not photos:
        await message.answer("❌ Нельзя отправить пустое предложение. Напишите что-нибудь или нажмите /cancel")
        return

    from_id = message.from_user.id
    proposal_id = await add_proposal(from_id, full_text, photos)
    member = await get_member(from_id)

    await state.clear()
    await message.answer(
        "✅ Ваше предложение успешно отправлено президентам клана!",
        reply_markup=main_menu(member),
    )

    presidents_ids = set()
    for uid, info in INITIAL_ADMINS.items():
        if info.get("role") == "president":
            presidents_ids.add(int(uid))

    all_m = await get_all_members()
    for m in all_m:
        if m.get("role") == "president" and m.get("user_id"):
            presidents_ids.add(int(m["user_id"]))

    sender_name = member.get("game_nick") or member.get("username") or message.from_user.first_name
    safe_sender = hd.quote(str(sender_name))

    for pid in presidents_ids:
        try:
            await bot.send_message(
                pid,
                f"📬 <b>Новое предложение</b> от игрока <b>{safe_sender}</b>!\n"
                f"Нажми кнопку «📬 Прочитать предложки» в своем меню для просмотра.",
                parse_mode="HTML",
            )
        except Exception:
            pass


# ─── 2. ЧИТАТЬ ПРЕДЛОЖКИ (ПРЕЗИДЕНТ) ──────────────────────────────────────────

@router.message(F.text == "📬 Прочитать предложки")
async def read_proposals(message: Message):
    member = await get_member(message.from_user.id)
    if not member or not can_read_proposals(member):
        await message.answer("⛔ Нет прав.")
        return

    proposals = await get_proposals("pending")
    if not proposals:
        await message.answer(
            "📭 Предложка пуста.",
            reply_markup=back_keyboard("proposal:back"),
        )
        return

    all_m = {int(m["user_id"]): m for m in await get_all_members() if m.get("user_id")}
    for p in proposals:
        sender = all_m.get(int(p["from_id"]))
        raw_name = (sender.get("game_nick") or sender.get("username") or str(p["from_id"])) if sender else str(p["from_id"])
        p["from_name"] = hd.quote(str(raw_name))

    await message.answer(
        "📬 <b>Предложения участников:</b>",
        parse_mode="HTML",
        reply_markup=proposals_list_keyboard(proposals),
    )


@router.callback_query(F.data == "proposal:list")
async def cb_proposal_list(call: CallbackQuery):
    member = await get_member(call.from_user.id)
    if not member or not can_read_proposals(member):
        await call.answer("⛔ Нет прав.", show_alert=True)
        return

    proposals = await get_proposals("pending")
    if not proposals:
        await call.message.edit_text(
            "📭 Предложка пуста.",
            reply_markup=back_keyboard("proposal:back"),
        )
        return

    all_m = {int(m["user_id"]): m for m in await get_all_members() if m.get("user_id")}
    for p in proposals:
        sender = all_m.get(int(p["from_id"]))
        raw_name = (sender.get("game_nick") or sender.get("username") or str(p["from_id"])) if sender else str(p["from_id"])
        p["from_name"] = hd.quote(str(raw_name))

    await call.message.edit_text(
        "📬 <b>Предложения участников:</b>",
        parse_mode="HTML",
        reply_markup=proposals_list_keyboard(proposals),
    )
    await call.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("proposal:view:"))
async def cb_view_proposal(call: CallbackQuery, bot: Bot):
    member = await get_member(call.from_user.id)
    if not member or not can_read_proposals(member):
        await call.answer("⛔ Нет прав.", show_alert=True)
        return

    proposal_id = int(call.data.split(":")[2])
    proposal = await get_proposal(proposal_id)
    if not proposal:
        await call.answer("Предложение не найдено.", show_alert=True)
        return

    all_m = {m["user_id"]: m for m in await get_all_members()}
    sender = all_m.get(proposal["from_id"])

    raw_name = (sender.get("username") or sender.get("first_name") or str(proposal["from_id"])) if sender else str(proposal["from_id"])
    from_name = hd.quote(str(raw_name))

    proposal_text = hd.quote(str(proposal['text'])) if proposal['text'] else '(без текста)'

    text = (
        f"📩 <b>От: {from_name}</b>\n"
        f"📅 {proposal['sent_at'][:16]}\n\n"
        f"{proposal_text}"
    )

    photos = proposal.get("media_json", [])
    if photos:
        media = [InputMediaPhoto(media=fid) for fid in photos]
        media[0].caption = text
        media[0].parse_mode = "HTML"
        await call.message.answer_media_group(media=media)
        await call.message.answer("Управление предложением:", reply_markup=proposal_actions_keyboard(proposal_id, proposal["from_id"]))
    else:
        await call.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=proposal_actions_keyboard(proposal_id, proposal["from_id"]),
        )
    await call.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("proposal:reject:"))
async def cb_reject_proposal(call: CallbackQuery, bot: Bot):
    member = await get_member(call.from_user.id)
    if not member or not can_read_proposals(member):
        await call.answer("⛔ Нет прав.", show_alert=True)
        return

    proposal_id = int(call.data.split(":")[2])
    proposal = await get_proposal(proposal_id)
    if not proposal:
        await call.answer("Не найдено.", show_alert=True)
        return

    await update_proposal_status(proposal_id, "rejected")

    try:
        await call.message.edit_text("❌ Предложение отклонено.")
    except Exception:
        try:
            await call.message.edit_reply_markup(reply_markup=None)
            await call.message.answer("❌ Предложение отклонено.")
        except Exception:
            pass

    await call.answer()

    try:
        await bot.send_message(
            proposal["from_id"],
            "😔 Твоё предложение было рассмотрено и отклонено.\n"
            "Ты можешь отправить новое предложение в любой момент.",
        )
    except Exception:
        pass


@router.callback_query(lambda c: c.data and c.data.startswith("proposal:reply:"))
async def cb_reply_proposal_start(call: CallbackQuery, state: FSMContext):
    member = await get_member(call.from_user.id)
    if not member or not can_read_proposals(member):
        await call.answer("⛔ Нет прав.", show_alert=True)
    # ─── Продолжение функции cb_reply_proposal_start ───
    parts = call.data.split(":")
    proposal_id = int(parts[2])
    target_user_id = int(parts[3])

    # Переводим администратора в состояние ожидания текста
    await state.set_state(AdminStates.waiting_proposal_answer)
    await state.update_data(reply_to_user_id=target_user_id, reply_proposal_id=proposal_id)

    await call.message.answer(
        "✍️ <b>Введите текст ответа для игрока:</b>\n"
        "Бот автоматически отправит его пользователю в личные сообщения.\n\n"
        "Для отмены отправьте /cancel",
        parse_mode="HTML"
    )
    await call.answer()


# ─── Хэндлер отмены режима ответа ─────────────────────────────────────────────

@router.message(AdminStates.waiting_proposal_answer, Command(commands=["cancel"]))
@router.message(AdminStates.waiting_proposal_answer, F.text.lower() == "/cancel")
async def cb_reply_proposal_cancel(message: Message, state: FSMContext):
    """Сбрасывает состояние ожидания ответа и возвращает админа в главное меню."""
    await state.clear()
    member = await get_member(message.from_user.id)
    await message.answer("❌ Отправка ответа отменена.", reply_markup=main_menu(member))


# ─── Хэндлер отправки ответа пользователю ──────────────────────────────────────

@router.message(AdminStates.waiting_proposal_answer, F.text)
async def cb_reply_proposal_send(message: Message, state: FSMContext, bot: Bot):
    """Отправляет введенный админом текст пользователю в ЛС и обновляет БД."""
    data = await state.get_data()
    target_user_id = data.get("reply_to_user_id")
    proposal_id = data.get("reply_proposal_id")

    if not target_user_id:
        await state.clear()
        return

    # Экранируем ответ админа, защищая ЛС игрока от сбоя HTML-разметки
    reply_text = hd.quote(message.text.strip())

    try:
        await bot.send_message(
            chat_id=target_user_id,
            text=f"✉️ <b>Ответ от Администрации по вашему предложению №{proposal_id}:</b>\n\n"
                 f"{reply_text}",
            parse_mode="HTML"
        )
        await message.answer("✅ Ответ успешно доставлен игроку в ЛС!")

        # Меняем статус предложения в базе данных на "отвечено"
        await update_proposal_status(proposal_id, "answered")

    except Exception:
        await message.answer(f"❌ Не удалось отправить. Игрок заблокировал бота или сменил настройки приватности.")

    member = await get_member(message.from_user.id)
    await state.clear()

    # Возвращаем админа в его панель управления
    await message.answer(
        "Возврат в панель управления.",
        reply_markup=admin_panel_keyboard(member.get("role", "member"))
    )
