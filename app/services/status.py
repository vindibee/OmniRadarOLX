"""Прозрачность: жив ли мониторинг и сколько нашёл сегодня.

Воркер после каждого успешного обхода кладёт отметку в Redis (порт ``Cache``), а Mini App
читает её через API. Redis выбран потому, что бот и Web API — разные процессы: общая память
им не подходит, а гонять запись в БД каждую минуту незачем.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.entities import WorkerStatus
from app.services.interfaces import Cache, UnitOfWorkFactory

logger = logging.getLogger(__name__)

HEARTBEAT_KEY = "monitoring:heartbeat"
# Живым считаем мониторинг, если отметка свежее двух интервалов плюс небольшой запас.
STALE_FACTOR = 2
STALE_GRACE_SECONDS = 30


@dataclass(frozen=True, slots=True)
class Heartbeat:
    at: datetime
    cycle_seconds: float
    filters: int

    def to_json(self) -> str:
        return json.dumps(
            {
                "at": self.at.isoformat(),
                "cycle_seconds": self.cycle_seconds,
                "filters": self.filters,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> Heartbeat | None:
        try:
            payload = json.loads(raw)
            return cls(
                at=datetime.fromisoformat(payload["at"]),
                cycle_seconds=float(payload["cycle_seconds"]),
                filters=int(payload["filters"]),
            )
        except (ValueError, KeyError, TypeError):
            logger.warning("Отметка мониторинга не читается — считаю, что её нет")
            return None


class StatusService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        interval_seconds: float,
        cache: Cache | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._uow_factory = uow_factory
        self._interval = interval_seconds
        self._cache = cache
        self._clock = clock

    async def heartbeat(self, *, cycle_seconds: float, filters: int) -> None:
        """Вызывается воркером после каждого успешного обхода."""
        if self._cache is None:
            return
        beat = Heartbeat(at=self._clock(), cycle_seconds=cycle_seconds, filters=filters)
        # TTL с запасом: протухшая отметка сама исчезает, и статус честно гаснет.
        ttl = self._interval * STALE_FACTOR + STALE_GRACE_SECONDS
        await self._cache.set(HEARTBEAT_KEY, beat.to_json(), ttl)

    async def status(self, user_id: int) -> WorkerStatus:
        now = self._clock()
        beat = await self._read()
        async with self._uow_factory() as uow:
            found_today = await uow.stats.found_today(user_id, _start_of_day(now))

        if beat is None:
            return WorkerStatus(
                is_running=False,
                last_run_at=None,
                seconds_ago=None,
                interval_seconds=self._interval,
                last_cycle_seconds=None,
                filters_checked=0,
                found_today=found_today,
            )
        seconds_ago = max(int((now - beat.at).total_seconds()), 0)
        return WorkerStatus(
            is_running=seconds_ago <= self._interval * STALE_FACTOR + STALE_GRACE_SECONDS,
            last_run_at=beat.at,
            seconds_ago=seconds_ago,
            interval_seconds=self._interval,
            last_cycle_seconds=beat.cycle_seconds,
            filters_checked=beat.filters,
            found_today=found_today,
        )

    async def _read(self) -> Heartbeat | None:
        if self._cache is None:
            return None
        raw = await self._cache.get(HEARTBEAT_KEY)
        return Heartbeat.from_json(raw) if raw else None


def _start_of_day(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
