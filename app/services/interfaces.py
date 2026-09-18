"""Порты — абстракции, которые сервисы используют, но не реализуют.

Реализации живут во внешних слоях (``app.repositories``, ``app.notifications``)
и подставляются в корне композиции (``app.main``). Благодаря этому сервисы
тестируются на фейках без PostgreSQL и Telegram.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from decimal import Decimal
from types import TracebackType
from typing import Protocol, Self

from app.domain.entities import (
    Filter,
    FoundListing,
    Listing,
    Overview,
    PendingDelivery,
    SearchCriteria,
    Subscription,
    User,
    UserSummary,
)
from app.domain.tariffs import Tariff


class UserRepository(Protocol):
    async def upsert(self, user: User) -> None: ...

    async def get(self, user_id: int) -> User | None: ...

    async def set_active(self, user_id: int, is_active: bool) -> None: ...

    async def set_language(self, user_id: int, language_code: str) -> None: ...


class FilterRepository(Protocol):
    async def add(
        self, *, user_id: int, marketplace: str, title: str, criteria: SearchCriteria
    ) -> Filter: ...

    async def get_for_user(self, filter_id: int, user_id: int) -> Filter | None: ...

    async def list_for_user(self, user_id: int) -> Sequence[Filter]: ...

    async def count_for_user(self, user_id: int) -> int: ...

    async def list_active(self, marketplaces: Sequence[str]) -> Sequence[Filter]: ...

    async def set_active(self, filter_id: int, is_active: bool) -> None: ...

    async def deactivate_for_user(self, user_id: int) -> None: ...

    async def delete(self, filter_id: int) -> None: ...

    async def mark_checked(self, filter_id: int, checked_at: datetime) -> None: ...


class SubscriptionRepository(Protocol):
    """Оплаченные периоды доступа (биллинг)."""

    async def add(
        self,
        *,
        user_id: int,
        tariff: Tariff,
        starts_at: datetime,
        ends_at: datetime,
        is_trial: bool = False,
        payment_provider: str | None = None,
        payment_id: str | None = None,
        amount: Decimal | None = None,
        currency: str | None = None,
    ) -> Subscription: ...

    async def latest_active(self, user_id: int, moment: datetime) -> Subscription | None: ...

    async def last_ends_at(self, user_id: int) -> datetime | None: ...

    async def has_trial(self, user_id: int) -> bool: ...

    async def find_by_payment(self, provider: str, payment_id: str) -> Subscription | None: ...


class ListingRepository(Protocol):
    async def upsert_many(self, listings: Sequence[Listing]) -> dict[tuple[str, str], int]:
        """Сохраняет объявления и возвращает ``(marketplace, external_id) -> id``."""
        ...


class DeliveryRepository(Protocol):
    async def add_many(
        self, filter_id: int, listing_ids: Sequence[int], *, skip_delivery: bool = False
    ) -> int:
        """Регистрирует объявления для фильтра, игнорируя уже известные. Возвращает число новых."""
        ...

    async def get_pending(
        self, filter_id: int, *, max_attempts: int, limit: int
    ) -> Sequence[PendingDelivery]: ...

    async def mark_sent(self, filter_id: int, listing_id: int, sent_at: datetime) -> None: ...

    async def register_failure(self, filter_id: int, listing_id: int) -> None: ...

    async def history(
        self, filter_id: int, *, limit: int = 50, offset: int = 0
    ) -> Sequence[FoundListing]:
        """История находок фильтра: новые сверху."""
        ...


class StatsRepository(Protocol):
    """Сводные запросы для админки владельца."""

    async def overview(self, now: datetime) -> Overview: ...

    async def users(self, now: datetime, *, limit: int = 100) -> Sequence[UserSummary]: ...


class UnitOfWork(Protocol):
    """Единица работы: одна транзакция и доступ ко всем репозиториям."""

    @property
    def users(self) -> UserRepository: ...

    @property
    def filters(self) -> FilterRepository: ...

    @property
    def subscriptions(self) -> SubscriptionRepository: ...

    @property
    def listings(self) -> ListingRepository: ...

    @property
    def deliveries(self) -> DeliveryRepository: ...

    @property
    def stats(self) -> StatsRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


UnitOfWorkFactory = Callable[[], UnitOfWork]


class Cache(Protocol):
    """Порт кэша. Сервисы не знают про Redis — им достаточно этих четырёх операций."""

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ttl_seconds: float) -> None: ...

    async def add(self, key: str, value: str, ttl_seconds: float) -> bool:
        """Атомарно записывает значение, только если ключа ещё нет (SET NX).

        Возвращает ``True``, если ключ создан этим вызовом — на этом строится
        блокировка «парсит только один».
        """
        ...

    async def delete(self, key: str) -> None: ...


class Notifier(Protocol):
    async def send_listing(self, chat_id: int, search_filter: Filter, listing: Listing) -> None:
        """Отправляет объявление.

        :raises RecipientUnavailableError: получатель недоступен навсегда.
        :raises NotificationError: временная ошибка, доставку можно повторить.
        """
        ...
