"""Тарифы — чистая доменная логика: длительность, скидка, цена.

Ни Telegram, ни CryptoBot здесь не упоминаются: провайдеры оплаты берут отсюда сумму,
а не наоборот. Цена одного дня приходит из настроек, поэтому прайс меняется без правок кода.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

TRIAL_DURATION = timedelta(days=7)


class Tariff(StrEnum):
    TRIAL = "trial"
    # Ручная выдача из админки: срок задаётся явно, прайс к нему не применяется.
    GRANT = "grant"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


@dataclass(frozen=True, slots=True)
class TariffPlan:
    tariff: Tariff
    duration: timedelta
    # Скидка от стоимости того же срока «по дням»: 0.10 — минус 10%.
    discount: Decimal

    @property
    def days(self) -> int:
        return self.duration.days

    def price(self, day_price: Decimal) -> Decimal:
        """Цена срока с учётом скидки, округлённая до двух знаков."""
        full = day_price * self.days
        return (full * (Decimal(1) - self.discount)).quantize(Decimal("0.01"), ROUND_HALF_UP)


PLANS: dict[Tariff, TariffPlan] = {
    Tariff.TRIAL: TariffPlan(Tariff.TRIAL, TRIAL_DURATION, Decimal("1.00")),
    Tariff.GRANT: TariffPlan(Tariff.GRANT, timedelta(days=30), Decimal("1.00")),
    Tariff.DAY: TariffPlan(Tariff.DAY, timedelta(days=1), Decimal("0.00")),
    Tariff.WEEK: TariffPlan(Tariff.WEEK, timedelta(days=7), Decimal("0.00")),
    Tariff.MONTH: TariffPlan(Tariff.MONTH, timedelta(days=30), Decimal("0.10")),
    Tariff.YEAR: TariffPlan(Tariff.YEAR, timedelta(days=365), Decimal("0.30")),
}

# Платные тарифы в порядке показа в меню оплаты.
PAID_TARIFFS = (Tariff.DAY, Tariff.WEEK, Tariff.MONTH, Tariff.YEAR)


def plan(tariff: Tariff) -> TariffPlan:
    return PLANS[tariff]
