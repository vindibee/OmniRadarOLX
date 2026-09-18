"""HTTP-клиент для парсеров на curl_cffi.

curl_cffi подменяет TLS/HTTP2-отпечаток на отпечаток настоящего браузера (``impersonate``),
поэтому запросы не отсекаются антибот-защитой по JA3/Akamai-фингерпринту, как у requests/httpx.

Клиент добавляет то, что нужно для стабильной работы в фоне:
- тайм-ауты и повторы с экспоненциальной задержкой и джиттером;
- уважение ``Retry-After`` при 429;
- пересоздание сессии (новые cookies и TLS-соединение) после блокировки;
- минимальный интервал между запросами, чтобы не провоцировать антифрод.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException

from app.services.parsers.errors import (
    ParserBlockedError,
    ParserNetworkError,
    ParserResponseError,
)

logger = logging.getLogger(__name__)

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
BLOCKED_STATUSES = frozenset({401, 403})


@dataclass(frozen=True, slots=True)
class HttpClientOptions:
    impersonate: str = "chrome"
    timeout_seconds: float = 20.0
    max_retries: int = 3
    backoff_base_seconds: float = 1.5
    backoff_max_seconds: float = 60.0
    min_request_interval_seconds: float = 2.0
    proxy: str | None = None


class HttpClient:
    def __init__(
        self,
        options: HttpClientOptions,
        *,
        default_headers: Mapping[str, str] | None = None,
    ) -> None:
        self._options = options
        self._default_headers = dict(default_headers or {})
        self._session: AsyncSession[Any] | None = None
        self._throttle_lock = asyncio.Lock()
        self._last_request_at = 0.0

    async def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        last_error: Exception | None = None

        for attempt in range(self._options.max_retries + 1):
            if attempt:
                await asyncio.sleep(self._backoff_delay(attempt, last_error))

            await self._throttle()
            try:
                response = await self._get_session().get(
                    url, params=dict(params or {}), headers=dict(headers or {})
                )
            except RequestException as exc:  # тайм-аут, DNS, обрыв соединения, TLS
                last_error = ParserNetworkError(f"Сетевая ошибка: {exc}")
                logger.warning("GET %s: %s (попытка %d)", url, exc, attempt + 1)
                continue

            status = response.status_code
            if status in BLOCKED_STATUSES:
                last_error = ParserBlockedError(f"Доступ запрещён: HTTP {status}")
                logger.warning("GET %s: HTTP %d, пересоздаю сессию", url, status)
                await self._reset_session()
                continue
            if status in RETRYABLE_STATUSES:
                last_error = _RetryAfterError(status, response.headers.get("Retry-After"))
                logger.warning("GET %s: HTTP %d (попытка %d)", url, status, attempt + 1)
                continue
            if status >= 400:
                raise ParserResponseError(f"Неожиданный ответ: HTTP {status}")

            try:
                return response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                # Вместо JSON пришла HTML-страница — типичный признак challenge-страницы.
                await self._reset_session()
                raise ParserBlockedError("Ответ не является JSON (возможна капча)") from exc

        if isinstance(last_error, _RetryAfterError):
            if last_error.status == 429:
                raise ParserBlockedError("Превышен лимит запросов (HTTP 429)")
            raise ParserNetworkError(f"Сервер недоступен: HTTP {last_error.status}")
        raise last_error or ParserNetworkError("Запрос не выполнен")

    async def aclose(self) -> None:
        await self._reset_session()

    def _get_session(self) -> AsyncSession[Any]:
        if self._session is None:
            self._session = AsyncSession(
                impersonate=self._options.impersonate,
                timeout=self._options.timeout_seconds,
                proxy=self._options.proxy,
                headers=self._default_headers,
            )
        return self._session

    async def _reset_session(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            await session.close()

    async def _throttle(self) -> None:
        interval = self._options.min_request_interval_seconds
        if interval <= 0:
            return
        async with self._throttle_lock:
            wait = self._last_request_at + interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.monotonic()

    def _backoff_delay(self, attempt: int, error: Exception | None) -> float:
        if isinstance(error, _RetryAfterError) and error.retry_after is not None:
            return min(error.retry_after, self._options.backoff_max_seconds)
        delay = self._options.backoff_base_seconds * (2 ** (attempt - 1))
        return float(min(delay, self._options.backoff_max_seconds) * random.uniform(0.8, 1.2))


class _RetryAfterError(Exception):
    def __init__(self, status: int, retry_after: str | None) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.retry_after = float(retry_after) if retry_after and retry_after.isdigit() else None
