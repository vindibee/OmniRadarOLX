from collections.abc import Sequence

from sqlalchemy import case, func
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
        "images": list(listing.images),
        "description": listing.description,
        "seller_id": listing.seller_id,
        "seller_name": listing.seller_name,
        "is_business": listing.is_business,
        "has_delivery": listing.has_delivery,
        "published_at": listing.published_at,
        "attributes": dict(listing.attributes),
    }


def to_entity(model: ListingModel) -> Listing:
    return Listing(
        id=model.id,
        marketplace=model.marketplace,
        external_id=model.external_id,
        url=model.url,
        title=model.title,
        price=model.price,
        currency=model.currency,
        location=model.location,
        image_url=model.image_url,
        images=tuple(model.images or ()),
        description=model.description,
        seller_id=model.seller_id,
        seller_name=model.seller_name,
        is_business=model.is_business,
        has_delivery=model.has_delivery,
        previous_price=model.previous_price,
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
                "images": insert_stmt.excluded.images,
                "description": insert_stmt.excluded.description,
                "seller_id": insert_stmt.excluded.seller_id,
                "seller_name": insert_stmt.excluded.seller_name,
                "is_business": insert_stmt.excluded.is_business,
                "has_delivery": insert_stmt.excluded.has_delivery,
                # Прошлую цену запоминаем только когда она реально изменилась —
                # так уведомление может показать «было → стало».
                "previous_price": case(
                    (
                        ListingModel.price.is_distinct_from(insert_stmt.excluded.price),
                        ListingModel.price,
                    ),
                    else_=ListingModel.previous_price,
                ),
                "attributes": insert_stmt.excluded.attributes,
                "last_seen_at": func.now(),
            },
        ).returning(ListingModel.id, ListingModel.marketplace, ListingModel.external_id)

        result = await self._session.execute(stmt)
        return {(row.marketplace, row.external_id): row.id for row in result}
