"""Бот в чате делает ровно две вещи: открывает Mini App и принимает оплату Stars."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from aiogram.types import InlineKeyboardMarkup, Message, PreCheckoutQuery
from pydantic import SecretStr

from app.config import BotSettings
from app.domain.entities import Subscription
from app.domain.tariffs import Tariff
from app.handlers.common import anything_else, cmd_start
from app.handlers.payments import confirm_checkout, payment_succeeded
from app.payments.stars import build_payload

WEBAPP_URL = "https://app.example.com"
SETTINGS = BotSettings(token=SecretStr("123:TEST"), webapp_url=WEBAPP_URL)
NO_APP_SETTINGS = BotSettings(token=SecretStr("123:TEST"))


@dataclass
class SentMessage:
    text: str
    reply_markup: InlineKeyboardMarkup | None


@dataclass
class FakeMessage:
    """Ровно та часть Message, которой пользуются хендлеры."""

    from_user: Any = None
    successful_payment: Any = None
    sent: list[SentMessage] = field(default_factory=list)

    async def answer(self, text: str, reply_markup: Any = None, **kwargs: Any) -> None:
        self.sent.append(SentMessage(text=text, reply_markup=reply_markup))


@dataclass
class FakeUser:
    id: int = 1
    username: str | None = "tester"
    full_name: str = "Тест"
    language_code: str | None = "uk"


class FakeFilterService:
    def __init__(self) -> None:
        self.registered: list[int] = []

    async def register_user(self, user_id: int, username: str | None, full_name: str) -> None:
        self.registered.append(user_id)


class FakeBilling:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def activate_paid(self, user_id: int, tariff: Tariff, **kwargs: Any) -> Subscription:
        self.calls.append({"user_id": user_id, "tariff": tariff, **kwargs})
        now = datetime(2026, 9, 18, tzinfo=UTC)
        return Subscription(
            id=1,
            user_id=user_id,
            tariff=tariff,
            starts_at=now,
            ends_at=now + timedelta(days=30),
        )


def _buttons(markup: InlineKeyboardMarkup | None) -> list[Any]:
    assert markup is not None
    return [button for row in markup.inline_keyboard for button in row]


async def test_start_offers_exactly_one_button_that_opens_the_app() -> None:
    message = FakeMessage(from_user=FakeUser())
    filters = FakeFilterService()

    await cmd_start(cast(Message, message), SETTINGS, cast(Any, filters))

    (sent,) = message.sent
    buttons = _buttons(sent.reply_markup)
    assert len(buttons) == 1, "в чате не должно быть меню из кнопок"
    assert buttons[0].web_app is not None and buttons[0].web_app.url == WEBAPP_URL
    assert filters.registered == [1], "пользователь заводится при первом /start"


async def test_start_without_configured_app_says_so_instead_of_crashing() -> None:
    message = FakeMessage(from_user=FakeUser())

    await cmd_start(cast(Message, message), NO_APP_SETTINGS, cast(Any, FakeFilterService()))

    (sent,) = message.sent
    assert sent.reply_markup is None
    assert "BOT__WEBAPP_URL" in sent.text


async def test_any_other_message_just_offers_the_app_again() -> None:
    """Чат — не меню: на любой текст бот отвечает той же кнопкой запуска."""
    message = FakeMessage(from_user=FakeUser())

    await anything_else(cast(Message, message), SETTINGS)

    (sent,) = message.sent
    assert _buttons(sent.reply_markup)[0].web_app is not None


# ---------- Оплата Stars ----------


@dataclass
class FakePreCheckout:
    invoice_payload: str
    answered: list[tuple[bool, str | None]] = field(default_factory=list)

    async def answer(self, ok: bool, error_message: str | None = None, **kwargs: Any) -> None:
        self.answered.append((ok, error_message))


@dataclass
class FakePayment:
    invoice_payload: str
    telegram_payment_charge_id: str = "charge-1"
    total_amount: int = 675
    currency: str = "XTR"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [(build_payload(Tariff.MONTH, 42), True), ("garbage", False)],
)
async def test_pre_checkout_is_always_answered(payload: str, expected: bool) -> None:
    query = FakePreCheckout(invoice_payload=payload)

    await confirm_checkout(cast(PreCheckoutQuery, query))

    assert query.answered[0][0] is expected


async def test_successful_payment_activates_the_subscription() -> None:
    message = FakeMessage(
        from_user=FakeUser(),
        successful_payment=FakePayment(invoice_payload=build_payload(Tariff.MONTH, 42)),
    )
    billing = FakeBilling()

    await payment_succeeded(cast(Message, message), cast(Any, billing), SETTINGS)

    (call,) = billing.calls
    assert (call["user_id"], call["tariff"]) == (42, Tariff.MONTH)
    assert call["provider"] == "stars"
    assert call["payment_id"] == "charge-1", "id платежа защищает от повторного зачисления"
    assert call["amount"] == Decimal(675)
    assert "18.10.2026" in message.sent[0].text


async def test_unreadable_payment_payload_does_not_credit_anything() -> None:
    message = FakeMessage(
        from_user=FakeUser(), successful_payment=FakePayment(invoice_payload="broken")
    )
    billing = FakeBilling()

    await payment_succeeded(cast(Message, message), cast(Any, billing), SETTINGS)

    assert billing.calls == []
    assert "поддержку" in message.sent[0].text
