"""
Модуль зеркальной верификации ViGarik Squad Bot.
Интегрирован с файлом admins.txt для автоматической выдачи прав на этапе старта.
Защищен от крашей типов данных и ложных срабатываний FSM.
"""

import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.utils.markdown import html_decoration as hd
from handlers.start import RegistrationState
import database as db
from services.api_service import get_player_profile
from utils.roster_sync import sync_roster_msg
from utils.keyboards import main_menu
from utils.permissions import can_edit_list, get_admin_rights_from_file
import config

logger = logging.getLogger(__name__)
router = Router()


@router.message(RegistrationState.entering_nick, F.text)
async def process_player_tag_registration(message: Message, state: FSMContext, bot: Bot):
    """Принимает тег, находит профиль. Админов из файла аппрувит сразу, игрокам шлет инлайн-выбор."""
    raw_tag = message.text.strip().upper().replace("#", "")
    user_id = message.from_user.id
    username = message.from_user.username or "нет"

    if raw_tag in ("CANCEL", "ОТМЕНА", "/CANCEL"):
        await state.clear()
        await message.answer("❌ Регистрация отменена. Напишите /start, когда будете готовы.")
        return

    await message.answer("⏳ <b>Проверяю ваш профиль в базах данных Supercell...</b>", parse_mode="HTML")

    # Проверяем тег по Brawl Stars API
    player_data = await get_player_profile(raw_tag)
    if not player_data:
        await message.answer(
            "❌ <b>Игрок с таким тегом не найден!</b>\n"
            "Введите ваш уникальный ТЕГ заново (например, #9PJYV82CC):",
            parse_mode="HTML"
        )
        return

    game_nick = player_data["name"]
    api_clan_tag = player_data["clan_tag"]
    trophies = player_data["trophies"]

    # Авто-определение клана
    detected_clan = None
    for clan_key, configured_tag in config.CLAN_TAGS.items():
        if api_clan_tag and api_clan_tag.strip().upper().replace("#", "") == configured_tag.strip().upper().replace("#", ""):
            detected_clan = clan_key
            break

    if not detected_clan:
        await message.answer(
            f"❌ <b>Ошибка!</b>\nИгрок <b>{hd.quote(game_nick)}</b> найден, но ваш клуб не входит в сеть ViGarik Squad.\n"
            f"Вступите в наш клан и введите тег заново.",
            parse_mode="HTML"
        )
        return

    # Сохраняем временные данные в FSM (нужно для обычных игроков)
    # Сохраняем также реальный username и имя юзера из Telegram, чтобы не затирать их потом заглушками!
    await state.update_data(
        raw_tag=raw_tag,
        game_nick=game_nick,
        trophies=trophies,
        detected_clan=detected_clan,
        tg_username=username,
        tg_firstname=message.from_user.first_name or "Игрок",
        tg_lastname=message.from_user.last_name or ""
    )

    # Проверяем, есть ли текущий игрок в файле admins.txt на лету
    file_rights = get_admin_rights_from_file(user_id)

    # ЛОГИКА АВТО-ОДОБРЕНИЯ АДМИНИСТРАЦИИ И ВЛАДЕЛЬЦА
    if user_id == 7899153362 or file_rights is not None:
        await state.clear()

        assigned_role = "president"
        if file_rights:
            assigned_role = file_rights.get("role", "helper")
            if file_rights.get("clan"):
                detected_clan = file_rights["clan"]

        # Сразу записываем администратора в базу данных с его реальными правами
        await db.upsert_member(
            user_id=user_id, username=username, first_name=message.from_user.first_name,
            last_name=message.from_user.last_name, game_nick=game_nick, player_tag=raw_tag,
            trophies=trophies, clan=detected_clan, role=assigned_role, registered=1
        )

        role_label = config.ROLE_LABELS.get(assigned_role, "Администрация")
        member_profile = await db.get_member(user_id)

        await message.answer(
            f"✨ <b>Система авторизации ViGarik Squad</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Успешно! Вы зарегистрированы как: <b>{role_label}</b>\n\n"
            f"👤 <b>Ник в игре:</b> {hd.quote(game_nick)}\n"
            f"🏆 <b>Кубки:</b> <code>{trophies:,}</code>\n"
            f"🏰 <b>Управляемый клан:</b> <b>{config.CLAN_DISPLAY.get(detected_clan, detected_clan).upper()}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Вам автоматически выдано административное меню бота.",
            parse_mode="HTML", reply_markup=main_menu(member_profile)
        )

        try:
            await sync_roster_msg(bot, detected_clan)
        except Exception:
            pass
        return

    # ЛОГИКА ДЛЯ ОБЫЧНЫХ ИГРОКОВ (FSM НЕ ЧИСТИМ)
    kb_user = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Запросить подтверждение", callback_data="user_confirm_reg"),
            InlineKeyboardButton(text="❌ Это не мой аккаунт", callback_data="user_wrong_account")
        ]
    ])

    safe_nick = hd.quote(game_nick)
    clan_title = config.CLAN_DISPLAY.get(detected_clan, detected_clan).upper()

    await message.answer(
        f"📝 <b>Ваш профиль найден! Это вы?</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎮 <b>Ник в игре:</b> <b>{safe_nick}</b>\n"
        f"🏷️ <b>Тег аккаунта:</b> <code>#{raw_tag}</code>\n"
        f"🏆 <b>Кубки:</b> <code>{trophies:,}</code>\n"
        f"🏰 <b>Клан:</b> {clan_title}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Нажмите кнопку ниже для отправки заявки модераторам 👇",
        parse_mode="HTML", reply_markup=kb_user
    )


# ─── ОБРАБОТКА НАЖАТИЙ ИГРОКА (В ЛС) ─────────────────────────────────────────

@router.callback_query(F.data == "user_wrong_account")
async def process_user_wrong_account(call: CallbackQuery, state: FSMContext):
    """Игрок нажал 'Это не мой аккаунт' — возвращаем к вводу тега."""
    await call.answer()
    await state.set_state(RegistrationState.entering_nick)
    await call.message.edit_text(
        "❌ <b>Регистрация сброшена.</b>\nПожалуйста, введите ваш корректный игровой ТЕГ заново (например, #9PJYV82CC):",
        parse_mode="HTML", reply_markup=None
    )


@router.callback_query(F.data == "user_confirm_reg")
async def process_user_confirm_registration(call: CallbackQuery, state: FSMContext, bot: Bot):
    """Игрок нажал 'Запросить подтверждение' — отправляем тикет в ADMIN_CHAT_ID."""
    user_data = await state.get_data()
    if not user_data:
        await call.answer("❌ Сессия устарела. Запустите бота заново через /start", show_alert=True)
        return

    await call.answer("🚀 Заявка отправляется администрации...")
    await state.clear()  # Сбрасываем FSM, ждем аппрува

    user_id = call.from_user.id
    username = call.from_user.username or "нет"
    raw_tag = user_data["raw_tag"]
    game_nick = user_data["game_nick"]
    detected_clan = user_data["detected_clan"]

    safe_username = f"@{call.from_user.username}" if call.from_user.username else call.from_user.first_name
    safe_nick = hd.quote(game_nick)

    admin_log_text = (
        f"{safe_username} хочет зарегестрироваться в боте. "
        f"Проверьте его соответствие никнейма ва игре с тегом в телеграмме:\n"
        f"@{username} {safe_nick}"
    )

    kb_admin = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принять", callback_data=f"v3:ap:{user_id}:{raw_tag}:init"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"v3:dc:{user_id}:{raw_tag}:init")
        ]
    ])

    chat_id = config.ADMIN_CHAT_ID
    setting_key = f"topic_id_{detected_clan}"
    thread_id_str = await db.get_setting(setting_key) or await db.get_setting("topic_id_main_admin")

    user_msg_id = call.message.message_id

    if chat_id:
        try:
            log_msg = await bot.send_message(
                chat_id=chat_id,
                message_thread_id=int(thread_id_str) if thread_id_str else None,
                text=admin_log_text,
                reply_markup=kb_admin
            )
            await db.set_setting(f"reg_msg_user_{user_id}", str(user_msg_id))
            await db.set_setting(f"reg_msg_log_{user_id}", str(log_msg.message_id))
            await db.set_setting(f"reg_log_chat_{user_id}", str(chat_id))
        except Exception as e:
            logger.error(f"Не удалось отправить заявку в чат админов: {e}")

    await call.message.edit_text(
        f"⏳ <b>Ваша заявка успешно отправлена модераторам сети ViGarik Squad!</b>\n"
        f"Ник для верификации: <b>{safe_nick}</b>\n\n"
        f"<i>Ожидайте системного уведомления в этом чате...</i>",
        parse_mode="HTML", reply_markup=None
    )


# ─── ОБРАБОТКА ДЕЙСТВИЙ АДМИНИСТРАЦИИ (В ADMIN_CHAT_ID) ──────────────────────

@router.callback_query(F.data.startswith("v3:"))
async def process_admin_verification_v3(call: CallbackQuery, bot: Bot):
    """Интерактивный хэндлер для работы админов с двойным подтверждением (Да/Нет)."""
    parts = call.data.split(":")
    action = parts[1]  # 'ap' или 'dc'
    target_id = int(parts[2])
    player_tag = parts[3]
    stage = parts[4]  # 'init', 'yes', 'no'

    # 1. ОБРАБОТКА ПЕРВОГО КЛИКА — Показываем вопрос "Вы уверены?"
    if stage == "init":
        await call.answer()

        player_data = await get_player_profile(player_tag)
        game_nick = player_data["name"] if player_data else "Игрок"
        action_word = "принять" if action == "ap" else "отклонить"
        confirm_text = f"Вы уверены что хотите {action_word} {hd.quote(game_nick)}?"

        kb_confirm = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🟢 ДА", callback_data=f"v3:{action}:{target_id}:{player_tag}:yes"),
                InlineKeyboardButton(text="🔴 НЕТ", callback_data=f"v3:{action}:{target_id}:{player_tag}:no")
            ]
        ])

        await call.message.edit_text(text=confirm_text, parse_mode="HTML", reply_markup=kb_confirm)
        return

    # 2. ОБРАБОТКА ОТМЕНЫ (НАЖАЛИ "НЕТ") — Возвращаем базовые кнопки
    if stage == "no":
        await call.answer("Отменено")
        try:
            player_data = await get_player_profile(player_tag)
            game_nick = player_data["name"] if player_data else "Игрок"
            action_word = "принять" if action == "ap" else "отклонить"

            kb_init = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Принять", callback_data=f"v3:ap:{target_id}:{player_tag}:init"),
                    InlineKeyboardButton(text="❌ Отклонить", callback_data=f"v3:dc:{target_id}:{player_tag}:init")
                ]
            ])
            await call.message.edit_text(text=f"Заявка игрока {hd.quote(game_nick)}:", parse_mode="HTML", reply_markup=kb_init)
        except Exception:
            pass
        return

    # 3. ОБРАБОТКА ПОДТВЕРЖДЕНИЯ (НАЖАЛИ "ДА")
    if stage == "yes":
        admin_name = f"@{call.from_user.username}" if call.from_user.username else call.from_user.first_name

        await call.answer("Обработка заявки...")
        player_data = await get_player_profile(player_tag)
        if not player_data:
            await call.answer("❌ Ошибка Brawl Stars API", show_alert=True)
            return

        game_nick = player_data["name"]
        clan_tag = player_data["clan_tag"]
        trophies = player_data["trophies"]

        # Кликнули "ДА" на ПРИНЯТИЕ
        if action == "ap":
            detected_clan = "squad"
            for clan_key, configured_tag in config.CLAN_TAGS.items():
                if clan_tag and clan_tag.strip().upper().replace("#", "") == configured_tag.strip().upper().replace("#", ""):
                    detected_clan = clan_key
                    break

            # По умолчанию роль обычного участника
            assigned_role = "member"

            # Проверяем, есть ли одобренный игрок в admins.txt
            file_rights = get_admin_rights_from_file(target_id)
            if file_rights:
                assigned_role = file_rights.get("role", "member")
                if file_rights.get("clan"):
                    detected_clan = file_rights["clan"]

            try:
                # ИСПРАВЛЕНО: Достаем реальные данные юзера из базы (если он там уже есть) или берем безопасные дефолты вместо затирания "unknown" и "Игрок"
                old_member = await db.get_member(target_id) or {}

                real_username = old_member.get("username") or f"user_{target_id}"
                real_firstname = old_member.get("first_name") or game_nick
                real_lastname = old_member.get("last_name") or ""

                # Фиксируем игрока в базе данных с нормальными данными и тегом
                await db.upsert_member(
                    user_id=target_id,
                    username=real_username,
                    first_name=real_firstname,
                    last_name=real_lastname,
                    game_nick=game_nick,
                    player_tag=player_tag,
                    trophies=trophies,
                    clan=detected_clan,
                    role=assigned_role,
                    registered=1
                )
            except Exception as e:
                logger.error(f"Ошибка сохранения игрока: {e}")

            # Меняем текст в чате админов: убираем кнопки и пишем, кто принял
            role_label = config.ROLE_LABELS.get(assigned_role, "Участник")
            final_admin_text = f"Игрок {hd.quote(game_nick)} был принят {hd.quote(admin_name)} (Роль: {role_label})"
            await call.message.edit_text(text=final_admin_text, parse_mode="HTML", reply_markup=None)

            # Оповещаем самого игрока в ЛС и выдаем ему Главное Меню бота
            try:
                member_profile = await db.get_member(target_id)
                await bot.send_message(
                    chat_id=target_id,
                    text=f"🎉 <b>Поздравляем! Ваша верификация одобрена администрацией.</b>\n"
                         f"Вы зачислены в систему как <b>{role_label}</b> клана <b>{config.CLAN_DISPLAY.get(detected_clan, detected_clan).upper()}</b>!",
                    parse_mode="HTML", reply_markup=main_menu(member_profile)
                )
            except Exception as e:
                logger.error(f"Не удалось отправить меню игроку в ЛС: {e}")

            # Обновляем живой ростер (список) клана в Telegram-канале
            await sync_roster_msg(bot, detected_clan)

        # ─── КЛИКНУЛИ "ДА" НА ОТКЛОНЕНИЕ ЗАЯВКИ ──────────────────────────────
        elif action == "dc":
            final_admin_text = f"Заявка игрока {hd.quote(game_nick)} была отклонена модератором {hd.quote(admin_name)}"
            await call.message.edit_text(text=final_admin_text, parse_mode="HTML", reply_markup=None)

            # Оповещаем игрока в ЛС, что его отклонили
            try:
                await bot.send_message(
                    chat_id=target_id,
                    text="❌ <b>Ваша заявка на верификацию была отклонена администрацией.</b>\n"
                         "Проверьте тег и попробуйте ввести его заново через команду /start.",
                    parse_mode="HTML"
                )
            except Exception:
                pass