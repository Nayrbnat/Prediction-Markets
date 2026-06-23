"""Factory for the shared async httpx client. Source clients receive a client
rather than constructing their own, so timeouts/headers/pooling are consistent.
"""

from __future__ import annotations

import time

import httpx

from app import __version__
from app.core.errors import RateLimitError, SourceError
from app.core.logging import get_logger
from app.core.rate_limit import AsyncRateLimiter, with_backoff

logger = get_logger(__name__)

_USER_AGENT = f"prediction-market-api/{__version__}"


def make_client(base_url: str = "", timeout: float = 15.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(timeout),
        headers={"user-agent": _USER_AGENT, "accept": "application/json"},
    )


async def fetch_json(
    client: httpx.AsyncClient,
    path: str,
    *,
    venue: str,
    limiter: AsyncRateLimiter,
    params: dict[str, str] | None = None,
) -> object:
    """GET ``path`` as JSON, rate-limited with 429 backoff, logging the boundary.

    Raises ``RateLimitError`` if throttling persists, ``SourceError`` for any other
    transport/HTTP failure.
    """

    async def _do() -> object:
        await limiter.acquire()
        start = time.perf_counter()
        resp = await client.get(path, params=params)
        latency_ms = round((time.perf_counter() - start) * 1000, 1)
        if resp.status_code == 429:
            logger.warning("source.throttled", extra={"venue": venue, "path": path})
            raise RateLimitError(f"{venue} returned 429 for {path}")
        logger.info(
            "source.call",
            extra={
                "venue": venue,
                "path": path,
                "status": resp.status_code,
                "latency_ms": latency_ms,
            },
        )
        resp.raise_for_status()
        return resp.json()

    try:
        return await with_backoff(_do, venue=venue)
    except RateLimitError:
        raise
    except httpx.HTTPError as exc:
        raise SourceError(f"{venue} request to {path} failed: {exc}") from exc
