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
from aiogram.types import InlineKeyboardMarkup, LinkPreviewOptions

from app.domain.entities import Filter, Listing
from app.domain.errors import NotificationError, RecipientUnavailableError
from app.handlers import keyboards

logger = logging.getLogger(__name__)

CAPTION_LIMIT = 1024
MAX_RETRY_AFTER_SECONDS = 60


class TelegramNotifier:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_listing(self, chat_id: int, search_filter: Filter, listing: Listing) -> None:
        text = format_listing(search_filter, listing)
        markup = keyboards.listing_actions(listing.url)
        try:
            await self._send(chat_id, text, listing.image_url, markup)
        except TelegramRetryAfter as exc:
            # Flood control: ждём, сколько просит Telegram, и пробуем один раз.
            if exc.retry_after > MAX_RETRY_AFTER_SECONDS:
                raise NotificationError(f"Flood control: {exc.retry_after} c") from exc
            await asyncio.sleep(exc.retry_after)
            await self._send_safely(chat_id, text, listing.image_url, markup)
        except TelegramForbiddenError as exc:
            raise RecipientUnavailableError(str(exc)) from exc
        except TelegramNotFound as exc:
            raise RecipientUnavailableError(str(exc)) from exc
        except (TelegramNetworkError, TelegramServerError, TelegramBadRequest) as exc:
            raise NotificationError(str(exc)) from exc

    async def _send_safely(
        self, chat_id: int, text: str, image_url: str | None, markup: InlineKeyboardMarkup
    ) -> None:
        try:
            await self._send(chat_id, text, image_url, markup)
        except TelegramForbiddenError as exc:
            raise RecipientUnavailableError(str(exc)) from exc
        except (TelegramNetworkError, TelegramServerError, TelegramBadRequest) as exc:
            raise NotificationError(str(exc)) from exc

    async def _send(
        self, chat_id: int, text: str, image_url: str | None, markup: InlineKeyboardMarkup
    ) -> None:
        if image_url and len(text) <= CAPTION_LIMIT:
            try:
                await self._bot.send_photo(
                    chat_id, photo=image_url, caption=text, reply_markup=markup
                )
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


def format_listing(search_filter: Filter, listing: Listing) -> str:
    lines = [f"🔔 <b>{escape(listing.title)}</b>"]
    if listing.price is not None:
        lines.append(f"💰 {_format_price(listing.price)} {escape(listing.currency or '')}".rstrip())
    if listing.location:
        lines.append(f"📍 {escape(listing.location)}")
    if listing.published_at:
        lines.append(f"🕒 {listing.published_at:%d.%m.%Y %H:%M}")
    lines.append(f"🔎 Фильтр: {escape(search_filter.title)}")
    # Ссылка есть в кнопке под сообщением, в тексте её дублировать не нужно.
    return "\n".join(lines)


def _format_price(price: Decimal) -> str:
    return f"{price:,.0f}".replace(",", " ")
