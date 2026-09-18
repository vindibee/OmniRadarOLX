"""Уведомление — единственное, что бот делает в чате, кроме запуска приложения."""

from datetime import UTC, datetime
from decimal import Decimal

from app.domain.entities import Filter, Listing, SearchCriteria
from app.notifications.telegram import format_listing, listing_keyboard

WEBAPP_URL = "https://app.example.com"
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
FILTER = Filter(
    id=1,
    user_id=1,
    marketplace="olx_ua",
    title="iPhone 13",
    criteria=SearchCriteria(query="iphone 13"),
    is_active=True,
    created_at=datetime(2026, 9, 18, tzinfo=UTC),
)


def test_keyboard_opens_listing_and_the_app() -> None:
    buttons = [b for row in listing_keyboard(LISTING.url, WEBAPP_URL).inline_keyboard for b in row]

    assert buttons[0].url == LISTING.url
    assert buttons[1].web_app is not None and buttons[1].web_app.url == WEBAPP_URL


def test_without_configured_app_only_the_listing_link_is_offered() -> None:
    buttons = [b for row in listing_keyboard(LISTING.url, None).inline_keyboard for b in row]

    assert len(buttons) == 1, "кнопку web_app нельзя создать без https-адреса приложения"


def test_listing_text_has_no_duplicate_link() -> None:
    text = format_listing(FILTER, LISTING)

    assert "iPhone 13" in text
    assert "15 500 UAH" in text
    assert "<a href" not in text, "ссылка живёт в кнопке, а не в тексте"
