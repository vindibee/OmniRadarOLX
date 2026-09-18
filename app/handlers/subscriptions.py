"""Создание фильтра (FSM) и управление существующими фильтрами."""

from decimal import Decimal, InvalidOperation
from html import escape
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain.entities import SearchCriteria, Subscription
from app.handlers import keyboards
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
from app.handlers.states import CreateFilter
from app.services.subscriptions import SubscriptionService

router = Router(name="subscriptions")

QUERY_MAX_LENGTH = 100


# ---------- Создание фильтра ----------


@router.callback_query(MenuCallback.filter(F.action == MenuAction.NEW_FILTER))
async def start_create(
    callback: CallbackQuery, state: FSMContext, subscription_service: SubscriptionService
) -> None:
    await state.clear()
    await subscription_service.register_user(
        callback.from_user.id, callback.from_user.username, callback.from_user.full_name
    )
    marketplaces = subscription_service.marketplaces()
    if len(marketplaces) == 1:
        # Одна площадка — не заставляем выбирать.
        await state.update_data(marketplace=marketplaces[0].code)
        await state.set_state(CreateFilter.query)
        await _edit(
            callback, "🔎 Что ищем? Введите запрос, например: <i>iPhone 13</i>", keyboards.cancel()
        )
    else:
        await state.set_state(CreateFilter.marketplace)
        await _edit(callback, "Выберите площадку:", keyboards.marketplaces(marketplaces))
    await callback.answer()


@router.callback_query(CreateFilter.marketplace, MarketplaceCallback.filter())
async def choose_marketplace(
    callback: CallbackQuery, callback_data: MarketplaceCallback, state: FSMContext
) -> None:
    await state.update_data(marketplace=callback_data.code)
    await state.set_state(CreateFilter.query)
    await _edit(
        callback, "🔎 Что ищем? Введите запрос, например: <i>iPhone 13</i>", keyboards.cancel()
    )
    await callback.answer()


@router.message(CreateFilter.query, F.text)
async def enter_query(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()
    if not query or len(query) > QUERY_MAX_LENGTH:
        await message.answer(f"Запрос должен содержать от 1 до {QUERY_MAX_LENGTH} символов.")
        return
    await state.update_data(query=query)
    await state.set_state(CreateFilter.price_min)
    await message.answer(
        "💵 Минимальная цена (число) или «Пропустить»:", reply_markup=keyboards.skip_or_cancel()
    )


@router.message(CreateFilter.price_min, F.text)
async def enter_price_min(message: Message, state: FSMContext) -> None:
    price = _parse_price(message.text)
    if price is None:
        await message.answer("Введите неотрицательное число, например 5000.")
        return
    await state.update_data(price_min=str(price))
    await _ask_price_max(message, state)


@router.callback_query(CreateFilter.price_min, SkipCallback.filter())
async def skip_price_min(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(price_min=None)
    if isinstance(callback.message, Message):
        await _ask_price_max(callback.message, state)
    await callback.answer()


@router.message(CreateFilter.price_max, F.text)
async def enter_price_max(message: Message, state: FSMContext) -> None:
    price = _parse_price(message.text)
    if price is None:
        await message.answer("Введите неотрицательное число, например 20000.")
        return
    await state.update_data(price_max=str(price))
    await _ask_confirm(message, state)


@router.callback_query(CreateFilter.price_max, SkipCallback.filter())
async def skip_price_max(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(price_max=None)
    if isinstance(callback.message, Message):
        await _ask_confirm(callback.message, state)
    await callback.answer()


@router.callback_query(CreateFilter.confirm, ConfirmCallback.filter(F.action == ConfirmAction.SAVE))
async def save_filter(
    callback: CallbackQuery, state: FSMContext, subscription_service: SubscriptionService
) -> None:
    data = await state.get_data()
    subscription = await subscription_service.create(
        user_id=callback.from_user.id,
        marketplace=data["marketplace"],
        criteria=_criteria_from(data),
    )
    await state.clear()
    await _edit(
        callback,
        f"✅ Фильтр «{escape(subscription.title)}» сохранён.\n"
        "Пришлю новые объявления, как только они появятся.",
        keyboards.main_menu(),
    )
    await callback.answer()


@router.callback_query(ConfirmCallback.filter(F.action == ConfirmAction.CANCEL))
async def cancel_create(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _edit(callback, "Создание фильтра отменено.", keyboards.main_menu())
    await callback.answer()


# ---------- Управление фильтрами ----------


@router.message(Command("filters"))
async def cmd_filters(message: Message, subscription_service: SubscriptionService) -> None:
    if message.from_user:
        await _send_filters(message, message.from_user.id, subscription_service)


@router.callback_query(MenuCallback.filter(F.action == MenuAction.MY_FILTERS))
async def on_my_filters(callback: CallbackQuery, subscription_service: SubscriptionService) -> None:
    if isinstance(callback.message, Message):
        await _send_filters(callback.message, callback.from_user.id, subscription_service)
    await callback.answer()


@router.callback_query(SubscriptionCallback.filter(F.action == SubscriptionAction.TOGGLE))
async def on_toggle(
    callback: CallbackQuery,
    callback_data: SubscriptionCallback,
    subscription_service: SubscriptionService,
) -> None:
    subscription = await subscription_service.toggle(
        callback.from_user.id, callback_data.subscription_id
    )
    await _edit(callback, _describe(subscription), keyboards.subscription_actions(subscription))
    await callback.answer("Фильтр включён" if subscription.is_active else "Фильтр на паузе")


@router.callback_query(SubscriptionCallback.filter(F.action == SubscriptionAction.DELETE))
async def on_delete(
    callback: CallbackQuery,
    callback_data: SubscriptionCallback,
    subscription_service: SubscriptionService,
) -> None:
    await subscription_service.delete(callback.from_user.id, callback_data.subscription_id)
    await _edit(callback, "🗑 Фильтр удалён.", None)
    await callback.answer()


# ---------- Вспомогательное ----------


async def _ask_price_max(message: Message, state: FSMContext) -> None:
    await state.set_state(CreateFilter.price_max)
    await message.answer(
        "💵 Максимальная цена (число) или «Пропустить»:", reply_markup=keyboards.skip_or_cancel()
    )


async def _ask_confirm(message: Message, state: FSMContext) -> None:
    await state.set_state(CreateFilter.confirm)
    criteria = _criteria_from(await state.get_data())
    await message.answer(
        "Проверьте фильтр:\n\n" + _describe_criteria(criteria),
        reply_markup=keyboards.confirm(),
    )


async def _send_filters(
    message: Message, user_id: int, subscription_service: SubscriptionService
) -> None:
    subscriptions = await subscription_service.list(user_id)
    if not subscriptions:
        await message.answer("У вас пока нет фильтров.", reply_markup=keyboards.main_menu())
        return
    for subscription in subscriptions:
        await message.answer(
            _describe(subscription), reply_markup=keyboards.subscription_actions(subscription)
        )


async def _edit(callback: CallbackQuery, text: str, markup: Any) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=markup)


def _parse_price(text: str | None) -> Decimal | None:
    raw = (text or "").replace(" ", "").replace(",", ".")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() and value >= 0 else None


def _criteria_from(data: dict[str, Any]) -> SearchCriteria:
    return SearchCriteria.from_json(
        {
            "query": data["query"],
            "price_min": data.get("price_min"),
            "price_max": data.get("price_max"),
        }
    )


def _describe_criteria(criteria: SearchCriteria) -> str:
    lines = [f"🔎 Запрос: <b>{escape(criteria.query)}</b>"]
    if criteria.price_min is not None:
        lines.append(f"💵 От: {criteria.price_min}")
    if criteria.price_max is not None:
        lines.append(f"💵 До: {criteria.price_max}")
    return "\n".join(lines)


def _describe(subscription: Subscription) -> str:
    status = "🟢 активен" if subscription.is_active else "⏸ на паузе"
    return f"<b>{escape(subscription.title)}</b>\nПлощадка: {subscription.marketplace} · {status}"
