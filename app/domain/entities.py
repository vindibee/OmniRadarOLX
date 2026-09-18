from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class SearchCriteria:
    """Параметры поиска. Общие поля + ``extra`` для специфики конкретной площадки."""

    query: str
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "price_min": str(self.price_min) if self.price_min is not None else None,
            "price_max": str(self.price_max) if self.price_max is not None else None,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> SearchCriteria:
        def to_decimal(value: Any) -> Decimal | None:
            return Decimal(str(value)) if value is not None else None

        return cls(
            query=str(data.get("query", "")),
            price_min=to_decimal(data.get("price_min")),
            price_max=to_decimal(data.get("price_max")),
            extra=dict(data.get("extra") or {}),
        )


@dataclass(frozen=True, slots=True)
class Listing:
    """Объявление в нормализованном виде — одинаковом для всех площадок."""

    marketplace: str
    external_id: str
    url: str
    title: str
    price: Decimal | None = None
    currency: str | None = None
    location: str | None = None
    image_url: str | None = None
    published_at: datetime | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class User:
    id: int
    username: str | None
    full_name: str
    is_active: bool = True


@dataclass(frozen=True, slots=True)
class Subscription:
    id: int
    user_id: int
    marketplace: str
    title: str
    criteria: SearchCriteria
    is_active: bool
    created_at: datetime
    last_checked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PendingDelivery:
    """Объявление, найденное по подписке, но ещё не доставленное пользователю."""

    subscription_id: int
    listing_id: int
    listing: Listing
    attempts: int


@dataclass(frozen=True, slots=True)
class MarketplaceInfo:
    code: str
    title: str
