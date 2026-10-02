"""
Обработчики команд пуш-системы. Включают личный таймер игрока и админские панели.
"""

import asyncio
import logging
from datetime import datetime, timedelta

import aiosqlite
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command  # Добавлено для команд старта/проверки сезона
from aiogram.utils.markdown import html_decoration as hd

import config
from database import DB_PATH, get_all_members, get_member, get_push_goals, save_push_goal
from services.api_service import get_player_profile
from utils.permissions import can_launch_push_goal, get_admin_rights_from_file, is_any_admin
from utils.formatting import PUSH_GOAL_TEXT
from utils.keyboards import (
    launch_push_confirm_keyboard,
    push_goal_keyboard,
    confirm_push_goal_keyboard,
    change_push_goal_keyboard,
    notify_undecided_keyboard,
    confirm_notify_keyboard,
    push_players_keyboard,
    push_player_goal_keyboard,
)

# Импортируем наши сервисы из соседних файлов папки
from .services import (
    launch_push_vote,
    get_undecided_members,
    notify_undecided_users,
    notify_clan_news,
)
from .control import (
    GOAL_LABELS,
    PUSH_NORMS,
    check_season_results,
    fmt_num,
    get_push_minimums,
    get_season_start,
    season_start_trophies,
    start_new_push_season,
)

logger = logging.getLogger(__name__)
router = Router()

OWNER_IDS = {7899153362, 5281584435}
MINTOP_HINT = "📏 Минималки и сколько тебе осталось до нормы — команда /MinTop"
LEAGUE_REMINDER = "⚠️ А также не забывай про лигу! К сожалению, бот не может её посмотреть :("

# Варианты нормы для каждой цели: ключи из PUSH_NORMS (control.py).
GOAL_VARIANTS = {
    "trophies": ("trophies_main", "trophies_alt"),  # X/1.2 + Лега 1 | X/1.1 + Мифик 1
    "league": ("league_main", "league_alt"),        # X/1.3 + Лега 3 | X/1.35 + Мастер 1
}
PLAYERS_SETTINGS_TEXT = (
    "⚙️ <b>Настройка игроков основы</b>\n\n"
    "Выбери игрока, чтобы перевести его с лиги на трофеи или обратно.\n"
    "🏆 — пушит трофеи, 🏅 — пушит лигу, ❓ — цель не выбрана"
)

# Защита от двойного нажатия «Да, запустить»: вторая рассылка стёрла бы выборы.
_launch_lock = asyncio.Lock()


# ─── helpers ─────────────────────────────────────────────────────────────────

def _goal_label(goal) -> str:
    return GOAL_LABELS.get(goal, "❓ не выбрана")


def _is_squad_player(member) -> bool:
    """Анкету видят только зарегистрированные участники основы."""
    return bool(member) and member.get("clan") == "squad" and member.get("registered") == 1


def _can_manage_push(member) -> bool:
    """Доступ такой же, как у папки «🎯 Управление пуш-сезоном»."""
    return bool(member) and member.get("role") not in ("member", "helper")


def _parse_chosen_at(raw):
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            return datetime.fromisoformat(raw)
        except Exception:
            return None


async def _answer_long(message: Message, text: str, **kwargs) -> None:
    """Отправляет длинный HTML-текст частями по целым строкам (лимит Telegram 4096)."""
    chunk = ""
    for line in text.split("\n"):
        candidate = f"{chunk}\n{line}" if chunk else line
        if chunk and len(candidate) > 3900:
            await message.answer(chunk, **kwargs)
            chunk = line
        else:
            chunk = candidate
    if chunk:
        await message.answer(chunk, **kwargs)


async def _force_set_goal(user_id: int, goal: str) -> bool:
    """Принудительная смена цели администрацией (в обход 2-дневного дедлайна)."""
    try:
        async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute(
                """
                INSERT INTO push_goals (user_id, goal, chosen_at, season_id)
                VALUES (?, ?, datetime('now'), 'current')
                ON CONFLICT(user_id) DO UPDATE SET
                    goal = excluded.goal,
                    chosen_at = datetime('now')
                """,
                (int(user_id), goal),
            )
            await db.commit()
        return True
    except Exception:
        logger.exception(f"Не удалось принудительно сохранить цель пуша для {user_id}")
        return False


async def _squad_push_players() -> list[dict]:
    """Зарегистрированные игроки основы с их текущей целью пуша."""
    goals_map = {}
    for g in await get_push_goals():
        try:
            goals_map[int(g["user_id"])] = g.get("goal")
        except (KeyError, TypeError, ValueError):
            continue

    players = []
    for m in await get_all_members():
        if not _is_squad_player(m):
            continue
        try:
            uid = int(m.get("user_id") or 0)
        except (TypeError, ValueError):
            continue
        if uid <= 0:
            continue
        players.append({
            "user_id": uid,
            "nick": str(m.get("game_nick") or m.get("username") or uid),
            "goal": goals_map.get(uid),
        })
    players.sort(key=lambda p: p["nick"].lower())
    return players


async def _squad_trophies_map() -> tuple[dict, dict]:
    """(user_id -> текущие кубки, user_id -> ник) по основе."""
    trophies_map, nick_map = {}, {}
    for m in await get_all_members():
        if not _is_squad_player(m):
            continue
        try:
            uid = int(m["user_id"])
        except (KeyError, TypeError, ValueError):
            continue
        trophies_map[uid] = int(m.get("trophies") or 0)
        nick_map[uid] = str(m.get("game_nick") or m.get("username") or uid)
    return trophies_map, nick_map


def _progress_suffix(user_id: int, snapshot: dict, trophies_map: dict) -> str:
    """« | старт 120 000 🏆 → 121 500 (+1 500)» — если старт сезона зафиксирован."""
    start = season_start_trophies(snapshot, user_id)
    if start is None:
        return ""
    now = trophies_map.get(user_id)
    if now is None:
        return f" | старт {fmt_num(start)} 🏆"
    delta = now - start
    sign = f"+{fmt_num(delta)}" if delta > 0 else fmt_num(delta)
    return f" | старт {fmt_num(start)} 🏆 → {fmt_num(now)} ({sign})"


async def _can_view_mintop(user_id: int, member) -> bool:
    """/MinTop: зарегистрированная основа + администрация."""
    if user_id in OWNER_IDS or _is_squad_player(member):
        return True
    if member and is_any_admin(member):
        return True
    try:
        return get_admin_rights_from_file(user_id) is not None
    except Exception:
        return False


def _mintop_text(mins: dict) -> str:
    return (
        "📏 <b>Минималки пуш-сезона на текущий момент</b>\n"
        f"👑 ТОП-1 основы: <b>{hd.quote(str(mins['top_nick']))}</b> — {fmt_num(mins['top_trophies'])} 🏆\n"
        "<i>Трофеи = трофеи топ-1 участника клана / коэффициент</i>\n\n"
        "🏆 <b>Пуш трофеев</b>\n"
        f"• Трофеи <b>{fmt_num(mins['trophies_main'])}</b> (/1.2) + лига минимум <b>Лега 1</b>\n"
        f"• Без пуша лиги: трофеи <b>{fmt_num(mins['trophies_alt'])}</b> (/1.1) + лига минимум <b>Мифик 1</b>\n\n"
        "🏅 <b>Пуш лиги</b>\n"
        f"• Лига минимум <b>Лега 3</b> + трофеи <b>{fmt_num(mins['league_main'])}</b> (/1.3)\n"
        f"• Без пуша кубков: лига минимум <b>Мастер 1</b> + трофеи <b>{fmt_num(mins['league_alt'])}</b> (/1.35)\n\n"
        "<i>Цифры меняются вместе с кубками топ-1 (по данным последнего обновления кубков).</i>"
    )


async def _player_trophies(member: dict) -> tuple[int, bool]:
    """(кубки игрока, live): актуальные из Brawl Stars API, при сбое API — из базы."""
    tag = member.get("player_tag")
    if tag:
        try:
            profile = await get_player_profile(tag)
        except Exception:
            profile = None
        if profile:
            try:
                return int(profile.get("trophies") or 0), True
            except (TypeError, ValueError):
                pass
    try:
        return int(member.get("trophies") or 0), False
    except (TypeError, ValueError):
        return 0, False


def _mintop_progress_text(trophies: int, live: bool, goal, mins: dict) -> str:
    """Личный блок /MinTop: сколько трофеев осталось до нормы выбранной цели."""
    source = "" if live else " <i>(по данным последнего обновления)</i>"
    lines = ["━━━━━━━", "🎯 <b>Твой прогресс</b>"]

    if goal not in GOAL_VARIANTS:
        lines.append(f"У тебя <b>{fmt_num(trophies)}</b> 🏆{source}, цель ещё не выбрана.")
        lines.append("Выбери её кнопкой «🎯 Выбрать цель пуша» — и я посчитаю, сколько тебе осталось.")
        return "\n".join(lines)

    lines.append(f"У тебя <b>{fmt_num(trophies)}</b> 🏆{source}, выбрано: <b>{_goal_label(goal)}</b>")

    variants = []
    for key in GOAL_VARIANTS[goal]:
        league = PUSH_NORMS[key][1]
        target = int(mins.get(key) or 0)
        variants.append((target, league, target - trophies))

    if all(left <= 0 for _target, _league, left in variants):
        lines.append("✅ По трофеям норма уже выполнена — держи кубки до конца сезона!")
    else:
        lines.append("Тебе осталось:")
        for target, league, left in variants:
            status = "✅ уже добрал!" if left <= 0 else f"<b>{fmt_num(left)}</b> 🏆"
            lines.append(f"• до {fmt_num(target)} 🏆 (вариант с лигой {league}) — {status}")

    lines.append("")
    lines.append(LEAGUE_REMINDER)
    return "\n".join(lines)


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

    if not _is_squad_player(member):
        await message.answer("⛔ Данная функция доступна только для участников Основного состава (Squad).")
        return

    # Нормы считаются от ТОП-1 зарегистрированного участника основы (наш X)
    mins = await get_push_minimums()
    X = mins["top_trophies"]

    goals = await get_push_goals()
    user_vote = next((g for g in goals if int(g["user_id"]) == user_id), None)

    # Если игрок еще не голосовал
    if not user_vote:
        await message.answer(PUSH_GOAL_TEXT, parse_mode="HTML", reply_markup=push_goal_keyboard())
        return

    deadline_days = getattr(config, "PUSH_CHANGE_DEADLINE_DAYS", 2)
    current_goal_str = "🏆 Трофеи" if user_vote["goal"] == "trophies" else "🏅 Лига"

    if user_vote["goal"] == "trophies":
        target_hint = (f"Апни <b>{fmt_num(mins['trophies_main'])}</b> трофеев и Легендарную лигу 1\nИЛИ\n"
                       f"Апни <b>{fmt_num(mins['trophies_alt'])}</b> трофеев и Мифическую лигу 1")
    else:
        target_hint = (f"Апни Легендарную лигу 3 и <b>{fmt_num(mins['league_main'])}</b> трофеев\nИЛИ\n"
                       f"Лигу Мастеров и <b>{fmt_num(mins['league_alt'])}</b> трофеев")

    chosen_time = _parse_chosen_at(user_vote.get("chosen_at"))
    if chosen_time:
        end_time = chosen_time + timedelta(days=deadline_days)
        now = datetime.now()

        if end_time > now:
            diff = end_time - now
            hours, remainder = divmod(int(diff.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)

            await message.answer(
                f"📊 <b>Ваш выбор: {current_goal_str}</b>\n"
                f"🎯 <b>Твоя норма на сезон (при текущем ТОП-1 = {fmt_num(X)} 🏆):</b>\n{target_hint}\n\n"
                f"⏱ До фиксации цели осталось: <b>⌛ {hours}ч {minutes}м</b>\n"
                f"{MINTOP_HINT}\n"
                f"Вы можете изменить решение, если передумали 👇",
                parse_mode="HTML",
                reply_markup=change_push_goal_keyboard()
            )
        else:
            await message.answer(
                f"🔒 <b>Ваш выбор окончательно зафиксирован!</b>\n"
                f"Выбранная цель: <b>{current_goal_str}</b>\n"
                f"🎯 <b>Твоя норма на сезон (при текущем ТОП-1 = {fmt_num(X)} 🏆):</b>\n{target_hint}\n\n"
                f"{MINTOP_HINT}\n"
                f"<i>Лимит времени исчерпан. Удачи в пуше!</i>",
                parse_mode="HTML"
            )
    else:
        await message.answer(
            f"✅ Ваш выбор уже сохранен: <b>{current_goal_str}</b>\n{MINTOP_HINT}",
            parse_mode="HTML",
        )


# ─── 2. ОБРАБОТКА ИНЛАЙН-КНОПОК ГОЛОСОВАНИЯ ОБЫЧНЫМ ИГРОКОМ ───
@router.callback_query(F.data.startswith("push_goal:") & (F.data != "push_goal:back"))
async def choose_goal(call: CallbackQuery):
    goal = call.data.split(":")[1]
    if goal not in GOAL_LABELS:
        await call.answer("Неизвестная цель", show_alert=True)
        return
    await call.message.edit_text(
        f"Ты выбрал: <b>{_goal_label(goal)}</b>\n\nПодтверди выбор:",
        parse_mode="HTML",
        reply_markup=confirm_push_goal_keyboard(goal),
    )
    await call.answer()


@router.callback_query(F.data.startswith("push_confirm:"))
async def confirm_goal(call: CallbackQuery):
    goal = call.data.split(":")[1]
    if goal not in GOAL_LABELS:
        await call.answer("Неизвестная цель", show_alert=True)
        return

    member = await get_member(call.from_user.id)
    if not _is_squad_player(member):
        await call.answer("⛔ Опрос доступен только зарегистрированным игрокам основы.", show_alert=True)
        return

    ok = await save_push_goal(call.from_user.id, goal)
    if not ok:
        await call.answer("⛔ Время вышло! Ты уже не можешь изменить выбор (прошло 48 часов).", show_alert=True)
        return

    await call.message.edit_text(
        f"✅ Твой выбор сохранён: <b>{_goal_label(goal)}</b>\n\n"
        "Изменить его можно в любой момент в течение 2 дней (48 часов).\n\n"
        f"{MINTOP_HINT}",
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data == "push_goal:back")
async def back_to_goal(call: CallbackQuery):
    await call.message.edit_text(PUSH_GOAL_TEXT, parse_mode="HTML", reply_markup=push_goal_keyboard())
    await call.answer()


# ─── 3. АДМИН-ОПОВЕЩЕНИЯ И ЗАПУСКИ ───
@router.message(F.text == "🎯 Запустить определение цели")
@router.message(Command("EventStarted", "eventstarted", ignore_case=True))
async def ask_launch_push(message: Message):
    """Подтверждение запуска опроса (кнопка папки или команда /EventStarted)."""
    member = await get_member(message.from_user.id)
    if not member or not can_launch_push_goal(member):
        await message.answer("⛔ Нет прав.")
        return
    await message.answer(
        "⚠️ Ты авторизован как администратор.\nХочешь запустить выбор цели сезона для ВСЕЙ ОСНОВЫ?",
        reply_markup=launch_push_confirm_keyboard(),
    )


@router.callback_query(F.data == "launch_push:yes")
async def launch_push_yes(call: CallbackQuery, bot: Bot):
    member = await get_member(call.from_user.id)
    if not member or not can_launch_push_goal(member):
        await call.answer("⛔ Нет прав.", show_alert=True)
        return
    if _launch_lock.locked():
        await call.answer("⏳ Рассылка уже идёт, подожди.", show_alert=True)
        return

    async with _launch_lock:
        # Отвечаем сразу: рассылка + фиксация трофеев могут идти дольше 15 секунд.
        await call.answer("🚀 Запускаю...")
        try:
            await call.message.edit_text(
                "⏳ Рассылаю анкету игрокам основы и фиксирую стартовые трофеи сезона...\n"
                "Это может занять до минуты."
            )
        except Exception:
            pass

        sent = await launch_push_vote(bot)
        snapshot = await get_season_start()

        fresh = False
        try:
            fresh = datetime.now() - datetime.fromisoformat(str(snapshot.get("started_iso"))) < timedelta(minutes=30)
        except (TypeError, ValueError):
            fresh = False

        if fresh:
            season_block = (
                f"🏁 Старт сезона зафиксирован: <b>{hd.quote(str(snapshot.get('started_at')))}</b>\n"
                f"• игроков записано: <b>{snapshot.get('total', 0)}</b> (включая тех, кто ещё не выбрал цель)\n"
                f"• трофеи сняты через Brawl Stars API: <b>{snapshot.get('from_api', 0)}</b>"
            )
        else:
            season_block = (
                "⚠️ Стартовые трофеи сезона зафиксировать не удалось — проверь логи "
                "и запусти /start_season."
            )

        await call.message.edit_text(
            f"📢 Голосование запущено: анкета доставлена <b>{sent}</b> игрокам основы.\n\n"
            f"{season_block}\n\n"
            "Прогресс — в «📊 Список кто что пушит», нормы — /MinTop.",
            parse_mode="HTML",
        )


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

    await call.answer()
    await call.message.edit_text("⏳ Отправка уведомлений основы...")
    await notify_undecided_users(bot)
    await notify_clan_news(bot)

    await call.message.edit_text("📢 Оповещение успешно отправлено в ЛС и новости основы.")


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


# ─── 4. НАСТРОЙКА ИГРОКОВ: ПЕРЕВОД МЕЖДУ ЛИГОЙ И ТРОФЕЯМИ ───
@router.message(F.text == "⚙️ Настройка игроков")
async def push_player_settings(message: Message):
    member = await get_member(message.from_user.id)
    if not _can_manage_push(member):
        await message.answer("⛔ У вас нет доступа к управлению пуш-сезоном.")
        return
    players = await _squad_push_players()
    if not players:
        await message.answer("📭 В основном составе нет зарегистрированных игроков.")
        return
    await message.answer(PLAYERS_SETTINGS_TEXT, parse_mode="HTML", reply_markup=push_players_keyboard(players))


@router.callback_query(F.data == "pushset:list")
async def pushset_list(call: CallbackQuery):
    member = await get_member(call.from_user.id)
    if not _can_manage_push(member):
        await call.answer("⛔ Нет прав.", show_alert=True)
        return
    players = await _squad_push_players()
    if not players:
        await call.message.edit_text("📭 В основном составе нет зарегистрированных игроков.")
        await call.answer()
        return
    await call.message.edit_text(PLAYERS_SETTINGS_TEXT, parse_mode="HTML",
                                 reply_markup=push_players_keyboard(players))
    await call.answer()


@router.callback_query(F.data.startswith("pushset:open:"))
async def pushset_open(call: CallbackQuery):
    member = await get_member(call.from_user.id)
    if not _can_manage_push(member):
        await call.answer("⛔ Нет прав.", show_alert=True)
        return
    try:
        target_id = int(call.data.rsplit(":", 1)[-1])
    except ValueError:
        await call.answer("Некорректный игрок", show_alert=True)
        return

    player = next((p for p in await _squad_push_players() if p["user_id"] == target_id), None)
    if not player:
        await call.answer("Игрок не найден среди зарегистрированных участников основы", show_alert=True)
        return

    await call.message.edit_text(
        f"👤 <b>{hd.quote(player['nick'])}</b>\n"
        f"Текущая цель: {_goal_label(player['goal'])}\n\n"
        "Куда перевести игрока? (2-дневный таймер на изменение перезапустится)",
        parse_mode="HTML",
        reply_markup=push_player_goal_keyboard(target_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("pushset:set:"))
async def pushset_set(call: CallbackQuery, bot: Bot):
    member = await get_member(call.from_user.id)
    if not _can_manage_push(member):
        await call.answer("⛔ Нет прав.", show_alert=True)
        return
    parts = call.data.split(":")
    if len(parts) != 4:
        await call.answer("Некорректная кнопка", show_alert=True)
        return
    try:
        target_id = int(parts[2])
    except ValueError:
        await call.answer("Некорректный игрок", show_alert=True)
        return
    goal = parts[3]
    if goal not in GOAL_LABELS:
        await call.answer("Неизвестная цель", show_alert=True)
        return

    if not any(p["user_id"] == target_id for p in await _squad_push_players()):
        await call.answer("Игрок больше не состоит среди зарегистрированных участников основы", show_alert=True)
        return

    if not await _force_set_goal(target_id, goal):
        await call.answer("Не удалось сохранить цель", show_alert=True)
        return

    try:
        await bot.send_message(
            target_id,
            f"🎯 Администрация изменила твою цель пуша на: <b>{_goal_label(goal)}</b>.\n"
            f"Изменить её можно в течение 2 дней.\n\n{MINTOP_HINT}",
            parse_mode="HTML",
        )
    except Exception:
        pass

    players = await _squad_push_players()
    nick = next((p["nick"] for p in players if p["user_id"] == target_id), f"ID {target_id}")
    await call.message.edit_text(
        f"✅ {hd.quote(nick)} переведён на {_goal_label(goal)}.\n\n{PLAYERS_SETTINGS_TEXT}",
        parse_mode="HTML",
        reply_markup=push_players_keyboard(players),
    )
    await call.answer("Цель изменена")


@router.callback_query(F.data == "pushset:close")
async def pushset_close(call: CallbackQuery):
    try:
        await call.message.delete()
    except Exception:
        await call.message.edit_text("❌ Окно закрыто.")
    await call.answer()


# ─── 5. АДМИНКА: ТАЙМЕРЫ ДЛЯ ЛИДЕРА И СТАТИСТИКА ───
@router.message(F.text == "📊 Список кто что пушит")
async def show_push_targets_list(message: Message):
    member = await get_member(message.from_user.id)
    if not member or member.get("role") == "member":
        await message.answer("⛔ Нет прав.")
        return

    squad_goals = [g for g in await get_push_goals() if g.get("clan") == "squad"]
    snapshot = await get_season_start()
    if not squad_goals and not snapshot.get("players"):
        await message.answer("📭 Пока никто не выбрал цель в этом сезоне.")
        return

    trophies_map, nick_map = await _squad_trophies_map()
    deadline_days = getattr(config, "PUSH_CHANGE_DEADLINE_DAYS", 2)

    trophies_list = []
    league_list = []
    decided_ids = set()

    for g in squad_goals:
        try:
            uid = int(g["user_id"])
        except (KeyError, TypeError, ValueError):
            continue
        decided_ids.add(uid)

        raw_nick = g.get("game_nick") or g.get("username") or nick_map.get(uid) or f"ID: {uid}"
        nick = hd.quote(str(raw_nick))

        timer_suffix = " (🔒)"
        chosen_time = _parse_chosen_at(g.get("chosen_at"))
        if chosen_time:
            end_time = chosen_time + timedelta(days=deadline_days)
            now = datetime.now()
            if end_time > now:
                diff = end_time - now
                hours, remainder = divmod(int(diff.total_seconds()), 3600)
                minutes, _ = divmod(remainder, 60)
                timer_suffix = f" (⌛ {hours}ч {minutes}м)"

        item_str = f"{nick}{timer_suffix}{_progress_suffix(uid, snapshot, trophies_map)}"
        if g["goal"] == "trophies":
            trophies_list.append(item_str)
        elif g["goal"] == "league":
            league_list.append(item_str)

    text = "<b>📊 Цели пуша основного состава с таймерами:</b>\n\n🏆 <b>Пушат Трофеи:</b>\n"
    text += "\n".join([f"  {i}. {l}" for i, l in enumerate(trophies_list, 1)]) if trophies_list else "  — нет игроков\n"
    text += "\n\n🏅 <b>Пушат Лигу:</b>\n"
    text += "\n".join([f"  {i}. {l}" for i, l in enumerate(league_list, 1)]) if league_list else "  — нет игроков\n"

    # Без цели: стартовые трофеи сезона зафиксированы и для них.
    undecided_ids = [uid for uid in nick_map if uid not in decided_ids]
    if undecided_ids:
        lines = []
        for uid in sorted(undecided_ids, key=lambda i: nick_map[i].lower()):
            start = season_start_trophies(snapshot, uid)
            start_text = f" — старт {fmt_num(start)} 🏆" if start is not None else ""
            lines.append(f"  • {hd.quote(nick_map[uid])}{start_text}")
        text += "\n\n❓ <b>Без цели:</b>\n" + "\n".join(lines)

    if snapshot.get("started_at"):
        text += f"\n\n🏁 Старт сезона: {hd.quote(str(snapshot['started_at']))}"
    text += "\n\n<i>🔒 — выбор зафиксирован</i>\n<i>⌛ — время на изменение решения</i>"
    await _answer_long(message, text, parse_mode="HTML")


# ─── 6. МИНИМАЛКИ НА ТЕКУЩИЙ МОМЕНТ ───
@router.message(Command("MinTop", "mintop", ignore_case=True))
async def cmd_min_top(message: Message):
    """Текущие нормы по трофеям (от ТОП-1 основы) + минималки по лиге текстом."""
    user_id = message.from_user.id
    member = await get_member(user_id)
    if not await _can_view_mintop(user_id, member):
        await message.answer("⛔ Команда доступна только участникам основного состава (Squad).")
        return

    mins = await get_push_minimums()
    if mins["top_trophies"] <= 0:
        await message.answer("📭 Пока нет данных о кубках основы — посчитать минималки не из чего.")
        return

    text = _mintop_text(mins)
    if _is_squad_player(member):
        goal = None
        for g in await get_push_goals():
            try:
                if int(g["user_id"]) == user_id:
                    goal = g.get("goal")
                    break
            except (KeyError, TypeError, ValueError):
                continue
        trophies, live = await _player_trophies(member)
        text += "\n\n" + _mintop_progress_text(trophies, live, goal, mins)
    else:
        text += "\n\n<i>Лигу бот не видит — её администрация проверяет вручную.</i>"
    await message.answer(text, parse_mode="HTML")


# ─── 7. ФИНАЛЬНЫЕ КОМАНДЫ НАЧАЛА И КОНЦА СЕЗОНА ДЛЯ СТРОГОГО КОНТРОЛЯ ───
@router.message(Command("start_season"))
async def cmd_start_season(message: Message):
    """Фиксирует текущие кубки основы как стартовую точку сезона (без рассылки)."""
    if message.from_user.id != 7899153362:
        return
    await message.answer("⏳ Фиксирую стартовые трофеи сезона по основе...")
    snapshot = await start_new_push_season()
    await message.answer(
        "🚀 <b>Старт нового сезона пуша зафиксирован!</b>\n"
        f"• время фиксации: {hd.quote(str(snapshot.get('started_at', '—')))}\n"
        f"• игроков записано: {snapshot.get('total', 0)}\n"
        f"• трофеи сняты через Brawl Stars API: {snapshot.get('from_api', 0)}",
        parse_mode="HTML",
    )


@router.message(Command("check_season"))
async def cmd_check_season(message: Message, bot: Bot):
    """Запрашивает API, проверяет трофейные нормы и выдает отчет по штрафникам."""
    if message.from_user.id != 7899153362:
        return
    await message.answer(
        "⏳ <b>Запуск проверки выполнения норм сезона...</b>\n"
        "Опрашиваю Brawl Stars API по каждому игроку основы, пожалуйста подождите...",
        parse_mode="HTML",
    )
    report = await check_season_results(bot)
    await _answer_long(message, report, parse_mode="HTML")
