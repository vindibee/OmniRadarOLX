"""Уведомление — точка входа в бота: из него можно открыть объявление и вернуться в меню."""

from datetime import UTC, datetime
from decimal import Decimal

from app.domain.entities import Listing, SearchCriteria, Subscription
from app.handlers import keyboards
from app.handlers.callbacks import MenuAction, MenuCallback
from app.notifications.telegram import format_listing

LISTING = Listing(
    marketplace="olx_ua",
    external_id="1",
    url="https://www.olx.ua/d/obyavlenie/1",
    title="iPhone 13",
    price=Decimal("15500"),
    currency="UAH",
    location="Київ",
    published_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
)
SUBSCRIPTION = Subscription(
    id=1,
    user_id=1,
    marketplace="olx_ua",
    title="iPhone 13",
    criteria=SearchCriteria(query="iphone 13"),
    is_active=True,
    created_at=datetime(2026, 9, 18, tzinfo=UTC),
)


def test_listing_keyboard_opens_listing_and_returns_to_menu() -> None:
    buttons = [
        button for row in keyboards.listing_actions(LISTING.url).inline_keyboard for button in row
    ]

    assert [button.url for button in buttons] == [LISTING.url, None]
    assert buttons[1].callback_data == MenuCallback(action=MenuAction.MAIN).pack()


def test_listing_text_has_no_duplicate_link() -> None:
    text = format_listing(SUBSCRIPTION, LISTING)

    assert "iPhone 13" in text
    assert "15 500 UAH" in text
    assert "<a href" not in text, "ссылка живёт в кнопке, а не в тексте"
