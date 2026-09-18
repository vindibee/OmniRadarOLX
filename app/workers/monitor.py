import asyncio
import logging
import time
from contextlib import suppress

from app.services.monitoring import MonitoringService

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
    ) -> None:
        self._service = service
        self._interval = interval_seconds
        self._max_backoff = max_backoff_seconds

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
                logger.info(
                    "Цикл за %.1f c: фильтров %d, запросов %d (ошибок %d), новых %d, "
                    "отправлено %d (ошибок %d)",
                    elapsed,
                    stats.subscriptions,
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
