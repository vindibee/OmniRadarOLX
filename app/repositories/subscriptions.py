"""Репозиторий оплаченных подписок (биллинг), не путать с фильтрами поиска."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import SubscriptionModel
from app.domain.entities import Subscription
from app.domain.tariffs import Tariff


def _to_entity(model: SubscriptionModel) -> Subscription:
    return Subscription(
        id=model.id,
        user_id=model.user_id,
        tariff=Tariff(model.tariff),
        starts_at=model.starts_at,
        ends_at=model.ends_at,
        is_trial=model.is_trial,
        payment_provider=model.payment_provider,
        payment_id=model.payment_id,
    )


class SqlAlchemySubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
    ) -> Subscription:
        model = SubscriptionModel(
            user_id=user_id,
            tariff=tariff.value,
            starts_at=starts_at,
            ends_at=ends_at,
            is_trial=is_trial,
            payment_provider=payment_provider,
            payment_id=payment_id,
            amount=amount,
            currency=currency,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_entity(model)

    async def latest_active(self, user_id: int, moment: datetime) -> Subscription | None:
        """Действующая подписка с самым поздним окончанием (их может быть несколько)."""
        model = await self._session.scalar(
            select(SubscriptionModel)
            .where(
                SubscriptionModel.user_id == user_id,
                SubscriptionModel.starts_at <= moment,
                SubscriptionModel.ends_at > moment,
            )
            .order_by(SubscriptionModel.ends_at.desc())
            .limit(1)
        )
        return _to_entity(model) if model else None

    async def last_ends_at(self, user_id: int) -> datetime | None:
        """Конец самого позднего периода — от него продлевается оплата."""
        ends_at: datetime | None = await self._session.scalar(
            select(SubscriptionModel.ends_at)
            .where(SubscriptionModel.user_id == user_id)
            .order_by(SubscriptionModel.ends_at.desc())
            .limit(1)
        )
        return ends_at

    async def has_trial(self, user_id: int) -> bool:
        return bool(
            await self._session.scalar(
                select(
                    exists().where(
                        SubscriptionModel.user_id == user_id,
                        SubscriptionModel.is_trial.is_(True),
                    )
                )
            )
        )

    async def find_by_payment(self, provider: str, payment_id: str) -> Subscription | None:
        """Идемпотентность: повтор вебхука провайдера не должен продлевать доступ дважды."""
        model = await self._session.scalar(
            select(SubscriptionModel).where(
                SubscriptionModel.payment_provider == provider,
                SubscriptionModel.payment_id == payment_id,
            )
        )
        return _to_entity(model) if model else None
