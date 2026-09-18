"""Избранное, бан-лист продавцов и виджет прозрачности."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.database.models import ListingModel
from app.domain.entities import Listing, SearchCriteria, User
from app.repositories.listings import to_entity
from app.services.collections import CollectionService
from app.services.monitoring import MonitoringOptions, MonitoringService
from app.services.parsers import ParserRegistry
from app.services.status import StatusService
from tests.conftest import FakeNotifier, FakeParser, UowFactory

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def listing(external_id: str, *, seller: str | None = None, price: str = "1000") -> Listing:
    return Listing(
        marketplace="fake",
        external_id=external_id,
        url=f"https://fake.example/{external_id}",
        title=f"Объявление {external_id}",
        price=Decimal(price),
        seller_id=seller,
        seller_name=f"Продавец {seller}" if seller else None,
        published_at=NOW,
    )


class FakeCache:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ttl_seconds: float) -> None:
        self.data[key] = value

    async def add(self, key: str, value: str, ttl_seconds: float) -> bool:
        return self.data.setdefault(key, value) == value

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


@pytest.fixture
async def user_id(uow_factory: UowFactory) -> int:
    async with uow_factory() as uow:
        await uow.users.upsert(User(id=1, username="u", full_name="U"))
        await uow.commit()
    return 1


@pytest.fixture
def collections(uow_factory: UowFactory) -> CollectionService:
    return CollectionService(uow_factory)


async def _store(uow_factory: UowFactory, items: list[Listing]) -> dict[tuple[str, str], int]:
    async with uow_factory() as uow:
        ids = await uow.listings.upsert_many(items)
        await uow.commit()
    return ids


async def _read(uow_factory: UowFactory, external_id: str) -> Listing:
    async with uow_factory() as uow:
        session = uow._require_session()  # type: ignore[attr-defined]
        model = await session.scalar(
            select(ListingModel).where(ListingModel.external_id == external_id)
        )
        assert model is not None
        return to_entity(model)


# ---------- Избранное ----------


async def test_favorite_is_saved_once_and_listed(
    collections: CollectionService, uow_factory: UowFactory, user_id: int
) -> None:
    ids = await _store(uow_factory, [listing("a"), listing("b")])

    first = await collections.add_favorite(user_id, ids[("fake", "a")])
    again = await collections.add_favorite(user_id, ids[("fake", "a")])
    saved = await collections.favorites(user_id)

    assert first is True, "добавлено этим нажатием"
    assert again is False, "повторное нажатие не создаёт дубль"
    assert [item.external_id for item in saved] == ["a"]


async def test_favorite_can_be_removed(
    collections: CollectionService, uow_factory: UowFactory, user_id: int
) -> None:
    ids = await _store(uow_factory, [listing("a")])
    await collections.add_favorite(user_id, ids[("fake", "a")])

    await collections.remove_favorite(user_id, ids[("fake", "a")])

    assert await collections.favorites(user_id) == []


# ---------- Бан-лист ----------


async def test_blocked_seller_disappears_from_new_finds(
    collections: CollectionService, uow_factory: UowFactory, user_id: int
) -> None:
    """Скрытый продавец не должен попадать даже в историю фильтра."""
    parser = FakeParser()
    notifier = FakeNotifier()
    monitoring = MonitoringService(
        uow_factory, ParserRegistry([parser]), notifier, MonitoringOptions()
    )
    async with uow_factory() as uow:
        created = await uow.filters.add(
            user_id=user_id,
            marketplace="fake",
            title="тест",
            criteria=SearchCriteria(query="spam"),
        )
        await uow.commit()
    parser.results["spam"] = [listing("good", seller="10"), listing("spam", seller="99")]

    await collections.block_seller(user_id, "fake", "99", "Спамер")
    await monitoring.run_cycle()

    async with uow_factory() as uow:
        history = await uow.deliveries.history(created.id)
    assert [item.listing.external_id for item in history] == ["good"]
    assert [seller.seller_id for seller in await collections.blocked_sellers(user_id)] == ["99"]


async def test_seller_can_be_unblocked(collections: CollectionService, user_id: int) -> None:
    await collections.block_seller(user_id, "fake", "99", "Спамер")

    await collections.unblock_seller(user_id, "fake", "99")

    assert await collections.blocked_sellers(user_id) == []


# ---------- Динамика цены ----------


async def test_previous_price_is_remembered_only_when_it_changes(
    uow_factory: UowFactory,
) -> None:
    """Уведомление показывает «было → стало», поэтому прошлую цену надо сохранить."""
    await _store(uow_factory, [listing("a", price="12000")])

    await _store(uow_factory, [listing("a", price="10500")])
    cheaper = await _read(uow_factory, "a")
    await _store(uow_factory, [listing("a", price="10500")])
    unchanged = await _read(uow_factory, "a")

    assert (cheaper.price, cheaper.previous_price) == (Decimal("10500.00"), Decimal("12000.00"))
    assert cheaper.price_drop_percent == 12
    assert unchanged.previous_price == Decimal("12000.00"), "повтор той же цены ничего не стирает"


# ---------- Виджет прозрачности ----------


async def test_status_reports_a_live_worker_and_todays_finds(
    uow_factory: UowFactory, user_id: int
) -> None:
    # Часы здесь настоящие: «найдено за сегодня» считается от начала текущих суток,
    # а доставки БД проставляет своим now().
    cache = FakeCache()
    status = StatusService(uow_factory, interval_seconds=60, cache=cache)
    ids = await _store(uow_factory, [listing("a"), listing("b")])
    async with uow_factory() as uow:
        created = await uow.filters.add(
            user_id=user_id, marketplace="fake", title="т", criteria=SearchCriteria(query="q")
        )
        await uow.deliveries.add_many(created.id, list(ids.values()))
        await uow.commit()

    await status.heartbeat(cycle_seconds=1.5, filters=3)
    reported = await status.status(user_id)

    assert reported.is_running is True
    assert reported.seconds_ago is not None and reported.seconds_ago < 5
    assert (reported.last_cycle_seconds, reported.filters_checked) == (1.5, 3)
    assert reported.found_today == 2


async def test_status_goes_dark_when_the_worker_stops(
    uow_factory: UowFactory, user_id: int
) -> None:
    cache = FakeCache()
    stale = StatusService(uow_factory, interval_seconds=60, cache=cache, clock=lambda: NOW)
    await stale.heartbeat(cycle_seconds=1.0, filters=1)

    later = StatusService(
        uow_factory,
        interval_seconds=60,
        cache=cache,
        clock=lambda: NOW + timedelta(minutes=10),
    )
    reported = await later.status(user_id)

    assert reported.is_running is False, "отметка протухла — мониторинг не считается живым"
    assert reported.seconds_ago == 600


async def test_status_without_cache_says_unknown(uow_factory: UowFactory, user_id: int) -> None:
    status = StatusService(uow_factory, interval_seconds=60, cache=None, clock=lambda: NOW)

    reported = await status.status(user_id)

    assert (reported.is_running, reported.last_run_at) == (False, None)
