"""Интеграционные фикстуры: реальный PostgreSQL из docker compose, БД ``<name>_test``."""

from collections.abc import AsyncIterator, Sequence
from datetime import datetime

import asyncpg
import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import DatabaseSettings
from app.database import models  # noqa: F401
from app.database.base import Base
from app.database.session import create_engine, create_session_factory
from app.domain.entities import Filter, Listing, SearchCriteria
from app.domain.errors import NotificationError
from app.repositories import SqlAlchemyUnitOfWork
from app.services.interfaces import UnitOfWork
from app.services.parsers import MarketplaceParser, ParserError


class _TestSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_nested_delimiter="__", extra="ignore")
    db: DatabaseSettings = DatabaseSettings()


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    base = _TestSettings().db
    test_db = base.model_copy(update={"name": f"{base.name}_test"})
    password = base.password.get_secret_value()

    try:
        conn = await asyncpg.connect(
            host=base.host, port=base.port, user=base.user, password=password, database=base.name
        )
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL недоступен ({exc}); запустите: docker compose up -d db")
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", test_db.name)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{test_db.name}"')
    finally:
        await conn.close()

    engine = create_engine(test_db)
    async with engine.begin() as connection:
        # Пересоздаём схему целиком: так тесты не спотыкаются о таблицы,
        # которые остались от прежних версий моделей.
        await connection.execute(text("DROP SCHEMA public CASCADE"))
        await connection.execute(text("CREATE SCHEMA public"))
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


@pytest.fixture
def uow_factory(session_factory: async_sessionmaker[AsyncSession]) -> "UowFactory":
    return UowFactory(session_factory)


class UowFactory:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    def __call__(self) -> UnitOfWork:
        return SqlAlchemyUnitOfWork(self._session_factory)


class FakeParser(MarketplaceParser):
    code = "fake"
    title = "Fake Market"

    def __init__(self) -> None:
        self.results: dict[str, Sequence[Listing]] = {}
        self.failing_queries: set[str] = set()
        self.calls: list[SearchCriteria] = []
        self.since_calls: list[datetime | None] = []

    async def search(
        self, criteria: SearchCriteria, *, since: datetime | None = None
    ) -> Sequence[Listing]:
        self.calls.append(criteria)
        self.since_calls.append(since)
        if criteria.query in self.failing_queries:
            raise ParserError("площадка недоступна")
        return self.results.get(criteria.query, [])


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.error: NotificationError | None = None

    async def send_listing(self, chat_id: int, search_filter: Filter, listing: Listing) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append((chat_id, listing.external_id))
