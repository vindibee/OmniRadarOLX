from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.domain.tariffs import Tariff


@dataclass(frozen=True, slots=True)
class SearchCriteria:
    """Параметры поиска. Общие поля + ``extra`` для специфики конкретной площадки."""

    query: str
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    # Площадка ищет нестрого: по «iphone» приедут и чехлы. Эти два поля отсекают лишнее
    # уже у нас, по заголовку объявления.
    exclude_words: tuple[str, ...] = ()
    match_all_words: bool = False
    extra: Mapping[str, Any] = field(default_factory=dict)

    def matches(self, listing: Listing) -> bool:
        """Проходит ли объявление точные требования пользователя."""
        title = listing.title.casefold()
        if any(word.casefold() in title for word in self.exclude_words if word.strip()):
            return False
        if self.match_all_words:
            return all(word.casefold() in title for word in self.query.split() if word.strip())
        return True

    def to_json(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "price_min": str(self.price_min) if self.price_min is not None else None,
            "price_max": str(self.price_max) if self.price_max is not None else None,
            "exclude_words": list(self.exclude_words),
            "match_all_words": self.match_all_words,
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
            exclude_words=tuple(data.get("exclude_words") or ()),
            match_all_words=bool(data.get("match_all_words")),
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
    # Язык интерфейса; None — пользователь ещё не выбирал его на онбординге.
    language_code: str | None = None


@dataclass(frozen=True, slots=True)
class Filter:
    id: int
    user_id: int
    marketplace: str
    title: str
    criteria: SearchCriteria
    is_active: bool
    created_at: datetime
    last_checked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Subscription:
    """Оплаченный (или пробный) период доступа."""

    id: int
    user_id: int
    tariff: Tariff
    starts_at: datetime
    ends_at: datetime
    is_trial: bool = False
    payment_provider: str | None = None
    payment_id: str | None = None

    def is_active_at(self, moment: datetime) -> bool:
        return self.starts_at <= moment < self.ends_at


@dataclass(frozen=True, slots=True)
class Access:
    """Ответ на единственный вопрос: можно ли пользователю пользоваться ботом сейчас."""

    is_allowed: bool
    until: datetime | None = None
    tariff: Tariff | None = None
    is_trial: bool = False
    trial_available: bool = True
    # Владелец сервиса: доступ бессрочный, лимиты не применяются.
    is_admin: bool = False


@dataclass(frozen=True, slots=True)
class Overview:
    """Сводка для админки: сколько всего и что происходило за сутки."""

    users: int
    active_users: int
    filters: int
    active_filters: int
    listings: int
    deliveries: int
    sent_last_day: int
    paying_users: int


@dataclass(frozen=True, slots=True)
class UserSummary:
    """Строка списка пользователей в админке."""

    user: User
    filters: int
    access_until: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FoundListing:
    """Строка истории: объявление, когда оно опубликовано и когда мы его нашли."""

    listing: Listing
    found_at: datetime
    sent_at: datetime | None


@dataclass(frozen=True, slots=True)
class PendingDelivery:
    """Объявление, найденное по подписке, но ещё не доставленное пользователю."""

    filter_id: int
    listing_id: int
    listing: Listing
    attempts: int


@dataclass(frozen=True, slots=True)
class MarketplaceInfo:
    code: str
    title: str
