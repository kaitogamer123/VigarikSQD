"""
Обработчик команды /start и первичная авторизация по кланам через API.
"""

from aiogram import Router, types, F, Bot
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardRemove

import database as db
from utils.chat_check import get_user_clans, is_chat_admin
from config import CLAN_DISPLAY, INITIAL_ADMINS
from utils.keyboards import main_menu

router = Router()


class RegistrationState(StatesGroup):
    # ИСПРАВЛЕНО: Состояние choosing_clan удалено, так как API определяет клан автоматически
    entering_nick = State()


@router.message(CommandStart(), F.chat.type == "private")
async def cmd_start(message: types.Message, state: FSMContext, bot: Bot):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username

    # 1. Проверяем, админ ли он чата администрации
    is_admin = await is_chat_admin(bot, user_id)

    # Определяем базовую роль
    current_role = "member"
    if user_id in INITIAL_ADMINS:
        current_role = INITIAL_ADMINS[user_id]["role"]
    elif is_admin:
        current_role = "vice"  # Дефолтная роль для админ-чата

    # 2. Проверяем, в каких чатах кланов состоит человек
    clans = await get_user_clans(bot, user_id)

    if not clans:
        await message.answer(
            "❌ Доступ заблокирован. Вас нет ни в одном чате наших кланов.\n\n"
            "Вступайте в наши кланы ViGarik Squad или Academy, чтобы пользоваться ботом!",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    # Проверяем, есть ли он уже в БД
    member = await db.get_member(user_id)

    if member and member.get("registered") == 1:
        await message.answer(
            f"Привет, {message.from_user.first_name}! Это главное меню бота ViGarik Squad. 🎮\n"
            f"Вы зарегистрированы в клане: <b>{CLAN_DISPLAY.get(member['clan'], member['clan']).upper()}</b>",
            parse_mode="HTML",
            reply_markup=main_menu(member)
        )
        return

    # 3. ИСПРАВЛЕНО: Больше не заставляем выбирать клан кнопками, если их несколько.
    # Мы сразу запрашиваем тег аккаунта, а API само определит его реальный клуб!
    await message.answer(
        f"Привет, @{username or message.from_user.first_name}! Это приветственное сообщение бота ViGarik Squad. 👋\n\n"
        f"Для верификации вашего аккаунта введите ваш <b>точный игровой тег</b> Brawl Stars "
        f"(например, #9PJYV82CC или 9PJYV82CC):",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove()
    )

    # Сохраняем базовую роль для последующего апсерта в registration.py
    await state.update_data(role=current_role)
    await state.set_state(RegistrationState.entering_nick)
