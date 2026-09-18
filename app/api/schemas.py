"""Схемы Web API. Отдельны от доменных сущностей: контракт HTTP меняется по своим причинам."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.entities import Access, Filter, FoundListing, SearchCriteria
from app.domain.tariffs import Tariff
from app.services.billing import Offer

QUERY_MAX_LENGTH = 100
NAME_MAX_LENGTH = 64

Language = Literal["uk", "ru", "en"]


class CriteriaIn(BaseModel):
    """Форма поиска из Mini App: общие поля + специфика площадки в extra."""

    query: str = Field(min_length=1, max_length=QUERY_MAX_LENGTH)
    price_min: Decimal | None = Field(default=None, ge=0)
    price_max: Decimal | None = Field(default=None, ge=0)
    # Состояние, категория, город — то, что умеет конкретная площадка.
    extra: dict[str, Any] = Field(default_factory=dict)

    def to_domain(self) -> SearchCriteria:
        return SearchCriteria(
            query=self.query,
            price_min=self.price_min,
            price_max=self.price_max,
            extra={key: value for key, value in self.extra.items() if value not in (None, "")},
        )


class FilterIn(BaseModel):
    name: str | None = Field(default=None, max_length=NAME_MAX_LENGTH)
    marketplace: str
    criteria: CriteriaIn


class LanguageIn(BaseModel):
    language_code: Language


class TariffIn(BaseModel):
    tariff: Tariff


class FilterOut(BaseModel):
    id: int
    marketplace: str
    title: str
    criteria: dict[str, Any]
    is_active: bool
    created_at: datetime
    last_checked_at: datetime | None

    @classmethod
    def from_domain(cls, item: Filter) -> FilterOut:
        return cls(
            id=item.id,
            marketplace=item.marketplace,
            title=item.title,
            criteria=item.criteria.to_json(),
            is_active=item.is_active,
            created_at=item.created_at,
            last_checked_at=item.last_checked_at,
        )


class ItemOut(BaseModel):
    """Строка истории: что нашли, когда опубликовано и когда мы это увидели."""

    external_id: str
    url: str
    title: str
    price: Decimal | None
    currency: str | None
    location: str | None
    image_url: str | None
    published_at: datetime | None
    found_at: datetime
    sent_at: datetime | None

    @classmethod
    def from_domain(cls, found: FoundListing) -> ItemOut:
        listing = found.listing
        return cls(
            external_id=listing.external_id,
            url=listing.url,
            title=listing.title,
            price=listing.price,
            currency=listing.currency,
            location=listing.location,
            image_url=listing.image_url,
            published_at=listing.published_at,
            found_at=found.found_at,
            sent_at=found.sent_at,
        )


class AccessOut(BaseModel):
    is_allowed: bool
    until: datetime | None
    tariff: Tariff | None
    is_trial: bool
    trial_available: bool

    @classmethod
    def from_domain(cls, access: Access) -> AccessOut:
        return cls(
            is_allowed=access.is_allowed,
            until=access.until,
            tariff=access.tariff,
            is_trial=access.is_trial,
            trial_available=access.trial_available,
        )


class OfferOut(BaseModel):
    tariff: Tariff
    days: int
    discount_percent: int
    price_stars: int
    price_usd: Decimal

    @classmethod
    def from_domain(cls, offer: Offer) -> OfferOut:
        return cls(
            tariff=offer.tariff,
            days=offer.days,
            discount_percent=offer.discount_percent,
            price_stars=offer.price_stars,
            price_usd=offer.price_usd,
        )


class MarketplaceOut(BaseModel):
    code: str
    title: str


class MeOut(BaseModel):
    """Всё, что нужно Mini App на старте: кто вошёл, язык, доступ, прайс, площадки."""

    id: int
    username: str | None
    full_name: str
    language_code: str | None
    # Язык ещё не выбирали — Mini App показывает экран выбора и онбординг.
    needs_onboarding: bool
    access: AccessOut
    marketplaces: list[MarketplaceOut]
    offers: list[OfferOut]
    payment_methods: list[Literal["stars", "cryptobot"]]


class InvoiceOut(BaseModel):
    """Ссылка на оплату: Stars — через WebApp.openInvoice, CryptoBot — openTelegramLink."""

    provider: Literal["stars", "cryptobot"]
    url: str
