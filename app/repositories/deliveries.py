from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import DeliveryModel, ListingModel
from app.domain.entities import FoundListing, PendingDelivery
from app.repositories.listings import to_entity as listing_to_entity


class SqlAlchemyDeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_many(
        self, filter_id: int, listing_ids: Sequence[int], *, skip_delivery: bool = False
    ) -> int:
        if not listing_ids:
            return 0
        # skip_delivery: объявление запоминается как «уже виденное» и никогда не отправляется.
        sent_at = func.now() if skip_delivery else None
        stmt = (
            insert(DeliveryModel)
            .values(
                [
                    {"filter_id": filter_id, "listing_id": lid, "sent_at": sent_at}
                    for lid in dict.fromkeys(listing_ids)
                ]
            )
            # Защита от дублей на уровне БД: повторная вставка той же пары молча игнорируется.
            .on_conflict_do_nothing(index_elements=["filter_id", "listing_id"])
            .returning(DeliveryModel.listing_id)
        )
        result = await self._session.execute(stmt)
        return len(result.all())

    async def get_pending(
        self, filter_id: int, *, max_attempts: int, limit: int
    ) -> Sequence[PendingDelivery]:
        rows = await self._session.execute(
            select(DeliveryModel, ListingModel)
            .join(ListingModel, ListingModel.id == DeliveryModel.listing_id)
            .where(
                DeliveryModel.filter_id == filter_id,
                DeliveryModel.sent_at.is_(None),
                DeliveryModel.attempts < max_attempts,
            )
            .order_by(ListingModel.published_at.asc().nulls_last(), ListingModel.id)
            .limit(limit)
        )
        return [
            PendingDelivery(
                filter_id=delivery.filter_id,
                listing_id=delivery.listing_id,
                listing=listing_to_entity(listing),
                attempts=delivery.attempts,
            )
            for delivery, listing in rows.tuples()
        ]

    async def history(
        self, filter_id: int, *, limit: int = 50, offset: int = 0
    ) -> Sequence[FoundListing]:
        """Что этот фильтр уже находил — для раздела «История» в Mini App."""
        rows = await self._session.execute(
            select(DeliveryModel, ListingModel)
            .join(ListingModel, ListingModel.id == DeliveryModel.listing_id)
            .where(DeliveryModel.filter_id == filter_id)
            .order_by(
                ListingModel.published_at.desc().nulls_last(),
                DeliveryModel.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return [
            FoundListing(
                listing=listing_to_entity(listing),
                found_at=delivery.created_at,
                sent_at=delivery.sent_at,
            )
            for delivery, listing in rows.tuples()
        ]

    async def mark_sent(self, filter_id: int, listing_id: int, sent_at: datetime) -> None:
        await self._session.execute(
            update(DeliveryModel)
            .where(
                DeliveryModel.filter_id == filter_id,
                DeliveryModel.listing_id == listing_id,
            )
            .values(sent_at=sent_at)
        )

    async def register_failure(self, filter_id: int, listing_id: int) -> None:
        await self._session.execute(
            update(DeliveryModel)
            .where(
                DeliveryModel.filter_id == filter_id,
                DeliveryModel.listing_id == listing_id,
            )
            .values(attempts=DeliveryModel.attempts + 1)
        )
