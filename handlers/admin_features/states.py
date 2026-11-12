"""FSM-состояния админ-панели и турниров."""
import logging
from aiogram.fsm.state import State, StatesGroup

logger = logging.getLogger(__name__)


class AdminStates(StatesGroup):
    waiting_appoint_user = State()  # Ожидание юзера для роли
    choosing_clan_to_edit = State()  # Выбор клана
    waiting_edit_member_id = State()  # Ввод ID игрока в клане
    waiting_new_nick_for_member = State()  # Ввод нового никнейма
    waiting_announcement_text = State()  # Рассылка новостей
    waiting_proposal_answer = State()  # Ответ на заявку
    waiting_twink_user = State()  # Ожидание ID или тега основы
    waiting_twink_nick = State()  # Ожидание игрового ника твинка


class TournamentStates(StatesGroup):
    """Шаги мастера: тип → формат (Бо3/Бо5) → лимит участников → дата и время."""
    choosing_type = State()
    choosing_format = State()
    choosing_limit = State()
    waiting_limit_number = State()
    waiting_datetime = State()


class TournamentResultStates(StatesGroup):
    waiting_result = State()


class TournamentAdminStates(StatesGroup):
    waiting_new_limit = State()
    waiting_recreate_datetime = State()  # новая дата при пересоздании турнира
