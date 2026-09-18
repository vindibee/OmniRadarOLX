"""Репозиторий пресетов поиска — сохранённых форм из Mini App."""

from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import SearchPresetModel
from app.domain.entities import SearchCriteria, SearchPreset


def _to_entity(model: SearchPresetModel) -> SearchPreset:
    return SearchPreset(
        id=model.id,
        user_id=model.user_id,
        name=model.name,
        marketplace=model.marketplace,
        criteria=SearchCriteria.from_json(model.criteria),
        created_at=model.created_at,
    )


class SqlAlchemyPresetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(
        self, *, user_id: int, name: str, marketplace: str, criteria: SearchCriteria
    ) -> SearchPreset:
        """Сохраняет пресет; повторное сохранение под тем же именем перезаписывает его."""
        stmt = (
            insert(SearchPresetModel)
            .values(
                user_id=user_id,
                name=name,
                marketplace=marketplace,
                criteria=criteria.to_json(),
            )
            .on_conflict_do_update(
                index_elements=[SearchPresetModel.user_id, SearchPresetModel.name],
                set_={"marketplace": marketplace, "criteria": criteria.to_json()},
            )
            .returning(SearchPresetModel)
        )
        model = (await self._session.scalars(stmt)).one()
        return _to_entity(model)

    async def get_for_user(self, preset_id: int, user_id: int) -> SearchPreset | None:
        model = await self._session.scalar(
            select(SearchPresetModel).where(
                SearchPresetModel.id == preset_id,
                SearchPresetModel.user_id == user_id,
            )
        )
        return _to_entity(model) if model else None

    async def list_for_user(self, user_id: int) -> Sequence[SearchPreset]:
        models = await self._session.scalars(
            select(SearchPresetModel)
            .where(SearchPresetModel.user_id == user_id)
            .order_by(SearchPresetModel.id)
        )
        return [_to_entity(m) for m in models]

    async def count_for_user(self, user_id: int) -> int:
        count = await self._session.scalar(
            select(func.count())
            .select_from(SearchPresetModel)
            .where(SearchPresetModel.user_id == user_id)
        )
        return count or 0

    async def delete(self, preset_id: int, user_id: int) -> None:
        await self._session.execute(
            delete(SearchPresetModel).where(
                SearchPresetModel.id == preset_id,
                SearchPresetModel.user_id == user_id,
            )
        )
