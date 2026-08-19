"""
Обработчик команды /start и первичная авторизация по кланам через API.
"""

import logging
from aiogram import Router, types, F, Bot
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardRemove

import database as db
from utils.chat_check import get_user_clans, is_chat_admin
from config import CLAN_DISPLAY, INITIAL_ADMINS, CLAN_CHATS
from utils.keyboards import main_menu

# Используем общий файловый логгер для событий бота
from utils.admin_logger import bot_events_logger
logger = bot_events_logger

router = Router()


class RegistrationState(StatesGroup):
    # ИСПРАВЛЕНО: Состояние choosing_clan удалено, так как API определяет клан автоматически
    entering_nick = State()


@router.message(CommandStart(), F.chat.type == "private")
async def cmd_start(message: types.Message, state: FSMContext, bot: Bot):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username
    
    # ───── НОВОЕ: ПОЛНОЕ ЛОГИРОВАНИЕ ДЛЯ ОТЛАДКИ ──────────────────────────────
    logger.info('\n' + '='*80)
    logger.info('🔍 /START КОМАНДА - Проверка доступа')
    logger.info(f'   👤 User ID: {user_id}')
    logger.info(f'   📱 Username: @{username or "нет"}')
    logger.info(f'   💾 В admins.txt: {"ДА" if user_id in INITIAL_ADMINS else "НЕТ"}')

    if user_id in INITIAL_ADMINS:
        logger.info(f'   ⭐ Роль админа: {INITIAL_ADMINS[user_id]}')

    # 1. Проверяем, админ ли он чата администрации
    logger.info('   🔎 Проверяю статус в админ-чате...')
    is_admin = await is_chat_admin(bot, user_id)
    logger.info(f'   📋 Админ чата: {is_admin}')

    # Определяем базовую роль
    current_role = "member"
    if user_id in INITIAL_ADMINS:
        current_role = INITIAL_ADMINS[user_id]["role"]
        logger.info(f'   ✅ Роль из admins.txt: {current_role}')
    elif is_admin:
        current_role = "vice"  # Дефолтная роль для админ-чата
        logger.info(f'   ✅ Роль как админ-чата: {current_role}')

    # 2. Проверяем, в каких чатах кланов состоит человек
    logger.info('   🔎 Проверяю кланы...')
    clans = await get_user_clans(bot, user_id)
    logger.info(f'   📊 Найденные кланы: {clans if clans else "НИЧЕГО НЕ НАЙДЕНО"}')
    
    # Логируем проверку каждого клана для отладки
    for clan_key, clan_info in CLAN_CHATS.items():
        result = '✅ найден' if clan_key in clans else '❌ не найден'
        logger.info(f"      └─ Клан '{clan_key}': {result}")
        logger.debug(f"      └─ Клан '{clan_key}' (ID: {clan_info['chat_id']}): {result}")

    if not clans:
        logger.warning(f"❌ ДОСТУП ЗАПРЕЩЕН - {user_id} (@{username}) ни в одном чате клана")
        
        # ВСЕГДА отправляем диагностический отчёт в админ-чат (даже если это обычный юзер)
        try:
            from utils.admin_logger import log_bot_event

            diag_lines = [
                f"❌ ДОСТУП ЗАПРЕЩЕН для пользователя: {user_id} @{username}",
                f"В admins.txt: {'ДА' if user_id in INITIAL_ADMINS else 'НЕТ'}",
                f"В админ-чате: {'ДА' if is_admin else 'НЕТ'}",
                "",
                "📊 Детальная проверка по кланам:"
            ]

            for clan_key, chat_info in CLAN_CHATS.items():
                try:
                    member = await bot.get_chat_member(chat_id=chat_info['chat_id'], user_id=user_id)
                    status = getattr(member, 'status', str(member))
                    diag_lines.append(f"  • {clan_key} (id={chat_info['chat_id']}): {status}")
                    logger.info(f"      Статус в {clan_key}: {status}")
                except Exception as e:
                    error_str = str(e)[:100]
                    diag_lines.append(f"  • {clan_key} (id={chat_info['chat_id']}): ❌ {error_str}")
                    logger.error(f"      Ошибка в {clan_key}: {e}")

            diag_text = "\n".join(diag_lines)

            # Отправляем как WARNING чтобы увидеть в админ-топике
            await log_bot_event(bot=bot, event_type="warning", description=diag_text, user_id=user_id, username=username)
        except Exception as e:
            logger.error(f"Не удалось отправить отладочный отчёт: {e}")

        await message.answer(
            "❌ Доступ заблокирован. Вас нет ни в одном чате наших кланов.\n\n"
            "Вступайте в наши кланы ViGarik Squad или Academy, чтобы пользоваться ботом!",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    logger.info('\n   ' + '═'*51)
    logger.info(f'   📍 ИТОГОВЫЙ РЕЗУЛЬТАТ: {"✅ ДОСТУП РАЗРЕШЕН" if has_access else "❌ ДОСТУП ЗАПРЕЩЕН"}')
    logger.info('   ' + '═'*51 + '\n')
    logger.info(f"   💾 В БД: {'ДА' if member else 'НЕТ'}")
    
    if member:
        logger.info(f"   📝 Регистрация: {'ЗАВЕРШЕНА' if member.get('registered') == 1 else 'НЕ ЗАВЕРШЕНА'}")
        logger.info(f"   🏆 Клан: {member.get('clan', 'не указан')}")

    if member and member.get("registered") == 1:
        logger.info(f"✅ Юзер уже зарегистрирован - показываю главное меню")
        await message.answer(
            f"Привет, {message.from_user.first_name}! Это главное меню бота ViGarik Squad. 🎮\n"
            f"Вы зарегистрированы в клане: <b>{CLAN_DISPLAY.get(member['clan'], member['clan']).upper()}</b>",
            parse_mode="HTML",
            reply_markup=main_menu(member)
        )
        return

    # 3. ИСПРАВЛЕНО: Больше не заставляем выбирать клан кнопками, если их несколько.
    # Мы сразу запрашиваем тег аккаунта, а API само определит его реальный клуб!
    logger.info(f"🎮 Запускаю регистрацию - прошу тег Brawl Stars")
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
