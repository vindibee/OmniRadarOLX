from collections.abc import Sequence

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.domain.entities import MarketplaceInfo, Subscription
from app.handlers.callbacks import (
    ConfirmAction,
    ConfirmCallback,
    MarketplaceCallback,
    MenuAction,
    MenuCallback,
    SkipCallback,
    SubscriptionAction,
    SubscriptionCallback,
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


def subscription_actions(subscription: Subscription) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="⏸ Пауза" if subscription.is_active else "▶️ Включить",
        callback_data=SubscriptionCallback(
            action=SubscriptionAction.TOGGLE, subscription_id=subscription.id
        ),
    )
    builder.button(
        text="🗑 Удалить",
        callback_data=SubscriptionCallback(
            action=SubscriptionAction.DELETE, subscription_id=subscription.id
        ),
    )
    return builder.as_markup()
