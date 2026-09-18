import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

from app.config import DatabaseSettings
from app.database import models  # noqa: F401 — регистрирует модели в metadata
from app.database.base import Base
from app.database.session import create_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_settings() -> DatabaseSettings:
    # Для миграций не нужен токен бота, поэтому читаем только группу DB__*.
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class MigrationSettings(BaseSettings):
        model_config = SettingsConfigDict(
            env_file=".env", env_nested_delimiter="__", extra="ignore"
        )
        db: DatabaseSettings = DatabaseSettings()

    return MigrationSettings().db


def run_migrations_offline() -> None:
    context.configure(
        url=_database_settings().url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_engine(_database_settings())
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
