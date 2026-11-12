"""Админское управление турнирами."""
import asyncio
import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.markdown import html_decoration as hd
from aiogram.fsm.context import FSMContext
from database import get_member, get_setting
from utils.permissions import is_any_admin, has_role
from utils.keyboards import (
    main_menu, admin_panel_keyboard, admin_push_keyboard, admin_members_keyboard,
    admin_participants_keyboard, admin_scrims_keyboard,
)
from utils.admin_logger import log_admin_action
from league import league_db as db
from league.competitive_common import _normalise_scheduled
from .common import (
    TOURNAMENT_GUARD_TEXTS,
    TOURNAMENT_STATUS_LABEL,
    TOURNAMENT_TYPES,
    _cb_is_admin,
    _is_duo,
    _is_solo_type,
    _is_test,
    _limit_label,
    _limit_text,
    _limit_unit,
    _split_text,
    _test_mark,
)
from .states import TournamentAdminStates
from .tournament_cards import _publish_tournament_posts, _refresh_tournament_posts
from .tournament_db import (
    _collect_tournament_for_delete,
    _drop_tournament_rows,
    _m_get,
    _pending_full_matches,
    _team_name,
    add_tournament_team,
    create_tournament_row,
    get_active_tournaments,
    get_tournament,
    get_tournament_posts,
    get_tournament_teams,
    set_tournament_limit,
    set_tournament_status,
)
from .tournament_notify import _repost_bracket
from .tournament_results import _apply_confirmed_result
from .tournament_start import start_tournament

logger = logging.getLogger(__name__)
router = Router()


def _tournaments_list_kb(rows) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for row in rows:
        teams = get_tournament_teams(int(row["id"]))
        mark = "🧪 " if _is_test(row) else ""
        builder.button(
            text=f"{mark}#{row['id']} {TOURNAMENT_TYPES.get(row['t_type'], row['t_type'])} · "
                 f"{len(teams)} уч. · {row['starts_at']}",
            callback_data=f"trnadm:open:{row['id']}",
        )
    builder.button(text="🔄 Обновить", callback_data="trnadm:list")
    builder.adjust(1)
    return builder.as_markup()


def _tournament_admin_card(tournament, teams) -> str:
    t_key = tournament["t_type"]
    lines = [
        f"{_test_mark(tournament)}🏆 <b>Турнир #{tournament['id']}</b>",
        "",
        f"• Статус: <b>{TOURNAMENT_STATUS_LABEL.get(tournament['status'], tournament['status'])}</b>",
        f"• Тип: <b>{TOURNAMENT_TYPES.get(t_key, t_key)}</b>",
    ]
    if t_key != "showdown":
        lines.append(f"• Формат матча: <b>{tournament['t_format'] or '—'}</b>")
    lines.append(f"• {_limit_label(t_key)}: <b>{_limit_text(tournament['team_limit'], t_key)}</b>")
    lines.append(f"• Старт: <b>{hd.quote(str(tournament['starts_at']))}</b>")
    lines.append("")
    unit = _limit_unit(t_key)
    lines.append(f"📋 <b>Поданные составы ({len(teams)} {unit}):</b>")
    if teams:
        for i, t in enumerate(teams, 1):
            tag = f" [{hd.quote(str(t['tag']))}]" if t["tag"] else ""
            when = str(t["registered_at"] or "")[:16]
            duo = " · дуо" if _is_duo(t_key, t) else ""
            lines.append(
                f"{i}. {hd.quote(str(t['name'] or 'Без названия'))}{tag}{duo} "
                f"— лидер ID {t['leader_id']} · {hd.quote(when)}"
            )
    else:
        lines.append("Пока никто не подал заявку.")
    return "\n".join(lines)


def _tournament_admin_kb(tournament) -> InlineKeyboardMarkup:
    tid = int(tournament["id"])
    rows = [
        [InlineKeyboardButton(text="✏️ Изменить лимит", callback_data=f"trnadm:limit:{tid}")],
        [InlineKeyboardButton(text="🔄 Обновить список", callback_data=f"trnadm:open:{tid}")],
        [InlineKeyboardButton(text="🔁 Обновить пост в топиках", callback_data=f"trnadm:repost:{tid}")],
    ]
    if tournament["status"] == "registration":
        rows.append([InlineKeyboardButton(text="▶️ Запустить турнир сейчас",
                                          callback_data=f"trnadm:start:{tid}")])
        rows.append([InlineKeyboardButton(text="🚫 Отменить турнир",
                                          callback_data=f"trnadm:cancel:{tid}")])
    if tournament["status"] == "running":
        rows.append([InlineKeyboardButton(text="⚖️ Записать итог матча",
                                          callback_data=f"trnadm:res:{tid}")])
        rows.append([InlineKeyboardButton(text="🚫 Отменить турнир",
                                          callback_data=f"trnadm:cancel:{tid}")])
    if tournament["status"] == "registration":
        # 1V1 и ShowDown — соло: участники регистрируются сами, добавлять нечего.
        if not _is_solo_type(tournament["t_type"]):
            rows.append([InlineKeyboardButton(text="➕ Добавить состав",
                                              callback_data=f"trnadm:addlist:{tid}:0")])
        if _is_test(tournament):
            rows.append([
                InlineKeyboardButton(text="🤖 +1 бот", callback_data=f"trnadm:addbot:{tid}"),
                InlineKeyboardButton(text="🤖 Заполнить до лимита", callback_data=f"trnadm:fillbots:{tid}"),
            ])
    if tournament["status"] in ("cancelled", "finished"):
        rows.append([InlineKeyboardButton(text="🔄 Пересоздать турнир",
                                          callback_data=f"trnadm:recreate:{tid}")])
    rows.append([InlineKeyboardButton(text="🗑 Удалить турнир", callback_data=f"trnadm:del:{tid}")])
    rows.append([InlineKeyboardButton(text="◀️ К списку турниров", callback_data="trnadm:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


ADD_PAGE_SIZE = 8
BOT_TAG = "BOT"


def _available_leagues(tid: int, tournament):
    """Составы, которых ещё нет в турнире (для ручной записи модератором)."""
    teams = get_tournament_teams(tid)
    used_leagues = {int(t["league_id"]) for t in teams if t["league_id"]}
    used_leaders = {int(t["leader_id"]) for t in teams}
    result = []
    for league in db.get_verified_leagues():
        if _is_solo_type(tournament["t_type"]):
            if int(league["leader_id"]) in used_leaders:
                continue
        elif int(league["id"]) in used_leagues:
            continue
        result.append(league)
    return result


def _add_list_kb(tid: int, tournament, page: int = 0):
    """Клавиатура «Добавить состав/игрока» с постраничным списком."""
    leagues = _available_leagues(tid, tournament)
    pages = max(1, (len(leagues) + ADD_PAGE_SIZE - 1) // ADD_PAGE_SIZE)
    page = max(0, min(int(page or 0), pages - 1))
    builder = InlineKeyboardBuilder()
    for league in leagues[page * ADD_PAGE_SIZE:(page + 1) * ADD_PAGE_SIZE]:
        title = f"{league['name']} [{league['tag']}]"
        builder.button(text=title[:60], callback_data=f"trnadm:addpick:{tid}:{league['id']}")
    builder.adjust(1)
    rows = list(builder.as_markup().inline_keyboard)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"trnadm:addlist:{tid}:{page - 1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="Далее ▶️", callback_data=f"trnadm:addlist:{tid}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="◀️ К турниру", callback_data=f"trnadm:open:{tid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows), len(leagues), page


async def _add_composition(tid: int, tournament, league_id: int):
    """Записывает состав (или его лидера как соло-игрока) на турнир вручную."""
    league = db.get_league(int(league_id))
    if not league:
        return False, "Состав не найден"
    teams = get_tournament_teams(tid)
    limit = tournament["team_limit"]
    if limit is not None and len(teams) >= int(limit):
        return False, "Все места уже заняты"
    if _is_solo_type(tournament["t_type"]):
        leader_id = int(league["leader_id"])
        if any(int(t["leader_id"]) == leader_id for t in teams):
            return False, "Этот игрок уже в списке"
        member = await get_member(leader_id) or {}
        name = member.get("game_nick") or member.get("username") or str(league["name"])
        tag = member.get("player_tag") or str(league["tag"] or "")
        added = add_tournament_team(tid, None, leader_id, name, tag)
        target = f"игрок «{name}»"
    else:
        if any(t["league_id"] and int(t["league_id"]) == int(league["id"]) for t in teams):
            return False, "Этот состав уже в списке"
        added = add_tournament_team(tid, int(league["id"]), int(league["leader_id"]),
                                    league["name"], league["tag"])
        target = f"состав «{league['name']}»"
    if not added:
        return False, "Не удалось добавить (возможно, уже в списке)"
    return True, f"✅ Добавлен {target}"


def _add_bots(tid: int, tournament, count: int) -> int:
    """Добавляет тестовых ботов. Возвращает, сколько реально добавлено."""
    teams = get_tournament_teams(tid)
    limit = tournament["team_limit"]
    if limit is not None:
        free = int(limit) - len(teams)
        if free <= 0:
            return 0
        count = min(count, free)
    count = max(0, min(count, 32))
    used_names = {str(t["name"]) for t in teams}
    used_leaders = {int(t["leader_id"]) for t in teams}
    added = 0
    idx = 1
    while added < count and idx <= 200:
        name = f"Бот {idx}"
        leader_id = -(900000 + idx)
        idx += 1
        if name in used_names or leader_id in used_leaders:
            continue
        if add_tournament_team(tid, None, leader_id, name, BOT_TAG):
            added += 1
    return added


@router.message(F.text == "📋 Текущие турниры")
async def admin_current_tournaments(message: Message):
    """Список активных турниров: выбор турнира для управления."""
    member = await get_member(message.from_user.id)
    if not member or not is_any_admin(member):
        await message.answer("⛔ Доступ только для администрации.")
        return
    rows = get_active_tournaments()
    if not rows:
        await message.answer("📭 Активных турниров нет. Создай новый через «🏆 Создание турнира».",
                             reply_markup=admin_scrims_keyboard())
        return
    await message.answer(
        "📋 <b>Текущие турниры</b>\n\nВыбери турнир для управления:",
        parse_mode="HTML", reply_markup=_tournaments_list_kb(rows),
    )


@router.callback_query(F.data == "trnadm:list")
async def admin_tournaments_list(call: CallbackQuery):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    rows = get_active_tournaments()
    if not rows:
        await call.message.edit_text("📭 Активных турниров нет.", reply_markup=None)
        await call.answer()
        return
    await call.message.edit_text(
        "📋 <b>Текущие турниры</b>\n\nВыбери турнир для управления:",
        parse_mode="HTML", reply_markup=_tournaments_list_kb(rows),
    )
    await call.answer()


@router.callback_query(F.data.startswith("trnadm:open:"))
async def admin_tournament_open(call: CallbackQuery, state: FSMContext):
    """Карточка турнира: параметры + актуальный список поданных составов."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    await state.clear()  # выходим из «пересоздания» при возврате к карточке
    tid = int(call.data.rsplit(":", 1)[-1])
    tournament = get_tournament(tid)
    if not tournament:
        await call.answer("Турнир не найден", show_alert=True)
        return
    teams = get_tournament_teams(tid)
    parts = _split_text(_tournament_admin_card(tournament, teams))
    try:
        await call.message.edit_text(parts[-1] if len(parts) == 1 else parts[0],
                                     parse_mode="HTML",
                                     reply_markup=_tournament_admin_kb(tournament))
    except Exception:
        await call.message.answer(parts[0], parse_mode="HTML",
                                  reply_markup=_tournament_admin_kb(tournament))
    for extra in parts[1:]:
        await call.message.answer(extra, parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("trnadm:limit:"))
async def admin_tournament_limit_ask(call: CallbackQuery, state: FSMContext):
    """Изменение лимита команд у активного турнира."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    tournament = get_tournament(tid)
    if not tournament:
        await call.answer("Турнир не найден", show_alert=True)
        return
    teams = get_tournament_teams(tid)
    unit = _limit_unit(tournament["t_type"])
    await state.set_state(TournamentAdminStates.waiting_new_limit)
    await state.update_data(edit_tournament_id=tid)
    await call.message.edit_text(
        f"✏️ <b>Лимит турнира #{tid}</b>\n\n"
        f"Сейчас: <b>{_limit_text(tournament['team_limit'], tournament['t_type'])}</b>, "
        f"уже подано: <b>{len(teams)}</b>.\n\n"
        f"Отправь новое число <b>{unit}</b> (от {max(2, len(teams))} до 128) "
        f"или нажми «♾ Снять лимит».\nДля отмены отправь /cancel.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="♾ Снять лимит", callback_data=f"trnadm:nolimit:{tid}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"trnadm:open:{tid}")],
        ]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("trnadm:nolimit:"))
async def admin_tournament_no_limit(call: CallbackQuery, state: FSMContext, bot: Bot):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    set_tournament_limit(tid, None)
    await state.clear()
    await _refresh_tournament_posts(bot, tid)
    tournament = get_tournament(tid)
    await call.message.edit_text(
        _tournament_admin_card(tournament, get_tournament_teams(tid)),
        parse_mode="HTML", reply_markup=_tournament_admin_kb(tournament),
    )
    await call.answer("Лимит снят")


@router.message(TournamentAdminStates.waiting_new_limit, F.text.startswith("/")
                | F.text.in_(TOURNAMENT_GUARD_TEXTS))
async def admin_tournament_limit_guard(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Изменение лимита отменено.", reply_markup=admin_scrims_keyboard())


@router.message(TournamentAdminStates.waiting_new_limit)
async def admin_tournament_limit_save(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    tid = int(data.get("edit_tournament_id") or 0)
    tournament = get_tournament(tid)
    if not tournament:
        await state.clear()
        await message.answer("❌ Турнир не найден.", reply_markup=admin_scrims_keyboard())
        return
    teams = get_tournament_teams(tid)
    unit = _limit_unit(tournament["t_type"])
    text = (message.text or "").strip()
    if not text.isdigit() or not (2 <= int(text) <= 128):
        await message.answer(f"❌ Нужно целое число {unit} от 2 до 128. "
                             f"Попробуй ещё раз или отправь /cancel.")
        return
    new_limit = int(text)
    if new_limit < len(teams):
        await message.answer(
            f"❌ Лимит не может быть меньше числа уже поданных заявок ({len(teams)}). "
            f"Введи число от {len(teams)} или отправь /cancel."
        )
        return
    set_tournament_limit(tid, new_limit)
    await state.clear()
    await _refresh_tournament_posts(bot, tid)
    tournament = get_tournament(tid)
    await message.answer(
        f"✅ Лимит турнира #{tid} изменён на <b>{new_limit} {unit}</b>.",
        parse_mode="HTML", reply_markup=admin_scrims_keyboard(),
    )
    await message.answer(
        _tournament_admin_card(tournament, get_tournament_teams(tid)),
        parse_mode="HTML", reply_markup=_tournament_admin_kb(tournament),
    )
    await log_admin_action(
        bot=bot, admin_id=message.from_user.id,
        admin_name=message.from_user.username or message.from_user.first_name or "admin",
        action_text=f"✏️ Изменил лимит турнира #{tid} на {new_limit} {unit}.",
    )


@router.callback_query(F.data.startswith("trnadm:start:"), ~F.data.startswith("trnadm:start_yes:"))
async def admin_tournament_start_ask(call: CallbackQuery):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"✅ Да, запустить #{tid}", callback_data=f"trnadm:start_yes:{tid}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"trnadm:open:{tid}"),
    ]]))
    await call.answer("Подтвердите запуск")


@router.callback_query(F.data.startswith("trnadm:start_yes:"))
async def admin_tournament_start_yes(call: CallbackQuery, bot: Bot):
    """Ручной досрочный старт турнира администратором."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    await call.answer("Запускаю турнир...")
    result = await start_tournament(bot, tid, manual_by=call.from_user.id)
    if result is None:
        await call.message.edit_text(f"⚠️ Турнир #{tid} уже запущен или не найден.", reply_markup=None)
        return
    if result.get("cancelled"):
        await call.message.edit_text(
            f"🚫 Турнир #{tid} отменён: участников недостаточно ({result['teams']}).",
            reply_markup=None,
        )
        return
    await call.message.edit_text(
        f"🔥 <b>Турнир #{tid} запущен!</b>\n\n"
        f"Участников: <b>{result['teams']}</b>, автопроходов: <b>{result.get('byes', 0)}</b>.\n"
        f"Уведомлений лидерам отправлено: <b>{result['notified']}</b>.\n"
        f"Финальная сетка и расписание пар опубликованы в топиках.",
        parse_mode="HTML", reply_markup=None,
    )
    await log_admin_action(
        bot=bot, admin_id=call.from_user.id,
        admin_name=call.from_user.username or call.from_user.first_name or "admin",
        action_text=f"▶️ Досрочно запустил турнир #{tid} ({result['teams']} участников).",
    )


@router.callback_query(F.data.startswith("trnadm:cancel:"))
async def admin_tournament_cancel(call: CallbackQuery, bot: Bot):
    """Отмена турнира администратором с уведомлением участников."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    if not set_tournament_status(tid, "cancelled"):
        await call.answer("Турнир уже закрыт", show_alert=True)
        return
    teams = get_tournament_teams(tid)
    for t in teams:
        try:
            await bot.send_message(int(t["leader_id"]),
                                   f"🚫 Турнир #{tid} отменён администрацией.")
        except Exception:
            pass
    for post in get_tournament_posts(tid):
        try:
            await bot.edit_message_reply_markup(chat_id=post["chat_id"],
                                                message_id=post["message_id"], reply_markup=None)
        except Exception:
            pass
    await call.message.edit_text(f"🚫 Турнир #{tid} отменён. Уведомлено команд: {len(teams)}.",
                                 reply_markup=None)
    await call.answer("Турнир отменён")
    await log_admin_action(
        bot=bot, admin_id=call.from_user.id,
        admin_name=call.from_user.username or call.from_user.first_name or "admin",
        action_text=f"🚫 Отменил турнир #{tid}.",
    )


@router.callback_query(F.data.startswith("trnadm:del:"))
async def admin_tournament_delete_ask(call: CallbackQuery):
    """Шаг 1: честное предупреждение перед необратимым удалением."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    pack = _collect_tournament_for_delete(tid)
    if not pack["tour"]:
        await call.answer("Турнир уже удалён", show_alert=True)
        return
    t = pack["tour"]
    await call.message.edit_text(
        f"🗑 <b>Удалить турнир #{tid}?</b>\n\n"
        f"Тип: {TOURNAMENT_TYPES.get(t['t_type'], t['t_type'])} · "
        f"статус: {TOURNAMENT_STATUS_LABEL.get(t['status'], t['status'])}\n"
        f"Зарегистрировано команд: <b>{pack['teams_cnt']}</b>, постов в топиках: <b>{len(pack['posts'])}</b>.\n\n"
        f"⚠️ Действие <b>необратимо</b>: будут удалены запись турнира, все заявки, "
        f"матчи, результаты, сетка. Посты-анонсы в топиках бот попробует удалить. "
        f"Лидерам уйдёт уведомление.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"✅ Да, удалить #{tid} полностью",
                                 callback_data=f"trnadm:del_yes:{tid}"),
            InlineKeyboardButton(text="◀️ Назад к турниру", callback_data=f"trnadm:open:{tid}"),
        ]]),
    )
    await call.answer("Подтвердите удаление")


@router.callback_query(F.data.startswith("trnadm:del_yes:"))
async def admin_tournament_delete_yes(call: CallbackQuery, bot: Bot):
    """Шаг 2: выполняем удаление — посты в топиках, уведомления лидерам, БД, лог."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    pack = _collect_tournament_for_delete(tid)
    if not pack["tour"]:
        await call.answer("Турнир уже удалён", show_alert=True)
        await call.message.edit_text("🗑 Турнир уже удалён.", reply_markup=None)
        return

    posts_deleted = 0
    for post in pack["posts"]:
        try:
            await bot.delete_message(chat_id=post["chat_id"], message_id=post["message_id"])
            posts_deleted += 1
        except Exception as e:
            logger.debug(f"Пост турнира {tid} не удалён из чата: {e}")

    for leader_id in pack["leaders"]:
        try:
            await bot.send_message(
                leader_id,
                f"🗑 Турнир #{tid} удалён администрацией. "
                f"Ваши заявки/матчи в нём аннулированы.",
            )
        except Exception:
            pass
        await asyncio.sleep(0.05)

    teams_cnt = pack["teams_cnt"]
    _drop_tournament_rows(tid)

    await call.message.edit_text(
        f"🗑 Турнир #{tid} удалён полностью.\n"
        f"Удалено постов в топиках: <b>{posts_deleted}</b>, уведомлено лидеров: "
        f"<b>{len(pack['leaders'])}</b>, команд было: <b>{teams_cnt}</b>.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀️ К списку турниров", callback_data="trnadm:list"),
        ]]),
    )
    await call.answer("Удалено")
    await log_admin_action(
        bot=bot, admin_id=call.from_user.id,
        admin_name=call.from_user.username or call.from_user.first_name or "admin",
        action_text=(f"🗑 Удалил турнир #{tid} полностью: команд {teams_cnt}, "
                     f"постов снято {posts_deleted}."),
    )


@router.callback_query(F.data.startswith("trnadm:repost:"))
async def admin_tournament_repost(call: CallbackQuery, bot: Bot):
    """Обновить уже существующую рассылку-пост турнира в топиках.

    На регистрации: перечитывает анонс и дорисовывает кнопку «✅ Зарегистрироваться»
    (лекарство от «в новостях нет кнопки вступления» на старых постах).
    На идущих турнирах: обновляет текущую сетку в тех же постах.
    """
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    tournament = get_tournament(tid)
    if not tournament:
        await call.answer("Турнир не найден", show_alert=True)
        return
    if not get_tournament_posts(tid):
        await call.answer("У турнира нет постов в топиках — анонс не публиковался",
                          show_alert=True)
        return
    if tournament["status"] == "registration":
        await _refresh_tournament_posts(bot, tid)
        await call.answer("Анонс обновлён, кнопка регистрации на месте")
    else:
        await _repost_bracket(bot, tid)
        await call.answer("Сетка в топиках обновлена")


@router.callback_query(F.data.startswith("trnadm:res:"))
async def admin_tournament_results_list(call: CallbackQuery):
    """Админ выбирает матч для ручной записи победителя."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    matches = _pending_full_matches(tid)
    builder = InlineKeyboardBuilder()
    for m in matches[:20]:
        stage = "🥉 3-е место" if m["bracket"] == "third" else f"круг {m['round_no']}"
        builder.button(
            text=f"{stage}: {_team_name(m['team_a_id'])} 🆚 {_team_name(m['team_b_id'])}",
            callback_data=f"trnadm:mview:{m['id']}",
        )
    builder.button(text="◀️ Назад к турниру", callback_data=f"trnadm:open:{tid}")
    builder.adjust(1)
    if not matches:
        await call.answer("📭 Нет матчей, ожидающих исхода", show_alert=True)
        return
    await call.message.edit_text(
        "⚖️ <b>Записать итог матча</b>\n\n"
        "Выбери матч — затем отметь победителя. Сетка обновится, а как только у пары "
        "следующего круга определятся обе команды, капитаны автоматически получат уведомление.",
        parse_mode="HTML", reply_markup=builder.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("trnadm:mview:"))
async def admin_match_view(call: CallbackQuery):
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    mid = int(call.data.rsplit(":", 1)[-1])
    m = _m_get(mid)
    if not m or m["status"] != "pending" or not m["team_a_id"] or not m["team_b_id"]:
        await call.answer("Матч уже закрыт или не готов", show_alert=True)
        return
    name_a, name_b = _team_name(m["team_a_id"]), _team_name(m["team_b_id"])
    stage = "🥉 матч за 3-е место" if m["bracket"] == "third" else f"круг {m['round_no']}"
    await call.message.edit_text(
        f"⚖️ <b>Турнир #{m['tournament_id']} · {stage}</b>\n\n"
        f"Матч: <b>{hd.quote(name_a)}</b> 🆚 <b>{hd.quote(name_b)}</b>\n\n"
        f"Кто победил?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🏆 Победил {name_a[:20]}",
                                  callback_data=f"trnadm:mset:{mid}:a")],
            [InlineKeyboardButton(text=f"🏆 Победил {name_b[:20]}",
                                  callback_data=f"trnadm:mset:{mid}:b")],
            [InlineKeyboardButton(text="◀️ Назад к матчам",
                                  callback_data=f"trnadm:res:{m['tournament_id']}")],
        ]))
    await call.answer()


@router.callback_query(F.data.startswith("trnadm:mset:"))
async def admin_match_set_winner(call: CallbackQuery, bot: Bot):
    """Администратор вручную фиксирует победителя матча."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    _, _, mid, side = call.data.split(":")
    m = _m_get(int(mid))
    if not m or m["status"] != "pending" or not m["team_a_id"] or not m["team_b_id"]:
        await call.answer("Матч уже закрыт", show_alert=True)
        return
    winner_id = int(m["team_a_id"]) if side == "a" else int(m["team_b_id"])
    loser_id = int(m["team_b_id"]) if side == "a" else int(m["team_a_id"])
    tid = int(m["tournament_id"])
    admin_name = call.from_user.username or call.from_user.first_name or "admin"
    await _apply_confirmed_result(bot, tid, m, winner_id, loser_id, "",
                                  admin_name, admin_id=call.from_user.id)
    tournament = get_tournament(tid)
    status_txt = TOURNAMENT_STATUS_LABEL.get(tournament["status"], tournament["status"])
    await call.message.edit_text(
        f"✅ Победитель записан: <b>{hd.quote(_team_name(winner_id))}</b>.\n"
        f"Сетка обновлена, команды получили уведомления. Статус турнира: {status_txt}.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚖️ Записать ещё итог", callback_data=f"trnadm:res:{tid}")],
            [InlineKeyboardButton(text="◀️ К карточке турнира", callback_data=f"trnadm:open:{tid}")],
        ]))
    await call.answer("Итог записан")


# ─── Ручная запись участников, боты и пересоздание турнира ────────────────────

@router.callback_query(F.data.startswith("trnadm:addlist:"))
async def admin_tournament_add_list(call: CallbackQuery):
    """Список составов/игроков, которых модератор может записать вручную."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    _, _, tid_s, page_s = call.data.split(":")
    tid = int(tid_s)
    tournament = get_tournament(tid)
    if not tournament:
        await call.answer("Турнир не найден", show_alert=True)
        return
    if tournament["status"] != "registration":
        await call.answer("Регистрация уже закрыта", show_alert=True)
        return
    if _is_solo_type(tournament["t_type"]):
        await call.answer("В 1V1 и ShowDown участники регистрируются сами", show_alert=True)
        return
    markup, total, page = _add_list_kb(tid, tournament, int(page_s))
    if total == 0:
        await call.answer("Все доступные составы уже добавлены", show_alert=True)
        return
    what = "игрока" if _is_solo_type(tournament["t_type"]) else "состав"
    await call.message.edit_text(
        f"➕ <b>Добавить {what} на турнир #{tid}</b>\n\n"
        f"Доступно: <b>{total}</b> (стр. {page + 1}).\n"
        f"Нажми на {what} — он сразу попадёт в список участников, "
        f"без уведомлений в ЛС.",
        parse_mode="HTML", reply_markup=markup,
    )
    await call.answer()


@router.callback_query(F.data.startswith("trnadm:addpick:"))
async def admin_tournament_add_pick(call: CallbackQuery, bot: Bot):
    """Модератор выбирает состав из списка — записываем на турнир."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    _, _, tid_s, lid_s = call.data.split(":")
    tid, lid = int(tid_s), int(lid_s)
    tournament = get_tournament(tid)
    if not tournament or tournament["status"] != "registration":
        await call.answer("Регистрация уже закрыта", show_alert=True)
        return
    if _is_solo_type(tournament["t_type"]):
        await call.answer("В 1V1 и ShowDown участники регистрируются сами", show_alert=True)
        return
    added, msg = await _add_composition(tid, tournament, lid)
    if added:
        await _refresh_tournament_posts(bot, tid)
    await call.answer(msg, show_alert=not added)
    markup, total, page = _add_list_kb(tid, tournament, 0)
    if total:
        what = "игрока" if _is_solo_type(tournament["t_type"]) else "состав"
        await call.message.edit_text(
            f"{msg}\n\n➕ <b>Добавить {what} на турнир #{tid}</b>\n\n"
            f"Осталось доступно: <b>{total}</b>. Нажми на следующего {what}.",
            parse_mode="HTML", reply_markup=markup,
        )
    else:
        teams = get_tournament_teams(tid)
        await call.message.edit_text(
            f"{msg}\n\n" + _tournament_admin_card(tournament, teams),
            parse_mode="HTML", reply_markup=_tournament_admin_kb(tournament),
        )


@router.callback_query(F.data.startswith("trnadm:addbot:"))
async def admin_tournament_add_bot(call: CallbackQuery, bot: Bot):
    """Добавляет одного бота в тестовый турнир."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    tournament = get_tournament(tid)
    if not tournament:
        await call.answer("Турнир не найден", show_alert=True)
        return
    if not _is_test(tournament):
        await call.answer("Боты доступны только в тестовых турнирах", show_alert=True)
        return
    if tournament["status"] != "registration":
        await call.answer("Регистрация уже закрыта", show_alert=True)
        return
    added = _add_bots(tid, tournament, 1)
    if not added:
        await call.answer("Свободных мест нет — подними лимит", show_alert=True)
        return
    await _refresh_tournament_posts(bot, tid)
    teams = get_tournament_teams(tid)
    await call.message.edit_text(
        _tournament_admin_card(tournament, teams),
        parse_mode="HTML", reply_markup=_tournament_admin_kb(tournament))
    await call.answer(f"🤖 Добавлен бот (всего участников: {len(teams)})")


@router.callback_query(F.data.startswith("trnadm:fillbots:"))
async def admin_tournament_fill_bots(call: CallbackQuery, bot: Bot):
    """Заполняет тестовый турнир ботами до лимита."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    tournament = get_tournament(tid)
    if not tournament:
        await call.answer("Турнир не найден", show_alert=True)
        return
    if not _is_test(tournament):
        await call.answer("Боты доступны только в тестовых турнирах", show_alert=True)
        return
    if tournament["status"] != "registration":
        await call.answer("Регистрация уже закрыта", show_alert=True)
        return
    if tournament["team_limit"] is None:
        await call.answer("Сначала задай лимит турнира", show_alert=True)
        return
    added = _add_bots(tid, tournament, 32)
    if not added:
        await call.answer("Свободных мест нет", show_alert=True)
        return
    await _refresh_tournament_posts(bot, tid)
    teams = get_tournament_teams(tid)
    await call.message.edit_text(
        _tournament_admin_card(tournament, teams),
        parse_mode="HTML", reply_markup=_tournament_admin_kb(tournament))
    await call.answer(f"🤖 Добавлено ботов: {added} (всего участников: {len(teams)})")


@router.callback_query(F.data.startswith("trnadm:recreate:"))
async def admin_tournament_recreate_ask(call: CallbackQuery, state: FSMContext):
    """Пересоздание закрытого турнира с переносом всех участников."""
    if not await _cb_is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав", show_alert=True)
        return
    tid = int(call.data.rsplit(":", 1)[-1])
    tournament = get_tournament(tid)
    if not tournament:
        await call.answer("Турнир не найден", show_alert=True)
        return
    if tournament["status"] not in ("cancelled", "finished"):
        await call.answer("Пересоздать можно только завершённый или отменённый турнир",
                          show_alert=True)
        return
    teams = get_tournament_teams(tid)
    await state.set_state(TournamentAdminStates.waiting_recreate_datetime)
    await state.update_data(recreate_from=tid)
    await call.message.edit_text(
        f"🔄 <b>Пересоздание турнира #{tid}</b>\n\n"
        f"Тип: <b>{TOURNAMENT_TYPES.get(tournament['t_type'], tournament['t_type'])}</b>, "
        f"зарегистрировано: <b>{len(teams)}</b>.\n"
        f"Все участники перенесутся автоматически — в ЛС никому писать не будем.\n\n"
        "🗓 Напиши новую дату и время начала, например: <code>25.12 19:00</code>.\n"
        "Для отмены отправь /cancel.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀️ К турниру", callback_data=f"trnadm:open:{tid}"),
        ]]),
    )
    await call.answer()


@router.message(TournamentAdminStates.waiting_recreate_datetime,
                F.text.startswith("/") | F.text.in_(TOURNAMENT_GUARD_TEXTS))
async def admin_tournament_recreate_guard(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Пересоздание турнира отменено.", reply_markup=admin_scrims_keyboard())


@router.message(TournamentAdminStates.waiting_recreate_datetime)
async def admin_tournament_recreate_save(message: Message, state: FSMContext, bot: Bot):
    """Создаёт новый турнир с теми же настройками и участниками (без ЛС)."""
    data = await state.get_data()
    old_id = int(data.get("recreate_from") or 0)
    old = get_tournament(old_id)
    if not old:
        await state.clear()
        await message.answer("❌ Исходный турнир не найден.", reply_markup=admin_scrims_keyboard())
        return
    scheduled = _normalise_scheduled(message.text or "")
    if not scheduled:
        await message.answer(
            "❌ Неверный формат даты. Введи так: <code>25.12 19:00</code> "
            "или <code>25.12.2026 19:00</code>.\nДля отмены отправь /cancel.",
            parse_mode="HTML")
        return
    new_id = create_tournament_row(
        old["t_type"], old["t_format"], old["team_limit"], scheduled,
        old["created_by"], is_test=int(old["is_test"] or 0))
    copied = 0
    for t in get_tournament_teams(old_id):
        if add_tournament_team(new_id, t["league_id"], int(t["leader_id"]),
                               t["name"], t["tag"]):
            copied += 1
    await state.clear()
    ok, failed = await _publish_tournament_posts(bot, new_id)
    await message.answer(
        f"✅ <b>Турнир #{new_id} пересоздан</b>\n\n"
        f"Перенесено участников: <b>{copied}</b>.\n"
        f"Анонс опубликован в топиках: <b>{len(ok)}</b>. "
        f"В ЛС никому не писали — регистрация уже перенесена.",
        parse_mode="HTML", reply_markup=admin_scrims_keyboard())
    await log_admin_action(
        bot=bot, admin_id=message.from_user.id,
        admin_name=message.from_user.username or message.from_user.first_name or "admin",
        action_text=f"🔄 Пересоздал турнир #{old_id} как #{new_id}, перенёс участников: {copied}.",
    )
