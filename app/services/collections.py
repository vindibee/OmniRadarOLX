"""Избранное и бан-лист продавцов: то, что пользователь копит кнопками под уведомлениями."""

from collections.abc import Sequence

from app.domain.entities import BlockedSeller, Listing
from app.services.interfaces import UnitOfWorkFactory


class CollectionService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def add_favorite(self, user_id: int, listing_id: int) -> bool:
        """``True`` — добавлено сейчас, ``False`` — уже было в избранном."""
        async with self._uow_factory() as uow:
            added = await uow.favorites.add(user_id, listing_id)
            await uow.commit()
        return added

    async def remove_favorite(self, user_id: int, listing_id: int) -> None:
        async with self._uow_factory() as uow:
            await uow.favorites.remove(user_id, listing_id)
            await uow.commit()

    async def favorites(self, user_id: int, *, limit: int = 50) -> Sequence[Listing]:
        async with self._uow_factory() as uow:
            return await uow.favorites.list_for_user(user_id, limit=limit)

    async def block_seller(
        self, user_id: int, marketplace: str, seller_id: str, seller_name: str | None = None
    ) -> bool:
        async with self._uow_factory() as uow:
            blocked = await uow.blocked_sellers.block(user_id, marketplace, seller_id, seller_name)
            await uow.commit()
        return blocked

    async def unblock_seller(self, user_id: int, marketplace: str, seller_id: str) -> None:
        async with self._uow_factory() as uow:
            await uow.blocked_sellers.unblock(user_id, marketplace, seller_id)
            await uow.commit()

    async def blocked_sellers(self, user_id: int) -> Sequence[BlockedSeller]:
        async with self._uow_factory() as uow:
            return await uow.blocked_sellers.list_for_user(user_id)
