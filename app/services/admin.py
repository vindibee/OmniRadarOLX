"""Админка владельца: сводка, пользователи и ручная выдача доступа.

Права проверяются по Telegram id из подписанной initData — см. ``Settings.admin_ids``.
Сервис о проверке не знает: ему приносят уже проверенного администратора.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from app.domain.entities import Overview, Subscription, UserSummary
from app.services.billing import BillingService
from app.services.interfaces import UnitOfWorkFactory

MAX_GRANT_DAYS = 3650


class AdminService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        billing: BillingService,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._uow_factory = uow_factory
        self._billing = billing
        self._clock = clock

    async def overview(self) -> Overview:
        async with self._uow_factory() as uow:
            return await uow.stats.overview(self._clock())

    async def users(self, *, limit: int = 100) -> Sequence[UserSummary]:
        async with self._uow_factory() as uow:
            return await uow.stats.users(self._clock(), limit=limit)

    async def grant(self, user_id: int, days: int, *, by_admin_id: int) -> Subscription:
        days = max(1, min(days, MAX_GRANT_DAYS))
        return await self._billing.grant(user_id, days, by_admin_id=by_admin_id)
