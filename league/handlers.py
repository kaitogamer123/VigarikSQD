"""Система составов. Точка сборки: подмодули в `league/` регистрируют свои роутеры."""
from aiogram import Router

from league.league_db import init_league_db

from .shared import LeagueStates
from .ui import router as ui_router, show_root
from .creation import router as creation_router
from .overview import router as overview_router
from .applications import router as applications_router
from .invitations import router as invitations_router
from .roster import router as roster_router
from .deputies import router as deputies_router

init_league_db()

router = Router(name="league")
router.include_routers(
    ui_router,
    creation_router,
    overview_router,
    applications_router,
    invitations_router,
    roster_router,
    deputies_router,
)

__all__ = ["router", "show_root", "LeagueStates"]
