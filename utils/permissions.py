"""
Система прав доступа для ViGarik Squad Bot.
Унифицирована под работу со всеми типами написания ролей.
Интегрирована с динамическим чтением файла admins.txt на лету.
"""

import os
from config import ROLES, EDIT_LIST_MIN_ROLE, APPOINT_ADMIN_MIN_ROLE, ADMINS_FILE_PATH, load_initial_admins

# ─── Служебные функции нормализации ───────────────────────────────────────────

def _normalize_role(role: str) -> str:
    """Приводит любые вариации написания ролей к единому ключу из config.py."""
    if not role:
        return "member"
    role_clean = role.strip().lower()
    if role_clean in ("grand_vice_president", "grand_vice"):
        return "grand_vice"
    if role_clean in ("vice_president", "vice"):
        return "vice"
    return role_clean

def _lvl(role: str) -> int:
    """Возвращает числовой уровень роли. Чем меньше число — тем выше роль."""
    norm_role = _normalize_role(role)
    return ROLES.get(norm_role, 999)

def has_role(member: dict, required_role: str) -> bool:
    """Проверка: уровень роли пользователя выше или равен требуемой роли."""
    if not member:
        return False
    return _lvl(member.get("role")) <= _lvl(required_role)

# ─── Основные проверки прав ───────────────────────────────────────────────────

def is_any_admin(member: dict) -> bool:
    """Любой админ (вся иерархия выше member, кроме обычного участника)."""
    if not member:
        return False
    norm_role = _normalize_role(member.get("role"))
    return norm_role in ROLES and norm_role != "member"

def is_top_admin(member: dict) -> bool:
    """Самые высокие роли (Президент / Гранд Вице)."""
    if not member:
        return False
    norm_role = _normalize_role(member.get("role"))
    return norm_role in ("president", "grand_vice")

def can_edit_list(member: dict) -> bool:
    """Разрешено ли редактирование списков клана."""
    return has_role(member, EDIT_LIST_MIN_ROLE)

def can_appoint_admins(member: dict) -> bool:
    """Разрешено ли назначение новых ролей (модерации)."""
    return has_role(member, APPOINT_ADMIN_MIN_ROLE)

def can_read_proposals(member: dict) -> bool:
    """Доступ к просмотру предложений игроков (Президенты и Гранд-Вице)."""
    if not member:
        return False
    norm_role = _normalize_role(member.get("role"))
    return norm_role in ("president", "grand_vice")

def can_launch_push_goal(member: dict) -> bool:
    """Запуск голосования цели сезона (Строго Президент)."""
    if not member:
        return False
    return _normalize_role(member.get("role")) == "president"

def can_view_push_stats(member: dict) -> bool:
    """Просмотр статистики пуша клана."""
    return has_role(member, "grand_vice")

def can_notify_users(member: dict) -> bool:
    """Массовые оповещения через новости клана."""
    return has_role(member, "grand_vice")

# ─── Дополнительные утилиты ───────────────────────────────────────────────────

def role_name(member: dict) -> str:
    """Возвращает нормализованное имя роли пользователя."""
    if not member:
        return "member"
    return _normalize_role(member.get("role", "member"))

def is_president(member: dict) -> bool:
    if not member:
        return False
    return _normalize_role(member.get("role")) == "president"

def is_grand_vice(member: dict) -> bool:
    if not member:
        return False
    return _normalize_role(member.get("role")) == "grand_vice"

# ─── Динамическое чтение файла администраторов ───────────────────────────────

def get_admin_rights_from_file(user_id: int) -> dict or None:
    """
    Проверяет наличие user_id в файле admins.txt на лету.
    Использует встроенную функцию парсинга конфигурации бота.
    Возвращает словарь с ролью и кланом или None, если пользователя нет в списке.
    """
    current_admins = load_initial_admins()
    return current_admins.get(user_id, None)
