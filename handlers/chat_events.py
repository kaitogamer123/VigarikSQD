"""
Обработчик событий в клановых чатах (вход, выход, сбор участников).
Полная синхронизация состава (Игра + Telegram).

Старые участники (old_memb):
  • вышел из чата и НЕ был зарегистрирован  -> запись удаляется из members;
  • вышел из чата и БЫЛ зарегистрирован     -> запись переносится в old_memb.db
    и пропадает из members (а значит из «Редактировать список клана»);
  • раз в сутки бот сверяет members с реальными чатами сети и чистит тех,
    кто вышел незаметно (пропущенное событие, бот был выключен и т.п.);
  • вернулся в чат / написал /start  -> если есть в old_memb, профиль
    восстанавливается и в ЛС приходит «Давно не виделись...» с кнопками Да/Нет.
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta

import aiosqlite
from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)
from aiogram.utils.markdown import html_decoration as hd

from config import CLAN_CHATS, CLAN_DISPLAY, CLAN_TAGS
from database import (
    DB_PATH,
    add_push_pending,
    get_all_members,
    get_member,
    remove_member,
    upsert_member,
)
from utils.roster_sync import sync_roster_msg
from utils.permissions import get_admin_rights_from_file
from league.league_db import cancel_pending_departure, get_user_league, handle_clan_departure
from handlers.clan_conflicts import send_clan_conflict_prompt
from services.api_service import get_player_profile

logger = logging.getLogger(__name__)
router = Router()

# ─── old_memb: настройки ────────────────────────────────────────────────────
OLD_MEMB_DB = "old_memb.db"  # в гит не входит (*.db в .gitignore)
OWNER_IDS = {7899153362, 5281584435}
PROTECTED_ROLES = {"president", "grand_vice", "grand_vice_president", "vice", "vice_president"}
CLEANUP_EVERY = timedelta(hours=23)
MAX_DEPARTED_SHARE = 0.4  # если «ушедших» больше 40% — считаем, что сбой, и ничего не трогаем
MAX_UNKNOWN_SHARE = 0.3  # если Telegram не смог ответить по >30% — тоже отмена

_old_db_ready = False
_dm_failed_at: dict = {}  # user_id -> time.time(): не долбим ЛС тем, кто не открыл бота
_bg_tasks: set = set()


# ═══════════════════════════════════════════════════════════════════════════
#   old_memb.db — хранилище (идемпотентные миграции)
# ═══════════════════════════════════════════════════════════════════════════

async def _connect_old() -> aiosqlite.Connection:
    global _old_db_ready
    db = await aiosqlite.connect(OLD_MEMB_DB, timeout=20.0)
    db.row_factory = aiosqlite.Row
    if not _old_db_ready:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS old_members (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                game_nick TEXT,
                player_tag TEXT,
                trophies INTEGER DEFAULT 0,
                clan TEXT,
                role TEXT,
                left_at TEXT DEFAULT (datetime('now')),
                left_reason TEXT
            )
            """
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS old_memb_meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        async with db.execute("PRAGMA table_info(old_members)") as cur:
            cols = {r["name"] for r in await cur.fetchall()}
        for col, ddl in (
            ("restored_clan", "ALTER TABLE old_members ADD COLUMN restored_clan TEXT"),
            ("pending_confirm", "ALTER TABLE old_members ADD COLUMN pending_confirm INTEGER DEFAULT 0"),
        ):
            if col not in cols:
                await db.execute(ddl)
        await db.commit()
        _old_db_ready = True
    return db


async def get_old_member(user_id: int) -> dict | None:
    db = await _connect_old()
    try:
        async with db.execute("SELECT * FROM old_members WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
    finally:
        await db.close()


async def archive_old_member(member: dict, reason: str) -> None:
    db = await _connect_old()
    try:
        await db.execute(
            """
            INSERT OR REPLACE INTO old_members
                (user_id, username, first_name, last_name, game_nick, player_tag,
                 trophies, clan, role, left_at, left_reason, restored_clan, pending_confirm)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, NULL, 0)
            """,
            (
                member.get("user_id"), member.get("username"), member.get("first_name"),
                member.get("last_name"), member.get("game_nick"), member.get("player_tag"),
                member.get("trophies") or 0, member.get("clan"), member.get("role"), reason,
            ),
        )
        await db.commit()
    finally:
        await db.close()


async def delete_old_member(user_id: int) -> None:
    db = await _connect_old()
    try:
        await db.execute("DELETE FROM old_members WHERE user_id = ?", (user_id,))
        await db.commit()
    finally:
        await db.close()


async def _mark_old_pending(user_id: int, clan: str) -> None:
    db = await _connect_old()
    try:
        await db.execute(
            "UPDATE old_members SET pending_confirm = 1, restored_clan = ? WHERE user_id = ?",
            (clan, user_id),
        )
        await db.commit()
    finally:
        await db.close()


async def _meta_get(key: str) -> str | None:
    db = await _connect_old()
    try:
        async with db.execute("SELECT value FROM old_memb_meta WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            return row["value"] if row else None
    finally:
        await db.close()


async def _meta_set(key: str, value: str) -> None:
    db = await _connect_old()
    try:
        await db.execute("INSERT OR REPLACE INTO old_memb_meta (key, value) VALUES (?, ?)", (key, value))
        await db.commit()
    finally:
        await db.close()


# ═══════════════════════════════════════════════════════════════════════════
#   Архивация / удаление
# ═══════════════════════════════════════════════════════════════════════════

def _is_registered(member: dict) -> bool:
    """Зарегистрирован = есть игровой тег (и ник или флаг registered)."""
    if not member or not member.get("player_tag"):
        return False
    return member.get("registered") == 1 or bool(member.get("game_nick"))


async def drop_departed_member(member: dict, reason: str) -> str:
    """Незарегистрированного удаляет, зарегистрированного переносит в old_memb.

    Возвращает 'archived' или 'deleted'. Сначала пишем архив, потом удаляем —
    при сбое ничего не теряется.
    """
    user_id = int(member["user_id"])
    if _is_registered(member):
        await archive_old_member(member, reason)
        await remove_member(user_id)
        return "archived"
    await remove_member(user_id)
    return "deleted"


def _is_protected(member: dict) -> bool:
    """Владельцев и администрацию ежедневная чистка не трогает."""
    try:
        uid = int(member.get("user_id") or 0)
    except (TypeError, ValueError):
        return True
    if uid <= 0 or uid in OWNER_IDS:
        return True
    try:
        if get_admin_rights_from_file(uid) is not None:
            return True
    except Exception:
        return True
    return str(member.get("role") or "").strip().lower() in PROTECTED_ROLES


# ═══════════════════════════════════════════════════════════════════════════
#   Проверка присутствия в чатах сети
# ═══════════════════════════════════════════════════════════════════════════

async def _in_chat(bot: Bot, chat_id: int, user_id: int):
    """True — в чате, False — нет, None — Telegram не смог ответить."""
    try:
        cm = await bot.get_chat_member(chat_id, user_id)
    except TelegramBadRequest as e:
        text = str(e).lower()
        if "user not found" in text or "participant_id_invalid" in text:
            return False
        return None
    except Exception:
        return None
    status = getattr(cm, "status", "")
    if status in ("creator", "administrator", "member"):
        return True
    if status == "restricted":
        return bool(getattr(cm, "is_member", True))
    return False  # left / kicked


async def _presence_in_network(bot: Bot, user_id: int, home_clan: str = None, exclude_chat_id: int = None):
    """True — состоит хотя бы в одном чате сети, False — ни в одном, None — не уверены."""
    chats = []
    if home_clan and home_clan in CLAN_CHATS:
        chats.append(CLAN_CHATS[home_clan]["chat_id"])
    for data in CLAN_CHATS.values():
        if data["chat_id"] not in chats:
            chats.append(data["chat_id"])
    unknown = False
    for chat_id in chats:
        if exclude_chat_id is not None and chat_id == exclude_chat_id:
            continue
        res = await _in_chat(bot, chat_id, user_id)
        if res is True:
            return True
        if res is None:
            unknown = True
    return None if unknown else False


# ═══════════════════════════════════════════════════════════════════════════
#   Возвращение: восстановление из old_memb + подтверждение «Да/Нет»
# ═══════════════════════════════════════════════════════════════════════════

def _restored_role(role: str) -> str:
    """Админские роли автоматически не возвращаем — только роли ростера."""
    r = str(role or "member").strip().lower()
    return r if r in ("veteran", "helper") else "member"


def _fmt_tag(tag: str) -> str:
    clean = str(tag or "").strip().upper().replace("#", "")
    return f"#{clean}" if clean else "—"


async def try_restore_old_member(bot: Bot, user: User, clan: str) -> bool:
    """Если игрок есть в old_memb — восстанавливает профиль и просит подтвердить.

    True  — игрок найден в старой базе (дальше обычную регистрацию НЕ запускаем).
    False — в старой базе нет (новый профиль) или не удалось написать в ЛС.
    """
    old = await get_old_member(user.id)
    if not old or not old.get("player_tag"):
        return False

    # Не пытаемся писать в ЛС чаще раза в час, если человек не открывал бота.
    last_fail = _dm_failed_at.get(user.id)
    if last_fail and time.time() - last_fail < 3600:
        return False

    profile = await get_player_profile(old["player_tag"])
    if profile:
        nick = profile.get("name") or old.get("game_nick") or "Игрок"
        trophies = int(profile.get("trophies") or 0)
        note = ""
    else:
        nick = old.get("game_nick") or "Игрок"
        trophies = int(old.get("trophies") or 0)
        note = "\n\n⚠️ Brawl Stars API сейчас недоступен — показан последний известный ник."

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да", callback_data=f"oldm:yes:{user.id}"),
        InlineKeyboardButton(text="❌ Нет", callback_data=f"oldm:no:{user.id}"),
    ]])
    text = (
        "👋 Давно не виделись, подтверди пожалуйста что это твой аккаунт :)\n\n"
        f"🎮 Ник: {hd.bold(hd.quote(str(nick)))}\n"
        f"🏷 Тег: {hd.code(hd.quote(_fmt_tag(old['player_tag'])))}"
        f"{note}"
    )
    try:
        await bot.send_message(user.id, text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        _dm_failed_at[user.id] = time.time()
        logger.info(f"old_memb: не удалось написать в ЛС {user.id}: {e}")
        return False
    _dm_failed_at.pop(user.id, None)

    fields = dict(
        user_id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        game_nick=nick,
        player_tag=str(old["player_tag"]).strip().upper(),
        trophies=trophies,
        clan=clan,
        role=_restored_role(old.get("role")),
        registered=1,
    )
    try:
        await upsert_member(username=user.username, **fields)
    except Exception as e:  # username UNIQUE мог занять чужой устаревший ряд
        logger.warning(f"old_memb: restore {user.id} с username упал ({e}), повтор без username")
        try:
            await upsert_member(username=None, **fields)
        except Exception:
            logger.exception(f"old_memb: не удалось восстановить {user.id}")
            return True  # старый профиль найден, сообщение отправлено — кнопки доведут до конца

    try:
        await _mark_old_pending(user.id, clan)
    except Exception:
        logger.exception("old_memb: не удалось пометить ожидание подтверждения")
    logger.info(f"old_memb: профиль {user.id} восстановлен, ждём подтверждения ({nick} {old['player_tag']})")
    return True


async def _reset_member_to_unregistered(user_id: int) -> None:
    """Откат восстановленного профиля до «вошёл в чат, но не зарегистрирован»."""
    async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
        await db.execute(
            "UPDATE members SET game_nick = NULL, player_tag = NULL, trophies = 0, "
            "registered = 0, role = 'member', updated_at = datetime('now') WHERE user_id = ?",
            (user_id,),
        )
        await db.commit()


@router.callback_query(F.data.startswith("oldm:"))
async def on_old_member_answer(call: CallbackQuery, bot: Bot):
    try:
        _, action, uid_raw = call.data.split(":")
        uid = int(uid_raw)
    except ValueError:
        await call.answer("Некорректная кнопка", show_alert=True)
        return
    if call.from_user.id != uid:
        await call.answer("Эта кнопка не для тебя", show_alert=True)
        return

    old = await get_old_member(uid)
    if not old:
        await call.answer("Уже обработано")
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    member = await get_member(uid)
    clan = (member or {}).get("clan") or old.get("restored_clan") or old.get("clan")

    if action == "yes":
        if not member or not member.get("player_tag"):
            # Восстановление при входе не дошло до базы — досоздаём из архива.
            try:
                await upsert_member(
                    user_id=uid, username=call.from_user.username,
                    first_name=call.from_user.first_name, last_name=call.from_user.last_name,
                    game_nick=old.get("game_nick"), player_tag=old.get("player_tag"),
                    trophies=old.get("trophies") or 0, clan=clan,
                    role=_restored_role(old.get("role")), registered=1,
                )
            except Exception:
                logger.exception(f"old_memb: не удалось досоздать профиль {uid}")
                await call.answer("Не удалось восстановить профиль, напиши /start", show_alert=True)
                return
        await delete_old_member(uid)
        await call.answer("Подтверждено ✅")
        try:
            await call.message.edit_text(
                "✅ Спасибо, аккаунт подтверждён! С возвращением 🎉\nНапиши /start, чтобы открыть меню.",
                reply_markup=None,
            )
        except Exception:
            pass
    elif action == "no":
        await delete_old_member(uid)
        if member:
            await _reset_member_to_unregistered(uid)
            try:
                await add_push_pending(uid)
            except Exception:
                pass
        await call.answer("Старая запись удалена")
        try:
            await call.message.edit_text(
                "🗑 Понял, старая запись удалена.\n\n"
                "Пройди регистрацию заново: нажми /start и введи свой игровой тег Brawl Stars.",
                reply_markup=None,
            )
        except Exception:
            pass
    else:
        await call.answer("Неизвестное действие", show_alert=True)
        return

    if clan:
        try:
            await sync_roster_msg(bot, clan)
        except Exception as e:
            logger.error(f"Roster sync error after old_memb answer: {e}")


# ═══════════════════════════════════════════════════════════════════════════
#   Ежедневная проверка
# ═══════════════════════════════════════════════════════════════════════════

async def run_departed_cleanup(bot: Bot) -> dict:
    """Сверяет members с чатами сети: ушедших удаляет / переносит в old_memb."""
    stats = {"checked": 0, "deleted": 0, "archived": 0, "skipped": 0, "aborted": False}
    members = await get_all_members()

    candidates = [m for m in members if m.get("user_id") and not _is_protected(m)]
    departed, unknown = [], 0
    for m in candidates:
        res = await _presence_in_network(bot, int(m["user_id"]), home_clan=m.get("clan"))
        stats["checked"] += 1
        if res is False:
            departed.append(m)
        elif res is None:
            unknown += 1
        await asyncio.sleep(0.1)

    checked = stats["checked"]
    if checked >= 10 and (
        len(departed) > checked * MAX_DEPARTED_SHARE or unknown > checked * MAX_UNKNOWN_SHARE
    ):
        stats["aborted"] = True
        logger.error(
            f"old_memb cleanup ОТМЕНЁН: проверено {checked}, ушедших {len(departed)}, неизвестно {unknown}"
        )
        try:
            from utils.admin_logger import log_admin_action
            await log_admin_action(
                bot=bot, admin_id=0, admin_name="Система Контроля",
                action_text=(
                    f"⚠️ Ежедневная чистка участников ОТМЕНЕНА как подозрительная: "
                    f"проверено {checked}, «ушедших» {len(departed)}, без ответа Telegram {unknown}. "
                    f"Проверь, что бот админ во всех чатах сети."
                ),
                clan_key="main_admin",
            )
        except Exception:
            pass
        return stats

    touched_clans, archived_names = set(), []
    for m in departed:
        uid = int(m["user_id"])
        try:
            composition = get_user_league(uid)
            if composition:
                # Лидеров ведёт 24-часовое ожидание (composition_departure_monitor) — не трогаем.
                if int(composition["leader_id"]) == uid:
                    stats["skipped"] += 1
                    continue
                result = handle_clan_departure(
                    uid, reason="telegram", old_clan=m.get("clan"), player_tag=m.get("player_tag")
                )
                if result.get("action") == "leadership_pending":
                    stats["skipped"] += 1
                    continue
                if result.get("action") == "member_removed" and result.get("revoked"):
                    try:
                        await bot.send_message(
                            result["leader_id"],
                            f"⚠️ Состав «{result['league_name']}» потерял верификацию: "
                            f"осталось {result['remaining']}/4 участников (нужно минимум 3). "
                            f"Добейте состав и подайте заявку заново.",
                        )
                    except Exception:
                        pass
            outcome = await drop_departed_member(m, reason="daily_check")
            stats["archived" if outcome == "archived" else "deleted"] += 1
            if outcome == "archived":
                archived_names.append(f"{m.get('game_nick') or '?'} ({m.get('player_tag')})")
            if m.get("clan"):
                touched_clans.add(m["clan"])
        except Exception:
            logger.exception(f"old_memb cleanup: ошибка на ID {uid}")
            stats["skipped"] += 1

    for clan in touched_clans:
        try:
            await sync_roster_msg(bot, clan)
        except Exception as e:
            logger.error(f"Roster sync error after cleanup ({clan}): {e}")

    if stats["archived"] or stats["deleted"]:
        lines = [
            "🧹 Ежедневная чистка участников:",
            f"• удалено незарегистрированных: {stats['deleted']}",
            f"• перенесено в old_memb: {stats['archived']}",
        ]
        if archived_names:
            shown = archived_names[:15]
            lines.append("В old_memb: " + ", ".join(hd.quote(n) for n in shown)
                         + (f" и ещё {len(archived_names) - len(shown)}" if len(archived_names) > len(shown) else ""))
        try:
            from utils.admin_logger import log_admin_action
            await log_admin_action(
                bot=bot, admin_id=0, admin_name="Система Контроля",
                action_text="\n".join(lines), clan_key="main_admin",
            )
        except Exception:
            pass
    logger.info(f"old_memb cleanup: {stats}")
    return stats


async def old_members_daily_task(bot: Bot) -> None:
    """Раз в сутки (с учётом перезапусков бота — время хранится в old_memb.db)."""
    await asyncio.sleep(90)
    while True:
        try:
            last_raw = await _meta_get("last_cleanup")
            due = True
            if last_raw:
                try:
                    due = datetime.now() - datetime.fromisoformat(last_raw) >= CLEANUP_EVERY
                except ValueError:
                    due = True
            if due:
                await run_departed_cleanup(bot)
                await _meta_set("last_cleanup", datetime.now().isoformat(timespec="seconds"))
        except Exception:
            logger.exception("old_memb daily task error")
        await asyncio.sleep(3600)


@router.startup()
async def _launch_old_members_task(bot: Bot):
    """Запуск суточной задачи вместе с ботом (main.py менять не нужно)."""
    task = asyncio.create_task(old_members_daily_task(bot))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


# ═══════════════════════════════════════════════════════════════════════════
#   Чаты кланов
# ═══════════════════════════════════════════════════════════════════════════

def detect_clan_by_chat(chat_id: int):
    for clan, data in CLAN_CHATS.items():
        if data["chat_id"] == chat_id:
            return clan
    return None


def build_user_link(user: User) -> str:
    """Генерирует кликабельную ссылку на игрока. Защищено от спецсимволов и смены юзернейма."""
    uid = user.id
    uname = user.username
    raw_name = user.first_name or uname or "Игрок"
    nick = hd.quote(str(raw_name))
    if uid and int(uid) > 0:
        return f'<a href="tg://user?id={uid}">{nick}</a>'
    if uname:
        return f"@{hd.quote(str(uname))}"
    return nick


@router.chat_member()
async def on_chat_member_update(event: ChatMemberUpdated, bot: Bot):
    chat_id = event.chat.id
    clan = detect_clan_by_chat(chat_id)
    if not clan:
        return

    user = event.new_chat_member.user
    user_id = user.id
    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status

    # ─── ВХОД В КЛАН (TELEGRAM) ─────────────────────────────
    if new_status in ("member", "administrator") and old_status in ("left", "kicked", "left_chat_member"):
        # Возвращение в любой чат сети отменяет ожидающую передачу лидерства.
        cancel_pending_departure(user_id)

        member = await get_member(user_id)

        # Вернулся старый участник — ищем в old_memb ДО создания нового профиля.
        if not member or not member.get("player_tag"):
            if await try_restore_old_member(bot, user, clan):
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"👋 С возвращением в {CLAN_DISPLAY.get(clan, clan).upper()}, {build_user_link(user)}!\n"
                             f"Подтверди свой аккаунт в личных сообщениях с ботом.",
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.warning(f"welcome-back msg failed: {e}")
                try:
                    await sync_roster_msg(bot, clan)
                except Exception as e:
                    logger.error(f"Roster sync error on member return: {e}")
                return

        if (
            member
            and member.get("player_tag")
            and member.get("game_nick")
            and member.get("clan")
            and member.get("clan") != clan
        ):
            # Не перезаписываем клан автоматически. Администрация решит, является ли это
            # переводом, твинком или административным доступом.
            await upsert_member(
                user_id=user_id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
            )
            await send_clan_conflict_prompt(bot, user, member, clan)
            try:
                await bot.send_message(
                    user_id,
                    f"ℹ️ Ты вошёл в чат {CLAN_DISPLAY.get(clan, clan)}, но твой профиль "
                    f"уже привязан к {CLAN_DISPLAY.get(member.get('clan'), member.get('clan'))}. "
                    "Администрация получила запрос и выберет нужное действие.",
                )
            except Exception:
                pass
            logger.info(
                f"Создан конфликт кланов для ID {user_id}: {member.get('clan')} -> {clan}"
            )
            return

        if not member:
            await upsert_member(
                user_id=user_id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                clan=clan,
                registered=0,
            )
        else:
            await upsert_member(
                user_id=user_id,
                clan=clan,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
            )

        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"👋 Добро пожаловать в {CLAN_DISPLAY.get(clan, clan).upper()}, {build_user_link(user)}!\n"
                     f"Пройди регистрацию в боте через ЛС, чтобы попасть в автоматический список участников.",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"welcome msg failed: {e}")

        try:
            await bot.send_message(
                chat_id=user_id,
                text=f"👋 Ты вступил в клан {CLAN_DISPLAY.get(clan, clan).upper()}.\n\n"
                     f"Обязательно пройди регистрацию в боте через команду /start, указав свой тег аккаунта Brawl Stars.",
                parse_mode="HTML",
            )
        except Exception:
            pass

        if not member or member.get("registered") != 1:
            await add_push_pending(user_id)

        try:
            await sync_roster_msg(bot, clan)
        except Exception as e:
            logger.error(f"Roster sync error on member join: {e}")

    # ─── ВЫХОД ИЗ КЛАНА (TELEGRAM) ─────────────────────────
    elif new_status in ("left", "kicked"):
        member = await get_member(user_id)

        # Если игровой аккаунт уже переведён в другой клан нашей сети, это не
        # выход из сети. Сохраняем регистрацию и состав до входа в новый чат,
        # где администрация получит четыре варианта решения.
        moved_to_network_clan = None
        if member and member.get("player_tag"):
            try:
                profile = await get_player_profile(member.get("player_tag"))
                current_club = str((profile or {}).get("clan_tag") or "").upper().replace("#", "")
                for key, configured_tag in (CLAN_TAGS or {}).items():
                    if current_club and current_club == str(configured_tag or "").upper().replace("#", ""):
                        if key != clan:
                            moved_to_network_clan = key
                            break
            except Exception as e:
                logger.debug(f"Не удалось проверить перевод ID {user_id} через Brawl Stars: {e}")

        if moved_to_network_clan:
            cancel_pending_departure(user_id)
            logger.info(
                f"ID {user_id} переводится {clan} -> {moved_to_network_clan}; "
                "регистрация и состав сохранены до решения администрации"
            )
            try:
                await sync_roster_msg(bot, clan)
            except Exception:
                pass
            return

        # Выход из клана автоматически исключает игрока из состава. Если это
        # лидер, передача откладывается на 24 часа и подтверждается по Telegram
        # и Brawl Stars. Обычные участники удаляются сразу.
        composition_result = handle_clan_departure(
            user_id,
            reason="telegram",
            old_clan=clan,
            player_tag=member.get("player_tag") if member else None,
        )
        if composition_result.get("action") == "leadership_pending":
            try:
                await bot.send_message(
                    user_id,
                    f"⏳ Ты вышел из чата клана. Лидерство составом "
                    f"{composition_result['league_name']} сохранено за тобой на 24 часа.\n"
                    "Если ты появишься в любом чате или клубе сети, ожидание отменится автоматически.",
                )
            except Exception:
                pass
            logger.info(
                f"Передача лидерства {composition_result['league_name']} отложена до "
                f"{composition_result.get('check_after')}"
            )
        elif (composition_result.get("action") == "member_removed"
              and composition_result.get("revoked")):
            try:
                await bot.send_message(
                    composition_result["leader_id"],
                    f"⚠️ Состав «{composition_result['league_name']}» потерял верификацию: "
                    f"осталось {composition_result['remaining']}/4 участников (нужно минимум 3). "
                    f"Добейте состав и подайте заявку заново.",
                )
            except Exception:
                pass
            logger.info(
                f"Состав {composition_result['league_name']} потерял верификацию "
                f"после выхода ID {user_id}: осталось {composition_result['remaining']}"
            )

        if member:
            # Для лидера во время 24-часового окна сохраняем регистрацию: это
            # позволяет распознать переход в другой клан и показать админское решение.
            if composition_result.get("action") != "leadership_pending":
                # Если человек всё ещё сидит в другом чате сети — прежнее поведение
                # (отвязка от клана). Иначе: незарегистрированного удаляем,
                # зарегистрированного переносим в old_memb.
                still_in_network = await _presence_in_network(
                    bot, user_id, exclude_chat_id=chat_id
                )
                if still_in_network is True:
                    await upsert_member(user_id=user_id, clan=None, registered=0)
                else:
                    try:
                        outcome = await drop_departed_member(member, reason="left_chat")
                        logger.info(f"Выход ID {user_id} из {clan}: {outcome}")
                    except Exception:
                        logger.exception(f"Не удалось архивировать вышедшего ID {user_id}")
                        await upsert_member(user_id=user_id, clan=None, registered=0)

        # Проверяем, есть ли у этого игрока твинки в нашей базе данных
        twinks_in_clan = []
        async with aiosqlite.connect(DB_PATH) as db_conn:
            db_conn.row_factory = aiosqlite.Row
            # Ищем записи с таким же user_id, которые числятся в этом клане
            async with db_conn.execute(
                "SELECT game_nick, player_tag FROM members WHERE user_id = ? AND clan = ?",
                (user_id, clan),
            ) as cursor:
                rows = await cursor.fetchall()
                for r in rows:
                    twinks_in_clan.append(f"• {hd.quote(str(r['game_nick']))} ({r['player_tag']})")

        # Если у ливнувшего человека обнаружены твинки в этом клане — оповещаем админов!
        if twinks_in_clan:
            from utils.admin_logger import log_admin_action
            twinks_str = "\n".join(twinks_in_clan)
            tg_user = f"@{event.from_user.username}" if (event.from_user and event.from_user.username) else f"ID: {user_id}"
            alert_text = (
                f"⚠️ ВНИМАНИЕ РУКОВОДСТВУ! УЧАСТНИК ВЫШЕЛ ИЗ ЧАТА\n\n"
                f"👤 Игрок в TG: {tg_user}\n"
                f"🏰 Клан: {CLAN_DISPLAY.get(clan, clan).upper()}\n\n"
                f"❗ У этого пользователя в данном клане остались привязанные аккаунты/твинки:\n{twinks_str}\n\n"
                f"👉 Пожалуйста, исключите эти аккаунты из клуба внутри игры Brawl Stars!"
            )
            try:
                await log_admin_action(
                    bot=bot,
                    admin_id=user_id,
                    admin_name="Система Контроля",
                    action_text=alert_text,
                    clan_key=clan,
                )
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление о твинках в логи админов: {e}")

        try:
            await sync_roster_msg(bot, clan)
        except Exception as e:
            logger.error(f"Roster sync error on member leave: {e}")


# ─── ПЕРЕХВАТ СООБЩЕНИЙ ДЛЯ СБОРА УЧАСТНИКОВ ───────────────────────────────────
@router.message(F.chat.type.in_({"group", "supergroup"}), ~F.text.startswith("/"))
async def on_group_message_collect_user(message: Message, bot: Bot):
    chat_id = message.chat.id
    clan = detect_clan_by_chat(chat_id)
    if not clan:
        return

    # Защита от служебных сообщений без автора
    if message.from_user is None:
        return

    user_id = message.from_user.id
    if message.from_user.is_bot:
        return

    member = await get_member(user_id)

    # Пропущенное событие входа: человек есть в old_memb — запускаем «Давно не виделись».
    if not member or not member.get("player_tag"):
        try:
            if await try_restore_old_member(bot, message.from_user, clan):
                return
        except Exception:
            logger.exception("old_memb: ошибка восстановления из группового сообщения")

    if member and member.get("registered") == 1:
        return

    if member and member.get("clan") == clan:
        return

    if not member or not member.get("game_nick"):
        await upsert_member(
            user_id=user_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            clan=clan,
            registered=0,
        )
        await add_push_pending(user_id)
        logger.info(f"Игрок {user_id} актуализирован по сообщению в чате {clan} и удержан в списке без ников.")
