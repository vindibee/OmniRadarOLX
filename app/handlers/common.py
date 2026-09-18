"""Единственный экран бота в чате: приветствие и кнопка запуска Mini App.

Всё управление живёт в Mini App. Здесь намеренно нет ни FSM, ни меню из кнопок:
чат нужен, чтобы открыть приложение и получать уведомления о находках.
"""

import logging

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)

from app.config import BotSettings
from app.services.filters import FilterService

logger = logging.getLogger(__name__)

router = Router(name="common")

WELCOME_TEXT = (
    "👋 Привет! Я слежу за объявлениями и пришлю сюда каждое новое, "
    "как только оно появится.\n\n"
    "Всё остальное — в приложении: язык, поиск, подписка."
)
NO_WEBAPP_TEXT = (
    "⚙️ Приложение ещё не подключено. Загляните позже — или сообщите администратору, "
    "что не задан BOT__WEBAPP_URL."
)
OPEN_APP_BUTTON = "📱 Открыть приложение"


def open_app_keyboard(webapp_url: str, text: str = OPEN_APP_BUTTON) -> InlineKeyboardMarkup:
    """Одна кнопка — вход в Mini App. Других кнопок в чате нет."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, web_app=WebAppInfo(url=webapp_url))]]
    )


def menu_button(webapp_url: str) -> MenuButtonWebApp:
    """Кнопка слева от поля ввода — второй вход в приложение, всегда под рукой."""
    return MenuButtonWebApp(text="Приложение", web_app=WebAppInfo(url=webapp_url))


async def _greet(message: Message, bot_settings: BotSettings) -> None:
    if not bot_settings.webapp_url:
        # Telegram принимает в web_app только https — без адреса кнопку создать нельзя.
        logger.warning("BOT__WEBAPP_URL не задан: кнопка запуска Mini App недоступна")
        await message.answer(NO_WEBAPP_TEXT)
        return
    await message.answer(WELCOME_TEXT, reply_markup=open_app_keyboard(bot_settings.webapp_url))


@router.message(CommandStart())
async def cmd_start(
    message: Message, bot_settings: BotSettings, filter_service: FilterService
) -> None:
    if message.from_user:
        await filter_service.register_user(
            user_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )
    await _greet(message, bot_settings)


@router.message()
async def anything_else(message: Message, bot_settings: BotSettings) -> None:
    """Любое сообщение — просьба открыть приложение, а не повод учить команды."""
    await _greet(message, bot_settings)
