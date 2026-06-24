"""Market-data gateway: a thin façade over the per-venue source clients so the
services depend on one small interface (and tests can supply a fake).

Base prediction-market venues (Polymarket, Kalshi) are queried for every topic.
Derivative-implied markets (Fed rates, BTC, ...) come from the registry in
``app/markets`` and are discovered generically — no per-market branches here.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

import httpx

from app.config import Settings
from app.core.errors import SourceError
from app.core.http import make_client
from app.core.logging import get_logger
from app.markets import registered_markets
from app.markets._shared.registry import DerivativeMarket
from app.models.domain import MarketRef, OrderBookTop
from app.models.provenance import Venue
from app.sources import kalshi, polymarket_clob, polymarket_gamma

logger = get_logger(__name__)

_BASE_VENUES: list[Venue] = ["polymarket", "kalshi"]
# All venues = base prediction markets + every registered derivative market's venue.
_ALL_VENUES: list[Venue] = [*_BASE_VENUES, *(m.venue for m in registered_markets())]


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
        # Derivative markets from the registry: build each one's clients once.
        self._markets: list[DerivativeMarket] = registered_markets()
        self._market_clients: dict[str, dict[str, httpx.AsyncClient]] = {
            m.name: {key: make_client(url) for key, url in m.base_urls(settings).items()}
            for m in self._markets
        }

    async def discover(
        self, topic: str, *, venues: list[Venue] | None = None, limit: int = 50
    ) -> list[MarketRef]:
        want = set(venues or _ALL_VENUES)

        # Phase 1: base prediction-market venues (concurrent). Their refs feed dynamic
        # derivative markets (e.g. crypto derives Deribit targets from live PM/Kalshi markets).
        base_tasks: list = []
        base_labels: list[Venue] = []
        if "polymarket" in want:
            base_tasks.append(polymarket_gamma.discover(self._gamma, topic, limit=limit))
            base_labels.append("polymarket")
        if "kalshi" in want:
            mapped = self._settings.kalshi_series.get(topic)
            if isinstance(mapped, str):
                series_tickers: list[str] | None = [mapped]
            elif isinstance(mapped, list):
                series_tickers = [str(s) for s in mapped]
            else:
                series_tickers = None
            category = self._settings.kalshi_categories.get(topic)
            base_tasks.append(
                kalshi.discover(
                    self._kalshi, topic, limit=limit,
                    series_tickers=series_tickers, category=category,
                )
            )
            base_labels.append("kalshi")
        base_refs = self._collect(
            base_labels, await asyncio.gather(*base_tasks, return_exceptions=True)
        )

        # Phase 2: registered derivative markets (concurrent), given the base refs.
        deriv_tasks: list = []
        deriv_labels: list[Venue] = []
        for market in self._markets:
            if (
                market.venue in want
                and market.enabled(self._settings)
                and market.serves_topic(topic, self._settings)
            ):
                deriv_tasks.append(
                    market.discover(
                        self._market_clients[market.name], self._settings, topic,
                        limit=limit, prediction_refs=base_refs,
                    )
                )
                deriv_labels.append(market.venue)
        deriv_refs = self._collect(
            deriv_labels, await asyncio.gather(*deriv_tasks, return_exceptions=True)
        )
        return [*base_refs, *deriv_refs]

    @staticmethod
    def _collect(labels: list[Venue], results: list) -> list[MarketRef]:
        """Flatten gather results, dropping (and logging) any venue that raised."""
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
        clients: list[httpx.AsyncClient] = [self._gamma, self._clob, self._kalshi]
        for cmap in self._market_clients.values():
            clients.extend(cmap.values())
        await asyncio.gather(*(c.aclose() for c in clients), return_exceptions=True)
