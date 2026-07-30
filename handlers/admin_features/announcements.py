"""
Модуль глобальных объявлений по новостным топикам кланов ViGarik Squad.
"""

import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command  # ДОБАВЛЕНО: Для перехвата отмены
from aiogram.exceptions import TelegramBadRequest  # ДОБАВЛЕНО: Защита от кривой HTML-верстки админа

from database import get_member
from utils.permissions import can_edit_list
from utils.keyboards import main_menu
from config import CLAN_DISPLAY, ADMIN_NEWS_TARGETS
from .base import AdminStates

logger = logging.getLogger(__name__)
router = Router()


@router.message(F.text == "📢 Сделать объявление")
async def cmd_announcement_start(message: Message, state: FSMContext):
    member = await get_member(message.from_user.id)
    if not member or not can_edit_list(member):
        await message.answer("⛔ Недостаточно прав. Требуется Вице Президент и выше.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="edit_list:cancel")]
    ])

    await message.answer(
        "📢 <b>Режим глобального объявления</b>\n\n"
        "Отправь следующим сообщением текст твоего объявления.\n"
        "Вы можете использовать HTML-разметку (b, i, code, u).\n\n"
        "<i>(Можно прикрепить одну картинку/фотографию — бот перешлет её вместе с текстом)</i>\n"
        "Для отмены отправьте /cancel",
        parse_mode="HTML",
        reply_markup=kb
    )
    await state.set_state(AdminStates.waiting_announcement_text)


# ДОБАВЛЕНО: Изолированный обработчик отмены рассылки
@router.message(AdminStates.waiting_announcement_text, Command(commands=["cancel"]))
@router.message(AdminStates.waiting_announcement_text, F.text.lower() == "/cancel")
async def process_announcement_cancel(message: Message, state: FSMContext):
    await state.clear()
    editor = await get_member(message.from_user.id)
    await message.answer("❌ Рассылка объявления отменена.", reply_markup=main_menu(editor))


@router.message(AdminStates.waiting_announcement_text)
async def process_announcement_send(message: Message, state: FSMContext, bot: Bot):
    editor = await get_member(message.from_user.id)
    await state.clear()

    success_chats = []
    failed_chats = []
    html_error = False

    for clan_key, target in ADMIN_NEWS_TARGETS.items():
        chat_id = target.get("chat_id")
        thread_id = target.get("thread_id")

        if not chat_id or not thread_id:
            continue

        try:
            if message.photo:
                photo_id = message.photo[-1].file_id
                # ИСПРАВЛЕНО: Безопасное приведение caption к строке во избежание ошибки None при parse_mode="HTML"
                safe_caption = message.caption if message.caption else ""
                await bot.send_photo(
                    chat_id=chat_id,
                    message_thread_id=thread_id,
                    photo=photo_id,
                    caption=safe_caption,
                    parse_mode="HTML"
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=thread_id,
                    text=message.text,
                    parse_mode="HTML"
                )
            success_chats.append(CLAN_DISPLAY.get(clan_key, clan_key))

        except TelegramBadRequest as e:
            # ИСПРАВЛЕНО: Перехват ошибок синтаксиса HTML-тегов, введенных админом вручную
            if "can't parse entities" in e.message or "character" in e.message:
                html_error = True
                failed_chats.append(f"{CLAN_DISPLAY.get(clan_key, clan_key)} (Ошибка тегов HTML)")
            else:
                failed_chats.append(CLAN_DISPLAY.get(clan_key, clan_key))
            logger.warning(f"Ошибка разметки/отправки в {clan_key}: {e}")
        except Exception as e:
            failed_chats.append(CLAN_DISPLAY.get(clan_key, clan_key))
            logger.warning(f"Непредвиденная ошибка отправки объявления в {clan_key}: {e}")

    # Формируем отчет о рассылке
    report = "📢 <b>Результат рассылки объявления:</b>\n\n"
    if success_chats:
        report += "✅ <b>Успешно отправлено в новости:</b>\n"
        for name in success_chats:
            report += f"  • {name}\n"

    if failed_chats:
        report += "\n❌ <b>Не удалось отправить в чаты:</b>\n"
        for name in failed_chats:
            report += f"  • {name}\n"

        if html_error:
            report += "\n⚠️ <b>Внимание:</b> Бот обнаружил ошибку в закрытии HTML тегов (например, незакрытая скобка < или > или некорректный знак &). Избегайте лишних спецсимволов."

    await message.answer(report, parse_mode="HTML", reply_markup=main_menu(editor))
