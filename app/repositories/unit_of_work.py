from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.deliveries import SqlAlchemyDeliveryRepository
from app.repositories.filters import SqlAlchemyFilterRepository
from app.repositories.listings import SqlAlchemyListingRepository
from app.repositories.subscriptions import SqlAlchemySubscriptionRepository
from app.repositories.users import SqlAlchemyUserRepository


class SqlAlchemyUnitOfWork:
    """Одна сессия (транзакция) на блок ``async with``.

    Изменения фиксируются только явным ``commit()``; при выходе всё незафиксированное
    откатывается — в том числе при исключении.
    """

    users: SqlAlchemyUserRepository
    filters: SqlAlchemyFilterRepository
    subscriptions: SqlAlchemySubscriptionRepository
    listings: SqlAlchemyListingRepository
    deliveries: SqlAlchemyDeliveryRepository

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> Self:
        self._session = self._session_factory()
        self.users = SqlAlchemyUserRepository(self._session)
        self.filters = SqlAlchemyFilterRepository(self._session)
        self.subscriptions = SqlAlchemySubscriptionRepository(self._session)
        self.listings = SqlAlchemyListingRepository(self._session)
        self.deliveries = SqlAlchemyDeliveryRepository(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        session = self._require_session()
        try:
            await session.rollback()
        finally:
            await session.close()
            self._session = None

    async def commit(self) -> None:
        await self._require_session().commit()

    async def rollback(self) -> None:
        await self._require_session().rollback()

    def _require_session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("UnitOfWork используется вне блока 'async with'")
        return self._session
