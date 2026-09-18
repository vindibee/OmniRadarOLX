"""Реализация порта ``Cache`` на redis.asyncio.

Единственное место в проекте, которое знает про Redis как про кэш (FSM-хранилище
aiogram подключается отдельно, но к тому же серверу).
"""

from __future__ import annotations

from types import TracebackType
from typing import Self

from redis.asyncio import Redis


class RedisCache:
    def __init__(self, client: Redis, *, prefix: str = "cache:") -> None:
        self._client = client
        self._prefix = prefix

    @classmethod
    def from_url(cls, url: str, *, prefix: str = "cache:") -> Self:
        # decode_responses=True — работаем со строками, сериализация целиком на стороне вызова.
        return cls(Redis.from_url(url, decode_responses=True), prefix=prefix)

    async def get(self, key: str) -> str | None:
        value: str | None = await self._client.get(self._prefix + key)
        return value

    async def set(self, key: str, value: str, ttl_seconds: float) -> None:
        await self._client.set(self._prefix + key, value, px=_to_millis(ttl_seconds))

    async def add(self, key: str, value: str, ttl_seconds: float) -> bool:
        created = await self._client.set(
            self._prefix + key, value, px=_to_millis(ttl_seconds), nx=True
        )
        return bool(created)

    async def delete(self, key: str) -> None:
        await self._client.delete(self._prefix + key)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()


def _to_millis(seconds: float) -> int:
    return max(1, int(seconds * 1000))
