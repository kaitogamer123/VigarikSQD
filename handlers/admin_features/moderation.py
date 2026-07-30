""" Модуль модерации: управление ролями администрации и синхронизация с admins.txt """
import logging
import os

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.markdown import html_decoration as hd

import config
from database import get_all_members, get_member, upsert_member
from utils.keyboards import admin_panel_keyboard, appoint_role_keyboard
from utils.permissions import can_appoint_admins
from utils.username_monitor import check_and_update_usernames

from .base import AdminStates
logger = logging.getLogger(__name__)
router = Router()

# Порядок иерархии ролей для кнопок Повысить/Понизить
# Список построен от самой высшей роли к самой низшей
HIERARCHY = ["president", "grand_vice", "vice", "veteran", "helper", "member"]


# ─── СТАРТОВЫЙ ШАГ: ВЫБОР ДЕЙСТВИЯ (ДОБАВИТЬ ИЛИ ИЗМЕНИТЬ) ──────────────────────
# ─── СТАРТОВЫЙ ШАГ: ВЫБОР ДЕЙСТВИЯ (ДОБАВИТЬ ИЛИ ИЗМЕНИТЬ) ──────────────────────

@router.message(F.text == "⚙️ Назначить модерацию")
async def appoint_start(message: Message, state: FSMContext):
    member = await get_member(message.from_user.id)
    if not member or not can_appoint_admins(member):
        await message.answer("⛔ У тебя нет прав для назначения модерации.")
        return

    await state.clear()

    kb_choice = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Добавить нового админа", callback_data="mod_action:new"),
            InlineKeyboardButton(text="✏️ Изменить существующего", callback_data="mod_action:edit")
        ],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="edit_list:cancel")]
    ])

    await message.answer(
        "💼 <b>Управление администрацией ViGarik Squad</b>\n\n"
        "Выберите, что вы хотите сделать с правами доступа:",
        parse_mode="HTML",
        reply_markup=kb_choice
    )


@router.callback_query(F.data.startswith("mod_action:"))
async def process_mod_action_choice(call: CallbackQuery, state: FSMContext):
    """Вызывается после выбора режима. При изменении выгружает ростер текущих админов."""
    action = call.data.split(":")[1]

    kb_back = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="edit_list:cancel")]
    ])

    if action == "new":
        text_prompt = (
            "📝 <b>Добавление нового администратора</b>\n\n"
            "Напиши <b>user_id</b> или <b>@username</b> игрока, "
            "которого нужно внесить в списки модерации:"
        )
        await call.message.edit_text(text=text_prompt, parse_mode="HTML", reply_markup=kb_back)

    elif action == "edit":
        # 1. Выгружаем всех участников из базы данных
        all_members = await get_all_members()

        # 2. Фильтруем только тех, кто имеет админ-роль (не member)
        admin_lines = []
        for m in all_members:
            m_role = m.get("role", "member")
            if m_role and m_role != "member":
                # Подтягиваем красивый эмодзи-лейбл роли из config.py
                role_label = config.ROLE_LABELS.get(m_role, m_role)

                # Собираем красивую строчку (Ник/Юзернейм | ID | Звание)
                m_nick = m.get("game_nick") or m.get("username") or "Игрок"
                m_tg = f"@{m['username']}" if m.get("username") and m["username"] != "unknown" else "ЛС"
                m_id = m.get("user_id")

                admin_lines.append(
                    f"• <b>{hd.quote(str(m_nick))}</b> ({m_tg}) | ID: <code>{m_id}</code>\n  └ Статус: {role_label}")

        # 3. Собираем итоговый текст
        if admin_lines:
            admin_list_str = "\n\n".join(admin_lines)
            text_prompt = (
                "🔄 <b>Редактирование текущей модерации</b>\n\n"
                "📋 <b>Список действующей администрации:</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{admin_list_str}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Напиши <b>user_id</b> или <b>@username</b> администратора из списка выше, "
                "которому нужно повысить/понизить или полностью переназначить роль:"
            )
        else:
            text_prompt = (
                "🔄 <b>Редактирование текущей модерации</b>\n\n"
                "⚠️ В базе данных пока нет зарегистрированных администраторов.\n\n"
                "Введите <b>user_id</b> или <b>@username</b> для принудительного поиска:"
            )

        # Выводим сообщение (если список огромный, разметка HTML защищена с помощью hd.quote)
        try:
            await call.message.edit_text(text=text_prompt, parse_mode="HTML", reply_markup=kb_back)
        except Exception:
            # На случай если список превысит лимит сообщения Telegram (4096 символов), шлем чистый промпт
            await call.message.edit_text(
                text="🔄 <b>Редактирование модерации</b>\n\nНапиши <b>user_id</b> или <b>@username</b> администратора:",
                parse_mode="HTML", reply_markup=kb_back
            )

    # Включаем состояние ожидания текста
    await state.set_state(AdminStates.waiting_appoint_user)
    await call.answer()


@router.message(AdminStates.waiting_appoint_user)
async def appoint_receive_user(message: Message, state: FSMContext, bot: Bot):
    text = message.text.strip()
    if text in ("/cancel", "/back"):
        await state.clear()
        member = await get_member(message.from_user.id)
        await message.answer(
            "❌ Назначение модерации отменено.",
            reply_markup=admin_panel_keyboard(member.get("role", "member"))
        )
        return

    target = None
    if text.lstrip("-").isdigit():
        target = await get_member(int(text))
    elif text.startswith("@"):
        uname = text[1:].lower().strip()
        all_members = await get_all_members()
        target = next((m for m in all_members if m.get("username") and m["username"].lower() == uname), None)

    if not target:
        await message.answer("❌ Участник не найден в базе. Попробуй ещё раз:")
        return

    await state.update_data(target_id=target["user_id"])
    raw_nick = target.get("game_nick") or target.get("username") or str(target["user_id"])
    nick = hd.quote(str(raw_nick))

    current_role = target.get("role", "member") or "member"
    current_label = config.ROLE_LABELS.get(current_role, "Участник")

    # Собираем меню управления
    inline_buttons = []

    # Если пользователь уже имеет какую-то роль, рассчитываем кнопки Повысить/Понизить
    if current_role in HIERARCHY:
        idx = HIERARCHY.index(current_role)

        # Кнопка ПОВЫСИТЬ (доступна, если роль не самая топовая 'president')
        if idx > 0:
            up_role = HIERARCHY[idx - 1]
            inline_buttons.append([InlineKeyboardButton(
                text=f"🔼 Повысить до {config.ROLE_LABELS.get(up_role, up_role)}",
                callback_data=f"appoint:{target['user_id']}:{up_role}"
            )])

        # Кнопка ПОНИЗИТЬ (доступна, если роль не самая низшая 'member')
        if idx < len(HIERARCHY) - 1:
            down_role = HIERARCHY[idx + 1]
            inline_buttons.append([InlineKeyboardButton(
                text=f"🔽 Понизить до {config.ROLE_LABELS.get(down_role, down_role)}",
                callback_data=f"appoint:{target['user_id']}:{down_role}"
            )])

    # Также добавляем кнопку ручного выбора абсолютно любой роли из вашего старого меню
    inline_buttons.append([InlineKeyboardButton(text="📋 Выбрать роль вручную", callback_data=f"manual_roles:{target['user_id']}")])
    inline_buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="appoint:cancel")])

    kb_manage = InlineKeyboardMarkup(inline_keyboard=inline_buttons)

    await message.answer(
        f"👤 Участник: <b>{nick}</b>\n"
        f"⚙️ Текущий статус: <b>{current_label}</b>\n\n"
        f"Выберите действие для управления правами:",
        parse_mode="HTML",
        reply_markup=kb_manage
    )
    await state.clear()


@router.callback_query(F.data.startswith("manual_roles:"))
async def manual_roles_cb(call: CallbackQuery):
    """Открывает стандартную клавиатуру со всеми доступными ролями."""
    target_id = int(call.data.split(":")[1])
    await call.message.edit_reply_markup(reply_markup=appoint_role_keyboard(target_id))
    await call.answer()


# ─── ЭТАП 2: ПОДТВЕРЖДЕНИЕ ВЫБОРА РОЛИ ("Вы уверены?") ───────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("appoint:") and c.data != "appoint:cancel")
async def appoint_role_cb(call: CallbackQuery, bot: Bot):
    parts = call.data.split(":")
    if len(parts) < 3:
        return

    target_id_str, chosen_role = parts[1], parts[2]
    admin = await get_member(call.from_user.id)
    if not admin or not can_appoint_admins(admin):
        await call.answer("⛔ Нет прав.", show_alert=True)
        return

    try:
        target_id = int(target_id_str)
    except ValueError:
        return

    target = await get_member(target_id)
    if not target:
        await call.answer("❌ Игрок не найден в БД чата.", show_alert=True)
        return

    old_role = target.get("role", "member") or "member"
    old_label = config.ROLE_LABELS.get(old_role, "Участник")
    new_label = config.ROLE_LABELS.get(chosen_role, chosen_role)

    raw_nick = target.get("game_nick") or target.get("username") or str(target_id)
    nick = hd.quote(str(raw_nick))

    # Определяем характер изменения для красивого вывода
    action_type = "изменить права для"
    if old_role in HIERARCHY and chosen_role in HIERARCHY:
        if HIERARCHY.index(chosen_role) < HIERARCHY.index(old_role):
            action_type = "🚀 ПОВЫСИТЬ"
        else:
            action_type = "📉 ПОНИЗИТЬ"

    confirm_text = (
        f"❓ <b>Подтверждение изменения прав</b>\n\n" 
        f"Вы действительно хотите {action_type} игрока <b>nick</b>?\n" 
        f"🔹 <b>Текущий статус:</b> {old_label}\n" 
        f"🔸 <b>Новый статус:</b> <b>{new_label}</b>\n\n" 
        f"Выберите дальнейшее действие 👇"
    )

    kb_confirm = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💾 Сохранить изменения", callback_data=f"cf_role:{target_id}:{chosen_role}:{old_role}:save"),
            InlineKeyboardButton(text="↩️ Отменить", callback_data=f"cf_role:{target_id}:{chosen_role}:{old_role}:cancel")
        ]
    ])

    await call.message.edit_text(text=confirm_text, parse_mode="HTML", reply_markup=kb_confirm)
    await call.answer()


# ─── ЭТАП 3: ФИКСАЦИЯ РЕЗУЛЬТАТА (КЛИК НА "СОХРАНИТЬ" ИЛИ "ОТМЕНИТЬ") ──────────

@router.callback_query(F.data.startswith("cf_role:"))
async def execute_role_confirm_cb(call: CallbackQuery, bot: Bot):
    parts = call.data.split(":")
    target_id = int(parts[1])
    new_role = parts[2]
    old_role = parts[3]
    action = parts[4]

    admin_name = f"@{call.from_user.username}" if call.from_user.username else call.from_user.first_name
    target = await get_member(target_id)

    raw_nick = target.get("game_nick") or target.get("username") or str(target_id) if target else f"ID {target_id}"
    nick = hd.quote(str(raw_nick))

    # 🟢 ВАРИАНТ А: АДМИН НАЖАЛ «СОХРАНИТЬ ИЗМЕНЕНИЯ»
    if action == "save":
        await call.answer("💾 Изменения применены!")
        if not target:
            await call.message.edit_text("❌ Ошибка: пользователь пропал из БД.")
            return

            # 1. Записываем новую роль в SQLite базу данных
        await upsert_member(user_id=target_id, role=new_role)

        # 2. Перезаписываем строку в файле admins.txt
        uname = target.get("username") or "unknown"
        clan = target.get("clan") or "none"
        lines = []

        if os.path.exists(config.ADMINS_FILE_PATH):
            with open(config.ADMINS_FILE_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    # Пропускаем старую строку этого пользователя
                    if not line.strip().startswith(f"{target_id}:"):
                        lines.append(line)

                        # Авто-очистка файла: если пользователя понизили до рядового member,
        # его строку в файл НЕ добавляем (он перестал быть админом)
        if new_role != "member":
            lines.append(f"{target_id}:{uname}:{new_role}:{clan}\n")

        with open(config.ADMINS_FILE_PATH, "w", encoding="utf-8") as f:
            f.writelines(lines)

            # 3. Принудительно перезагружаем конфиг в оперативной памяти сервера на лету
        from utils.admin_logger import log_admin_action
        from config import reload_config
        reload_config()

        new_label = config.ROLE_LABELS.get(new_role, new_role)

        # Меняем текст сообщения на финальный отчет
        final_text = f"✅ Успешно! Статус <b>{new_label}</b> назначен участнику <b>{nick}</b> и сохранен в системе!"
        await call.message.edit_text(text=final_text, parse_mode="HTML", reply_markup=None)

        # Логируем действие админа в чат общих логов
        await log_admin_action(
            bot=bot,
            admin_id=call.from_user.id,
            admin_name=call.from_user.username or call.from_user.first_name,
            action_text=f"Изменил права пользователя {nick} (ID: <code>{target_id}</code>). Новая роль: <b>{new_label}</b>."
        )

        # Оповещаем самого игрока в ЛС
        try:
            await bot.send_message(
                target_id,
                f"🔔 <b>Ваш статус администрации был изменен!</b>\n\n"
                f"Новое звание в сети ViGarik Squad: <b>{new_label}</b>\n"
                f"Перезапустите меню через команду /start",
                parse_mode="HTML",
            )
        except Exception:
            pass

            # 🔴 ВАРИАНТ Б: АДМИН НАЖАЛ «ОТМЕНИТЬ»
    elif action == "cancel":
        await call.answer("↩️ Действие отменено")

        old_label = config.ROLE_LABELS.get(old_role, "Участник")

        cancel_text = (
            f"❌ <b>Изменение прав отменено!</b>\n"
            f"Администратор {admin_name} отменил редактирование прав для <b>{nick}</b>.\n"
            f"Пользователю сохранен его текущий статус: <b>{old_label}</b>."
        )
        await call.message.edit_text(text=cancel_text, parse_mode="HTML", reply_markup=None)

    # ─── ВСПОМОГАТЕЛЬНЫЕ КНОПКИ НАЗАД / ОТМЕНА СТАРТОВОГО СЛОЯ ──────────────────


@router.callback_query(F.data == "appoint:cancel")
async def appoint_cancel(call: CallbackQuery):
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.answer()

# Используй тот router, который объявлен в этом файле (например, router = Router())
@router.message(F.text == "🔄 Проверить юзернеймы")
async def cmd_check_usernames(message: Message, bot):
    await message.answer("🔄 Запускаю ручную проверку юзернеймов...")

    # Вызываем ту же функцию, что и в планировщике
    await check_and_update_usernames(bot)

    await message.answer("✅ Проверка юзернеймов успешно завершена!")