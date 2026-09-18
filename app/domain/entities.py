from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from app.domain.tariffs import Tariff


@dataclass(frozen=True, slots=True)
class Location:
    """Город или область из справочника площадки — то, что пользователь выбрал в списке."""

    kind: Literal["city", "region"]
    id: int
    name: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": self.id, "name": self.name}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Location:
        kind: Literal["city", "region"] = "region" if str(data.get("kind")) == "region" else "city"
        return cls(kind=kind, id=int(data["id"]), name=str(data.get("name") or ""))


@dataclass(frozen=True, slots=True)
class SearchCriteria:
    """Параметры поиска. Общие поля + ``extra`` для специфики конкретной площадки."""

    query: str
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    # Площадка ищет нестрого: по «iphone» приедут и чехлы. Минус-слова и режим «все слова»
    # отсекают лишнее уже у нас — по заголовку и описанию.
    minus_words: tuple[str, ...] = ()
    match_all_words: bool = False
    # Несколько городов или областей сразу: парсер обходит их по очереди.
    locations: tuple[Location, ...] = ()
    # Флаги, которых нет в поиске OLX: применяем сами по данным объявления.
    only_private: bool = False
    only_with_delivery: bool = False
    only_with_photo: bool = False
    extra: Mapping[str, Any] = field(default_factory=dict)

    def matches(self, listing: Listing) -> bool:
        """Проходит ли объявление точные требования пользователя."""
        haystack = f"{listing.title} {listing.description or ''}".casefold()
        if any(word.casefold() in haystack for word in self.minus_words if word.strip()):
            return False
        if self.match_all_words:
            title = listing.title.casefold()
            if not all(word.casefold() in title for word in self.query.split() if word.strip()):
                return False
        if self.only_private and listing.is_business:
            return False
        if self.only_with_delivery and not listing.has_delivery:
            return False
        return not (self.only_with_photo and not listing.images and not listing.image_url)

    def to_json(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "price_min": str(self.price_min) if self.price_min is not None else None,
            "price_max": str(self.price_max) if self.price_max is not None else None,
            "minus_words": list(self.minus_words),
            "match_all_words": self.match_all_words,
            "locations": [location.to_json() for location in self.locations],
            "only_private": self.only_private,
            "only_with_delivery": self.only_with_delivery,
            "only_with_photo": self.only_with_photo,
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
            # exclude_words — имя поля в старых пресетах, читаем оба.
            minus_words=tuple(data.get("minus_words") or data.get("exclude_words") or ()),
            match_all_words=bool(data.get("match_all_words")),
            locations=tuple(Location.from_json(item) for item in data.get("locations") or ()),
            only_private=bool(data.get("only_private")),
            only_with_delivery=bool(data.get("only_with_delivery")),
            only_with_photo=bool(data.get("only_with_photo")),
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
    # Несколько фото — для медиагруппы в уведомлении.
    images: tuple[str, ...] = ()
    description: str | None = None
    seller_id: str | None = None
    seller_name: str | None = None
    is_business: bool = False
    has_delivery: bool = False
    # Прошлая цена того же объявления: заполняется при сохранении, если цена изменилась.
    previous_price: Decimal | None = None
    published_at: datetime | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)
    # Идентификатор в нашей базе. У свежеразобранного объявления его ещё нет.
    id: int | None = None

    @property
    def price_drop_percent(self) -> int | None:
        """На сколько процентов подешевело. ``None`` — цена не менялась или выросла."""
        if self.previous_price is None or self.price is None or self.previous_price <= 0:
            return None
        if self.price >= self.previous_price:
            return None
        return int((self.previous_price - self.price) / self.previous_price * 100)


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
class BlockedSeller:
    """Продавец в персональном бан-листе пользователя."""

    marketplace: str
    seller_id: str
    seller_name: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class WorkerStatus:
    """Что показывает виджет прозрачности: жив ли мониторинг и когда был последний обход."""

    is_running: bool
    last_run_at: datetime | None
    seconds_ago: int | None
    interval_seconds: float
    last_cycle_seconds: float | None
    filters_checked: int
    found_today: int


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
