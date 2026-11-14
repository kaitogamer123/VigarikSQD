"""
Конфигурация бота ViGarik Squad
"""
import os

# Токены берутся из server_secrets.env (или переменных окружения), а не из git.
from utils.secrets import BRAWL_API_TOKEN, TELEGRAM_TOKEN, get_secret

TOKEN = TELEGRAM_TOKEN

# Скрытые тестовые аккаунты (твинки): полный доступ, но их нет в списках.
# Задаётся в server_secrets.env: HIDDEN_TESTER_IDS=123456789,987654321
HIDDEN_TESTER_IDS = frozenset(
    int(part.strip())
    for part in get_secret("HIDDEN_TESTER_IDS", "").replace(";", ",").replace(" ", ",").split(",")
    if part.strip().isdigit()
)


def is_hidden_tester(user_id) -> bool:
    try:
        return int(user_id) in HIDDEN_TESTER_IDS
    except (TypeError, ValueError):
        return False

# ID закрытого чата администрации для логов (пока оставляем None, бот сам его заполнит)
LOGS_CHAT_ID = -1003900162237

CLAN_TAGS = {
    "squad": "#2UUQ0989V",
    "academy": "#2CL9LRVCL",
    "events": "#2CP8R2Q8U"
}

# ─── Чаты кланов ───────────────────────────────────────────────────────────────
CLAN_CHATS = {
    "academy": {
        "title": "ViGarik Squad Academy",
        "chat_id": -1002237164277,
    },
    "squad": {
        "title": "ViGarik Squad",
        "chat_id": -1002187842577,
    },
    "events": {
        "title": "ViGarik Events",
        "chat_id": -1002451600406,
    },
}

# ─── Топики новостей кланов ────────────────────────────────────────────────────
ADMIN_NEWS_TARGETS = {
    "academy": {
        "title": "ViGarik Squad Academy",
        "chat_id": -1002237164277,
        "thread_id": 33272,
    },
    "squad": {
        "title": "ViGarik Squad",
        "chat_id": -1002187842577,
        "thread_id": 129460,
    },
    "events": {
        "title": "ViGarik Events",
        "chat_id": -1002451600406,
        "thread_id": 5361,
    },
}

# ─── Топики списков участников ────────────────────────────────────────────────
ROSTER_TOPICS = {
    "academy": {
        "chat_id": -1002237164277,
        "thread_id": 44831,   # Топик списка академии
    },
    "squad": {
        "chat_id": -1002187842577,
        "thread_id": 153207,  # Топик списка основного клана
    },
    "events": {
        "chat_id": -1002451600406,
        "thread_id": 5363,    # Топик списка клана Events
    },
}

# ─── Чат администрации ────────────────────────────────────────────────────────
ADMIN_CHAT_ID = -1002225847319  # <-- впиши chat_id чата администрации

# ─── Иерархия ролей ───────────────────────────────────────────────────────────
ROLES = {
    "president":       0,   # Президент          (наивысший приоритет)
    "grand_vice":      1,   # Гранд Вице Президент
    "vice":            2,   # Вице Президент
    "veteran":         3,   # Ветеран
    "helper":          4,   # Помощник
    "member":          5,   # Участник
}

ROLE_LABELS = {
    "president":  "👑 Президент",
    "grand_vice": "🔱 Гранд Вице Президент",
    "vice":       "⚜️ Вице Президент",
    "veteran":    "🎖️ Ветеран",
    "helper":     "🤝 Помощник",
    "member":     "👤 Участник",
}

# ─── Начальные президенты / гранд-вице-президенты ─────────────────────────────
import os

# Путь к текстовому файлу с администрацией
ADMINS_FILE_PATH = "admins.txt"

def load_initial_admins() -> dict:
    """Динамически считывает и парсит список администраторов из файла admins.txt."""
    admins_dict = {}
    if not os.path.exists(ADMINS_FILE_PATH):
        # Если файла нет, создаем пустой
        with open(ADMINS_FILE_PATH, "w", encoding="utf-8") as f:
            pass
        return admins_dict

    with open(ADMINS_FILE_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                parts = line.split(":")
                if len(parts) == 4:
                    uid = int(parts[0].strip())
                    uname = parts[1].strip()
                    role = parts[2].strip()
                    clan = parts[3].strip()
                    # Если клан указан как None, записываем его как None тип
                    clan_val = None if clan.lower() == "none" else clan

                    admins_dict[uid] = {
                        "username": uname,
                        "role": role,
                        "clan": clan_val
                    }
            except Exception:
                continue
    return admins_dict

PUSH_CHANGE_DEADLINE_DAYS = 2  # Срок изменения цели в днях

# Инициализируем словарь динамически
INITIAL_ADMINS = load_initial_admins()


def _apply_hidden_admins() -> None:
    """Скрытые тестеры получают права президента, не попадая в admins.txt."""
    for uid in HIDDEN_TESTER_IDS:
        INITIAL_ADMINS.setdefault(int(uid), {"role": "president", "clan": "squad"})


_apply_hidden_admins()

# Минимальная роль для редактирования списков
EDIT_LIST_MIN_ROLE = "vice"

# Минимальная роль для назначения модерации
APPOINT_ADMIN_MIN_ROLE = "president"

# Deadline смены решения о пуше (дней)
PUSH_CHANGE_DEADLINE_DAYS = 2

# Названия кланов (для отображения)
CLAN_DISPLAY = {
    "academy": "ViGarik Academy",
    "squad":   "ViGarik Squad",
    "events":  "ViGarik Events",
}

# Emoji-шапки списков
CLAN_HEADER_EMOJI = {
    "academy": "🎓",
    "squad":   "👑",
    "events":  "🎉",
}


# ─── Поиск команды (LFT) ─────────────────────────────────────────────────────
LFT_TTL_DAYS = 7
LFT_PAGE_SIZE = 10
LFT_TOGGLE_COOLDOWN_SEC = 60
LFT_COMMENT_MAX_LEN = 200


def reload_config():
    """
    Принудительно перезагружает текущий модуль конфигурации в памяти
    и обновляет списки администраторов без перезапуска процесса бота.
    """
    import sys
    import importlib

    # Находим текущий модуль в кэше Python
    current_module = sys.modules[__name__]

    # Перезагружаем сам файл config.py в памяти сервера
    importlib.reload(current_module)

    # Принудительно заново считываем файл admins.txt
    global INITIAL_ADMINS
    INITIAL_ADMINS = load_initial_admins()
    _apply_hidden_admins()

    print("🔄 Конфигурация config.py и список admins.txt успешно перезагружены в памяти!")
