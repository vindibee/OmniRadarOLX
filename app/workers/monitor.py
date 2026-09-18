import asyncio
import logging
import time
from contextlib import suppress

from app.services.monitoring import MonitoringService
from app.services.status import StatusService

logger = logging.getLogger(__name__)


class MonitoringWorker:
    """Бесконечный цикл мониторинга с graceful degradation.

    Любая ошибка цикла (БД недоступна, сеть упала) логируется, после чего воркер
    ждёт с растущей паузой и пробует снова — процесс бота при этом не падает.
    """

    def __init__(
        self,
        service: MonitoringService,
        *,
        interval_seconds: float,
        max_backoff_seconds: float = 600.0,
        status: StatusService | None = None,
    ) -> None:
        self._service = service
        self._interval = interval_seconds
        self._max_backoff = max_backoff_seconds
        self._status = status

    async def run(self, stop_event: asyncio.Event) -> None:
        logger.info("Мониторинг запущен, интервал %.0f c", self._interval)
        consecutive_failures = 0

        while not stop_event.is_set():
            started = time.monotonic()
            try:
                stats = await self._service.run_cycle()
            except Exception:
                consecutive_failures += 1
                delay = min(self._interval * 2**consecutive_failures, self._max_backoff)
                logger.exception(
                    "Цикл мониторинга упал (%d подряд), повтор через %.0f c",
                    consecutive_failures,
                    delay,
                )
            else:
                consecutive_failures = 0
                elapsed = time.monotonic() - started
                delay = max(self._interval - elapsed, 0.0)
                if self._status is not None:
                    # Отметка живости для виджета в Mini App; её потеря цикл не ломает.
                    with suppress(Exception):
                        await self._status.heartbeat(
                            cycle_seconds=round(elapsed, 2), filters=stats.filters
                        )
                logger.info(
                    "Цикл за %.1f c: фильтров %d, запросов %d (ошибок %d), новых %d, "
                    "отправлено %d (ошибок %d)",
                    elapsed,
                    stats.filters,
                    stats.searches,
                    stats.failed_searches,
                    stats.new_listings,
                    stats.sent,
                    stats.failed_deliveries,
                )

            # Ждём паузу, но просыпаемся сразу, если пришёл сигнал остановки.
            with suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=delay)

        logger.info("Мониторинг остановлен")
