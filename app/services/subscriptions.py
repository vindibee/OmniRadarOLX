from collections.abc import Sequence

from app.domain.entities import MarketplaceInfo, SearchCriteria, Subscription, User
from app.domain.errors import SubscriptionLimitExceededError, SubscriptionNotFoundError
from app.services.interfaces import UnitOfWork, UnitOfWorkFactory
from app.services.parsers.registry import ParserRegistry

TITLE_MAX_LENGTH = 128


class SubscriptionService:
    """Сценарии пользователя: регистрация и управление фильтрами поиска."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        parsers: ParserRegistry,
        *,
        max_subscriptions_per_user: int,
    ) -> None:
        self._uow_factory = uow_factory
        self._parsers = parsers
        self._max_per_user = max_subscriptions_per_user

    def marketplaces(self) -> Sequence[MarketplaceInfo]:
        return self._parsers.marketplaces()

    async def register_user(self, user_id: int, username: str | None, full_name: str) -> None:
        async with self._uow_factory() as uow:
            await uow.users.upsert(User(id=user_id, username=username, full_name=full_name))
            await uow.commit()

    async def create(
        self, user_id: int, marketplace: str, criteria: SearchCriteria
    ) -> Subscription:
        parser = self._parsers.get(marketplace)  # UnknownMarketplaceError, если площадки нет
        parser.validate_criteria(criteria)  # InvalidCriteriaError

        async with self._uow_factory() as uow:
            if await uow.subscriptions.count_for_user(user_id) >= self._max_per_user:
                raise SubscriptionLimitExceededError(self._max_per_user)
            subscription = await uow.subscriptions.add(
                user_id=user_id,
                marketplace=marketplace,
                title=_make_title(criteria),
                criteria=criteria,
            )
            await uow.commit()
        return subscription

    async def list(self, user_id: int) -> Sequence[Subscription]:
        async with self._uow_factory() as uow:
            return await uow.subscriptions.list_for_user(user_id)

    async def toggle(self, user_id: int, subscription_id: int) -> Subscription:
        async with self._uow_factory() as uow:
            subscription = await self._get_owned(uow, user_id, subscription_id)
            await uow.subscriptions.set_active(subscription_id, not subscription.is_active)
            await uow.commit()
            updated = await uow.subscriptions.get_for_user(subscription_id, user_id)
        if updated is None:
            raise SubscriptionNotFoundError(subscription_id)
        return updated

    async def delete(self, user_id: int, subscription_id: int) -> None:
        async with self._uow_factory() as uow:
            await self._get_owned(uow, user_id, subscription_id)
            await uow.subscriptions.delete(subscription_id)
            await uow.commit()

    @staticmethod
    async def _get_owned(uow: UnitOfWork, user_id: int, subscription_id: int) -> Subscription:
        subscription = await uow.subscriptions.get_for_user(subscription_id, user_id)
        if subscription is None:
            raise SubscriptionNotFoundError(subscription_id)
        return subscription


def _make_title(criteria: SearchCriteria) -> str:
    parts = [criteria.query.strip()]
    if criteria.price_min is not None or criteria.price_max is not None:
        low = f"{criteria.price_min:,.0f}" if criteria.price_min is not None else "…"
        high = f"{criteria.price_max:,.0f}" if criteria.price_max is not None else "…"
        parts.append(f"({low} – {high})".replace(",", " "))
    return " ".join(parts)[:TITLE_MAX_LENGTH]
