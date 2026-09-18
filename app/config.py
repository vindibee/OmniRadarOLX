"""Централизованная конфигурация приложения.

Значения читаются из переменных окружения и файла ``.env``.
Вложенные группы разделяются двойным подчёркиванием: ``DB__HOST``, ``BOT__TOKEN``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class BotSettings(BaseModel):
    token: SecretStr
    # memory — состояние диалога живёт в процессе и теряется при перезапуске;
    # redis — переживает перезапуск и нужен, если экземпляров бота больше одного.
    fsm_storage: Literal["memory", "redis"] = "memory"
    redis_url: str = "redis://localhost:6379/0"


class DatabaseSettings(BaseModel):
    host: str = "localhost"
    port: int = 5432
    user: str = "omniradar"
    password: SecretStr = SecretStr("omniradar")
    name: str = "omniradar"
    echo: bool = False
    pool_size: int = Field(default=5, ge=1)
    max_overflow: int = Field(default=10, ge=0)

    @property
    def url(self) -> URL:
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.user,
            password=self.password.get_secret_value(),
            host=self.host,
            port=self.port,
            database=self.name,
        )


class HttpSettings(BaseModel):
    """Параметры HTTP-клиента парсеров (curl_cffi)."""

    impersonate: str = "chrome"
    timeout_seconds: float = Field(default=20.0, gt=0)
    max_retries: int = Field(default=3, ge=0)
    backoff_base_seconds: float = Field(default=1.5, gt=0)
    backoff_max_seconds: float = Field(default=60.0, gt=0)
    min_request_interval_seconds: float = Field(default=2.0, ge=0)
    proxy: str | None = None


class ParserSettings(BaseModel):
    """Общие для всех площадок параметры обхода выдачи."""

    page_size: int = Field(default=40, ge=1, le=50)
    # Сколько страниц максимум читать за один проход фильтра. Защита от бесконечного листания,
    # если площадка отдаёт выдачу без конца.
    max_pages: int = Field(default=5, ge=1)


class MonitoringSettings(BaseModel):
    interval_seconds: float = Field(default=60.0, ge=5)
    concurrency: int = Field(default=3, ge=1)
    # Объявление считается «новым», если опубликовано не раньше, чем фильтр создан минус это окно.
    # Так старые платные объявления, поднятые в топ, не приходят как новые.
    publish_grace_minutes: int = Field(default=10, ge=0)
    max_delivery_attempts: int = Field(default=3, ge=1)
    delivery_batch_size: int = Field(default=20, ge=1)
    max_subscriptions_per_user: int = Field(default=10, ge=1)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    bot: BotSettings
    db: DatabaseSettings = DatabaseSettings()
    http: HttpSettings = HttpSettings()
    parser: ParserSettings = ParserSettings()
    monitoring: MonitoringSettings = MonitoringSettings()
    enabled_marketplaces: list[str] = ["olx_ua"]
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
