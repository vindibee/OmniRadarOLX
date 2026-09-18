from collections.abc import Sequence

from app.domain.entities import Filter, FoundListing, MarketplaceInfo, SearchCriteria, User
from app.domain.errors import FilterLimitExceededError, FilterNotFoundError
from app.services.interfaces import UnitOfWork, UnitOfWorkFactory
from app.services.parsers.registry import ParserRegistry

TITLE_MAX_LENGTH = 128


class FilterService:
    """Сценарии пользователя: регистрация и управление фильтрами поиска."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        parsers: ParserRegistry,
        *,
        max_filters_per_user: int,
    ) -> None:
        self._uow_factory = uow_factory
        self._parsers = parsers
        self._max_per_user = max_filters_per_user

    def marketplaces(self) -> Sequence[MarketplaceInfo]:
        return self._parsers.marketplaces()

    async def register_user(self, user_id: int, username: str | None, full_name: str) -> None:
        """Язык здесь не трогаем: его выбирает сам пользователь на онбординге в Mini App."""
        async with self._uow_factory() as uow:
            await uow.users.upsert(User(id=user_id, username=username, full_name=full_name))
            await uow.commit()

    async def get_user(self, user_id: int) -> User | None:
        async with self._uow_factory() as uow:
            return await uow.users.get(user_id)

    async def set_language(self, user_id: int, language_code: str) -> None:
        async with self._uow_factory() as uow:
            await uow.users.set_language(user_id, language_code)
            await uow.commit()

    async def history(
        self, user_id: int, filter_id: int, *, limit: int = 50, offset: int = 0
    ) -> Sequence[FoundListing]:
        """История находок фильтра. Чужой фильтр не отдаём — проверяем владельца."""
        async with self._uow_factory() as uow:
            await self._get_owned(uow, user_id, filter_id)
            return await uow.deliveries.history(filter_id, limit=limit, offset=offset)

    async def create(
        self, user_id: int, marketplace: str, criteria: SearchCriteria, *, title: str | None = None
    ) -> Filter:
        parser = self._parsers.get(marketplace)  # UnknownMarketplaceError, если площадки нет
        parser.validate_criteria(criteria)  # InvalidCriteriaError

        async with self._uow_factory() as uow:
            if await uow.filters.count_for_user(user_id) >= self._max_per_user:
                raise FilterLimitExceededError(self._max_per_user)
            search_filter = await uow.filters.add(
                user_id=user_id,
                marketplace=marketplace,
                title=(title or "").strip()[:TITLE_MAX_LENGTH] or _make_title(criteria),
                criteria=criteria,
            )
            await uow.commit()
        return search_filter

    async def list(self, user_id: int) -> Sequence[Filter]:
        async with self._uow_factory() as uow:
            return await uow.filters.list_for_user(user_id)

    async def toggle(self, user_id: int, filter_id: int) -> Filter:
        async with self._uow_factory() as uow:
            search_filter = await self._get_owned(uow, user_id, filter_id)
            await uow.filters.set_active(filter_id, not search_filter.is_active)
            await uow.commit()
            updated = await uow.filters.get_for_user(filter_id, user_id)
        if updated is None:
            raise FilterNotFoundError(filter_id)
        return updated

    async def delete(self, user_id: int, filter_id: int) -> None:
        async with self._uow_factory() as uow:
            await self._get_owned(uow, user_id, filter_id)
            await uow.filters.delete(filter_id)
            await uow.commit()

    @staticmethod
    async def _get_owned(uow: UnitOfWork, user_id: int, filter_id: int) -> Filter:
        search_filter = await uow.filters.get_for_user(filter_id, user_id)
        if search_filter is None:
            raise FilterNotFoundError(filter_id)
        return search_filter


def _make_title(criteria: SearchCriteria) -> str:
    parts = [criteria.query.strip()]
    if criteria.price_min is not None or criteria.price_max is not None:
        low = f"{criteria.price_min:,.0f}" if criteria.price_min is not None else "…"
        high = f"{criteria.price_max:,.0f}" if criteria.price_max is not None else "…"
        parts.append(f"({low} – {high})".replace(",", " "))
    return " ".join(parts)[:TITLE_MAX_LENGTH]
