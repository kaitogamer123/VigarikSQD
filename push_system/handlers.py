""" Обработчики команд пуш-системы. Поддержка кланов Основа + Академия:
у каждого клана свой текст анкеты и свои коэффициенты (control.py / formatting.py). """
import asyncio
import logging
from datetime import datetime, timedelta

import aiosqlite
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command
from aiogram.utils.markdown import html_decoration as hd

import config
from database import DB_PATH, get_all_members, get_member, get_push_goals, save_push_goal
from services.api_service import get_player_profile
from utils.permissions import can_launch_push_goal, get_admin_rights_from_file, is_any_admin
from utils.formatting import PUSH_GOAL_TEXTS, PUSH_GOAL_TEXT
from utils.keyboards import (
    launch_push_confirm_keyboard, push_goal_keyboard, confirm_push_goal_keyboard,
    change_push_goal_keyboard, notify_undecided_keyboard, confirm_notify_keyboard,
    push_players_keyboard, push_player_goal_keyboard,
)

from .services import (
    launch_push_vote, get_undecided_members, notify_undecided_users, notify_clan_news,
)
from .control import (
    GOAL_LABELS, PUSH_NORMS, PUSH_NORMS_BY_CLAN, PUSH_CLANS,
    check_season_results, fmt_num, get_push_minimums,
    get_season_start, season_start_trophies, start_new_push_season,
)

logger = logging.getLogger(__name__)
router = Router()

OWNER_IDS = {7899153362, 5281584435}
MINTOP_HINT = "📏 Минималки и сколько тебе осталось до нормы — команда /MinTop"
LEAGUE_REMINDER = "⚠️ А также не забывай про лигу! К сожалению, бот не может её посмотреть :("

CLAN_SHORT = {"squad": "Основа", "academy": "Академия"}

# Варианты нормы для каждой цели: ключи из PUSH_NORMS_BY_CLAN (control.py).
GOAL_VARIANTS = {
    "trophies": ("trophies_main", "trophies_alt"),
    "league": ("league_main", "league_alt"),
}

PLAYERS_SETTINGS_TEXT = (
    "⚙️ Настройка игроков (Основа и Академия) \n\n"
    "Выбери игрока, чтобы перевести его с лиги на трофеи или обратно.\n"
    "🏆 — пушит трофеи, 🏅 — пушит лигу, ❓ — цель не выбрана"
)

# Защита от двойного нажатия «Да, запустить»: вторая рассылка стёрла бы выборы.
_launch_lock = asyncio.Lock()


# ─── helpers ─────────────────────────────────────────────────────────────────

def _goal_label(goal) -> str:
    return GOAL_LABELS.get(goal, "❓ не выбрана")


def _clan_of(member) -> str:
    return (member or {}).get("clan") or ""


def _is_push_player(member) -> bool:
    """Анкету видят зарегистрированные участники кланов с пуш-системой."""
    return bool(member) and _clan_of(member) in PUSH_CLANS and member.get("registered") == 1


def _can_manage_push(member) -> bool:
    """Доступ такой же, как у папки «🎯 Управление пуш-сезоном»."""
    return bool(member) and member.get("role") not in ("member", "helper")


def _norms(clan: str) -> dict:
    return PUSH_NORMS_BY_CLAN.get(clan, PUSH_NORMS)


def _target_hint(clan: str, goal: str, mins: dict) -> str:
    """Подсказка нормы игроку по его клану/цели (все 4 варианта из коэффициентов)."""
    norms = _norms(clan)
    if goal == "trophies":
        return (
            f"Апни {fmt_num(mins['trophies_main'])} трофеев и лигу минимум {norms['trophies_main'][1]}\n"
            f"ИЛИ\n"
            f"Апни {fmt_num(mins['trophies_alt'])} трофеев и лигу минимум {norms['trophies_alt'][1]}"
        )
    return (
        f"Добей лигу минимум {norms['league_main'][1]} и {fmt_num(mins['league_main'])} трофеев\n"
        f"ИЛИ\n"
        f"Лигу минимум {norms['league_alt'][1]} и {fmt_num(mins['league_alt'])} трофеев"
    )


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
                ON CONFLICT(user_id) DO UPDATE SET goal = excluded.goal, chosen_at = datetime('now')
                """,
                (int(user_id), goal),
            )
            await db.commit()
        return True
    except Exception:
        logger.exception(f"Не удалось принудительно сохранить цель пуша для {user_id}")
        return False


async def _push_players() -> list[dict]:
    """Зарегистрированные игроки кланов пуш-системы с их текущей целью пуша."""
    goals_map = {}
    for g in await get_push_goals():
        try:
            goals_map[int(g["user_id"])] = g.get("goal")
        except (KeyError, TypeError, ValueError):
            continue
    players = []
    for m in await get_all_members():
        if not _is_push_player(m):
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
            "clan": _clan_of(m),
            "goal": goals_map.get(uid),
        })
    return players


async def _push_trophies_map() -> tuple[dict, dict]:
    """(user_id -> текущие кубки, user_id -> ник) по кланам пуш-системы."""
    trophies_map, nick_map = {}, {}
    for m in await get_all_members():
        if not _is_push_player(m):
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
    """/MinTop: зарегистрированные игроки пуш-кланов + администрация."""
    if user_id in OWNER_IDS or _is_push_player(member):
        return True
    if member and is_any_admin(member):
        return True
    try:
        return get_admin_rights_from_file(user_id) is not None
    except Exception:
        return False


def _mintop_text(mins: dict) -> str:
    """Блок минималок: коэффициенты и лиги берутся из норм клана (control.py)."""
    clan = mins.get("clan") or "squad"
    norms = _norms(clan)
    clan_title = CLAN_SHORT.get(clan, clan)
    return (
        f"📏 Минималки пуш-сезона ({clan_title}) на текущий момент \n"
        f"👑 ТОП-1 {clan_title}: {hd.quote(str(mins['top_nick']))} — {fmt_num(mins['top_trophies'])} 🏆\n"
        "   Трофеи = трофеи топ-1 участника клана ÷ коэффициент \n\n"
        "🏆 Пуш трофеев \n"
        f"• Трофеи {fmt_num(mins['trophies_main'])} (÷{norms['trophies_main'][0]}) "
        f"+ лига минимум {norms['trophies_main'][1]} \n"
        f"• Без пуша лиги: трофеи {fmt_num(mins['trophies_alt'])} (÷{norms['trophies_alt'][0]}) "
        f"+ лига минимум {norms['trophies_alt'][1]} \n\n"
        "🏅 Пуш лиги \n"
        f"• Лига минимум {norms['league_main'][1]} + трофеи {fmt_num(mins['league_main'])} "
        f"(÷{norms['league_main'][0]})\n"
        f"• Без пуша кубков: лига минимум {norms['league_alt'][1]} + трофеи "
        f"{fmt_num(mins['league_alt'])} (÷{norms['league_alt'][0]})\n\n"
        "   Цифры меняются вместе с кубками топ-1 (по данным последнего обновления кубков). "
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


def _mintop_progress_text(trophies: int, live: bool, goal, mins: dict, clan: str) -> str:
    """Личный блок /MinTop: сколько трофеев осталось до нормы выбранной цели."""
    source = "" if live else " (по данным последнего обновления) "
    lines = ["━━━━━━━", "🎯 Твой прогресс "]
    if goal not in GOAL_VARIANTS:
        lines.append(f"У тебя {fmt_num(trophies)} 🏆{source}, цель ещё не выбрана.")
        lines.append("Выбери её кнопкой «🎯 Выбрать цель пуша» — и я посчитаю, сколько тебе осталось.")
        return "\n".join(lines)
    lines.append(f"У тебя {fmt_num(trophies)} 🏆{source}, выбрано: {_goal_label(goal)} ")
    norms = _norms(clan)
    variants = []
    for key in GOAL_VARIANTS[goal]:
        league = norms[key][1]
        target = int(mins.get(key) or 0)
        variants.append((target, league, target - trophies))
    if all(left <= 0 for _, _, left in variants):
        lines.append("✅ Обе нормы по кубкам добраны — остаётся проверить лигу вручную.")
    else:
        for target, league, left in variants:
            if left <= 0:
                status = "✅ добрано"
            else:
                status = f"осталось добрать {fmt_num(left)} 🏆"
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
            "⚠️ Ты авторизован как администратор.\n"
            "Хочешь запустить выбор цели сезона для Основы и Академии?",
            reply_markup=launch_push_confirm_keyboard(),
        )
        return

    if not _is_push_player(member):
        await message.answer("⛔ Данная функция доступна только для участников Основы и Академии.")
        return

    clan = _clan_of(member)
    mins = await get_push_minimums(clan)
    X = mins["top_trophies"]

    goals = await get_push_goals()
    user_vote = next((g for g in goals if int(g["user_id"]) == user_id), None)

    # Если игрок еще не голосовал — показываем анкету его клана
    if not user_vote:
        await message.answer(PUSH_GOAL_TEXTS.get(clan, PUSH_GOAL_TEXT),
                             parse_mode="HTML", reply_markup=push_goal_keyboard())
        return

    deadline_days = getattr(config, "PUSH_CHANGE_DEADLINE_DAYS", 2)
    current_goal_str = "🏆 Трофеи" if user_vote["goal"] == "trophies" else "🏅 Лига"
    target_hint = _target_hint(clan, user_vote["goal"], mins)

    chosen_time = _parse_chosen_at(user_vote.get("chosen_at"))
    if chosen_time:
        end_time = chosen_time + timedelta(days=deadline_days)
        now = datetime.now()
        if end_time > now:
            diff = end_time - now
            hours, remainder = divmod(int(diff.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            await message.answer(
                f"📊 Ваш выбор: {current_goal_str} \n"
                f"🎯 Твоя норма на сезон (при текущем ТОП-1 = {fmt_num(X)} 🏆): \n{target_hint}\n\n"
                f"⏱ До фиксации цели осталось: ⌛ {hours}ч {minutes}м \n"
                f"{MINTOP_HINT}\n"
                f"Вы можете изменить решение, если передумали 👇",
                parse_mode="HTML",
                reply_markup=change_push_goal_keyboard()
            )
        else:
            await message.answer(
                f"🔒 Ваш выбор окончательно зафиксирован! \n"
                f"Выбранная цель: {current_goal_str} \n"
                f"🎯 Твоя норма на сезон (при текущем ТОП-1 = {fmt_num(X)} 🏆): \n{target_hint}\n\n"
                f"{MINTOP_HINT}\n"
                f" Лимит времени исчерпан. Удачи в пуше! ",
                parse_mode="HTML"
            )
    else:
        await message.answer(
            f"✅ Ваш выбор уже сохранен: {current_goal_str} \n{MINTOP_HINT}",
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
        f"Ты выбрал: {_goal_label(goal)} \n\nПодтверди выбор:",
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
    if not _is_push_player(member):
        await call.answer("⛔ Опрос доступен только зарегистрированным игрокам Основы и Академии.",
                          show_alert=True)
        return
    ok = await save_push_goal(call.from_user.id, goal)
    if not ok:
        await call.answer("⛔ Время вышло! Ты уже не можешь изменить выбор (прошло 48 часов).",
                          show_alert=True)
        return
    await call.message.edit_text(
        f"✅ Твой выбор сохранён: {_goal_label(goal)} \n\n"
        "Изменить его можно в любой момент в течение 2 дней (48 часов).\n\n"
        f"{MINTOP_HINT}",
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data == "push_goal:back")
async def back_to_goal(call: CallbackQuery):
    member = await get_member(call.from_user.id)
    clan = _clan_of(member) if _is_push_player(member) else "squad"
    await call.message.edit_text(PUSH_GOAL_TEXTS.get(clan, PUSH_GOAL_TEXT),
                                 parse_mode="HTML", reply_markup=push_goal_keyboard())
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
        "⚠️ Ты авторизован как администратор.\n"
        "Хочешь запустить выбор цели сезона для Основы и Академии?",
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
                "⏳ Рассылаю анкеты игрокам Основы и Академии и фиксирую стартовые трофеи сезона...\n"
                "Это может занять до минуты."
            )
        except Exception:
            pass

        sent = await launch_push_vote(bot)
        # По каждому клану — свой отчёт о анкете и снимке сезона
        season_blocks = []
        for clan in PUSH_CLANS:
            snapshot = await get_season_start(clan)
            fresh = False
            try:
                fresh = datetime.now() - datetime.fromisoformat(str(snapshot.get("started_iso"))) \
                        < timedelta(minutes=2)
            except Exception:
                fresh = False
            if fresh:
                season_blocks.append(
                    f"✅ Старт зафиксирован ({CLAN_SHORT.get(clan, clan)}): "
                    f"{hd.quote(str(snapshot.get('started_at')))} , "
                    f"игроков записано: {snapshot.get('total', 0)}, из API: {snapshot.get('from_api', 0)}"
                )
            else:
                season_blocks.append(
                    f"⚠️ Старт ({CLAN_SHORT.get(clan, clan)}) зафиксировать не удалось — "
                    f"проверь логи и запусти /start_season."
                )
        sent_line = " · ".join(f"{CLAN_SHORT.get(clan, clan)}: {sent.get(clan, 0)}"
                               for clan in PUSH_CLANS)
        await call.message.edit_text(
            f"📢 Голосование запущено: анкета доставлена — {sent_line}.\n\n"
            + "\n".join(season_blocks) + "\n\n"
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
        await message.answer("✅ Все игроки основы и академии успешно выбрали цели!")
        return
    by_clan: dict[str, list] = {}
    for u in undecided:
        by_clan.setdefault(u.get("clan") or "squad", []).append(u)
    chunks = []
    for clan in PUSH_CLANS:
        clan_members = by_clan.get(clan)
        if not clan_members:
            continue
        text = f" ❗ Не определились ({CLAN_SHORT.get(clan, clan)}): \n\n"
        for u in clan_members:
            raw_nick = u.get("game_nick") or u.get("username") or str(u["user_id"])
            text += f"• {hd.quote(str(raw_nick))}\n"
        chunks.append(text)
    await message.answer("\n".join(chunks), reply_markup=notify_undecided_keyboard())


@router.callback_query(F.data == "undecided:notify")
async def undecided_notify_confirm(call: CallbackQuery):
    member = await get_member(call.from_user.id)
    if not member or member.get("role") == "member":
        await call.answer("⛔ Нет прав.", show_alert=True)
        return
    await call.message.edit_text(
        "⚠️ Вы уверены, что хотите отправить список должников в новостные топики кланов?",
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
    await call.message.edit_text("⏳ Отправка уведомлений...")
    await notify_undecided_users(bot)
    await notify_clan_news(bot)
    await call.message.edit_text("📢 Оповещение успешно отправлено в ЛС и новости кланов.")


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
    players = await _push_players()
    if not players:
        await message.answer("📭 Нет зарегистрированных игроков пуш-кланов.")
        return
    await message.answer(PLAYERS_SETTINGS_TEXT, parse_mode="HTML",
                         reply_markup=push_players_keyboard(players))


@router.callback_query(F.data == "pushset:list")
async def pushset_list(call: CallbackQuery):
    member = await get_member(call.from_user.id)
    if not _can_manage_push(member):
        await call.answer("⛔ Нет прав.", show_alert=True)
        return
    players = await _push_players()
    if not players:
        await call.message.edit_text("📭 Нет зарегистрированных игроков пуш-кланов.")
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
    player = next((p for p in await _push_players() if p["user_id"] == target_id), None)
    if not player:
        await call.answer("Игрок не найден среди зарегистрированных участников пуш-кланов",
                          show_alert=True)
        return
    await call.message.edit_text(
        f"👤 {hd.quote(player['nick'])} ({CLAN_SHORT.get(player['clan'], player['clan'])}) \n"
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
    if not any(p["user_id"] == target_id for p in await _push_players()):
        await call.answer("Игрок больше не состоит среди зарегистрированных участников пуш-кланов",
                          show_alert=True)
        return
    if not await _force_set_goal(target_id, goal):
        await call.answer("Не удалось сохранить цель", show_alert=True)
        return
    try:
        await bot.send_message(
            target_id,
            f"🎯 Администрация изменила твою цель пуша на: {_goal_label(goal)} .\n"
            f"Изменить её можно в течение 2 дней.\n\n{MINTOP_HINT}",
            parse_mode="HTML",
        )
    except Exception:
        pass
    players = await _push_players()
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


# ─── 5. АДМИНКА: ТАЙМЕРЫ И СТАТИСТИКА ПО КЛАНАМ ───

@router.message(F.text == "📊 Список кто что пушит")
async def show_push_targets_list(message: Message):
    member = await get_member(message.from_user.id)
    if not member or member.get("role") == "member":
        await message.answer("⛔ Нет прав.")
        return

    deadline_days = getattr(config, "PUSH_CHANGE_DEADLINE_DAYS", 2)
    all_goals = await get_push_goals()
    sections = []
    for clan in PUSH_CLANS:
        clan_goals = [g for g in all_goals if g.get("clan") == clan]
        snapshot = await get_season_start(clan)
        if not clan_goals and not snapshot.get("players"):
            continue

        # карты трофеев по клану — строим из членов клана
        clan_members = {int(m["user_id"]): m for m in await get_all_members()
                        if _is_push_player(m) and _clan_of(m) == clan and m.get("user_id")}
        trophies_map = {uid: int(m.get("trophies") or 0) for uid, m in clan_members.items()}
        nick_map = {uid: str(m.get("game_nick") or m.get("username") or uid)
                    for uid, m in clan_members.items()}

        trophies_list, league_list = [], []
        decided_ids = set()
        for g in clan_goals:
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

        text = f" 📊 Цели пуша ({CLAN_SHORT.get(clan, clan)}) с таймерами: \n\n🏆 Пушат Трофеи: \n"
        text += "\n".join([f" {i}. {l}" for i, l in enumerate(trophies_list, 1)]) if trophies_list else " — нет игроков\n"
        text += "\n\n🏅 Пушат Лигу: \n"
        text += "\n".join([f" {i}. {l}" for i, l in enumerate(league_list, 1)]) if league_list else " — нет игроков\n"

        # Без цели: стартовые трофеи сезона зафиксированы и для них.
        undecided_ids = [uid for uid in nick_map if uid not in decided_ids]
        if undecided_ids:
            lines = []
            for uid in sorted(undecided_ids, key=lambda i: nick_map[i].lower()):
                start = season_start_trophies(snapshot, uid)
                start_text = f" — старт {fmt_num(start)} 🏆" if start is not None else ""
                lines.append(f" • {hd.quote(nick_map[uid])}{start_text}")
            text += "\n\n❓ Без цели: \n" + "\n".join(lines)
        if snapshot.get("started_at"):
            text += f"\n\n🏁 Старт сезона: {hd.quote(str(snapshot['started_at']))}"
        sections.append(text)

    if not sections:
        await message.answer("📭 Пока никто не выбрал цель в этом сезоне.")
        return
    sections.append("\n 🔒 — выбор зафиксирован \n ⌛ — время на изменение решения ")
    await _answer_long(message, "\n\n━━━━━━━\n\n".join(sections), parse_mode="HTML")


# ─── 6. МИНИМАЛКИ НА ТЕКУЩИЙ МОМЕНТ ───

@router.message(Command("MinTop", "mintop", ignore_case=True))
async def cmd_min_top(message: Message):
    """Текущие нормы по трофеям (от ТОП-1 своего клана) + минималки по лиге текстом."""
    user_id = message.from_user.id
    member = await get_member(user_id)
    if not await _can_view_mintop(user_id, member):
        await message.answer("⛔ Команда доступна только участникам Основы и Академии.")
        return
    clan = _clan_of(member) if _is_push_player(member) else "squad"
    mins = await get_push_minimums(clan)
    if mins["top_trophies"] <= 0:
        await message.answer("Пока нет данных по кубкам топ-1 клана — подожди обновление ростера.")
        return
    trophies, live = await _player_trophies(member or {})
    goal = None
    for g in await get_push_goals():
        gw = g.get("user_id")
        if gw is None:  # старые записи без user_id — пропускаем, не падаем
            continue
        try:
            if int(gw) == user_id:
                goal = g.get("goal")
                break
        except (TypeError, ValueError):
            continue
    text = _mintop_text(mins)
    text += "\n" + _mintop_progress_text(trophies, live, goal, mins, clan)
    text += "\nЛигу бот не видит — её администрация проверяет вручную. "
    await message.answer(text, parse_mode="HTML")


# ─── 7. ФИНАЛЬНЫЕ КОМАНДЫ НАЧАЛА И КОНЦА СЕЗОНА ДЛЯ СТРОГОГО КОНТРОЛЯ ───

@router.message(Command("start_season"))
async def cmd_start_season(message: Message):
    """Фиксирует текущие кубки кланов как стартовую точку сезона (без рассылки)."""
    if message.from_user.id != 7899153362:
        return
    await message.answer("⏳ Фиксирую стартовые трофеи сезона по основе и академии...")
    lines = []
    for clan in PUSH_CLANS:
        snapshot = await start_new_push_season(clan)
        lines.append(
            f"• {CLAN_SHORT.get(clan, clan)}: игроков {snapshot.get('total', 0)}, "
            f"из API {snapshot.get('from_api', 0)}, время {hd.quote(str(snapshot.get('started_at', '—')))}"
        )
    await message.answer(
        "🚀 Старт нового сезона пуша зафиксирован! \n" + "\n".join(lines),
        parse_mode="HTML",
    )


@router.message(Command("check_season"))
async def cmd_check_season(message: Message, bot: Bot):
    """Запрашивает API, проверяет трофейные нормы и выдает отчет по штрафникам."""
    if message.from_user.id != 7899153362:
        return
    await message.answer(
        "⏳ Запуск проверки выполнения норм сезона... \n"
        "Опрашиваю Brawl Stars API по каждому игроку основы и академии, пожалуйста подождите...",
        parse_mode="HTML",
    )
    for clan in PUSH_CLANS:
        report = await check_season_results(bot, clan)
        await _answer_long(message, report, parse_mode="HTML")
