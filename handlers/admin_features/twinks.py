"""Добавление твинка через админ-меню без дублирования Telegram ID в members."""
import logging

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup
from aiogram.utils.markdown import html_decoration as hd

from config import CLAN_DISPLAY, CLAN_TAGS
from database import get_all_members, get_member
from services.api_service import get_player_profile
from utils.clan_account_service import add_twink, get_all_twinks
from utils.keyboards import admin_members_keyboard
from utils.permissions import can_edit_list, get_admin_rights_from_file
from utils.roster_sync import sync_roster_msg

logger = logging.getLogger(__name__)
router = Router()


class AddTwinkState(StatesGroup):
    owner = State()
    tag = State()


def _back_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="◀️ Назад"), KeyboardButton(text="❌ Отмена")],
        ],
        resize_keyboard=True,
    )


async def _authorized(user_id: int) -> bool:
    member = await get_member(user_id)
    rights = get_admin_rights_from_file(user_id)
    return can_edit_list(member) or can_edit_list(rights)


def _tag(value: str) -> str:
    return str(value or "").strip().upper().replace("#", "")


async def _can_open_members(message: Message) -> bool:
    return await _authorized(message.from_user.id)


@router.message(F.text == "👥 Управление участниками", _can_open_members)
async def open_members_folder(message: Message):
    await message.answer(
        "📂 Управление участниками. Выбери действие:",
        reply_markup=admin_members_keyboard(),
    )


async def _owner_from_input(text: str):
    raw = (text or "").strip()
    if raw.isdigit():
        return await get_member(int(raw))
    username = raw.lstrip("@").casefold()
    if not username:
        return None
    members = await get_all_members()
    return next(
        (member for member in members
         if str(member.get("username") or "").lstrip("@").casefold() == username),
        None,
    )


@router.message(F.text == "➕ Добавить твинк")
async def add_twink_start(message: Message, state: FSMContext):
    if not await _authorized(message.from_user.id):
        await message.answer("⛔ Недостаточно прав для добавления твинка.")
        return
    await state.clear()
    await state.set_state(AddTwinkState.owner)
    await message.answer(
        "➕ Введи Telegram ID или @username владельца основного аккаунта:",
        reply_markup=_back_keyboard(),
    )


@router.message(
    StateFilter(AddTwinkState.owner, AddTwinkState.tag),
    F.text.in_({"◀️ Назад", "❌ Отмена", "🔙 Назад в админку"}),
)
async def add_twink_back(message: Message, state: FSMContext):
    if not await _authorized(message.from_user.id):
        await state.clear()
        await message.answer("⛔ Нет доступа.")
        return

    current = await state.get_state()
    if message.text == "◀️ Назад" and current == AddTwinkState.tag.state:
        await state.set_state(AddTwinkState.owner)
        await message.answer(
            "◀️ Вернулись к выбору владельца. Введи его Telegram ID или @username:",
            reply_markup=_back_keyboard(),
        )
        return

    await state.clear()
    if message.text == "🔙 Назад в админку":
        from utils.keyboards import admin_panel_keyboard
        member = await get_member(message.from_user.id) or {}
        rights = get_admin_rights_from_file(message.from_user.id) or {}
        role = member.get("role") if can_edit_list(member) else rights.get("role", "member")
        await message.answer(
            "◀️ Возврат в админку.",
            reply_markup=admin_panel_keyboard(role, user_id=message.from_user.id),
        )
        return
    await message.answer(
        "❌ Добавление твинка отменено. Возвращаюсь к управлению участниками.",
        reply_markup=admin_members_keyboard(),
    )


@router.message(AddTwinkState.owner)
async def add_twink_owner(message: Message, state: FSMContext):
    if not await _authorized(message.from_user.id):
        await state.clear()
        await message.answer("⛔ Нет доступа.")
        return
    if not message.text:
        await message.answer("Введи Telegram ID или @username текстом.", reply_markup=_back_keyboard())
        return

    owner = await _owner_from_input(message.text)
    if not owner or not owner.get("user_id") or not owner.get("player_tag"):
        await message.answer(
            "❌ Основа с таким ID/username не найдена в базе бота или не имеет игрового тега. "
            "Проверь данные и попробуй снова.",
            reply_markup=_back_keyboard(),
        )
        return

    await state.update_data(owner_id=int(owner["user_id"]))
    await state.set_state(AddTwinkState.tag)
    owner_name = hd.quote(str(owner.get("game_nick") or owner.get("username") or owner["user_id"]))
    await message.answer(
        f"👤 Владелец: {owner_name} (ID: {owner['user_id']}).\n\n"
        "Введи игровой тег твинка Brawl Stars, например #9PJYV82CC:",
        parse_mode="HTML",
        reply_markup=_back_keyboard(),
    )


@router.message(AddTwinkState.tag)
async def add_twink_tag(message: Message, state: FSMContext, bot: Bot):
    if not await _authorized(message.from_user.id):
        await state.clear()
        await message.answer("⛔ Нет доступа.")
        return
    raw_tag = _tag(message.text)
    if not raw_tag or len(raw_tag) < 3 or not raw_tag.isalnum():
        await message.answer("❌ Введи корректный игровой тег, например #9PJYV82CC.")
        return

    data = await state.get_data()
    owner_id = data.get("owner_id")
    owner = await get_member(owner_id) if owner_id else None
    if not owner:
        await state.set_state(AddTwinkState.owner)
        await message.answer("❌ Владелец больше не найден. Выбери ID или @username заново.")
        return

    await message.answer("⏳ Проверяю твинк и его клуб через Brawl Stars API...")
    try:
        profile = await get_player_profile(raw_tag)
    except Exception:
        logger.exception("Ошибка API при проверке твинка %s", raw_tag)
        await message.answer("❌ API временно недоступен. Попробуй ещё раз позже или нажми «Назад».")
        return
    if not profile:
        await message.answer("❌ Игровой профиль не найден. Проверь тег и попробуй ещё раз.")
        return

    game_club = _tag(profile.get("clan_tag"))
    clan = next(
        (key for key, configured in CLAN_TAGS.items() if game_club and _tag(configured) == game_club),
        None,
    )
    if not clan:
        await message.answer(
            "❌ Этот аккаунт пока не состоит в клубе нашей сети. Вступи в клуб и введи тег снова."
        )
        return

    if any(_tag(member.get("player_tag")) == raw_tag for member in await get_all_members()):
        await message.answer("❌ Этот тег уже привязан как основной аккаунт. Введи тег другого твинка.")
        return

    existing = next(
        (twink for twink in await get_all_twinks() if _tag(twink.get("player_tag")) == raw_tag),
        None,
    )
    if existing and int(existing["owner_user_id"]) != owner_id:
        await message.answer("❌ Этот твинк уже привязан к другому владельцу.")
        return

    game_tag = profile.get("tag") or f"#{raw_tag}"
    nick = profile.get("name") or "Игрок"
    trophies = int(profile.get("trophies") or 0)
    try:
        await add_twink(owner_id, game_tag, nick, trophies, clan)
    except ValueError as exc:
        await message.answer(f"❌ {exc} Введи другой тег или нажми «Назад».")
        return
    except Exception:
        logger.exception("Ошибка сохранения твинка %s для ID %s", game_tag, owner_id)
        await message.answer("❌ Не удалось сохранить твинк. Попробуй ещё раз или нажми «Назад».")
        return
    await state.clear()

    roster_ok = False
    try:
        roster_ok = await sync_roster_msg(bot, clan, force=True)
    except Exception:
        logger.exception("Не удалось обновить ростер после добавления твинка %s", game_tag)

    try:
        from utils.admin_logger import log_admin_action
        await log_admin_action(
            bot=bot,
            admin_id=message.from_user.id,
            admin_name=message.from_user.username or message.from_user.first_name or str(message.from_user.id),
            action_text=(f"Добавил твинк {hd.quote(str(nick))} ({hd.quote(str(game_tag))}) "
                         f"для TG ID {owner_id} в {hd.quote(CLAN_DISPLAY.get(clan, clan))}."),
            clan_key=clan,
        )
    except Exception:
        logger.exception("Не удалось записать действие добавления твинка в админ-лог")

    status = "Ростер обновлён." if roster_ok else "Запись сохранена, но ростер пока не обновился."
    await message.answer(
        f"✅ Твинк {hd.quote(str(nick))} (#{hd.quote(raw_tag)}) добавлен "
        f"в {hd.quote(CLAN_DISPLAY.get(clan, clan))}. {status}",
        parse_mode="HTML",
        reply_markup=admin_members_keyboard(),
    )