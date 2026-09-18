"""Порты — абстракции, которые сервисы используют, но не реализуют.

Реализации живут во внешних слоях (``app.repositories``, ``app.notifications``)
и подставляются в корне композиции (``app.main``). Благодаря этому сервисы
тестируются на фейках без PostgreSQL и Telegram.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self

from app.domain.entities import Listing, PendingDelivery, SearchCriteria, Subscription, User


class UserRepository(Protocol):
    async def upsert(self, user: User) -> None: ...

    async def set_active(self, user_id: int, is_active: bool) -> None: ...


class SubscriptionRepository(Protocol):
    async def add(
        self, *, user_id: int, marketplace: str, title: str, criteria: SearchCriteria
    ) -> Subscription: ...

    async def get_for_user(self, subscription_id: int, user_id: int) -> Subscription | None: ...

    async def list_for_user(self, user_id: int) -> Sequence[Subscription]: ...

    async def count_for_user(self, user_id: int) -> int: ...

    async def list_active(self, marketplaces: Sequence[str]) -> Sequence[Subscription]: ...

    async def set_active(self, subscription_id: int, is_active: bool) -> None: ...

    async def deactivate_for_user(self, user_id: int) -> None: ...

    async def delete(self, subscription_id: int) -> None: ...

    async def mark_checked(self, subscription_id: int, checked_at: datetime) -> None: ...


class ListingRepository(Protocol):
    async def upsert_many(self, listings: Sequence[Listing]) -> dict[tuple[str, str], int]:
        """Сохраняет объявления и возвращает ``(marketplace, external_id) -> id``."""
        ...


class DeliveryRepository(Protocol):
    async def add_many(
        self, subscription_id: int, listing_ids: Sequence[int], *, skip_delivery: bool = False
    ) -> int:
        """Регистрирует объявления для фильтра, игнорируя уже известные. Возвращает число новых."""
        ...

    async def get_pending(
        self, subscription_id: int, *, max_attempts: int, limit: int
    ) -> Sequence[PendingDelivery]: ...

    async def mark_sent(self, subscription_id: int, listing_id: int, sent_at: datetime) -> None: ...

    async def register_failure(self, subscription_id: int, listing_id: int) -> None: ...


class UnitOfWork(Protocol):
    """Единица работы: одна транзакция и доступ ко всем репозиториям."""

    @property
    def users(self) -> UserRepository: ...

    @property
    def subscriptions(self) -> SubscriptionRepository: ...

    @property
    def listings(self) -> ListingRepository: ...

    @property
    def deliveries(self) -> DeliveryRepository: ...

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


class Notifier(Protocol):
    async def send_listing(
        self, chat_id: int, subscription: Subscription, listing: Listing
    ) -> None:
        """Отправляет объявление.

        :raises RecipientUnavailableError: получатель недоступен навсегда.
        :raises NotificationError: временная ошибка, доставку можно повторить.
        """
        ...
