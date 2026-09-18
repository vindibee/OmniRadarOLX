from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import SubscriptionModel, UserModel
from app.domain.entities import SearchCriteria, Subscription


def _to_entity(model: SubscriptionModel) -> Subscription:
    return Subscription(
        id=model.id,
        user_id=model.user_id,
        marketplace=model.marketplace,
        title=model.title,
        criteria=SearchCriteria.from_json(model.criteria),
        is_active=model.is_active,
        created_at=model.created_at,
        last_checked_at=model.last_checked_at,
    )


class SqlAlchemySubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self, *, user_id: int, marketplace: str, title: str, criteria: SearchCriteria
    ) -> Subscription:
        model = SubscriptionModel(
            user_id=user_id,
            marketplace=marketplace,
            title=title,
            criteria=criteria.to_json(),
            is_active=True,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)  # подтягиваем server_default (created_at)
        return _to_entity(model)

    async def get_for_user(self, subscription_id: int, user_id: int) -> Subscription | None:
        model = await self._session.scalar(
            select(SubscriptionModel).where(
                SubscriptionModel.id == subscription_id,
                SubscriptionModel.user_id == user_id,
            )
        )
        return _to_entity(model) if model else None

    async def list_for_user(self, user_id: int) -> Sequence[Subscription]:
        models = await self._session.scalars(
            select(SubscriptionModel)
            .where(SubscriptionModel.user_id == user_id)
            .order_by(SubscriptionModel.id)
        )
        return [_to_entity(m) for m in models]

    async def count_for_user(self, user_id: int) -> int:
        count = await self._session.scalar(
            select(func.count())
            .select_from(SubscriptionModel)
            .where(SubscriptionModel.user_id == user_id)
        )
        return count or 0

    async def list_active(self, marketplaces: Sequence[str]) -> Sequence[Subscription]:
        models = await self._session.scalars(
            select(SubscriptionModel)
            .join(UserModel, UserModel.id == SubscriptionModel.user_id)
            .where(
                SubscriptionModel.is_active.is_(True),
                UserModel.is_active.is_(True),
                SubscriptionModel.marketplace.in_(marketplaces),
            )
            .order_by(SubscriptionModel.id)
        )
        return [_to_entity(m) for m in models]

    async def set_active(self, subscription_id: int, is_active: bool) -> None:
        await self._session.execute(
            update(SubscriptionModel)
            .where(SubscriptionModel.id == subscription_id)
            .values(is_active=is_active)
        )

    async def deactivate_for_user(self, user_id: int) -> None:
        await self._session.execute(
            update(SubscriptionModel)
            .where(SubscriptionModel.user_id == user_id)
            .values(is_active=False)
        )

    async def delete(self, subscription_id: int) -> None:
        await self._session.execute(
            delete(SubscriptionModel).where(SubscriptionModel.id == subscription_id)
        )

    async def mark_checked(self, subscription_id: int, checked_at: datetime) -> None:
        await self._session.execute(
            update(SubscriptionModel)
            .where(SubscriptionModel.id == subscription_id)
            .values(last_checked_at=checked_at)
        )
