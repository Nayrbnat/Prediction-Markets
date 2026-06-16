"""Market-data gateway: a thin façade over the per-venue source clients so the
services depend on one small interface (and tests can supply a fake).
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from app.config import Settings
from app.core.errors import SourceError
from app.core.http import make_client
from app.core.logging import get_logger
from app.models.domain import MarketRef, OrderBookTop
from app.models.provenance import Venue
from app.sources import kalshi, polymarket_clob, polymarket_gamma

logger = get_logger(__name__)

_ALL_VENUES: list[Venue] = ["polymarket", "kalshi"]


class Gateway(Protocol):
    async def discover(
        self, topic: str, *, venues: list[Venue] | None = None, limit: int = 50
    ) -> list[MarketRef]: ...

    async def order_book(self, token_id: str) -> OrderBookTop | None: ...

    async def aclose(self) -> None: ...


class HttpGateway:
    """Concrete gateway backed by live httpx clients (one per source base URL)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._gamma = make_client(settings.gamma_base_url)
        self._clob = make_client(settings.clob_base_url)
        self._kalshi = make_client(settings.kalshi_base_url)

    async def discover(
        self, topic: str, *, venues: list[Venue] | None = None, limit: int = 50
    ) -> list[MarketRef]:
        want = set(venues or _ALL_VENUES)
        tasks: list = []
        labels: list[Venue] = []
        if "polymarket" in want:
            tasks.append(polymarket_gamma.discover(self._gamma, topic, limit=limit))
            labels.append("polymarket")
        if "kalshi" in want:
            series = self._settings.kalshi_series.get(topic)
            series_ticker = str(series) if isinstance(series, str) else None
            tasks.append(
                kalshi.discover(self._kalshi, topic, limit=limit, series_ticker=series_ticker)
            )
            labels.append("kalshi")

        results = await asyncio.gather(*tasks, return_exceptions=True)
        refs: list[MarketRef] = []
        for label, result in zip(labels, results, strict=False):
            if isinstance(result, BaseException):
                logger.warning(
                    "discover.venue_failed", extra={"venue": label, "error": str(result)}
                )
                continue
            refs.extend(result)
        return refs

    async def order_book(self, token_id: str) -> OrderBookTop | None:
        try:
            return await polymarket_clob.order_book(self._clob, token_id)
        except SourceError as exc:
            logger.warning("order_book.failed", extra={"token_id": token_id, "error": str(exc)})
            return None

    async def aclose(self) -> None:
        await asyncio.gather(
            self._gamma.aclose(),
            self._clob.aclose(),
            self._kalshi.aclose(),
            return_exceptions=True,
        )
