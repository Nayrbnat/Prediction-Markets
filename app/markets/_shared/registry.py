"""The derivative-market registry: the single seam the gateway and digest iterate.

Each vertical market under ``app/markets/<name>/`` defines a descriptor implementing
``DerivativeMarket`` and calls ``register(...)`` at import time. The gateway then
discovers all registered markets generically — no per-market ``if`` branches.

A "derivative market" contributes a derivative-implied distribution (futures or
options) for some topic, flowing through the existing source -> pricing -> snapshot
-> digest pipeline as its own venue. The base prediction-market venues (Polymarket,
Kalshi) are NOT registered here — they are cross-cutting and queried for every topic.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import httpx

from app.config import Settings
from app.models.domain import MarketRef
from app.models.provenance import Venue


@runtime_checkable
class DerivativeMarket(Protocol):
    """A self-contained derivative-implied market module (Fed rates, BTC, ...)."""

    name: str  # stable module name, e.g. "fed_rates"
    venue: Venue  # the venue string its observations are persisted under, e.g. "cme"
    signals: list[str]  # per-venue signal availability, e.g. ["futures-implied"]

    def base_urls(self, settings: Settings) -> dict[str, str]:
        """Logical client key -> base URL. The gateway builds one httpx client each."""
        ...

    def enabled(self, settings: Settings) -> bool:
        """Whether this market is switched on (config flag)."""
        ...

    def serves_topic(self, topic: str, settings: Settings) -> bool:
        """Whether this market should respond to the given ingest topic."""
        ...

    async def discover(
        self,
        clients: dict[str, httpx.AsyncClient],
        settings: Settings,
        topic: str,
        *,
        limit: int,
    ) -> list[MarketRef]:
        """Fetch + compute the derivative-implied MarketRefs for ``topic``."""
        ...


_REGISTRY: dict[str, DerivativeMarket] = {}


def register(market: DerivativeMarket) -> None:
    """Register a derivative market (idempotent by name)."""
    _REGISTRY[market.name] = market


def registered_markets() -> list[DerivativeMarket]:
    """All registered derivative markets, in registration order."""
    return list(_REGISTRY.values())
