"""Presentation бота: только запуск Mini App и приём платежей Telegram Stars."""

from aiogram import Router

from app.handlers import common, payments
from app.handlers.errors import register_error_handlers


def create_root_router() -> Router:
    root = Router(name="root")
    register_error_handlers(root)
    # Порядок важен: платежи раньше, иначе их перехватит catch-all из common.
    root.include_routers(payments.router, common.router)
    return root
