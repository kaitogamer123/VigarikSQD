"""
Обработчик вызова списков кланов обычными игроками из Главного Меню.
С поддержкой кубков Brawl Stars API и защитой от спецсимволов.
"""

import logging
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from database import get_member, get_clan_members
from utils.formatting import format_roster
from config import CLAN_DISPLAY

logger = logging.getLogger(__name__)
router = Router()


@router.message(F.text == "📊 Списки участников кланов")
async def show_clan_lists_menu(message: Message):
    """Показывает инлайн-меню выбора клана для обычного игрока в ЛС."""
    member = await get_member(message.from_user.id)
    if not member or not member.get("registered"):
        await message.answer("❌ Сначала пройдите регистрацию. Напишите /start")
        return

    # Создаем клавиатуру для просмотра списков любого клана сети
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏰 Список Основы (Squad)", callback_data="view_roster:squad")],
        [InlineKeyboardButton(text="🎓 Список Академии (Academy)", callback_data="view_roster:academy")],
        [InlineKeyboardButton(text="⚔️ Список Ивентов (Events)", callback_data="view_roster:events")]
    ])

    await message.answer(
        "<b>📊 Списки участников ViGarik Squad</b>\n\n"
        "Выбери интересующий тебя клан ниже, чтобы посмотреть его актуальный состав, "
        "отсортированный по ролям и кубкам из игры:",
        parse_mode="HTML",
        reply_markup=kb
    )


@router.callback_query(F.data.startswith("view_roster:"))
async def process_view_roster_callback(call: CallbackQuery):
    """Выводит список конкретного клана по нажатию инлайн-кнопки."""
    member = await get_member(call.from_user.id)
    if not member or not member.get("registered"):
        await call.answer("⛔ Вы не зарегистрированы в боте.", show_alert=True)
        return

    clan_key = call.data.split(":")[1]

    # 1. Загружаем из БД профили участников (в БД они уже отсортированы: Роль -> Кубки DESC)
    members = await get_clan_members(clan_key)

    if not members:
        await call.message.edit_text(
            f"📭 Список клана <b>{CLAN_DISPLAY.get(clan_key, clan_key).upper()}</b> сейчас пуст.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад к выбору", callback_data="view_roster_back")]
            ])
        )
        await call.answer()
        return

    # 2. Генерируем красивый HTML через нашу проверенную утилиту formatting.py
    # Она сама расставит медали (🥇🥈🥉), добавит кубки и экранирует спецсимволы в никах
    roster_text = format_roster(clan_key, members)

    # Кнопка возврата в меню выбора кланов
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад к выбору клана", callback_data="view_roster_back")]
    ])

    try:
        await call.message.edit_text(
            text=roster_text,
            parse_mode="HTML",
            reply_markup=back_kb
        )
    except Exception as e:
        logger.error(f"Ошибка при выводе списка клана {clan_key} для игрока: {e}")
        await call.answer("❌ Произошла ошибка при генерации списка.", show_alert=True)

    await call.answer()


@router.callback_query(F.data == "view_roster_back")
async def process_view_roster_back(call: CallbackQuery):
    """Возвращает пользователя назад к кнопкам выбора кланов."""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏰 Список Основы (Squad)", callback_data="view_roster:squad")],
        [InlineKeyboardButton(text="🎓 Список Академии (Academy)", callback_data="view_roster:academy")],
        [InlineKeyboardButton(text="⚔️ Список Ивентов (Events)", callback_data="view_roster:events")]
    ])

    await call.message.edit_text(
        "<b>📊 Списки участников ViGarik Squad</b>\n\n"
        "Выбери интересующий тебя клан ниже, чтобы посмотреть его актуальный состав, "
        "отсортированный по ролям и кубкам из игры:",
        parse_mode="HTML",
        reply_markup=kb
    )
    await call.answer()
