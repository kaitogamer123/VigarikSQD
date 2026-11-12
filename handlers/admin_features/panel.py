"""Вход в админ-панель и папки меню."""
import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from database import get_member, get_setting
from utils.permissions import is_any_admin, has_role
from utils.keyboards import (
    main_menu, admin_panel_keyboard, admin_push_keyboard, admin_members_keyboard,
    admin_participants_keyboard, admin_scrims_keyboard,
)
from config import ROLE_LABELS

logger = logging.getLogger(__name__)
router = Router()


@router.message(F.text == "👔 Для админов")
@router.message(F.text == "👔 Для аgминов")
@router.message(F.text.contains("Для админ"))
@router.message(F.text.contains("Для admin"))
async def cmd_open_admin_panel(message: Message):
    member = await get_member(message.from_user.id)
    if not member or not is_any_admin(member):
        await message.answer("⛔ Доступ заблокирован. Меню только для администрации.")
        return
    role = member.get("role", "member")
    # ВАЖНО: Проверьте, что в конце строки написано user_id=message.from_user.id
    await message.answer(
        f"👔 Панель управления ViGarik Squad \n"
        f"Ваша текущая роль: {ROLE_LABELS.get(role, role)}\n\n"
        f"Выбери необходимое действие на клавиатуре ниже:",
        parse_mode="HTML",
        reply_markup=admin_panel_keyboard(role, user_id=message.from_user.id)
    )


# ─── ПАПКА «УЧАСТНИКИ» (Этап 1 ТЗ) ────────────────────────────────────────────
@router.message(F.text == "👥 Участники")
async def open_admin_participants_folder(message: Message):
    """Открывает папку «Участники»: управление списками, модерация, пуш-сезон, проверка юзернеймов."""
    member = await get_member(message.from_user.id)
    if not member or not has_role(member, "vice"):
        await message.answer("⛔ У вас нет доступа к разделу «Участники».")
        return
    await message.answer(
        "📂 Участники сети \n\n"
        "Выбери необходимое действие:\n"
        "• Управление списками и твинками\n"
        "• Управление пуш-сезоном (Президент / Гранд Вице)\n"
        "• Назначить модерацию (Президент)\n"
        "• Проверить юзернеймы (Президент)",
        parse_mode="HTML",
        reply_markup=admin_participants_keyboard(member.get("role", "member"))
    )


# ─── ПАПКА «СКРИМЫ» (Этап 1 ТЗ) ──────────────────────────────────────────────
@router.message(F.text == "🤺 Скримы")
async def open_admin_scrims_folder(message: Message):
    """Открывает папку «Скримы»: история игр, мониторинг незакрытых скримов, создание турнира."""
    member = await get_member(message.from_user.id)
    if not member or not has_role(member, "vice"):
        await message.answer("⛔ У вас нет доступа к разделу «Скримы».")
        return
    await message.answer(
        "📂 Скримы и турниры \n\n"
        "Выбери необходимое действие:\n"
        "• История последних игр сети\n"
        "• Мониторинг незакрытых скримов\n"
        "• Создание турнира (в разработке)",
        parse_mode="HTML",
        reply_markup=admin_scrims_keyboard()
    )


# ─── ЛОГИКА ОТКРЫТИЯ ПАПКИ ПУШ-СИСТЕМЫ ─────────────────────────────────────────
@router.message(F.text == "🎯 Управление пуш-сезоном")
async def open_admin_push_folder(message: Message):
    """Открывает компактное подменю (папку) со всеми кнопками пуш-сезона основы."""
    member = await get_member(message.from_user.id)
    if not member or member.get("role") in ("member", "helper"):
        await message.answer("⛔ У вас нет доступа к управлению пуш-сезоном.")
        return
    await message.answer(
        "📂 Управление пуш-сезоном Основы \n\n"
        "Здесь вы можете запустить опрос целей, проверить должников, "
        "посмотреть статистику с таймерами игроков или прочитать предложки.",
        parse_mode="HTML",
        reply_markup=admin_push_keyboard()
    )


# ─── ЛОГИКА ОТКРЫТИЯ ПАПКИ УПРАВЛЕНИЯ УЧАСТНИКАМИ ─────────────────────────────
@router.message(F.text == "👥 Управление участниками")
async def open_admin_members_folder(message: Message):
    """Открывает подменю (папку) со всеми инструментами управления списками."""
    member = await get_member(message.from_user.id)
    if not member or member.get("role") == "member":
        await message.answer("⛔ У вас нет доступа к управлению участниками.")
        return
    await message.answer(
        "📂 Управление списками и участниками сети \n\n"
        "Выбери необходимое действие:\n"
        "• Изменить профиль/удалить игрока через ID\n"
        "• Посмотреть список участников без привязанных ников\n"
        "• Быстро привязать твинк к существующей основе игрока",
        parse_mode="HTML",
        reply_markup=admin_members_keyboard()
    )
