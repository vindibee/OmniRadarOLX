"""Пресеты поиска: сохранённые формы Mini App и разворачивание их в рабочий фильтр."""

from collections.abc import Sequence

from app.domain.entities import Filter, SearchCriteria, SearchPreset
from app.domain.errors import PresetLimitExceededError, PresetNotFoundError
from app.services.filters import FilterService
from app.services.interfaces import UnitOfWorkFactory
from app.services.parsers.registry import ParserRegistry

NAME_MAX_LENGTH = 64


class PresetService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        parsers: ParserRegistry,
        filters: FilterService,
        *,
        max_presets_per_user: int = 20,
    ) -> None:
        self._uow_factory = uow_factory
        self._parsers = parsers
        self._filters = filters
        self._max_per_user = max_presets_per_user

    async def save(
        self, user_id: int, name: str, marketplace: str, criteria: SearchCriteria
    ) -> SearchPreset:
        # Те же проверки, что и при создании фильтра: пресет обязан быть рабочим.
        parser = self._parsers.get(marketplace)  # UnknownMarketplaceError
        parser.validate_criteria(criteria)  # InvalidCriteriaError

        async with self._uow_factory() as uow:
            existing = {preset.name for preset in await uow.presets.list_for_user(user_id)}
            if name not in existing and len(existing) >= self._max_per_user:
                raise PresetLimitExceededError(self._max_per_user)
            preset = await uow.presets.save(
                user_id=user_id,
                name=name.strip()[:NAME_MAX_LENGTH],
                marketplace=marketplace,
                criteria=criteria,
            )
            await uow.commit()
        return preset

    async def list(self, user_id: int) -> Sequence[SearchPreset]:
        async with self._uow_factory() as uow:
            return await uow.presets.list_for_user(user_id)

    async def delete(self, user_id: int, preset_id: int) -> None:
        async with self._uow_factory() as uow:
            if await uow.presets.get_for_user(preset_id, user_id) is None:
                raise PresetNotFoundError(preset_id)
            await uow.presets.delete(preset_id, user_id)
            await uow.commit()

    async def start_monitoring(self, user_id: int, preset_id: int) -> Filter:
        """Включает мониторинг по пресету — формат критериев у них общий, конвертация не нужна."""
        async with self._uow_factory() as uow:
            preset = await uow.presets.get_for_user(preset_id, user_id)
        if preset is None:
            raise PresetNotFoundError(preset_id)
        return await self._filters.create(user_id, preset.marketplace, preset.criteria)
