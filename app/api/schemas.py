"""Схемы Web API. Отдельны от доменных сущностей: контракт HTTP меняется по своим причинам."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.entities import (
    Access,
    BlockedSeller,
    Filter,
    FoundListing,
    Listing,
    Location,
    Overview,
    SearchCriteria,
    UserSummary,
    WorkerStatus,
)
from app.domain.tariffs import Tariff
from app.services.billing import Offer

QUERY_MAX_LENGTH = 100
NAME_MAX_LENGTH = 64

Language = Literal["uk", "ru", "en"]


class LocationIn(BaseModel):
    """Выбранный в списке город или область — id приходит из справочника."""

    kind: Literal["city", "region"] = "city"
    id: int = Field(gt=0)
    name: str = ""

    def to_domain(self) -> Location:
        return Location(kind=self.kind, id=self.id, name=self.name)

    @classmethod
    def from_domain(cls, location: Location) -> LocationIn:
        return cls(kind=location.kind, id=location.id, name=location.name)


class CriteriaIn(BaseModel):
    """Форма поиска из Mini App — понятные поля, id приходят из справочника.

    Mini App показывает названия («Киев», «Электроника»), а сюда присылает выбранные id;
    парсер проверяет их по справочнику и сам собирает параметры запроса к площадке.
    """

    query: str = Field(min_length=1, max_length=QUERY_MAX_LENGTH)
    price_min: Decimal | None = Field(default=None, ge=0)
    price_max: Decimal | None = Field(default=None, ge=0)
    condition: Literal["new", "used"] | None = None
    category_id: int | None = Field(default=None, gt=0)
    # Несколько городов или областей сразу.
    locations: list[LocationIn] = Field(default_factory=list, max_length=5)
    # Минус-слова: «iphone» минус «чехол» — проверяются по заголовку и описанию.
    minus_words: list[str] = Field(default_factory=list, max_length=20)
    # Требовать все слова запроса в заголовке — точное «аксесуари для iphone».
    match_all_words: bool = False
    only_private: bool = False
    only_with_delivery: bool = False
    only_with_photo: bool = False

    def to_domain(self) -> SearchCriteria:
        extra: dict[str, Any] = {
            "state": self.condition,
            "category_id": self.category_id,
        }
        return SearchCriteria(
            query=self.query,
            price_min=self.price_min,
            price_max=self.price_max,
            minus_words=tuple(word.strip() for word in self.minus_words if word.strip()),
            match_all_words=self.match_all_words,
            locations=tuple(item.to_domain() for item in self.locations),
            only_private=self.only_private,
            only_with_delivery=self.only_with_delivery,
            only_with_photo=self.only_with_photo,
            extra={key: value for key, value in extra.items() if value not in (None, "")},
        )

    @classmethod
    def from_domain(cls, criteria: SearchCriteria) -> CriteriaIn:
        """Обратное преобразование — чтобы Mini App показал сохранённый фильтр той же формой."""
        extra = criteria.extra
        locations = [LocationIn.from_domain(item) for item in criteria.locations]
        # Пресеты, сохранённые до мульти-локаций, хранили город прямо в extra.
        for kind, key in (("city", "city_id"), ("region", "region_id")):
            if not locations and extra.get(key):
                locations = [LocationIn(kind=kind, id=int(extra[key]))]
        return cls(
            query=criteria.query,
            price_min=criteria.price_min,
            price_max=criteria.price_max,
            condition=extra.get("state"),
            category_id=extra.get("category_id"),
            locations=locations,
            minus_words=list(criteria.minus_words),
            match_all_words=criteria.match_all_words,
            only_private=criteria.only_private,
            only_with_delivery=criteria.only_with_delivery,
            only_with_photo=criteria.only_with_photo,
        )


class FilterIn(BaseModel):
    name: str | None = Field(default=None, max_length=NAME_MAX_LENGTH)
    marketplace: str
    criteria: CriteriaIn


class LanguageIn(BaseModel):
    language_code: Language


class TariffIn(BaseModel):
    tariff: Tariff


class CatalogItemOut(BaseModel):
    id: int
    names: dict[str, str]
    children: list[CatalogItemOut] = Field(default_factory=list)


class CatalogOut(BaseModel):
    """Справочник для выпадающих списков: области с городами и категории с подкатегориями."""

    marketplace: str
    generated_at: str
    regions: list[CatalogItemOut]
    categories: list[CatalogItemOut]


class FilterOut(BaseModel):
    id: int
    marketplace: str
    title: str
    criteria: CriteriaIn
    is_active: bool
    created_at: datetime
    last_checked_at: datetime | None

    @classmethod
    def from_domain(cls, item: Filter) -> FilterOut:
        return cls(
            id=item.id,
            marketplace=item.marketplace,
            title=item.title,
            criteria=CriteriaIn.from_domain(item.criteria),
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


class ListingOut(BaseModel):
    """Объявление для Mini App: избранное и история показываются одинаково."""

    id: int | None
    url: str
    title: str
    price: Decimal | None
    previous_price: Decimal | None
    currency: str | None
    location: str | None
    image_url: str | None
    seller_name: str | None
    is_business: bool
    has_delivery: bool
    published_at: datetime | None

    @classmethod
    def from_domain(cls, listing: Listing) -> ListingOut:
        return cls(
            id=listing.id,
            url=listing.url,
            title=listing.title,
            price=listing.price,
            previous_price=listing.previous_price,
            currency=listing.currency,
            location=listing.location,
            image_url=listing.image_url,
            seller_name=listing.seller_name,
            is_business=listing.is_business,
            has_delivery=listing.has_delivery,
            published_at=listing.published_at,
        )


class BlockedSellerOut(BaseModel):
    marketplace: str
    seller_id: str
    seller_name: str | None
    created_at: datetime

    @classmethod
    def from_domain(cls, seller: BlockedSeller) -> BlockedSellerOut:
        return cls(
            marketplace=seller.marketplace,
            seller_id=seller.seller_id,
            seller_name=seller.seller_name,
            created_at=seller.created_at,
        )


class StatusOut(BaseModel):
    """Виджет прозрачности: жив ли мониторинг и сколько нашёл сегодня."""

    is_running: bool
    last_run_at: datetime | None
    seconds_ago: int | None
    interval_seconds: float
    last_cycle_seconds: float | None
    filters_checked: int
    found_today: int

    @classmethod
    def from_domain(cls, status: WorkerStatus) -> StatusOut:
        return cls(**asdict(status))


class AccessOut(BaseModel):
    is_allowed: bool
    until: datetime | None
    tariff: Tariff | None
    is_trial: bool
    trial_available: bool
    is_admin: bool

    @classmethod
    def from_domain(cls, access: Access) -> AccessOut:
        return cls(
            is_allowed=access.is_allowed,
            until=access.until,
            tariff=access.tariff,
            is_trial=access.is_trial,
            trial_available=access.trial_available,
            is_admin=access.is_admin,
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


class OverviewOut(BaseModel):
    users: int
    active_users: int
    filters: int
    active_filters: int
    listings: int
    deliveries: int
    sent_last_day: int
    paying_users: int

    @classmethod
    def from_domain(cls, overview: Overview) -> OverviewOut:
        # Overview — slots-dataclass, поэтому vars() не подходит.
        return cls(**asdict(overview))


class AdminUserOut(BaseModel):
    id: int
    username: str | None
    full_name: str
    language_code: str | None
    is_active: bool
    filters: int
    access_until: datetime | None
    created_at: datetime

    @classmethod
    def from_domain(cls, summary: UserSummary) -> AdminUserOut:
        return cls(
            id=summary.user.id,
            username=summary.user.username,
            full_name=summary.user.full_name,
            language_code=summary.user.language_code,
            is_active=summary.user.is_active,
            filters=summary.filters,
            access_until=summary.access_until,
            created_at=summary.created_at,
        )


class GrantIn(BaseModel):
    user_id: int
    days: int = Field(default=30, ge=1, le=3650)


class InvoiceOut(BaseModel):
    """Ссылка на оплату: Stars — через WebApp.openInvoice, CryptoBot — openTelegramLink."""

    provider: Literal["stars", "cryptobot"]
    url: str
