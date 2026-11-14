"""Регистрация на турнир: соло-игроки (1V1, ShowDown) и команды (2V2, 3V3)."""
import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.markdown import html_decoration as hd
from config import CLAN_CHATS, is_hidden_tester
from database import get_member, get_setting
from league import league_db as db
from utils.chat_check import get_user_clans
from .common import _is_solo_type, _players_per_team
from .tournament_cards import _refresh_tournament_posts
from .tournament_db import add_tournament_team, get_tournament, get_tournament_teams

logger = logging.getLogger(__name__)
router = Router()


async def _in_clan_chat(bot: Bot, user_id: int, member) -> bool:
    """Состоит ли игрок в клановом чате сети.

    Живая проверка через Telegram, а если бот не видит чаты — запасной вариант
    по клану из профиля (он проставляется при регистрации в боте).
    """
    if is_hidden_tester(user_id):
        return True
    try:
        if await get_user_clans(bot, user_id):
            return True
    except Exception as e:
        logger.warning(f"Не удалось проверить чаты кланов для {user_id}: {e}")
    try:
        return str(member.get("clan") or "") in CLAN_CHATS
    except Exception:
        return False


@router.callback_query(F.data.startswith("trn:reg:"))
async def tournament_register(call: CallbackQuery, bot: Bot):
    """Регистрация на турнир.

    1V1 и ShowDown — соло: любой зарегистрированный в боте игрок (для 1V1 ещё и
    участник кланового чата сети). 2V2 / 3V3 — лидер состава регистрирует команду.
    """
    tournament_id = int(call.data.rsplit(":", 1)[-1])
    tournament = get_tournament(tournament_id)
    if not tournament or tournament["status"] != "registration":
        await call.answer("Регистрация на турнир закрыта", show_alert=True)
        return
    teams = get_tournament_teams(tournament_id)
    limit = tournament["team_limit"]
    if limit is not None and len(teams) >= int(limit):
        await call.answer("😔 Все места уже заняты", show_alert=True)
        return
    if any(int(t["leader_id"]) == call.from_user.id for t in teams):
        await call.answer("Ты уже зарегистрирован на этот турнир", show_alert=True)
        return

    if _is_solo_type(tournament["t_type"]):
        member = await get_member(call.from_user.id)
        if not member or int(member.get("registered") or 0) != 1:
            await call.answer("Сначала пройди регистрацию в боте (/start)", show_alert=True)
            return
        if tournament["t_type"] == "1v1" and not await _in_clan_chat(
                bot, call.from_user.id, member):
            await call.answer(
                "1V1 — для игроков наших кланов. Нужно состоять в одном из "
                "клановых чатов сети (Squad / Academy / Events).",
                show_alert=True,
            )
            return
        name = member.get("game_nick") or member.get("username") or f"ID {call.from_user.id}"
        added = add_tournament_team(tournament_id, None, call.from_user.id, name,
                                    member.get("player_tag") or "")
    else:
        composition = db.get_user_league(call.from_user.id)
        if not composition:
            await call.answer(
                "У тебя нет состава. Создай его в разделе «Составы🏆» — "
                "в состав может входить любой участник из трёх кланов.",
                show_alert=True,
            )
            return
        if int(composition["leader_id"]) != call.from_user.id:
            await call.answer("Регистрирует команду только лидер состава", show_alert=True)
            return
        need_players = _players_per_team(tournament["t_type"])
        if need_players > 1 and not is_hidden_tester(call.from_user.id):
            try:
                members_cnt = len(db.get_league_members(int(composition["id"]), occupied_only=True))
            except Exception:
                members_cnt = 0
            if members_cnt < need_players:
                await call.answer(
                    f"Для формата {str(tournament['t_type']).upper()} в составе нужно "
                    f"минимум {need_players} игрока (сейчас {members_cnt}).",
                    show_alert=True,
                )
                return
        if any(t["league_id"] and int(t["league_id"]) == int(composition["id"]) for t in teams):
            await call.answer("Твоя команда уже в списке", show_alert=True)
            return
        added = add_tournament_team(tournament_id, int(composition["id"]), call.from_user.id,
                                    composition["name"], composition["tag"])

    if not added:
        await call.answer("Не удалось зарегистрировать (возможно, уже в списке)", show_alert=True)
        return
    await call.answer("✅ Ты в списке участников!", show_alert=True)
    try:
        await bot.send_message(
            call.from_user.id,
            f"✅ Регистрация на турнир #{tournament_id} подтверждена.\n"
            f"Начало: <b>{hd.quote(str(tournament['starts_at']))}</b>. "
            f"Сетка обновлена в клановых топиках «Турниры».",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await _refresh_tournament_posts(bot, tournament_id)
