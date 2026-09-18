"""Telegram Stars: выставление счёта.

Mini App просит у Web API ссылку на счёт и открывает её через ``WebApp.openInvoice``;
подтверждение приходит боту апдейтами (``app.handlers.payments``). Разделение то же,
что и везде: провайдер умеет только «выставить счёт», сроки считает BillingService.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import LabeledPrice

from app.domain.tariffs import Tariff
from app.services.billing import Offer

logger = logging.getLogger(__name__)

STARS_PROVIDER = "stars"
# Счета в Stars выставляются в валюте XTR и без provider_token.
STARS_CURRENCY = "XTR"


def build_payload(tariff: Tariff, user_id: int) -> str:
    """Payload возвращается Telegram'ом обратно — в нём всё, что нужно для зачисления."""
    return f"{tariff.value}:{user_id}"


def parse_payload(payload: str) -> tuple[Tariff, int] | None:
    tariff_code, _, user_id = payload.partition(":")
    try:
        return Tariff(tariff_code), int(user_id)
    except ValueError:
        return None


class StarsPayments:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def create_invoice_link(self, user_id: int, offer: Offer, *, title: str) -> str:
        return await self._bot.create_invoice_link(
            title=title,
            description=f"Доступ на {offer.days} дн.",
            payload=build_payload(offer.tariff, user_id),
            currency=STARS_CURRENCY,
            prices=[LabeledPrice(label=title, amount=offer.price_stars)],
            provider_token="",
        )

    async def aclose(self) -> None:
        await self._bot.session.close()
