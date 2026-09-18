"""Биллинг на реальной БД: триал один раз, продление не сжигает остаток, платёж идемпотентен."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.entities import User
from app.domain.errors import TrialAlreadyUsedError
from app.domain.tariffs import Tariff
from app.services.billing import BillingOptions, BillingService
from tests.conftest import UowFactory

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


@pytest.fixture
async def billing(uow_factory: UowFactory) -> BillingService:
    async with uow_factory() as uow:
        await uow.users.upsert(User(id=1, username="u1", full_name="User"))
        await uow.commit()
    return BillingService(uow_factory, BillingOptions(), clock=lambda: NOW)


async def test_trial_is_granted_once(billing: BillingService) -> None:
    before = await billing.access(1)
    trial = await billing.activate_trial(1)
    after = await billing.access(1)

    assert before.is_allowed is False and before.trial_available is True
    assert trial.is_trial and trial.ends_at == NOW + timedelta(days=7)
    assert after.is_allowed is True and after.tariff is Tariff.TRIAL
    assert after.trial_available is False

    with pytest.raises(TrialAlreadyUsedError):
        await billing.activate_trial(1)


async def test_payment_extends_from_the_end_of_current_period(billing: BillingService) -> None:
    await billing.activate_trial(1)  # 7 дней с NOW

    paid = await billing.activate_paid(1, Tariff.MONTH, provider="stars", payment_id="p-1")

    assert paid.starts_at == NOW + timedelta(days=7), "оплата не должна сжигать остаток триала"
    assert paid.ends_at == NOW + timedelta(days=37)
    access = await billing.access(1)
    assert access.until == paid.ends_at, "доступ — до конца цепочки периодов"
    assert access.tariff is Tariff.TRIAL, "сейчас действует ещё триал"


async def test_repeated_payment_notification_does_not_extend_twice(
    billing: BillingService,
) -> None:
    first = await billing.activate_paid(1, Tariff.WEEK, provider="cryptobot", payment_id="inv-7")
    second = await billing.activate_paid(1, Tariff.WEEK, provider="cryptobot", payment_id="inv-7")

    assert second.id == first.id, "повтор вебхука — та же подписка, а не новый период"
    assert (await billing.access(1)).until == first.ends_at


async def test_access_expires(billing: BillingService, uow_factory: UowFactory) -> None:
    await billing.activate_paid(1, Tariff.DAY, provider="stars", payment_id="p-day")

    tomorrow = BillingService(uow_factory, BillingOptions(), clock=lambda: NOW + timedelta(days=2))

    assert (await tomorrow.access(1)).is_allowed is False


def test_long_tariffs_are_cheaper_per_day(billing: BillingService) -> None:
    day = billing.offer(Tariff.DAY)
    month = billing.offer(Tariff.MONTH)
    year = billing.offer(Tariff.YEAR)

    assert (day.discount_percent, month.discount_percent, year.discount_percent) == (0, 10, 30)
    assert month.price_stars == 1350, "30 дней по 50 звёзд минус 10%"
    assert year.price_usd == Decimal("255.50"), "365 дней по $1.00 минус 30%"
    assert year.price_usd / year.days < day.price_usd
