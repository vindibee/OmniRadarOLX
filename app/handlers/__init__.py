"""Presentation layer: только Telegram-интерфейс. Бизнес-логика — в сервисах."""

from aiogram import Router

from app.handlers import common, subscriptions
from app.handlers.errors import register_error_handlers


def create_root_router() -> Router:
    # Роутеры модулей создаются на уровне модуля, поэтому корневой роутер собирается один раз
    # за процесс; в тестах используйте свежий Dispatcher.
    root = Router(name="root")
    register_error_handlers(root)
    # Порядок важен: сначала сценарии с состояниями, затем общие команды и fallback.
    root.include_routers(subscriptions.router, common.router)
    return root
