"""Presentation бота: запуск Mini App, кнопки под уведомлением и оплата Stars."""

from aiogram import Router

from app.handlers import actions, common, payments
from app.handlers.errors import register_error_handlers


def create_root_router() -> Router:
    root = Router(name="root")
    register_error_handlers(root)
    # Порядок важен: платежи раньше, иначе их перехватит catch-all из common.
    root.include_routers(actions.router, payments.router, common.router)
    return root
