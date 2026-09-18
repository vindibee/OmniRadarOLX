"""Сводные запросы для админки. Только чтение: ничего не меняет, считает по месту."""

from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    DeliveryModel,
    FilterModel,
    ListingModel,
    SubscriptionModel,
    UserModel,
)
from app.domain.entities import Overview, User, UserSummary


class SqlAlchemyStatsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def overview(self, now: datetime) -> Overview:
        day_ago = now - timedelta(days=1)
        return Overview(
            users=await self._count(select(func.count()).select_from(UserModel)),
            active_users=await self._count(
                select(func.count()).select_from(UserModel).where(UserModel.is_active.is_(True))
            ),
            filters=await self._count(select(func.count()).select_from(FilterModel)),
            active_filters=await self._count(
                select(func.count()).select_from(FilterModel).where(FilterModel.is_active.is_(True))
            ),
            listings=await self._count(select(func.count()).select_from(ListingModel)),
            deliveries=await self._count(select(func.count()).select_from(DeliveryModel)),
            sent_last_day=await self._count(
                select(func.count())
                .select_from(DeliveryModel)
                .where(DeliveryModel.sent_at.is_not(None), DeliveryModel.sent_at >= day_ago)
            ),
            paying_users=await self._count(
                select(func.count(func.distinct(SubscriptionModel.user_id))).where(
                    SubscriptionModel.is_trial.is_(False),
                    SubscriptionModel.payment_provider.is_not(None),
                )
            ),
        )

    async def users(self, now: datetime, *, limit: int = 100) -> Sequence[UserSummary]:
        """Пользователи с числом фильтров и сроком доступа — новые сверху."""
        filters_count = (
            select(FilterModel.user_id, func.count().label("filters"))
            .group_by(FilterModel.user_id)
            .subquery()
        )
        access_until = (
            select(
                SubscriptionModel.user_id,
                func.max(SubscriptionModel.ends_at).label("access_until"),
            )
            .where(SubscriptionModel.ends_at > now)
            .group_by(SubscriptionModel.user_id)
            .subquery()
        )
        rows = await self._session.execute(
            select(UserModel, filters_count.c.filters, access_until.c.access_until)
            .outerjoin(filters_count, filters_count.c.user_id == UserModel.id)
            .outerjoin(access_until, access_until.c.user_id == UserModel.id)
            .order_by(UserModel.created_at.desc())
            .limit(limit)
        )
        return [
            UserSummary(
                user=User(
                    id=model.id,
                    username=model.username,
                    full_name=model.full_name,
                    is_active=model.is_active,
                    language_code=model.language_code,
                ),
                filters=filters or 0,
                access_until=until,
                created_at=model.created_at,
            )
            for model, filters, until in rows.tuples()
        ]

    async def _count(self, statement: Select[tuple[int]]) -> int:
        return await self._session.scalar(statement) or 0
