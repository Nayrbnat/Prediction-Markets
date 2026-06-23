"""Per-source async rate limiting plus exponential-backoff-with-jitter retry on
429s. Pure mechanism; sources declare their own limits from config.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

from app.core.errors import RateLimitError
from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class AsyncRateLimiter:
    """Spaces calls to at most ``rate_per_sec`` by sleeping between acquisitions."""

    def __init__(self, rate_per_sec: float) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be positive")
        self._min_interval = 1.0 / rate_per_sec
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            wait = self._last + self._min_interval - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = asyncio.get_running_loop().time()


async def with_backoff(
    func: Callable[[], Awaitable[T]],
    *,
    venue: str,
    retries: int = 3,
    base_delay: float = 0.5,
) -> T:
    """Run ``func`` retrying on HTTP 429 / RateLimitError with jittered backoff.

    Raises ``RateLimitError`` if all retries are exhausted; other errors propagate.
    """
    attempt = 0
    while True:
        try:
            return await func()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 429 or attempt >= retries:
                raise
        except RateLimitError:
            if attempt >= retries:
                raise
        delay = base_delay * (2**attempt) + random.uniform(0, base_delay)
        logger.warning(
            "rate_limit.backoff",
            extra={"venue": venue, "attempt": attempt + 1, "sleep_s": round(delay, 3)},
        )
        await asyncio.sleep(delay)
        attempt += 1
