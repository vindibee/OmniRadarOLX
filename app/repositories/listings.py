from collections.abc import Sequence

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import ListingModel
from app.domain.entities import Listing


def _to_row(listing: Listing) -> dict[str, object]:
    return {
        "marketplace": listing.marketplace,
        "external_id": listing.external_id,
        "url": listing.url,
        "title": listing.title,
        "price": listing.price,
        "currency": listing.currency,
        "location": listing.location,
        "image_url": listing.image_url,
        "published_at": listing.published_at,
        "attributes": dict(listing.attributes),
    }


def to_entity(model: ListingModel) -> Listing:
    return Listing(
        marketplace=model.marketplace,
        external_id=model.external_id,
        url=model.url,
        title=model.title,
        price=model.price,
        currency=model.currency,
        location=model.location,
        image_url=model.image_url,
        published_at=model.published_at,
        attributes=model.attributes,
    )


class SqlAlchemyListingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_many(self, listings: Sequence[Listing]) -> dict[tuple[str, str], int]:
        # ON CONFLICT DO UPDATE не может дважды обновить одну строку в одном запросе,
        # поэтому сначала убираем дубли внутри пачки (последняя версия побеждает).
        unique = {(item.marketplace, item.external_id): item for item in listings}
        if not unique:
            return {}

        insert_stmt = insert(ListingModel).values([_to_row(item) for item in unique.values()])
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=[ListingModel.marketplace, ListingModel.external_id],
            set_={
                "url": insert_stmt.excluded.url,
                "title": insert_stmt.excluded.title,
                "price": insert_stmt.excluded.price,
                "currency": insert_stmt.excluded.currency,
                "location": insert_stmt.excluded.location,
                "image_url": insert_stmt.excluded.image_url,
                "attributes": insert_stmt.excluded.attributes,
                "last_seen_at": func.now(),
            },
        ).returning(ListingModel.id, ListingModel.marketplace, ListingModel.external_id)

        result = await self._session.execute(stmt)
        return {(row.marketplace, row.external_id): row.id for row in result}
