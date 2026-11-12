"""Админ-панель: сборка подразделов в один роутер.

Модуль оставлен как точка входа для `handlers.admin_features` и реэкспортирует
FSM-состояния, которые импортируются из других разделов бота.
"""
from aiogram import Router

from .states import (
    AdminStates,
    TournamentAdminStates,
    TournamentResultStates,
    TournamentStates,
)
from .panel import router as panel_router
from .tournament_create import router as tournament_create_router
from .tournament_registration import router as tournament_registration_router
from .tournament_results import router as tournament_results_router
from .scheduler import router as scheduler_router
from .membership import router as membership_router
from .tournament_admin import router as tournament_admin_router
from .scrims import router as scrims_router
from .system import router as system_router

router = Router(name="admin_base")
router.include_routers(
    panel_router,
    tournament_create_router,
    tournament_registration_router,
    tournament_results_router,
    scheduler_router,
    membership_router,
    tournament_admin_router,
    scrims_router,
    system_router,
)

__all__ = [
    "router",
    "AdminStates",
    "TournamentStates",
    "TournamentResultStates",
    "TournamentAdminStates",
]
