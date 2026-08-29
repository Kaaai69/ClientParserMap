import asyncio
import inspect
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.core.errors import SourceRequestError

Sleeper = Callable[[float], Awaitable[None] | None]


class ResilientHttpClient:
    """Rate-limited JSON client with bounded retries for transient failures."""

    RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_attempts: int = 3,
        backoff_base_seconds: float = 1.0,
        requests_per_second: float = 5.0,
        sleeper: Sleeper = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self._client = client
        self._max_attempts = max_attempts
        self._backoff_base_seconds = backoff_base_seconds
        self._minimum_interval = 1 / requests_per_second
        self._sleeper = sleeper
        self._jitter = jitter
        self._rate_lock = asyncio.Lock()
        self._last_request_started: float | None = None

    async def request_json(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        for attempt in range(1, self._max_attempts + 1):
            await self._wait_for_rate_limit()
            try:
                response = await self._client.request(method, url, **kwargs)
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                if attempt == self._max_attempts:
                    raise SourceRequestError(
                        "SOURCE_UNAVAILABLE",
                        "Источник временно недоступен",
                        retryable=True,
                    ) from error
                await self._sleep(self._backoff(attempt))
                continue

            if response.status_code in self.RETRYABLE_STATUSES:
                if attempt == self._max_attempts:
                    raise SourceRequestError(
                        "SOURCE_RETRY_EXHAUSTED",
                        "Источник временно недоступен после повторных попыток",
                        retryable=True,
                    )
                await self._sleep(self._retry_delay(response, attempt))
                continue

            if response.status_code >= 400:
                raise SourceRequestError(
                    f"SOURCE_HTTP_{response.status_code}",
                    "Источник отклонил запрос",
                    retryable=False,
                )

            try:
                payload = response.json()
            except ValueError as error:
                raise SourceRequestError(
                    "SOURCE_INVALID_JSON",
                    "Источник вернул некорректный ответ",
                    retryable=False,
                ) from error
            if not isinstance(payload, dict):
                raise SourceRequestError(
                    "SOURCE_INVALID_PAYLOAD",
                    "Источник вернул ответ неожиданного формата",
                    retryable=False,
                )
            return payload

        raise AssertionError("retry loop must return or raise")

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _wait_for_rate_limit(self) -> None:
        async with self._rate_lock:
            now = time.monotonic()
            if self._last_request_started is not None:
                remaining = self._minimum_interval - (now - self._last_request_started)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            self._last_request_started = time.monotonic()

    async def _sleep(self, delay: float) -> None:
        result = self._sleeper(delay)
        if inspect.isawaitable(result):
            await result

    def _backoff(self, attempt: int) -> float:
        multiplier = float(2 ** (attempt - 1))
        return self._backoff_base_seconds * multiplier + self._jitter()

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = _parse_retry_after(response.headers)
        return retry_after if retry_after is not None else self._backoff(attempt)


def _parse_retry_after(headers: Mapping[str, str]) -> float | None:
    raw_value = headers.get("Retry-After")
    if not raw_value:
        return None
    try:
        return max(0.0, float(raw_value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(raw_value)
        except (TypeError, ValueError, OverflowError):
            return None
        return max(0.0, retry_at.timestamp() - time.time())
