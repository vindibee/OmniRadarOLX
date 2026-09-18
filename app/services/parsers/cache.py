"""Кэш выдачи площадки: один запрос — один поход на сайт, сколько бы пользователей его ни ждали.

Декоратор оборачивает любой ``MarketplaceParser`` и подставляется вместо него в реестре,
поэтому ни мониторинг, ни хендлеры о кэше не знают.

Две разные задачи решаются здесь одновременно:

* **Повторный запрос** в пределах TTL (по умолчанию ~90 c) берётся из кэша — площадка
  не видит одинаковых обращений от разных процессов и циклов.
* **Одновременный запрос** (два воркера стартанули в одну секунду) не превращается в два
  похода: первый берёт блокировку и парсит, второй ждёт его результат в кэше.

Глубина учитывается честно: обход с ``since`` раньше запрошенного покрывает запрос
(в нём страниц больше), обратное — нет, иначе мы отдали бы неполную выдачу.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.domain.entities import Listing, SearchCriteria
from app.services.interfaces import Cache
from app.services.parsers.base import MarketplaceParser

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CacheOptions:
    ttl_seconds: float = 90.0
    # Сколько живёт блокировка, если парсер умрёт, не сняв её.
    lock_ttl_seconds: float = 30.0
    # Сколько ждать чужой результат, прежде чем идти на площадку самому.
    wait_seconds: float = 5.0
    poll_interval_seconds: float = 0.25


class CachingParser(MarketplaceParser):
    """Прозрачная обёртка: тот же контракт, что у обычного парсера."""

    def __init__(self, inner: MarketplaceParser, cache: Cache, options: CacheOptions) -> None:
        self._inner = inner
        self._cache = cache
        self._options = options
        # code/title — часть контракта реестра, берём у обёрнутого парсера.
        self.code = inner.code
        self.title = inner.title

    async def search(
        self, criteria: SearchCriteria, *, since: datetime | None = None
    ) -> Sequence[Listing]:
        key = _cache_key(self.code, criteria)
        cached = await self._read(key, since)
        if cached is not None:
            logger.debug("Кэш: %s '%s' — попадание", self.code, criteria.query)
            return cached

        lock_key = f"{key}:lock"
        if not await self._cache.add(lock_key, "1", self._options.lock_ttl_seconds):
            waited = await self._wait_for_other(key, since)
            if waited is not None:
                return waited
            logger.debug("Кэш: не дождался чужого результата, иду на площадку сам")
            return await self._inner.search(criteria, since=since)

        try:
            listings = await self._inner.search(criteria, since=since)
            await self._cache.set(key, _dump(listings, since), self._options.ttl_seconds)
            return listings
        finally:
            # Блокировку снимаем всегда: иначе ошибка парсера заморозит запрос на lock_ttl.
            await self._cache.delete(lock_key)

    def validate_criteria(self, criteria: SearchCriteria) -> None:
        self._inner.validate_criteria(criteria)

    async def aclose(self) -> None:
        await self._inner.aclose()

    async def _read(self, key: str, since: datetime | None) -> list[Listing] | None:
        raw = await self._cache.get(key)
        if raw is None:
            return None
        try:
            return _load(raw, since)
        except (ValueError, KeyError, TypeError):
            # Формат кэша поменялся между версиями — считаем это промахом.
            logger.warning("Кэш: не читается запись %s, игнорирую", key)
            return None

    async def _wait_for_other(self, key: str, since: datetime | None) -> list[Listing] | None:
        deadline = asyncio.get_running_loop().time() + self._options.wait_seconds
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(self._options.poll_interval_seconds)
            cached = await self._read(key, since)
            if cached is not None:
                logger.debug("Кэш: дождался результата соседнего запроса")
                return cached
        return None


def _cache_key(marketplace: str, criteria: SearchCriteria) -> str:
    """Площадка плюс отпечаток критериев: одинаковые запросы разных пользователей совпадут."""
    payload = json.dumps(criteria.to_json(), sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"search:{marketplace}:{digest}"


def _dump(listings: Sequence[Listing], since: datetime | None) -> str:
    return json.dumps(
        {
            "since": since.isoformat() if since else None,
            "listings": [_dump_listing(item) for item in listings],
        },
        ensure_ascii=False,
    )


def _load(raw: str, since: datetime | None) -> list[Listing] | None:
    payload = json.loads(raw)
    cached_since = payload["since"]
    cached_since_at = datetime.fromisoformat(cached_since) if cached_since else None
    if not _covers(cached_since_at, since):
        return None
    return [_load_listing(item) for item in payload["listings"]]


def _covers(cached_since: datetime | None, requested_since: datetime | None) -> bool:
    """Покрывает ли сохранённый обход запрошенную глубину."""
    if requested_since is None:
        # Нужна только первая страница — её содержит любой обход.
        return True
    if cached_since is None:
        # В кэше лежит одна страница, а спрашивают глубже — данных может не хватить.
        return False
    return cached_since <= requested_since


def _dump_listing(listing: Listing) -> dict[str, Any]:
    return {
        "marketplace": listing.marketplace,
        "external_id": listing.external_id,
        "url": listing.url,
        "title": listing.title,
        "price": str(listing.price) if listing.price is not None else None,
        "currency": listing.currency,
        "location": listing.location,
        "image_url": listing.image_url,
        "published_at": listing.published_at.isoformat() if listing.published_at else None,
        "attributes": dict(listing.attributes),
    }


def _load_listing(data: dict[str, Any]) -> Listing:
    return Listing(
        marketplace=data["marketplace"],
        external_id=data["external_id"],
        url=data["url"],
        title=data["title"],
        price=Decimal(data["price"]) if data["price"] is not None else None,
        currency=data["currency"],
        location=data["location"],
        image_url=data["image_url"],
        published_at=(
            datetime.fromisoformat(data["published_at"]) if data["published_at"] else None
        ),
        attributes=data["attributes"],
    )
