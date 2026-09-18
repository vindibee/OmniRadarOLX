"""Личные списки пользователя: избранное и бан-лист продавцов.

Обе таблицы — с составным первичным ключом, поэтому повторное нажатие кнопки
под уведомлением не создаёт дублей: конфликт просто игнорируется.
"""

from collections.abc import Sequence

from sqlalchemy import delete, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import BlockedSellerModel, FavoriteModel, ListingModel
from app.domain.entities import BlockedSeller, Listing
from app.repositories.listings import to_entity as listing_to_entity


class SqlAlchemyFavoriteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, user_id: int, listing_id: int) -> bool:
        """Возвращает ``True``, если объявление добавлено именно этим вызовом."""
        stmt = (
            insert(FavoriteModel)
            .values(user_id=user_id, listing_id=listing_id)
            .on_conflict_do_nothing(index_elements=["user_id", "listing_id"])
            .returning(FavoriteModel.listing_id)
        )
        return (await self._session.scalar(stmt)) is not None

    async def remove(self, user_id: int, listing_id: int) -> None:
        await self._session.execute(
            delete(FavoriteModel).where(
                FavoriteModel.user_id == user_id,
                FavoriteModel.listing_id == listing_id,
            )
        )

    async def list_for_user(self, user_id: int, *, limit: int = 50) -> Sequence[Listing]:
        models = await self._session.scalars(
            select(ListingModel)
            .join(FavoriteModel, FavoriteModel.listing_id == ListingModel.id)
            .where(FavoriteModel.user_id == user_id)
            .order_by(FavoriteModel.created_at.desc())
            .limit(limit)
        )
        return [listing_to_entity(model) for model in models]


class SqlAlchemyBlockedSellerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def block(
        self, user_id: int, marketplace: str, seller_id: str, seller_name: str | None = None
    ) -> bool:
        stmt = (
            insert(BlockedSellerModel)
            .values(
                user_id=user_id,
                marketplace=marketplace,
                seller_id=seller_id,
                seller_name=seller_name,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "marketplace", "seller_id"])
            .returning(BlockedSellerModel.seller_id)
        )
        return (await self._session.scalar(stmt)) is not None

    async def unblock(self, user_id: int, marketplace: str, seller_id: str) -> None:
        await self._session.execute(
            delete(BlockedSellerModel).where(
                BlockedSellerModel.user_id == user_id,
                BlockedSellerModel.marketplace == marketplace,
                BlockedSellerModel.seller_id == seller_id,
            )
        )

    async def is_blocked(self, user_id: int, marketplace: str, seller_id: str) -> bool:
        return bool(
            await self._session.scalar(
                select(
                    exists().where(
                        BlockedSellerModel.user_id == user_id,
                        BlockedSellerModel.marketplace == marketplace,
                        BlockedSellerModel.seller_id == seller_id,
                    )
                )
            )
        )

    async def list_for_user(self, user_id: int) -> Sequence[BlockedSeller]:
        models = await self._session.scalars(
            select(BlockedSellerModel)
            .where(BlockedSellerModel.user_id == user_id)
            .order_by(BlockedSellerModel.created_at.desc())
        )
        return [
            BlockedSeller(
                marketplace=model.marketplace,
                seller_id=model.seller_id,
                seller_name=model.seller_name,
                created_at=model.created_at,
            )
            for model in models
        ]

    async def blocked_ids(self, user_id: int, marketplace: str) -> frozenset[str]:
        """Все забаненные продавцы одной площадки — чтобы отфильтровать пачку объявлений сразу."""
        rows = await self._session.scalars(
            select(BlockedSellerModel.seller_id).where(
                BlockedSellerModel.user_id == user_id,
                BlockedSellerModel.marketplace == marketplace,
            )
        )
        return frozenset(rows)
