"""Доставка находок в чат: карточка объявления, галерея фото и действия под ней.

Единственное, что бот делает в чате помимо запуска Mini App. Телеграм не разрешает
клавиатуру у медиагруппы, поэтому фото уходят группой, а карточка с кнопками — следом.
"""

import asyncio
import logging
from decimal import Decimal
from html import escape

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    LinkPreviewOptions,
    WebAppInfo,
)

from app.domain.entities import Filter, Listing
from app.domain.errors import NotificationError, RecipientUnavailableError
from app.handlers.callbacks import BlockSellerCallback, FavoriteCallback

logger = logging.getLogger(__name__)

CAPTION_LIMIT = 1024
MAX_RETRY_AFTER_SECONDS = 60
# Сколько фото показывать галереей: больше — уже спам в ленте чата.
MAX_GALLERY_PHOTOS = 5
MIN_GALLERY_PHOTOS = 2


class TelegramNotifier:
    """Единственная задача бота, кроме запуска Mini App: доставить находку в чат."""

    def __init__(self, bot: Bot, *, webapp_url: str | None = None) -> None:
        self._bot = bot
        self._webapp_url = webapp_url

    async def send_listing(self, chat_id: int, search_filter: Filter, listing: Listing) -> None:
        text = format_listing(search_filter, listing)
        markup = listing_keyboard(listing, self._webapp_url)
        try:
            await self._send(chat_id, text, listing, markup)
        except TelegramRetryAfter as exc:
            # Flood control: ждём, сколько просит Telegram, и пробуем один раз.
            if exc.retry_after > MAX_RETRY_AFTER_SECONDS:
                raise NotificationError(f"Flood control: {exc.retry_after} c") from exc
            await asyncio.sleep(exc.retry_after)
            await self._send_safely(chat_id, text, listing, markup)
        except TelegramForbiddenError as exc:
            raise RecipientUnavailableError(str(exc)) from exc
        except TelegramNotFound as exc:
            raise RecipientUnavailableError(str(exc)) from exc
        except (TelegramNetworkError, TelegramServerError, TelegramBadRequest) as exc:
            raise NotificationError(str(exc)) from exc

    async def _send_safely(
        self, chat_id: int, text: str, listing: Listing, markup: InlineKeyboardMarkup
    ) -> None:
        try:
            await self._send(chat_id, text, listing, markup)
        except TelegramForbiddenError as exc:
            raise RecipientUnavailableError(str(exc)) from exc
        except (TelegramNetworkError, TelegramServerError, TelegramBadRequest) as exc:
            raise NotificationError(str(exc)) from exc

    async def _send(
        self, chat_id: int, text: str, listing: Listing, markup: InlineKeyboardMarkup
    ) -> None:
        gallery = list(listing.images[:MAX_GALLERY_PHOTOS])
        if len(gallery) >= MIN_GALLERY_PHOTOS:
            await self._send_gallery(chat_id, gallery)
            # Клавиатуру Telegram к медиагруппе не принимает — карточка идёт отдельным сообщением.
            await self._bot.send_message(
                chat_id,
                text,
                reply_markup=markup,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
            return

        image = listing.image_url or (gallery[0] if gallery else None)
        if image and len(text) <= CAPTION_LIMIT:
            try:
                await self._bot.send_photo(chat_id, photo=image, caption=text, reply_markup=markup)
                return
            except TelegramBadRequest as exc:
                if "chat not found" in exc.message.lower():
                    raise RecipientUnavailableError(exc.message) from exc
                # Telegram не смог скачать картинку — отправляем без неё.
                logger.debug("Фото не отправилось (%s), шлю текст", exc.message)
        await self._bot.send_message(
            chat_id,
            text,
            reply_markup=markup,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    async def _send_gallery(self, chat_id: int, photos: list[str]) -> None:
        try:
            await self._bot.send_media_group(
                chat_id, media=[InputMediaPhoto(media=url) for url in photos]
            )
        except TelegramBadRequest as exc:
            if "chat not found" in exc.message.lower():
                raise RecipientUnavailableError(exc.message) from exc
            # Галерея — украшение: если фото недоступны, карточка всё равно должна уйти.
            logger.debug("Галерея не отправилась (%s), шлю только карточку", exc.message)


def listing_keyboard(listing: Listing, webapp_url: str | None) -> InlineKeyboardMarkup:
    """Действия под объявлением: открыть, сохранить, скрыть продавца, открыть приложение."""
    rows = [[InlineKeyboardButton(text="🔗 Открыть в OLX", url=listing.url)]]

    actions: list[InlineKeyboardButton] = []
    if listing.id is not None:
        actions.append(
            InlineKeyboardButton(
                text="⭐ В избранное",
                callback_data=FavoriteCallback(listing_id=listing.id).pack(),
            )
        )
    if listing.seller_id:
        actions.append(
            InlineKeyboardButton(
                text="🚫 Скрыть продавца",
                callback_data=BlockSellerCallback(
                    marketplace=listing.marketplace, seller_id=listing.seller_id
                ).pack(),
            )
        )
    if actions:
        rows.append(actions)
    if webapp_url:
        rows.append(
            [InlineKeyboardButton(text="📱 Мои фильтры", web_app=WebAppInfo(url=webapp_url))]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_listing(search_filter: Filter, listing: Listing) -> str:
    lines = [f"🔔 <b>{escape(listing.title)}</b>"]
    price_line = format_price(listing)
    if price_line:
        lines.append(price_line)
    if listing.location:
        lines.append(f"📍 {escape(listing.location)}")
    if listing.published_at:
        lines.append(f"🕒 {listing.published_at:%d.%m.%Y %H:%M}")
    if listing.seller_name:
        seller = escape(listing.seller_name)
        lines.append(f"👤 {seller}{' · магазин' if listing.is_business else ''}")
    if listing.has_delivery:
        lines.append("📦 Есть OLX Доставка")
    lines.append(f"🔎 Фильтр: {escape(search_filter.title)}")
    return "\n".join(lines)


def format_price(listing: Listing) -> str:
    """Цена, а при снижении — «было → стало» со скидкой."""
    if listing.price is None:
        return ""
    currency = escape(listing.currency or "")
    drop = listing.price_drop_percent
    if drop is not None and listing.previous_price is not None:
        was = f"<s>{_amount(listing.previous_price)} {currency}</s>".strip()
        now = f"{_amount(listing.price)} {currency}".strip()
        return f"💰 {was} ➔ <b>{now}</b> (−{drop}%)"
    return f"💰 {_amount(listing.price)} {currency}".rstrip()


def _amount(price: Decimal) -> str:
    return f"{price:,.0f}".replace(",", " ")
