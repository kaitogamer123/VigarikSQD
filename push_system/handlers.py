"""
Обработчики команд пуш-системы. Включают личный таймер игрока и админские панели.
"""

import logging
from datetime import datetime, timedelta
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command  # Добавлено для команд старта/проверки сезона
from aiogram.utils.markdown import html_decoration as hd

import config
from database import get_member, get_push_goals, save_push_goal
from utils.permissions import can_launch_push_goal
from utils.formatting import PUSH_GOAL_TEXT
from utils.keyboards import (
    launch_push_confirm_keyboard,
    push_goal_keyboard,
    confirm_push_goal_keyboard,
    change_push_goal_keyboard,
    notify_undecided_keyboard,
    confirm_notify_keyboard,
)

# Импортируем наши сервисы из соседних файлов папки
from .services import (
    launch_push_vote,
    get_undecided_members,
    notify_undecided_users,
    notify_clan_news,
)
from .control import start_new_push_season, check_season_results

logger = logging.getLogger(__name__)
router = Router()


# ─── 1. ТЕКСТОВАЯ КНОПКА МЕНЮ: ВЫБОР ЦЕЛИ ИЛИ АДМИН-ОПРОС ───
@router.message(F.text == "🎯 Выбрать цель пуша")
async def start_push_goal(message: Message):
    user_id = message.from_user.id
    member = await get_member(user_id)

    if member and can_launch_push_goal(member):
        await message.answer(
            "⚠️ Ты авторизован как администратор.\nХочешь запустить выбор цели сезона для ВСЕЙ ОСНОВЫ?",
            reply_markup=launch_push_confirm_keyboard(),
        )
        return

    if not member or member.get("clan") != "squad":
        await message.answer("⛔ Данная функция доступна только для участников Основного состава (Squad).")
        return

    # Запрашиваем участников, чтобы найти ТОП-1 (наш X)
    from database import get_all_members
    all_members = await get_all_members()
    squad_members = [m for m in all_members if m.get("clan") == "squad" and m.get("registered") == 1]

    X = 0
    if squad_members:
        top_1 = max(squad_members, key=lambda m: m.get("trophies", 0))
        X = top_1.get("trophies", 0)

    # Рассчитываем точные планки кубков для вывода игроку
    x_1_1 = int(X / 1.1) if X > 0 else 0
    x_1_2 = int(X / 1.2) if X > 0 else 0
    x_1_3 = int(X / 1.3) if X > 0 else 0
    x_1_35 = int(X / 1.35) if X > 0 else 0

    goals = await get_push_goals()
    user_vote = next((g for g in goals if int(g["user_id"]) == user_id), None)

    # Если игрок еще не голосовал
    if not user_vote:
        await message.answer(PUSH_GOAL_TEXT, parse_mode="HTML", reply_markup=push_goal_keyboard())
        return

    deadline_days = getattr(config, "PUSH_CHANGE_DEADLINE_DAYS", 2)
    chosen_at_str = user_vote.get("chosen_at")
    current_goal_str = "🏆 Трофеи" if user_vote["goal"] == "trophies" else "🏅 Лига"

    # ИСПРАВЛЕНО: Убрали упоминания формул (X/1.2 и т.д.) из текста для игрока
    if user_vote["goal"] == "trophies":
        target_hint = f"Апни <b>{x_1_2:,}</b> трофеев и Легендарную лигу 1\nИЛИ\nАпни <b>{x_1_1:,}</b> трофеев и Мифическую лигу 1"
    else:
        target_hint = f"Апни Легендарную лигу 3 и <b>{x_1_3:,}</b> трофеев\nИЛИ\nЛигу Мастеров и <b>{x_1_35:,}</b> трофеев"

    chosen_time = None
    if chosen_at_str:
        try:
            chosen_time = datetime.strptime(chosen_at_str, "%Y-%m-%d %H:%M:%S")
        except Exception:
            try:
                chosen_time = datetime.fromisoformat(chosen_at_str)
            except Exception:
                chosen_time = None

    if chosen_time:
        end_time = chosen_time + timedelta(days=deadline_days)
        now = datetime.now()

        if end_time > now:
            diff = end_time - now
            hours, remainder = divmod(int(diff.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)

            await message.answer(
                f"📊 <b>Ваш выбор: {current_goal_str}</b>\n"
                f"🎯 <b>Твоя норма на сезон (при текущем ТОП-1 = {X:,} 🏆):</b>\n{target_hint}\n\n"
                f"⏱ До фиксации цели осталось: <b>⌛ {hours}ч {minutes}м</b>\n"
                f"Вы можете изменить решение, если передумали 👇",
                parse_mode="HTML",
                reply_markup=change_push_goal_keyboard()
            )
        else:
            await message.answer(
                f"🔒 <b>Ваш выбор окончательно зафиксирован!</b>\n"
                f"Выбранная цель: <b>{current_goal_str}</b>\n"
                f"🎯 <b>Твоя норма на сезон (при текущем ТОП-1 = {X:,} 🏆):</b>\n{target_hint}\n\n"
                f"<i>Лимит времени исчерпан. Удачи в пуше!</i>",
                parse_mode="HTML"
            )
    else:
        await message.answer(f"✅ Ваш выбор уже сохранен: <b>{current_goal_str}</b>", parse_mode="HTML")


# ─── 2. ОБРАБОТКА ИНЛАЙН-КНОПОК ГОЛОСОВАНИЯ ОБЫЧНЫМ ИГРОКОМ ───
@router.callback_query(F.data.startswith("push_goal:") & (F.data != "push_goal:back"))
async def choose_goal(call: CallbackQuery):
    goal = call.data.split(":")[1]
    await call.message.edit_text(
        f"Ты выбрал: <b>{'🏆 Трофеи' if goal == 'trophies' else '🏅 Лига'}</b>\n\nПодтверди выбор:",
        parse_mode="HTML",
        reply_markup=confirm_push_goal_keyboard(goal),
    )
    await call.answer()


@router.callback_query(F.data.startswith("push_confirm:"))
async def confirm_goal(call: CallbackQuery):
    goal = call.data.split(":")[1]
    user_id = call.from_user.id

    ok = await save_push_goal(user_id, goal)
    if not ok:
        await call.answer("⛔ Время вышло! Ты уже не можешь изменить выбор (прошло 48 часов).", show_alert=True)
        return

    await call.message.edit_text(
        "✅ Твой выбор сохранён!\n\nИзменить его можно в любой момент в течение 2 дней (48 часов).")
    await call.answer()


@router.callback_query(F.data == "push_goal:back")
async def back_to_goal(call: CallbackQuery):
    await call.message.edit_text(PUSH_GOAL_TEXT, parse_mode="HTML", reply_markup=push_goal_keyboard())
    await call.answer()


# ─── 3. АДМИН-ОПОВЕЩЕНИЯ И ЗАПУСКЫ ───
@router.callback_query(F.data == "launch_push:yes")
async def launch_push_yes(call: CallbackQuery, bot: Bot):
    member = await get_member(call.from_user.id)
    if not member or not can_launch_push_goal(member):
        await call.answer("⛔ Нет прав.", show_alert=True)
        return

    await launch_push_vote(bot)
    await call.message.edit_text("📢 Голосование успешно запущено всем участникам основы.")
    await call.answer()


@router.callback_query(F.data == "launch_push:no")
async def launch_push_no(call: CallbackQuery):
    await call.message.edit_text("❌ Отменено.")
    await call.answer()


@router.message(F.text == "❓ Кто не определился с пушем")
async def undecided_list(message: Message):
    member = await get_member(message.from_user.id)
    if not member or not can_launch_push_goal(member):
        await message.answer("⛔ Нет прав.")
        return

    undecided = await get_undecided_members()
    if not undecided:
        await message.answer("✅ Все игроки основы успешно выбрали цели!")
        return

    text = "<b>❗ Не определились в ОСНОВЕ:</b>\n\n"
    for u in undecided:
        raw_nick = u.get("game_nick") or u.get("username") or str(u["user_id"])
        text += f"• {hd.quote(str(raw_nick))}\n"

    await message.answer(text, reply_markup=notify_undecided_keyboard())


@router.callback_query(F.data == "undecided:notify")
async def undecided_notify_confirm(call: CallbackQuery):
    member = await get_member(call.from_user.id)
    if not member or member.get("role") == "member":
        await call.answer("⛔ Нет прав.", show_alert=True)
        return

    await call.message.edit_text(
        "⚠️ Вы уверены, что хотите отправить список должников в НОВОСТНОЙ топик основы?",
        reply_markup=confirm_notify_keyboard()
    )
    await call.answer()


@router.callback_query(F.data == "notify:confirm")
async def notify_send(call: CallbackQuery, bot: Bot):
    member = await get_member(call.from_user.id)
    if not member or member.get("role") == "member":
        await call.answer("⛔ Нет прав.", show_alert=True)
        return

    await call.message.edit_text("⏳ Отправка уведомлений основы...")
    await notify_undecided_users(bot)
    await notify_clan_news(bot)

    await call.message.edit_text("📢 Оповещение успешно отправлено в ЛС и новости основы.")
    await call.answer()


@router.callback_query(F.data == "notify:cancel")
async def notify_cancel(call: CallbackQuery):
    await call.message.edit_text("❌ Отменено.")
    await call.answer()


@router.callback_query(F.data == "undecided:back")
async def undecided_back(call: CallbackQuery):
    try:
        await call.message.delete()
    except Exception:
        await call.message.edit_text("❌ Окно закрыто.")
    await call.answer()


# ─── 4. АДМИНКА: ТАЙМЕРЫ ДЛЯ ЛИДЕРА И СТАТИСТИКА ───
@router.message(F.text == "📊 Список кто что пушит")
async def show_push_targets_list(message: Message):
    member = await get_member(message.from_user.id)
    if not member or member.get("role") == "member":
        await message.answer("⛔ Нет прав.")
        return

    goals = await get_push_goals()
    if not goals:
        await message.answer("📭 Пока никто не выбрал цель в этом сезоне.")
        return

    trophies_list = []
    league_list = []
    deadline_days = getattr(config, "PUSH_CHANGE_DEADLINE_DAYS", 2)

    for g in goals:
        if g.get("clan") != "squad":
            continue

        raw_nick = g.get("game_nick") or g.get("username") or f"ID: {g['user_id']}"
        nick = hd.quote(str(raw_nick))

        chosen_at_str = g.get("chosen_at")
        timer_suffix = " (🔒)"

        if chosen_at_str:
            try:
                chosen_time = datetime.strptime(chosen_at_str, "%Y-%m-%d %H:%M:%S")
            except Exception:
                try:
                    chosen_time = datetime.fromisoformat(chosen_at_str)
                except Exception:
                    chosen_time = None

            if chosen_time:
                end_time = chosen_time + timedelta(days=deadline_days)
                now = datetime.now()
                if end_time > now:
                    diff = end_time - now
                    hours, remainder = divmod(int(diff.total_seconds()), 3600)
                    minutes, _ = divmod(remainder, 60)
                    timer_suffix = f" (⌛ {hours}ч {minutes}м)"

        item_str = f"{nick}{timer_suffix}"
        if g["goal"] == "trophies":
            trophies_list.append(item_str)
        elif g["goal"] == "league":
            league_list.append(item_str)

    text = "<b>📊 Цели пуша основного состава с таймерами:</b>\n\n🏆 <b>Пушат Трофеи:</b>\n"
    text += "\n".join([f"  {i}. {l}" for i, l in enumerate(trophies_list, 1)]) if trophies_list else "  — нет игроков\n"
    text += "\n\n🏅 <b>Пушат Лигу:</b>\n"
    text += "\n".join([f"  {i}. {l}" for i, l in enumerate(league_list, 1)]) if league_list else "  — нет игроков\n"
    text += "\n\n<i>🔒 — выбор зафиксирован\n⌛ — время на изменение решения</i>"
    await message.answer(text, parse_mode="HTML")


# ─── 5. ФИНАЛЬНЫЕ КОМАНДЫ НАЧАЛА И КОНЦА СЕЗОНА ДЛЯ СТРОГОГО КОНТРОЛЯ ───
@router.message(Command("start_season"))
async def cmd_start_season(message: Message):
    """Фиксирует текущие кубки основы как стартовую точку сезона."""
    if message.from_user.id != 7899153362:
        return
    await start_new_push_season()
    await message.answer("🚀 <b>Старт нового сезона пуша зафиксирован!</b>\nТекущие кубки всех участников записаны в базу данных.", parse_mode="HTML")


@router.message(Command("check_season"))
async def cmd_check_season(message: Message, bot: Bot):
    """Запрашивает API, проверяет ТЗ и выдает отчет по штрафникам."""
    if message.from_user.id != 7899153362:
        return
    await message.answer("⏳ <b>Запуск проверки выполнения норм сезона...</b>\nОпрашиваю Brawl Stars API по каждому игроку основы, пожалуйста подождите...", parse_mode="HTML")
    report = await check_season_results(bot)
    await message.answer(report, parse_mode="HTML")

