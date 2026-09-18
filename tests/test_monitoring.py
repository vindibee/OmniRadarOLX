from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.database.models import DeliveryModel, SubscriptionModel, UserModel
from app.domain.entities import Listing, SearchCriteria
from app.domain.errors import (
    NotificationError,
    RecipientUnavailableError,
    SubscriptionLimitExceededError,
    SubscriptionNotFoundError,
)
from app.services.monitoring import MonitoringOptions, MonitoringService
from app.services.parsers import ParserRegistry
from app.services.subscriptions import SubscriptionService
from tests.conftest import FakeNotifier, FakeParser, UowFactory


def listing(external_id: str, *, age: timedelta = timedelta(0)) -> Listing:
    return Listing(
        marketplace="fake",
        external_id=external_id,
        url=f"https://fake.example/{external_id}",
        title=f"Объявление {external_id}",
        published_at=datetime.now(UTC) - age,
        attributes={"params": {"state": "used"}},
    )


@pytest.fixture
def parser() -> FakeParser:
    return FakeParser()


@pytest.fixture
def notifier() -> FakeNotifier:
    return FakeNotifier()


@pytest.fixture
def subscriptions(uow_factory: UowFactory, parser: FakeParser) -> SubscriptionService:
    return SubscriptionService(uow_factory, ParserRegistry([parser]), max_subscriptions_per_user=2)


@pytest.fixture
def monitoring(
    uow_factory: UowFactory, parser: FakeParser, notifier: FakeNotifier
) -> MonitoringService:
    return MonitoringService(
        uow_factory,
        ParserRegistry([parser]),
        notifier,
        MonitoringOptions(max_delivery_attempts=2, publish_grace=timedelta(minutes=10)),
    )


async def create_subscription(service: SubscriptionService, user_id: int, query: str) -> int:
    await service.register_user(user_id, f"user{user_id}", f"User {user_id}")
    return (await service.create(user_id, "fake", SearchCriteria(query=query))).id


async def test_new_listing_is_delivered_exactly_once(
    subscriptions: SubscriptionService,
    monitoring: MonitoringService,
    parser: FakeParser,
    notifier: FakeNotifier,
    uow_factory: UowFactory,
) -> None:
    await create_subscription(subscriptions, 1, "iphone")
    parser.results["iphone"] = [listing("new"), listing("old-promoted", age=timedelta(days=30))]

    first = await monitoring.run_cycle()
    second = await monitoring.run_cycle()

    assert notifier.sent == [(1, "new")], "старое поднятое объявление не должно приходить"
    assert (first.sent, second.sent) == (1, 0)
    assert second.new_listings == 0

    async with uow_factory() as uow:
        session = uow._require_session()  # type: ignore[attr-defined]
        deliveries = await session.scalar(select(func.count()).select_from(DeliveryModel))
    assert deliveries == 2, "старое объявление запомнено как «виденное»"


async def test_same_search_is_requested_once_for_all_users(
    subscriptions: SubscriptionService,
    monitoring: MonitoringService,
    parser: FakeParser,
    notifier: FakeNotifier,
) -> None:
    await create_subscription(subscriptions, 1, "bike")
    await create_subscription(subscriptions, 2, "bike")
    parser.results["bike"] = [listing("b1")]

    stats = await monitoring.run_cycle()

    assert len(parser.calls) == 1
    assert stats.searches == 1
    assert sorted(notifier.sent) == [(1, "b1"), (2, "b1")]


async def test_parser_failure_does_not_stop_other_searches(
    subscriptions: SubscriptionService,
    monitoring: MonitoringService,
    parser: FakeParser,
    notifier: FakeNotifier,
) -> None:
    await create_subscription(subscriptions, 1, "broken")
    await create_subscription(subscriptions, 1, "working")
    parser.failing_queries.add("broken")
    parser.results["working"] = [listing("w1")]

    stats = await monitoring.run_cycle()

    assert stats.failed_searches == 1
    assert notifier.sent == [(1, "w1")]


async def test_temporary_notification_failure_is_retried_limited_times(
    subscriptions: SubscriptionService,
    monitoring: MonitoringService,
    parser: FakeParser,
    notifier: FakeNotifier,
) -> None:
    await create_subscription(subscriptions, 1, "tv")
    parser.results["tv"] = [listing("t1")]
    notifier.error = NotificationError("network down")

    stats = [await monitoring.run_cycle() for _ in range(3)]

    assert [s.failed_deliveries for s in stats] == [1, 1, 0], "после max_delivery_attempts — стоп"


async def test_unavailable_recipient_is_deactivated(
    subscriptions: SubscriptionService,
    monitoring: MonitoringService,
    parser: FakeParser,
    notifier: FakeNotifier,
    uow_factory: UowFactory,
) -> None:
    await create_subscription(subscriptions, 1, "sofa")
    parser.results["sofa"] = [listing("s1")]
    notifier.error = RecipientUnavailableError("bot was blocked by the user")

    stats = await monitoring.run_cycle()
    next_stats = await monitoring.run_cycle()

    assert stats.deactivated_users == {1}
    assert next_stats.subscriptions == 0
    async with uow_factory() as uow:
        session = uow._require_session()  # type: ignore[attr-defined]
        assert await session.scalar(select(UserModel.is_active).where(UserModel.id == 1)) is False
        assert await session.scalar(select(SubscriptionModel.is_active)) is False


async def test_subscription_service_limits_and_ownership(
    subscriptions: SubscriptionService,
) -> None:
    sub_id = await create_subscription(subscriptions, 1, "a")
    await subscriptions.create(1, "fake", SearchCriteria(query="b"))

    with pytest.raises(SubscriptionLimitExceededError):
        await subscriptions.create(1, "fake", SearchCriteria(query="c"))

    await subscriptions.register_user(2, None, "Stranger")
    with pytest.raises(SubscriptionNotFoundError):
        await subscriptions.delete(2, sub_id)

    toggled = await subscriptions.toggle(1, sub_id)
    assert toggled.is_active is False
    assert toggled.criteria == SearchCriteria(query="a")

    await subscriptions.delete(1, sub_id)
    assert [s.criteria.query for s in await subscriptions.list(1)] == ["b"]


async def _last_checked_at(uow_factory: UowFactory) -> datetime:
    async with uow_factory() as uow:
        session = uow._require_session()  # type: ignore[attr-defined]
        value = await session.scalar(select(SubscriptionModel.last_checked_at))
    assert isinstance(value, datetime)
    return value


async def test_pagination_boundary_grows_from_last_check(
    subscriptions: SubscriptionService,
    monitoring: MonitoringService,
    parser: FakeParser,
    uow_factory: UowFactory,
) -> None:
    await create_subscription(subscriptions, 1, "car")
    parser.results["car"] = [listing("c1")]

    await monitoring.run_cycle()  # первый прогон: границы нет, парсеру хватит одной страницы
    after_first = await _last_checked_at(uow_factory)
    await monitoring.run_cycle()

    assert parser.since_calls[0] is None
    assert parser.since_calls[1] == after_first - timedelta(minutes=10), "проверка минус grace"


async def test_pagination_boundary_covers_the_oldest_check_in_group(
    subscriptions: SubscriptionService,
    monitoring: MonitoringService,
    parser: FakeParser,
) -> None:
    """Один запрос обслуживает несколько фильтров — листать надо под самый отстающий из них."""
    await create_subscription(subscriptions, 1, "bike")
    parser.results["bike"] = [listing("b1")]
    await monitoring.run_cycle()

    await create_subscription(subscriptions, 2, "bike")
    await monitoring.run_cycle()
    await monitoring.run_cycle()

    stale = parser.since_calls[1]
    assert stale is not None, "новый фильтр не должен обнулять глубину обхода для старого"
    assert parser.since_calls[2] is not None and parser.since_calls[2] > stale
