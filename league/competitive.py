"""Скримы, верификация и история. Точка сборки подмодулей."""
from aiogram import Router

from .competitive_common import SCRIM_TYPE_LABEL, _normalise_scheduled
from .verification import router as verification_router
from .scrims import router as scrims_router
from .scrim_results import router as scrim_results_router
from .history import router as history_router
from .watchdog import scrim_watchdog_task

router = Router(name="competitive")
router.include_routers(
    verification_router,
    scrims_router,
    scrim_results_router,
    history_router,
)

__all__ = [
    "router",
    "scrim_watchdog_task",
    "SCRIM_TYPE_LABEL",
    "_normalise_scheduled",
]
