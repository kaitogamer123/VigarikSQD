"""
Модуль изменения игрового никнейма для участников ViGarik Squad.
Вынесен в отдельный изолированный хэндлер.
"""

import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.markdown import html_decoration as hd

import database as db
import config
from utils.keyboards import main_menu
from utils.roster_sync import sync_roster_msg

logger = logging.getLogger(__name__)
router = Router()


# Состояние FSM для отслеживания ввода нового имени
class ProfileEditState(StatesGroup):
    changing_nick = State()


# ─── 1. ХЭНДЛЕР НАЖАТИЯ ТЕКСТОВОЙ КНОПКИ МЕНЮ ────────────────────────────────
@router.message(F.text == "✏️ Изменить текущий ник")
async def start_change_nick(message: Message, state: FSMContext):
    """Переводит игрока в режим ожидания нового игрового никнейма."""
    user_id = message.from_user.id
    member = await db.get_member(user_id)

    if not member or not member.get("registered"):
        await message.answer("❌ <b>Вы не зарегистрированы в системе!</b> Напишите /start.")
        return

    # Включаем состояние ожидания текста
    await state.set_state(ProfileEditState.changing_nick)

    await message.answer(
        "📝 <b>Режим изменения никнейма</b>\n\n"
        "Введите ваш новый игровой никнейм (точно так же, как он написан внутри Brawl Stars):",
        parse_mode="HTML"
    )


# ─── 2. ХЭНДЛЕР ПРИЕМА И СОХРАНЕНИЯ НОВОГО ТЕКСТА ────────────────────────────
@router.message(ProfileEditState.changing_nick, F.text)
async def save_changed_nick(message: Message, state: FSMContext, bot: Bot):
    """Принимает текст, валидирует длину и сохраняет изменения в SQLite базу."""
    new_nick = message.text.strip()
    user_id = message.from_user.id

    # Простая проверка на длину никнейма в игре
    if len(new_nick) < 2 or len(new_nick) > 15:
        await message.answer("❌ <b>Длина никнейма должна быть от 2 до 15 символов!</b>\nВведите заново:")
        return

    # Мгновенно чистим FSM, чтобы вернуть обычную работу кнопок
    await state.clear()

    try:
        # Достаем старый профиль, чтобы не затереть остальные важные поля
        member = await db.get_member(user_id)
        if member:
            # Сохраняем обновленный ник в SQLite
            await db.upsert_member(
                user_id=user_id,
                username=member.get("username", "unknown"),
                first_name=member.get("first_name", "Игрок"),
                last_name=member.get("last_name", ""),
                game_nick=new_nick,  # Перезаписываем имя новым
                player_tag=member.get("player_tag", ""),
                trophies=member.get("trophies", 0),
                clan=member.get("clan", "squad"),
                role=member.get("role", "member"),
                registered=1
            )

            # Достаем обновленный профиль из базы для генерации актуального меню
            updated_member = await db.get_member(user_id)

            await message.answer(
                f"✅ <b>Никнейм успешно изменен!</b>\n"
                f"Ваше новое имя в системе бота: <b>{hd.quote(new_nick)}</b>",
                parse_mode="HTML",
                reply_markup=main_menu(updated_member)
            )

            # Автоматически обновляем живые списки (ростеры) в Telegram-каналах
            try:
                await sync_roster_msg(bot, member.get("clan", "squad"))
            except Exception as e:
                logger.error(f"Не удалось синхронизировать ростер после смены ника: {e}")
        else:
            await message.answer("❌ <b>Ошибка:</b> ваш профиль не найден в базе данных.")

    except Exception as e:
        logger.error(f"Критическая ошибка при изменении ника для ID {user_id}: {e}")
        await message.answer("❌ Произошла системная ошибка при сохранении никнейма в базу данных.")
