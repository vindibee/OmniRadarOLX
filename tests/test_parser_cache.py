"""Кэш парсера: одинаковые запросы не превращаются в несколько походов на площадку."""

import asyncio
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.entities import Listing, SearchCriteria
from app.services.parsers.base import MarketplaceParser
from app.services.parsers.cache import CacheOptions, CachingParser

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
OPTIONS = CacheOptions(
    ttl_seconds=60, lock_ttl_seconds=10, wait_seconds=1, poll_interval_seconds=0.01
)


class FakeCache:
    """Словарь вместо Redis: TTL не имитируем, проверяем логику попаданий и блокировки."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ttl_seconds: float) -> None:
        self.data[key] = value

    async def add(self, key: str, value: str, ttl_seconds: float) -> bool:
        if key in self.data:
            return False
        self.data[key] = value
        return True

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


class CountingParser(MarketplaceParser):
    code = "fake"
    title = "Fake"

    def __init__(self, *, delay: float = 0.0) -> None:
        self.calls = 0
        self._delay = delay

    async def search(
        self, criteria: SearchCriteria, *, since: datetime | None = None
    ) -> Sequence[Listing]:
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        return [
            Listing(
                marketplace=self.code,
                external_id="1",
                url="https://example.com/1",
                title="iPhone 13",
                price=Decimal("15500.00"),
                currency="UAH",
                published_at=NOW,
                attributes={"params": {"state": "used"}},
            )
        ]


def _caching(inner: MarketplaceParser, cache: FakeCache) -> CachingParser:
    return CachingParser(inner, cache, OPTIONS)


async def test_second_identical_search_is_served_from_cache() -> None:
    inner = CountingParser()
    parser = _caching(inner, FakeCache())
    criteria = SearchCriteria(query="iphone 13")

    first = await parser.search(criteria)
    second = await parser.search(criteria)

    assert inner.calls == 1, "второй одинаковый запрос не должен идти на площадку"
    assert [item.external_id for item in second] == [item.external_id for item in first]
    assert second[0].price == Decimal("15500.00"), "Decimal и datetime переживают сериализацию"
    assert second[0].published_at == NOW


async def test_different_criteria_are_cached_separately() -> None:
    inner = CountingParser()
    parser = _caching(inner, FakeCache())

    await parser.search(SearchCriteria(query="iphone 13"))
    await parser.search(SearchCriteria(query="iphone 14"))
    await parser.search(SearchCriteria(query="iphone 13", price_max=Decimal(20000)))

    assert inner.calls == 3


async def test_deeper_crawl_is_not_served_from_a_shallower_cache() -> None:
    inner = CountingParser()
    parser = _caching(inner, FakeCache())
    criteria = SearchCriteria(query="iphone 13")

    await parser.search(criteria)  # одна страница, since=None
    await parser.search(criteria, since=NOW - timedelta(hours=6))  # нужен обход глубже
    await parser.search(criteria, since=NOW - timedelta(hours=1))  # мельче — уже покрыт

    assert inner.calls == 2


async def test_simultaneous_requests_hit_the_site_once() -> None:
    inner = CountingParser(delay=0.05)
    parser = _caching(inner, FakeCache())
    criteria = SearchCriteria(query="iphone 13")

    results = await asyncio.gather(*(parser.search(criteria) for _ in range(5)))

    assert inner.calls == 1, "пятеро ждут результат первого, а не идут на площадку сами"
    assert all(len(result) == 1 for result in results)


async def test_lock_is_released_when_the_parser_fails() -> None:
    class FailingParser(CountingParser):
        async def search(
            self, criteria: SearchCriteria, *, since: datetime | None = None
        ) -> Sequence[Listing]:
            self.calls += 1
            raise RuntimeError("площадка недоступна")

    inner = FailingParser()
    cache = FakeCache()
    parser = _caching(inner, cache)
    criteria = SearchCriteria(query="iphone 13")

    for _ in range(2):
        with suppress(RuntimeError):
            await parser.search(criteria)

    assert inner.calls == 2, "упавший запрос не должен блокировать следующий"
    assert not [key for key in cache.data if key.endswith(":lock")]
