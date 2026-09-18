"""Уведомление: карточка, галерея фото, динамика цены и кнопки под объявлением."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

import pytest
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.entities import Filter, Listing, SearchCriteria
from app.handlers.callbacks import BlockSellerCallback, FavoriteCallback
from app.notifications.telegram import (
    MAX_GALLERY_PHOTOS,
    TelegramNotifier,
    format_listing,
    format_price,
    listing_keyboard,
)

WEBAPP_URL = "https://app.example.com"
PHOTOS = tuple(f"https://img.example/{index}.jpg" for index in range(8))
LISTING = Listing(
    id=42,
    marketplace="olx_ua",
    external_id="1",
    url="https://www.olx.ua/d/obyavlenie/1",
    title="iPhone 13",
    price=Decimal("10500"),
    currency="грн",
    location="Київ",
    image_url=PHOTOS[0],
    images=PHOTOS,
    seller_id="555",
    seller_name="Микола",
    has_delivery=True,
    published_at=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
)
FILTER = Filter(
    id=1,
    user_id=1,
    marketplace="olx_ua",
    title="iPhone 13",
    criteria=SearchCriteria(query="iphone 13"),
    is_active=True,
    created_at=datetime(2026, 9, 19, tzinfo=UTC),
)


def _buttons(markup: InlineKeyboardMarkup) -> list[InlineKeyboardButton]:
    return [button for row in markup.inline_keyboard for button in row]


# ---------- Кнопки ----------


def test_keyboard_offers_listing_favorite_block_and_app() -> None:
    buttons = _buttons(listing_keyboard(LISTING, WEBAPP_URL))

    assert buttons[0].url == LISTING.url
    assert buttons[1].callback_data == FavoriteCallback(listing_id=42).pack()
    assert (
        buttons[2].callback_data
        == BlockSellerCallback(marketplace="olx_ua", seller_id="555").pack()
    )
    assert buttons[3].web_app is not None and buttons[3].web_app.url == WEBAPP_URL


def test_unknown_seller_leaves_out_the_block_button() -> None:
    listing = Listing(
        id=42, marketplace="olx_ua", external_id="1", url="https://olx.ua/1", title="Без продавца"
    )

    texts = [button.text for button in _buttons(listing_keyboard(listing, None))]

    assert texts == ["🔗 Открыть в OLX", "⭐ В избранное"], "нечего блокировать — нет и кнопки"


# ---------- Цена ----------


def test_price_drop_is_shown_as_before_and_after() -> None:
    cheaper = Listing(
        marketplace="olx_ua",
        external_id="1",
        url="u",
        title="t",
        price=Decimal("10500"),
        previous_price=Decimal("12000"),
        currency="грн",
    )

    assert cheaper.price_drop_percent == 12
    assert format_price(cheaper) == "💰 <s>12 000 грн</s> ➔ <b>10 500 грн</b> (−12%)"


@pytest.mark.parametrize(
    ("price", "previous"),
    [(Decimal(12000), Decimal(10000)), (Decimal(10000), Decimal(10000)), (Decimal(10000), None)],
)
def test_price_without_a_drop_is_shown_plainly(price: Decimal, previous: Decimal | None) -> None:
    listing = Listing(
        marketplace="olx_ua",
        external_id="1",
        url="u",
        title="t",
        price=price,
        previous_price=previous,
        currency="грн",
    )

    assert listing.price_drop_percent is None
    assert "➔" not in format_price(listing), "подорожание скидкой не показываем"


def test_card_mentions_seller_and_delivery() -> None:
    text = format_listing(FILTER, LISTING)

    assert "Микола" in text
    assert "OLX Доставка" in text
    assert "<a href" not in text, "ссылка живёт в кнопке"


# ---------- Отправка ----------


@dataclass
class FakeBot:
    """Считает вызовы Telegram, чтобы проверить порядок и состав сообщений."""

    media_groups: list[list[Any]] = field(default_factory=list)
    photos: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)

    async def send_media_group(self, chat_id: int, media: list[Any]) -> None:
        self.media_groups.append(media)

    async def send_photo(self, chat_id: int, **kwargs: Any) -> None:
        self.photos.append(kwargs)

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        self.messages.append({"text": text, **kwargs})


async def test_several_photos_go_as_one_gallery_plus_a_card() -> None:
    bot = FakeBot()
    notifier = TelegramNotifier(cast(Bot, bot), webapp_url=WEBAPP_URL)

    await notifier.send_listing(1, FILTER, LISTING)

    (gallery,) = bot.media_groups
    assert len(gallery) == MAX_GALLERY_PHOTOS, "восемь фото — показываем первые пять"
    assert bot.photos == [], "галерея вместо одиночного фото"
    # Клавиатуру Telegram к медиагруппе не принимает, поэтому карточка идёт следом.
    (card,) = bot.messages
    assert "iPhone 13" in card["text"]
    assert card["reply_markup"] is not None


async def test_single_photo_is_sent_as_one_message_with_caption() -> None:
    bot = FakeBot()
    listing = Listing(
        id=42,
        marketplace="olx_ua",
        external_id="1",
        url="https://olx.ua/1",
        title="iPhone 13",
        image_url=PHOTOS[0],
        images=PHOTOS[:1],
    )

    await TelegramNotifier(cast(Bot, bot)).send_listing(1, FILTER, listing)

    assert bot.media_groups == []
    (photo,) = bot.photos
    assert "iPhone 13" in photo["caption"]
    assert photo["reply_markup"] is not None
