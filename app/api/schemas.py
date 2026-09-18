"""Схемы Web API. Отдельны от доменных сущностей: контракт HTTP меняется по своим причинам."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.domain.entities import Access, Filter, SearchCriteria, SearchPreset
from app.domain.tariffs import Tariff
from app.services.billing import Offer

QUERY_MAX_LENGTH = 100
NAME_MAX_LENGTH = 64


class CriteriaIn(BaseModel):
    """Форма поиска из Mini App."""

    query: str = Field(min_length=1, max_length=QUERY_MAX_LENGTH)
    price_min: Decimal | None = Field(default=None, ge=0)
    price_max: Decimal | None = Field(default=None, ge=0)
    # Специфика площадки: город, категория, состояние — валидирует парсер площадки.
    extra: dict[str, Any] = Field(default_factory=dict)

    def to_domain(self) -> SearchCriteria:
        return SearchCriteria(
            query=self.query,
            price_min=self.price_min,
            price_max=self.price_max,
            extra=self.extra,
        )


class PresetIn(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    marketplace: str
    criteria: CriteriaIn


class PresetOut(BaseModel):
    id: int
    name: str
    marketplace: str
    criteria: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_domain(cls, preset: SearchPreset) -> PresetOut:
        return cls(
            id=preset.id,
            name=preset.name,
            marketplace=preset.marketplace,
            criteria=preset.criteria.to_json(),
            created_at=preset.created_at,
        )


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
    """Всё, что нужно Mini App на старте: кто вошёл, что ему доступно, чем можно продлить."""

    id: int
    username: str | None
    full_name: str
    language_code: str | None
    access: AccessOut
    marketplaces: list[MarketplaceOut]
    offers: list[OfferOut]
