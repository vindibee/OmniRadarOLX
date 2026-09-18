"""Биллинг: пробный период, продление, проверка доступа.

Сервис ничего не знает ни о Telegram Stars, ни о CryptoBot: провайдер сообщает сюда
свой идентификатор платежа, а решение «продлить и на сколько» принимает домен.
Повторный вызов с тем же ``payment_id`` ничего не продлевает — это защита от
повторной доставки вебхука.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal

from sqlalchemy.exc import IntegrityError

from app.domain.entities import Access, Subscription
from app.domain.errors import TrialAlreadyUsedError
from app.domain.tariffs import PLANS, Tariff, TariffPlan, plan
from app.services.interfaces import UnitOfWorkFactory

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BillingOptions:
    """Прайс задаётся ценой одного дня; скидки длинных тарифов живут в домене."""

    day_price_stars: int = 25
    day_price_usd: Decimal = Decimal("0.50")


@dataclass(frozen=True, slots=True)
class Offer:
    """Что показать в меню оплаты по одному тарифу."""

    tariff: Tariff
    days: int
    discount_percent: int
    price_stars: int
    price_usd: Decimal


class BillingService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        options: BillingOptions,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._uow_factory = uow_factory
        self._options = options
        self._clock = clock

    async def access(self, user_id: int) -> Access:
        """Единственный источник правды о доступе — и для бота, и для Mini App."""
        now = self._clock()
        async with self._uow_factory() as uow:
            current = await uow.subscriptions.latest_active(user_id, now)
            trial_used = await uow.subscriptions.has_trial(user_id)
            # Оплата во время действующего периода создаёт следующий, начинающийся позже.
            # Пользователю важен конец всей цепочки, а не текущего куска.
            until = await uow.subscriptions.last_ends_at(user_id)
        if current is None:
            return Access(is_allowed=False, trial_available=not trial_used)
        return Access(
            is_allowed=True,
            until=until or current.ends_at,
            tariff=current.tariff,
            is_trial=current.is_trial,
            trial_available=not trial_used,
        )

    async def activate_trial(self, user_id: int) -> Subscription:
        """Демо-доступ. Один на пользователя — это гарантирует уникальный индекс в БД."""
        now = self._clock()
        try:
            async with self._uow_factory() as uow:
                if await uow.subscriptions.has_trial(user_id):
                    raise TrialAlreadyUsedError
                trial = await uow.subscriptions.add(
                    user_id=user_id,
                    tariff=Tariff.TRIAL,
                    starts_at=now,
                    ends_at=now + plan(Tariff.TRIAL).duration,
                    is_trial=True,
                )
                await uow.commit()
        except IntegrityError as exc:
            # Два одновременных нажатия «Демо-доступ»: второе упирается в уникальный индекс.
            raise TrialAlreadyUsedError from exc
        return trial

    async def activate_paid(
        self,
        user_id: int,
        tariff: Tariff,
        *,
        provider: str,
        payment_id: str,
        amount: Decimal | None = None,
        currency: str | None = None,
    ) -> Subscription:
        """Продлевает доступ от конца текущего периода, а не от «сейчас».

        Оплата во время действующей подписки не сжигает остаток.
        """
        now = self._clock()
        async with self._uow_factory() as uow:
            existing = await uow.subscriptions.find_by_payment(provider, payment_id)
            if existing is not None:
                logger.info("Платёж %s:%s уже учтён", provider, payment_id)
                return existing

            last_ends_at = await uow.subscriptions.last_ends_at(user_id)
            starts_at = max(now, last_ends_at) if last_ends_at else now
            subscription = await uow.subscriptions.add(
                user_id=user_id,
                tariff=tariff,
                starts_at=starts_at,
                ends_at=starts_at + plan(tariff).duration,
                payment_provider=provider,
                payment_id=payment_id,
                amount=amount,
                currency=currency,
            )
            await uow.commit()
        return subscription

    def offer(self, tariff: Tariff) -> Offer:
        """Цены — чистая функция от прайса и тарифа, без обращения к БД."""
        tariff_plan: TariffPlan = PLANS[tariff]
        stars = tariff_plan.price(Decimal(self._options.day_price_stars))
        return Offer(
            tariff=tariff,
            days=tariff_plan.days,
            discount_percent=int(tariff_plan.discount * 100),
            # Stars — целые: округляем вверх, чтобы не продавать дешевле прайса.
            price_stars=int(stars.to_integral_value(ROUND_CEILING)),
            price_usd=tariff_plan.price(self._options.day_price_usd),
        )
