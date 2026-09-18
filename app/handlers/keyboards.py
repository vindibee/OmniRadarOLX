from collections.abc import Sequence

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.domain.entities import Filter, MarketplaceInfo
from app.handlers.callbacks import (
    ConfirmAction,
    ConfirmCallback,
    FilterAction,
    FilterCallback,
    MarketplaceCallback,
    MenuAction,
    MenuCallback,
    SkipCallback,
)


def main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Новый фильтр", callback_data=MenuCallback(action=MenuAction.NEW_FILTER))
    builder.button(text="📋 Мои фильтры", callback_data=MenuCallback(action=MenuAction.MY_FILTERS))
    builder.button(text="ℹ️ Помощь", callback_data=MenuCallback(action=MenuAction.HELP))
    builder.adjust(1)
    return builder.as_markup()


def back_to_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ В меню", callback_data=MenuCallback(action=MenuAction.MAIN))
    return builder.as_markup()


def listing_actions(listing_url: str) -> InlineKeyboardMarkup:
    """Клавиатура под уведомлением: открыть объявление и вернуться в меню одной кнопкой."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔗 Открыть объявление", url=listing_url)
    builder.button(text="🏠 Меню", callback_data=MenuCallback(action=MenuAction.MAIN))
    builder.adjust(1)
    return builder.as_markup()


def marketplaces(items: Sequence[MarketplaceInfo]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in items:
        builder.button(text=item.title, callback_data=MarketplaceCallback(code=item.code))
    builder.button(text="✖️ Отмена", callback_data=ConfirmCallback(action=ConfirmAction.CANCEL))
    builder.adjust(1)
    return builder.as_markup()


def skip_or_cancel() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⏭ Пропустить", callback_data=SkipCallback())
    builder.button(text="✖️ Отмена", callback_data=ConfirmCallback(action=ConfirmAction.CANCEL))
    return builder.as_markup()


def cancel() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✖️ Отмена", callback_data=ConfirmCallback(action=ConfirmAction.CANCEL))
    return builder.as_markup()


def confirm() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Сохранить", callback_data=ConfirmCallback(action=ConfirmAction.SAVE))
    builder.button(text="✖️ Отмена", callback_data=ConfirmCallback(action=ConfirmAction.CANCEL))
    return builder.as_markup()


def filter_actions(search_filter: Filter) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="⏸ Пауза" if search_filter.is_active else "▶️ Включить",
        callback_data=FilterCallback(action=FilterAction.TOGGLE, filter_id=search_filter.id),
    )
    builder.button(
        text="🗑 Удалить",
        callback_data=FilterCallback(action=FilterAction.DELETE, filter_id=search_filter.id),
    )
    return builder.as_markup()
