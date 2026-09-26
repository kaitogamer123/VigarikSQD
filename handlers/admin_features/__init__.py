"""Объединяет разделы административного меню в один роутер."""
from aiogram import Router

from .base import router as base_router
from .moderation import router as moderation_router
from .twinks import router as twinks_router
from .clan_management import router as clan_management_router
from .announcements import router as announcements_router

admin_main_router = Router()
admin_main_router.include_routers(
    twinks_router,
    base_router,
    moderation_router,
    clan_management_router,
    announcements_router,
)