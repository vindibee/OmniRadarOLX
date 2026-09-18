from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import FilterModel, UserModel
from app.domain.entities import Filter, SearchCriteria


def _to_entity(model: FilterModel) -> Filter:
    return Filter(
        id=model.id,
        user_id=model.user_id,
        marketplace=model.marketplace,
        title=model.title,
        criteria=SearchCriteria.from_json(model.criteria),
        is_active=model.is_active,
        created_at=model.created_at,
        last_checked_at=model.last_checked_at,
    )


class SqlAlchemyFilterRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self, *, user_id: int, marketplace: str, title: str, criteria: SearchCriteria
    ) -> Filter:
        model = FilterModel(
            user_id=user_id,
            marketplace=marketplace,
            title=title,
            criteria=criteria.to_json(),
            is_active=True,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)  # подтягиваем server_default (created_at)
        return _to_entity(model)

    async def get_for_user(self, filter_id: int, user_id: int) -> Filter | None:
        model = await self._session.scalar(
            select(FilterModel).where(
                FilterModel.id == filter_id,
                FilterModel.user_id == user_id,
            )
        )
        return _to_entity(model) if model else None

    async def list_for_user(self, user_id: int) -> Sequence[Filter]:
        models = await self._session.scalars(
            select(FilterModel).where(FilterModel.user_id == user_id).order_by(FilterModel.id)
        )
        return [_to_entity(m) for m in models]

    async def count_for_user(self, user_id: int) -> int:
        count = await self._session.scalar(
            select(func.count()).select_from(FilterModel).where(FilterModel.user_id == user_id)
        )
        return count or 0

    async def list_active(self, marketplaces: Sequence[str]) -> Sequence[Filter]:
        models = await self._session.scalars(
            select(FilterModel)
            .join(UserModel, UserModel.id == FilterModel.user_id)
            .where(
                FilterModel.is_active.is_(True),
                UserModel.is_active.is_(True),
                FilterModel.marketplace.in_(marketplaces),
            )
            .order_by(FilterModel.id)
        )
        return [_to_entity(m) for m in models]

    async def set_active(self, filter_id: int, is_active: bool) -> None:
        await self._session.execute(
            update(FilterModel).where(FilterModel.id == filter_id).values(is_active=is_active)
        )

    async def deactivate_for_user(self, user_id: int) -> None:
        await self._session.execute(
            update(FilterModel).where(FilterModel.user_id == user_id).values(is_active=False)
        )

    async def delete(self, filter_id: int) -> None:
        await self._session.execute(delete(FilterModel).where(FilterModel.id == filter_id))

    async def mark_checked(self, filter_id: int, checked_at: datetime) -> None:
        await self._session.execute(
            update(FilterModel)
            .where(FilterModel.id == filter_id)
            .values(last_checked_at=checked_at)
        )
