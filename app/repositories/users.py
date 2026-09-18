from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import UserModel
from app.domain.entities import User


class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, user: User) -> None:
        stmt = insert(UserModel).values(
            id=user.id,
            username=user.username,
            full_name=user.full_name,
            is_active=user.is_active,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[UserModel.id],
            set_={
                "username": stmt.excluded.username,
                "full_name": stmt.excluded.full_name,
                "is_active": stmt.excluded.is_active,
            },
        )
        await self._session.execute(stmt)

    async def set_active(self, user_id: int, is_active: bool) -> None:
        await self._session.execute(
            update(UserModel).where(UserModel.id == user_id).values(is_active=is_active)
        )
