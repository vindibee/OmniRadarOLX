"""Один цикл мониторинга: опрос площадок → сохранение → доставка новых объявлений."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.domain.entities import Listing, Subscription
from app.domain.errors import NotificationError, RecipientUnavailableError
from app.services.interfaces import Notifier, UnitOfWorkFactory
from app.services.parsers.errors import ParserError
from app.services.parsers.registry import ParserRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MonitoringOptions:
    concurrency: int = 3
    publish_grace: timedelta = timedelta(minutes=10)
    max_delivery_attempts: int = 3
    delivery_batch_size: int = 20


@dataclass(slots=True)
class CycleStats:
    subscriptions: int = 0
    searches: int = 0
    failed_searches: int = 0
    new_listings: int = 0
    sent: int = 0
    failed_deliveries: int = 0
    deactivated_users: set[int] = field(default_factory=set)


class MonitoringService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        parsers: ParserRegistry,
        notifier: Notifier,
        options: MonitoringOptions,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._uow_factory = uow_factory
        self._parsers = parsers
        self._notifier = notifier
        self._options = options
        self._clock = clock

    async def run_cycle(self) -> CycleStats:
        stats = CycleStats()
        async with self._uow_factory() as uow:
            subscriptions = await uow.subscriptions.list_active(self._parsers.codes)
        stats.subscriptions = len(subscriptions)

        # Одинаковые запросы разных пользователей выполняем один раз — меньше нагрузка и риск бана.
        groups: dict[tuple[str, str], list[Subscription]] = defaultdict(list)
        for subscription in subscriptions:
            groups[_search_key(subscription)].append(subscription)

        semaphore = asyncio.Semaphore(self._options.concurrency)

        async def process_group(group: list[Subscription]) -> None:
            async with semaphore:
                await self._process_group(group, stats)

        async with asyncio.TaskGroup() as tg:
            for group in groups.values():
                tg.create_task(process_group(group))
        return stats

    async def _process_group(self, group: Sequence[Subscription], stats: CycleStats) -> None:
        head = group[0]
        stats.searches += 1
        try:
            listings = await self._parsers.get(head.marketplace).search(
                head.criteria, since=self._since(group)
            )
        except ParserError as exc:
            stats.failed_searches += 1
            logger.warning("Поиск '%s' на %s не удался: %s", head.title, head.marketplace, exc)
            return
        except Exception:
            # Ошибка одного парсера не должна останавливать весь цикл.
            stats.failed_searches += 1
            logger.exception("Непредвиденная ошибка парсера %s", head.marketplace)
            return

        for subscription in group:
            try:
                await self._store(subscription, listings, stats)
                await self._deliver(subscription, stats)
            except Exception:
                logger.exception("Ошибка обработки фильтра #%d", subscription.id)

    def _since(self, group: Sequence[Subscription]) -> datetime | None:
        """Насколько глубоко парсеру листать выдачу: до самой ранней «последней проверки» группы.

        Фильтры, которые ещё ни разу не проверялись, границу не задают: для них достаточно первой
        страницы — история всё равно не отправляется, а помечается «виденной».
        """
        checked = [s.last_checked_at for s in group if s.last_checked_at is not None]
        if not checked:
            return None
        return min(checked) - self._options.publish_grace

    async def _store(
        self, subscription: Subscription, listings: Sequence[Listing], stats: CycleStats
    ) -> None:
        now = self._clock()
        async with self._uow_factory() as uow:
            ids = await uow.listings.upsert_many(listings)
            fresh: list[int] = []
            stale: list[int] = []
            for listing in listings:
                listing_id = ids[(listing.marketplace, listing.external_id)]
                (fresh if self._is_fresh(subscription, listing) else stale).append(listing_id)

            stats.new_listings += await uow.deliveries.add_many(subscription.id, fresh)
            # Старые объявления запоминаем как «виденные», чтобы не отправить их позже.
            await uow.deliveries.add_many(subscription.id, stale, skip_delivery=True)
            await uow.subscriptions.mark_checked(subscription.id, now)
            await uow.commit()

    def _is_fresh(self, subscription: Subscription, listing: Listing) -> bool:
        if listing.published_at is None:
            # Без даты публикации полагаемся только на дедупликацию, но не в первом прогоне.
            return subscription.last_checked_at is not None
        return listing.published_at >= subscription.created_at - self._options.publish_grace

    async def _deliver(self, subscription: Subscription, stats: CycleStats) -> None:
        async with self._uow_factory() as uow:
            pending = await uow.deliveries.get_pending(
                subscription.id,
                max_attempts=self._options.max_delivery_attempts,
                limit=self._options.delivery_batch_size,
            )
            for item in pending:
                try:
                    await self._notifier.send_listing(
                        subscription.user_id, subscription, item.listing
                    )
                except RecipientUnavailableError:
                    logger.info(
                        "Пользователь %d недоступен, отключаю его фильтры", subscription.user_id
                    )
                    await uow.users.set_active(subscription.user_id, False)
                    await uow.subscriptions.deactivate_for_user(subscription.user_id)
                    await uow.commit()
                    stats.deactivated_users.add(subscription.user_id)
                    return
                except NotificationError as exc:
                    logger.warning("Не удалось отправить объявление %d: %s", item.listing_id, exc)
                    await uow.deliveries.register_failure(item.subscription_id, item.listing_id)
                    stats.failed_deliveries += 1
                else:
                    await uow.deliveries.mark_sent(
                        item.subscription_id, item.listing_id, self._clock()
                    )
                    stats.sent += 1
                # Фиксируем после каждого сообщения: при сбое уже отправленное не уйдёт повторно.
                await uow.commit()


def _search_key(subscription: Subscription) -> tuple[str, str]:
    return subscription.marketplace, json.dumps(subscription.criteria.to_json(), sort_keys=True)
