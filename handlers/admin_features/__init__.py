from aiogram import Router
from .base import router as base_router
from .moderation import router as moderation_router
from .clan_management import router as clan_management_router
from .announcements import router as announcements_router

admin_main_router = Router()

# Объединяем все ветки админ-панели в один общий роутер
admin_main_router.include_routers(
    base_router,
    moderation_router,
    clan_management_router,
    announcements_router
)
